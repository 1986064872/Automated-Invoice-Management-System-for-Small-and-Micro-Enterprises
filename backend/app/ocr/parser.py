"""版面坐标解析器 —— 本项目的「识别」核心。

为什么不能直接用正则扫全文？
    电子发票 PDF 里的文字是绘图指令流，提取顺序是「先画所有静态标签、再画所有数值」，
    所以全文会变成「发票号码：开票日期：名称：…2600000000…2026年01月02日…」这种乱序。
    直接用正则扫，购买方/销售方的名称和税号就会串位。

正确做法：拿到每个文字块的 (x, y) 坐标
    → 按 y 聚成「行」
    → 按 x 排序还原左右顺序
    → 购买方在左半页、销售方在右半页，同名标签靠 x 顺序区分
    → 明细表按「从右往左取数字」解析，换行片段按 x 归回所属列

这个模块是纯函数，不依赖 PDF 库：本地 PDF 解析器和图片 OCR 解析器共用它。
"""

from __future__ import annotations

import re
import statistics
from typing import NamedTuple

from .base import InvoiceItemData, InvoiceResult, normalize_date, to_float

# 一个文字块：(x, y, 文本)
Fragment = tuple[float, float, str]

ROW_TOLERANCE = 3.5  # 同一行允许的 y 偏差（PDF 点数）

MONEY_RE = re.compile(r"^[¥￥]?\s*-?[\d,]+\.\d{1,2}$")
# 允许负号：增值税发票上的「折扣行 / 退货行」金额和税额都是负数，
# 不认负数的话这些行会被当成非明细行丢掉，还顺带把上一行的名称拼重复、让合计对不上。
NUMBER_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
PERCENT_RE = re.compile(r"^\d+(?:\.\d+)?%$")
# 计量单位：中文单字 / 常见拉丁缩写。
# 注意这只是**兜底**：主判据是表头定义的「单位列」位置（见 is_unit_fragment），
# 因为单位写法太多了 —— PCS / SET / KG / M2 之类靠词表列不全。
# ⚠️ 改这个字符集时务必保留「只」「批」—— 曾经重写时漏掉，
#    结果某一批工程票的单位全部掉进规格型号里（回归）。
UNIT_RE = re.compile(
    r"^(?:"
    r"[台个件套张次吨米只把批辆条份间项人月年组箱盒瓶卷支袋罐片块株棵付对升毫升]"
    r"|平方米|立方米|平方|立方|千米|公里|公斤|千克|克|厘米|毫米|毫升|延长米"
    r"|PCS?|PCE?S?|SETS?|PKGS?|PACKS?|BOX(?:ES)?|ROLLS?|BAGS?|PAIRS?|UNITS?|LOTS?"
    r"|KG|MG|G|T|L|ML|M2|M3|M²|M³|M|CM|MM|KM|KWH|KW|HP|PCS/SET"
    r")$",
    re.IGNORECASE,
)

LABEL_NAME = re.compile(r"名\s*称\s*[:：]")
LABEL_TAX_ID = re.compile(r"纳税人识别号\s*[:：]")
LABEL_NUMBER = re.compile(r"发\s*票\s*号\s*码\s*[:：]")
LABEL_CODE = re.compile(r"发\s*票\s*代\s*码\s*[:：]")
LABEL_DATE = re.compile(r"开\s*票\s*日\s*期\s*[:：]")
LABEL_TOTAL = re.compile(r"价税合计")
LABEL_TOTAL_SMALL = re.compile(r"[（(]\s*小\s*写\s*[)）]")

# 票面竖排的装饰性单字，取字段值时必须跳过
COLUMN_MARKERS = set("购买方信息销售合计备注发票密码区项目名称规格型号单位数量单价金额税率征收纳税人识别号")

# OCR 常见形近字符误认。实测：税号尾部的字母 X 会被 PaddleOCR 读成全角 ×，
# 不修正的话下面的正则会在 × 前停下，得到一个残缺税号。
_OCR_CHAR_FIXES = str.maketrans(
    {
        "×": "X",
        "х": "X",
        "Ⅹ": "X",
        "Ｘ": "X",
        "Ｏ": "O",
        "О": "O",
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "Ａ": "A",
        "Ｂ": "B",
        "Ｃ": "C",
        "Ｄ": "D",
        "Ｅ": "E",
        "Ｆ": "F",
        "Ｇ": "G",
        "Ｈ": "H",
    }
)


def normalize_code_text(text: str) -> str:
    """把编码类字段（发票号码 / 发票代码 / 税号）里的 OCR 形近字符拉回 ASCII。"""
    return (text or "").translate(_OCR_CHAR_FIXES)

# 置信度：直接读到标签给高分，推导出来给低一点，读不到给 0
CONF_LABELED = 0.98
CONF_ITEM = 0.92
CONF_DERIVED = 0.80
CONF_GUESSED = 0.60


# --------------------------------------------------------------------------
# 第一步：把文字块聚成行
# --------------------------------------------------------------------------
class Row(NamedTuple):
    """一行文字：y 是这一行的纵向位置，frags 是行内按 x 排好的碎片。"""

    y: float
    frags: list[tuple[float, str]]


def build_rows_with_y(
    fragments: list[Fragment], tolerance: float = ROW_TOLERANCE
) -> list[Row]:
    """按 y 聚行，保留每行的 y（下游要靠 y 判断「多近算同一条明细的文字」）。

    tolerance 是「同一行」允许的 y 偏差。**这里刻意取得比较紧**：
    它只负责把「数值行」准确切出来（同一数值行的碎片 y 几乎完全一致）。
    单元格内折行的文字与数值行常常相差大半行，不能靠加大容差去凑，
    否则会把相邻的表格行也并进来 —— 那部分交给 parse_items 里的邻近吸附逻辑。
    """
    cleaned = [(float(x), float(y), t.strip()) for x, y, t in fragments if t and t.strip()]
    cleaned.sort(key=lambda f: (-f[1], f[0]))

    rows: list[Row] = []
    for x, y, text in cleaned:
        target = None
        for row in rows:
            if abs(row.y - y) <= tolerance:
                target = row
                break
        if target is None:
            rows.append(Row(y=y, frags=[(x, text)]))
        else:
            target.frags.append((x, text))

    rows.sort(key=lambda r: -r.y)
    for row in rows:
        row.frags.sort(key=lambda f: f[0])
    return rows


def build_rows(
    fragments: list[Fragment], tolerance: float = ROW_TOLERANCE
) -> list[list[tuple[float, str]]]:
    """兼容旧调用：只要行内碎片，不要 y。"""
    return [row.frags for row in build_rows_with_y(fragments, tolerance)]


def row_text(frags: list[tuple[float, str]]) -> str:
    """行内文字拼成串（用空格分隔），用于判断这一行是什么。"""
    return " ".join(t for _, t in frags)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


# --------------------------------------------------------------------------
# 第二步：同一行里有多个同名标签时，按 x 顺序取各自的值
# --------------------------------------------------------------------------
def extract_labeled_pairs(frags: list[tuple[float, str]], label_re: re.Pattern) -> list[str]:
    """一行里可能有「购买方 名称：X」和「销售方 名称：Y」两处同名标签。

    返回按 x 从左到右排列的取值列表，即 [购买方值, 销售方值]。
    兼容两种情况：标签和值在同一个文字块里，或拆成两个块。

    注意：票面上「购买方信息」「销售方信息」是竖排的装饰性单字（购/买/方/信/息），
    它们和取值在同一行，必须跳过，否则会串进公司名里。
    """
    values: list[str] = []
    buffer: list[str] | None = None

    for _, text in frags:
        stripped = text.strip()
        m = label_re.search(text)
        if m:
            if buffer is not None:
                values.append("".join(buffer))
            rest = text[m.end():].strip()
            buffer = [rest] if rest else []
            continue
        if buffer is None:
            continue
        # 竖排列标识，丢弃
        if stripped and len(stripped) <= 2 and all(ch in COLUMN_MARKERS for ch in stripped):
            continue
        buffer.append(stripped)

    if buffer is not None:
        values.append("".join(buffer))

    return [_compact(v) for v in values]


def _first_value(frags: list[tuple[float, str]], label_re: re.Pattern) -> str:
    pairs = extract_labeled_pairs(frags, label_re)
    return pairs[0] if pairs else ""


# --------------------------------------------------------------------------
# 第三步：定位明细表
# --------------------------------------------------------------------------
def _is_header_row(frags: list[tuple[float, str]]) -> bool:
    t = _compact(row_text(frags))
    return "项目名称" in t and "税率" in t


def header_columns(frags: list[tuple[float, str]]) -> dict[str, float]:
    """从表头行里读出各列的 x 坐标 —— 它才是「这一列在哪」的权威定义。

    用途：
      ① 明细行折行时，判断碎片属于「项目名称」还是「规格型号」列；
      ② 判断某个碎块是不是「单位」—— 靠**列位置**而不是靠词表，
         否则遇到 PCS / SET / KG 这类拉丁单位就会漏判，被拼进规格型号里。

    实现要点：**先把整行碎片按 x 拼成一个字符串再找标签**。
    因为不同版式会把表头标签拆成单字碎片（实测有 `单` + `位`、`数` + `量`），
    逐个碎片做 startswith 匹配会全部落空。

    另外表头文字通常是**居中**在列里的，标签 x 是列中心而不是列左边界，
    所以列边界要取相邻标签的中点（下游 _wrap_target / unit_band 负责）。
    """
    ordered = sorted(frags, key=lambda f: f[0])

    # 拼成整行文本，并记下每个字符来自哪个碎片（用于把匹配位置映射回 x）
    text_parts: list[str] = []
    char_x: list[float] = []
    for x, raw in ordered:
        piece = _compact(raw)
        if not piece:
            continue
        text_parts.append(piece)
        char_x.extend([x] * len(piece))
    merged = "".join(text_parts)

    labels: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("name", ("项目名称",)),
        ("spec", ("规格型号",)),
        ("unit", ("计量单位", "单位")),
        ("qty", ("数量",)),
        ("price", ("单价",)),
    )
    cols: dict[str, float] = {}
    for key, names in labels:
        for name in names:
            index = merged.find(name)
            if index >= 0:
                cols[key] = char_x[index]
                break
    return cols


def unit_band(cols: dict[str, float]) -> tuple[float, float] | None:
    """「单位」列的 x 区间（取相邻表头标签的中点当边界）。拿不到就返回 None。"""
    x_spec = cols.get("spec")
    x_unit = cols.get("unit")
    if x_spec is None or x_unit is None or x_unit <= x_spec:
        return None
    start = (x_spec + x_unit) / 2
    right = cols.get("qty")
    if right is None or right <= x_unit:
        right = cols.get("price")
    if right is None or right <= x_unit:
        # 右边一列没解析出来，按单位列宽度推一个保守上界
        right = x_unit + (x_unit - x_spec)
    return start, (x_unit + right) / 2


# 单位不可能只由这些字符组成（纯上标/标点）。
# 实测：`m³` 被文本层拆成 `m` + `³`，`³` 单独落进单位列 —— 那不是单位，是规格的一部分。
_UNIT_JUNK_CHARS = set("²³¹⁰⁴⁵⁶⁷⁸⁹·.,、。*+-/\\'\"()（）[]【】　 ")


def is_unit_fragment(x: float, text: str, cols: dict[str, float]) -> bool:
    """这块文字是不是「单位」？

    优先看列位置（表头定义的 单位列 区间内就认），拿不到列信息时退回词表。
    两条路都要过「不是纯标点」这关，避免把上标之类的碎片当成单位。
    """
    stripped = text.strip()
    if not stripped:
        return False
    if set(stripped) <= _UNIT_JUNK_CHARS:
        return False

    band = unit_band(cols)
    if band is not None:
        start, end = band
        return start <= x <= end
    return bool(UNIT_RE.match(stripped))


def name_spec_boundary(cols: dict[str, float]) -> float | None:
    """「项目名称」列和「规格型号」列的分界 x。拿不到表头信息就返回 None。

    为什么不直接取两个表头标签的中点：
        表头文字是**居中**在列里的，标签 x 是**列中心**；而数据是**左对齐**的。
        当两列宽度差得多时（实测「项目名称」宽、「规格型号」窄），
        中点会落在名称列内部，把名称的最后一截（如「台下盆」）误判成规格。
        所以改用「规格型号标签 x 稍微往左一点」——规格数据基本贴着该列左边界开始。
    """
    x_spec = cols.get("spec")
    if x_spec is None:
        return None
    # 12 是给「标签居中导致的偏移」留的余量，对应约 1 个多字符宽度
    return x_spec - 12.0


def _wrap_target(x: float, cols: dict[str, float]) -> str | None:
    """折行/同一行的碎片该归到哪一列：'name' / 'spec' / None（不属于明细表文字列）。

    返回 None 的一律丢弃 —— 表格中部的水印、页码之类都在这里被挡掉。

    这里同时卡**左右两侧**的边界，退 None 的一律丢弃。
    左边界容易被忽略：实测发票预览截图在图片左边缘有零散单字（x≈10~17），
    而项目名称列的数据从 x≈215 才开始。只卡右边界的话，这些杂字会被当成名称收进去，
    名称前后各挂一段垃圾（`导*信息安全产品*…系统你全产`）。
    """
    x_name = cols.get("name")
    x_spec = cols.get("spec")
    boundary = name_spec_boundary(cols)
    if x_name is None or x_spec is None or boundary is None or boundary <= x_name:
        return None

    # 两个表头标签的间距可以当「列宽量级」用（标签是居中的，
    # 数据最多能比标签中心靠左约半个列宽，所以取一整段间距当余量足够安全）
    label_gap = x_spec - x_name
    if x < x_name - label_gap:
        return None

    x_unit = cols.get("unit")
    if x_unit is not None and x_unit > x_spec:
        # 规格列的上界取「规格型号标签」和「单位标签」的中点。
        # 这里用**标签本身的中点**是对的（两个标签都居中，偏移互相抵消）；
        # 而名称/规格的分界不能用中点 —— 原因见 name_spec_boundary 的说明。
        spec_end = (x_spec + x_unit) / 2
    else:
        # 单位列没识别出来时给个保守上界，避免把数量/金额列也算进规格
        spec_end = boundary + max(60.0, label_gap)

    if x < boundary:
        return "name"
    if x < spec_end:
        return "spec"
    return None


def _is_total_row(frags: list[tuple[float, str]]) -> bool:
    t = _compact(row_text(frags))
    if "价税合计" in t:
        return False
    # 「合   计」这一行：左边是"合计"两个字，右边是金额和税额
    if "合计" not in t:
        return False
    return any(MONEY_RE.match(txt) for _, txt in frags)


def _is_item_row(frags: list[tuple[float, str]]) -> bool:
    """明细行特征：带税率（如 13%），且至少有两个金额。"""
    has_rate = any(PERCENT_RE.match(t) for _, t in frags)
    money_count = sum(1 for _, t in frags if MONEY_RE.match(t))
    return has_rate and money_count >= 2


def _parse_item_row(
    frags: list[tuple[float, str]],
    cols: dict[str, float] | None = None,
) -> tuple[InvoiceItemData, float, float] | None:
    """解析一行明细。

    做法：从最右边往左依次"抠"出 税额→税率→金额→单价→数量→单位，
    剩下最左边的文字就是 项目名称 + 规格型号。
    返回 (明细, 名称列x, 规格列x)。

    关键：抠不到某列时**索引必须原地不动**，不能一路退到 -1。
    否则像「规格型号/单位/数量 这几列是空的」这种常见票面，
    最左边的项目名称会被一起吃掉，整行明细就没了。
    """
    ordered = sorted(frags, key=lambda f: f[0])

    def take_money(start: int) -> tuple[float | None, int]:
        j = start
        while j >= 0 and not MONEY_RE.match(ordered[j][1]):
            j -= 1
        if j < 0:
            return None, start          # 没找到：位置不动
        return to_float(ordered[j][1]), j - 1

    def take_number(start: int) -> tuple[float | None, int]:
        j = start
        while j >= 0 and not NUMBER_RE.match(ordered[j][1]):
            j -= 1
        if j < 0:
            return None, start
        return to_float(ordered[j][1]), j - 1

    i = len(ordered) - 1

    tax_amount, i = take_money(i)
    if MONEY_RE.match(ordered[-1][1]) is None:
        # 最右边连个金额都没有，基本不是明细行
        return None

    tax_rate = ""
    if i >= 0 and PERCENT_RE.match(ordered[i][1]):
        tax_rate = ordered[i][1].strip()
        i -= 1

    amount, i = take_money(i)
    unit_price, i = take_number(i)
    quantity, i = take_number(i)

    unit = ""
    # 单位列可能是空的，只有确认是单位才吃掉，否则留给规格型号。
    # 判断依据优先用表头定义的列位置（能认出 PCS 这类拉丁单位），拿不到才退回词表。
    if i >= 0 and is_unit_fragment(ordered[i][0], ordered[i][1], cols or {}):
        unit = ordered[i][1].strip()
        i -= 1

    head = ordered[: i + 1]
    if not head:
        return None

    name_parts: list[str] = []
    spec_parts: list[str] = []
    can_use_columns = bool(cols) and cols.get("name") is not None and cols.get("spec") is not None

    if can_use_columns:
        # 有表头列信息时**按 x 分配**，不能简单地「第一块是名称、其余是规格」——
        # 实测有些版式把名称拆成好几块（`*` + `配电控制设备` + `*` + `单联单控`），
        # 那样会把名称的前半段错误地塞进规格型号里。
        for x, text in head:
            target = _wrap_target(x, cols)
            if target == "spec":
                spec_parts.append(text)
            elif target == "name":
                name_parts.append(text)
            # target 为 None：不在名称/规格列范围内（水印、页码等），丢弃。
            # 早期版本把 None 当成 name，导致名称里出现重复片段。
    elif len(head) == 1:
        # 名称和规格挤在同一个文字块里，用 2 个以上空格或全角空格切开
        parts = re.split(r"\s{2,}|\u3000", head[0][1], maxsplit=1)
        name_parts = [parts[0]]
        if len(parts) > 1:
            spec_parts = [parts[1]]
    else:
        name_parts = [head[0][1]]
        spec_parts = [t for _, t in head[1:]]

    # 名称按「无分隔」拼接（中文名称被拆成单字碎片时要拼回原样，如 单联单控 + 开关）；
    # 规格按空格拼接（型号常被拆成 DEMO + AP01，中间本来就有空格）
    item_name = "".join(p.strip() for p in name_parts).strip()
    specification = " ".join(p.strip() for p in spec_parts).strip()
    name_x = head[0][0]
    spec_x = head[1][0] if len(head) > 1 else name_x + 80

    item = InvoiceItemData(
        item_name=item_name,
        specification=specification,
        unit=unit,
        quantity=quantity,
        unit_price=unit_price,
        tax_rate=tax_rate,
        amount=amount,
        tax_amount=tax_amount,
    )
    return item, name_x, spec_x


def _append_wrap(
    item: InvoiceItemData,
    frags: list[tuple[float, str]],
    cols: dict[str, float],
    name_x: float,
    spec_x: float,
) -> None:
    """把一行折行文字按列位置并进明细的名称 / 规格。列外的碎片丢弃。

    拼接一律**原样相接、不补空格**：单元格内的折行位置是渲染时定的，
    可能落在词中间（实测 `δ=30×1200×6` + `00/30kg/m3`、`带网络功` + `能`），
    补空格反而会破坏原文。也别用「两边都是字母数字就补空格」这种判断 ——
    中文的 str.isalnum() 也是 True，会把中文之间也塞上空格。
    """
    ordered = sorted(frags, key=lambda f: f[0])
    if cols:
        for x, text in ordered:
            target = _wrap_target(x, cols)
            if target == "name":
                item.item_name += text.strip()
            elif target == "spec":
                item.specification += text.strip()
        return
    # 表头没能解析出列坐标时退回中点判断
    mid = (name_x + spec_x) / 2 if spec_x > name_x else name_x + 40
    for x, text in ordered:
        if x <= mid:
            item.item_name += text.strip()
        else:
            item.specification += text.strip()


# 「像规格型号」的判据：纯 ASCII、含至少一个数字。
# 只用来处理「名称和型号被 OCR 粘在同一块」的情况，见 split_trailing_spec。
_SPEC_LIKE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-\.,/_+()*]*$")


def split_trailing_spec(item: InvoiceItemData) -> None:
    """把「粘在名称尾巴上的规格型号」挪回规格列。

    为什么需要：OCR 切块不稳定。同一批发票里，
    有时型号会单独成为一块（落在规格列的 x 上，按列位置就能分对），
    有时却和名称一起被识别成**同一块**：
        `*示例设备*示例设备总成 DEMO-QCB-7`
    这时它们 x 完全相同，靠列位置无论如何也分不开 —— 只能按文字本身拆。

    判据（力求保守，宁可不拆也别拆错）：
        · 只在「规格型号还空着」时动手，不覆盖已经分好的规格
        · 按**最后一个空白**切开，尾巴必须是纯 ASCII、含数字、长度 >= 3
          → `DX-QCB-7`、`75BC20`、`DX410-M` 会被挪走
          → `SFP端口,AC`（含中文）不会被误挪
    """
    if item.specification or not item.item_name:
        return
    text = item.item_name.strip()
    parts = [p for p in re.split(r"[\s\u3000]+", text) if p]
    if len(parts) < 2:
        return
    tail = parts[-1]
    if len(tail) < 3 or not any(ch.isdigit() for ch in tail):
        return
    if not _SPEC_LIKE.match(tail):
        return
    cut = text.rfind(tail)
    if cut <= 0:
        return
    item.item_name = text[:cut].strip()
    item.specification = tail


# 备注栏的竖排标签（「备注」两个字会拆成两行），横向位于内容左侧
REMARK_LABELS = {"备", "注", "备注"}
# 备注区下界：开票人这一行（再往下没有备注内容了）
ISSUER_RE = re.compile(r"(开票人|收款人|复核人)")


def parse_remark(rows: list[Row]) -> str:
    """提取发票左下角「备注」栏的内容。

    为什么需要：工程类发票的备注栏塞了很多关键信息 ——
    销方开户银行、银行账号、工程名称、工程地址。这些不记下来，
    台账里就看不出这张票是哪个项目的。

    版面（实测全电发票，y 越大越靠上）：
        价税合计（大写）… （小写）¥0,000.00            ← 上界
        销方开户银行:XX银行XX支行;  银行账号:00000000000000000;
        工程名称：XX工程总承包项目
        备                                        ← 「备注」是**竖排**标签，x≈17
        工程地址：XX市XX区XX路XX号
        注
        开票人： XXX                              ← 下界

    判据（靠结构，不靠关键词）：
        取「价税合计行」与「开票人行」之间、横向在**备注标签右侧**的文字。
        标签本身（'备'/'注'）按文字精确剔除 —— 不用 x 阈值一刀切，
        因为不同版式的标签列宽度不一样。

    注意：这里只认「紧邻的整行」，不按距离吸附 ——
    同类问题在前面踩过坑（行距太近，距离阈值区分不了相邻行）。
    """
    total_row = next(
        (r for r in rows if LABEL_TOTAL.search(_compact(row_text(r.frags)))), None
    )
    if total_row is None:
        return ""
    issuer_row = next((r for r in rows if ISSUER_RE.search(_compact(row_text(r.frags)))), None)

    label_x: float | None = None
    for row in rows:
        for x, text in row.frags:
            if text.strip() in REMARK_LABELS:
                label_x = x if label_x is None else min(label_x, x)

    lines: list[str] = []
    for row in rows:
        if row.y >= total_row.y:  # 价税合计行及其上方，不是备注
            continue
        if issuer_row is not None and row.y <= issuer_row.y:  # 开票人及以下
            continue
        pieces: list[str] = []
        for x, text in sorted(row.frags, key=lambda f: f[0]):
            stripped = text.strip()
            if stripped in REMARK_LABELS:
                continue  # 竖排的「备注」标签本身
            if label_x is not None and x <= label_x + 2:
                continue  # 标签左边（或就是标签那一列）
            pieces.append(stripped)
        if pieces:
            lines.append(" ".join(pieces))

    return "\n".join(lines)


def parse_items(
    rows: list[Row],
) -> tuple[list[InvoiceItemData], float | None, float | None, int | None]:
    """解析明细表，返回 (明细列表, 金额合计, 税额合计, 合计行下标)。

    行分组策略（这一块踩过坑，改动前务必读完）：
        数值列（数量/单价/金额/税额）在一行里是**垂直居中**的，y 几乎完全一致，
        所以「带数字的行」可以当**锚点**准确定位一条明细。
        但「项目名称」在单元格里会折成好几行，这些文字行的 y 会落在锚点的**上下两侧** ——
        实测某张图片票：名称第一段在数值行**上方 11px**、第二段在**下方 11px**。

        所以绝不能要求「所有碎片严格同一 y」：那会把名称拆散，
        只留下恰好与数值同行的那一段（实测就是只剩一个「撞系」，整条名称面目全非）。

        ⚠️ 也不要改成「按 y 距离把附近文字吸到最近锚点」：试过，
        吸附半径一放宽就会把**隔壁明细**的文字吸过来（规格从「带网络功能」变成「带网络功」、
        名称里冒出别的行的片段），一次赔掉 12 张票。
        正确做法是**只认紧邻的行**：从锚点向上 / 向下逐行吃掉连续的「纯文字行」
        （无金额、无税率），遇到表头、另一条明细的锚点、或已被吃过的行就停。
        已经被上一条明细吃过的行不会再给下一条，所以向上走不会抢到隔壁的折行。
    """
    header_idx = next((i for i, r in enumerate(rows) if _is_header_row(r.frags)), None)
    if header_idx is None:
        return [], None, None, None

    # 表头行定义了「项目名称 / 规格型号 / 单位 / 数量」各列的位置
    cols = header_columns(rows[header_idx].frags)

    total_idx = next(
        (i for i in range(header_idx + 1, len(rows)) if _is_total_row(rows[i].frags)), None
    )
    end = total_idx if total_idx is not None else len(rows)
    body = list(range(header_idx + 1, end))

    item_row_ids = [i for i in body if _is_item_row(rows[i].frags)]

    def is_wrap_row(j: int) -> bool:
        """是不是「折行文字行」：纯文字、没有金额也没有税率。

        有金额/税率的行是另一条明细或合计行，绝不能当折行吸进来。
        """
        return not any(
            PERCENT_RE.match(t) or MONEY_RE.match(t) for _, t in rows[j].frags
        )

    items: list[InvoiceItemData] = []
    consumed: set[int] = set()

    for idx in item_row_ids:
        parsed = _parse_item_row(rows[idx].frags, cols)
        if parsed is None:
            continue
        item, name_x, spec_x = parsed

        above: list[int] = []
        below: list[int] = []

        # ① 锚点**上方**紧邻的折行文字。
        #    为什么需要这一步：名称在单元格里折行时，第一段可能落在数值行**上方**
        #    （实测某图片票：名称第一段在数值行上方 11px、第二段在下方 11px）。
        #    旧逻辑只看锚点下方，结果只把「撞系」接上去，整条名称面目全非。
        #    向上走到表头或上一条明细的锚点就停；已经被上一条明细吃掉的跳过。
        j = idx - 1
        while (
            j > header_idx
            and j not in item_row_ids
            and j not in consumed
            and is_wrap_row(j)
        ):
            above.append(j)
            consumed.add(j)
            j -= 1

        # ② 锚点**下方**连续的折行文字（保持链式：一条名称折三行就吃三行）
        j = idx + 1
        while j < end and j not in item_row_ids and j not in consumed and is_wrap_row(j):
            below.append(j)
            consumed.add(j)
            j += 1

        # 按从上到下拼接（y 越大越靠上），名称顺序才对
        for j in sorted(above + below, key=lambda k: -rows[k].y):
            _append_wrap(item, rows[j].frags, cols, name_x, spec_x)

        # 都拼完了再处理「型号粘在名称尾巴上」的情况
        split_trailing_spec(item)

        items.append(item)

    # 合计行里取「金额」「税额」
    amount_total = tax_total = None
    if total_idx is not None:
        monies = [
            to_float(t)
            for _, t in sorted(rows[total_idx].frags, key=lambda f: f[0])
            if MONEY_RE.match(t)
        ]
        monies = [m for m in monies if m is not None]
        if len(monies) >= 2:
            amount_total, tax_total = monies[0], monies[1]
        elif len(monies) == 1:
            amount_total = monies[0]

    # 合计行没取到就用明细行累加
    if amount_total is None and items:
        amounts = [it.amount for it in items if it.amount is not None]
        if amounts:
            amount_total = round(sum(amounts), 2)
    if tax_total is None and items:
        taxes = [it.tax_amount for it in items if it.tax_amount is not None]
        if taxes:
            tax_total = round(sum(taxes), 2)

    return items, amount_total, tax_total, total_idx


# --------------------------------------------------------------------------
# 第四步：票种、价税合计
# --------------------------------------------------------------------------
_TYPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 容忍 OCR 在「电子发票」和括号之间插字（实测有把「电子发票（普通发票）」读成「电子发票统（普通发票）」）
    (re.compile(r"电子发票[^（(]{0,4}[（(]\s*([^）)]+?)\s*[)）]"), "电子发票（{0}）"),
    (re.compile(r"(增值税电子(?:普通|专用)发票)"), "{0}"),
    (re.compile(r"(增值税(?:普通|专用)发票)"), "{0}"),
    (re.compile(r"(电子发票)"), "{0}"),
    (re.compile(r"(机动车销售统一发票)"), "{0}"),
    (re.compile(r"(二手车销售统一发票)"), "{0}"),
]


def parse_invoice_type(full_text: str) -> str:
    for pattern, template in _TYPE_PATTERNS:
        m = pattern.search(full_text)
        if m:
            return template.format(m.group(1).strip())
    return "未知票种"


def parse_total_amount(rows: list[Row]) -> float | None:
    """从「价税合计（大写）…（小写）¥19250.00」这一行取价税合计。"""
    for row in rows:
        frags = row.frags
        joined = _compact(row_text(frags))
        if not LABEL_TOTAL.search(joined):
            continue
        # 优先取「（小写）」后面的金额
        texts = [t for _, t in sorted(frags, key=lambda f: f[0])]
        for i, text in enumerate(texts):
            if LABEL_TOTAL_SMALL.search(text):
                after = text[LABEL_TOTAL_SMALL.search(text).end():]
                m = re.search(r"[¥￥]?\s*([\d,]+\.\d{1,2})", after)
                if m:
                    return to_float(m.group(1))
        # 退而求其次：本行最右边的金额
        monies = [to_float(t) for t in texts if MONEY_RE.match(t)]
        monies = [m for m in monies if m is not None]
        if monies:
            return monies[-1]
    return None


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
def parse_invoice_fragments(
    fragments: list[Fragment],
    provider: str = "local",
    tolerance: float = ROW_TOLERANCE,
) -> InvoiceResult:
    """把 (x, y, 文本) 文字块流解析成标准发票结构。"""
    result = InvoiceResult(provider=provider)
    rows = build_rows_with_y(fragments, tolerance=tolerance)
    if not rows:
        return result

    full_text = "\n".join(row_text(r.frags) for r in rows)
    flat_text = _compact(full_text)
    conf: dict[str, float] = {}

    # ---------- 票种 / 号码 / 日期 ----------
    result.invoice_type = parse_invoice_type(full_text)

    for row in rows:
        frags = row.frags
        joined = _compact(row_text(frags))
        if not result.invoice_number and LABEL_NUMBER.search(joined):
            value = normalize_code_text(_first_value(frags, LABEL_NUMBER))
            m = re.search(r"\d{8,25}", value)
            if m:
                result.invoice_number = m.group()
                conf["invoice_number"] = CONF_LABELED
        if not result.invoice_code and LABEL_CODE.search(joined):
            value = normalize_code_text(_first_value(frags, LABEL_CODE))
            m = re.search(r"\d{10,12}", value)
            if m:
                result.invoice_code = m.group()
                conf["invoice_code"] = CONF_LABELED
        if not result.invoice_date and LABEL_DATE.search(joined):
            result.invoice_date = normalize_date(_first_value(frags, LABEL_DATE))
            if result.invoice_date:
                conf["invoice_date"] = CONF_LABELED

    # 兜底：整页扫一遍
    if not result.invoice_number:
        m = re.search(r"发票号码[:：]?\s*(\d{8,25})", flat_text)
        if m:
            result.invoice_number = m.group(1)
            conf["invoice_number"] = CONF_GUESSED
    if not result.invoice_date:
        m = LABEL_DATE.search(flat_text)
        if m:
            result.invoice_date = normalize_date(flat_text[m.end(): m.end() + 20])
            if result.invoice_date:
                conf["invoice_date"] = CONF_GUESSED

    # ---------- 购销方：同一行取两处标签，左=购买方，右=销售方 ----------
    for row in rows:
        frags = row.frags
        joined = _compact(row_text(frags))
        if not result.buyer_name and LABEL_NAME.search(joined):
            pairs = extract_labeled_pairs(frags, LABEL_NAME)
            if len(pairs) >= 2:
                result.buyer_name = pairs[0]
                result.seller_name = pairs[1]
                conf["buyer_name"] = CONF_LABELED
                conf["seller_name"] = CONF_LABELED
            elif len(pairs) == 1:
                # 只有一列：用 y 位置和后面的税号行对齐判断，退化情况先记成销售方
                result.seller_name = pairs[0]
                conf["seller_name"] = CONF_GUESSED
        if not result.seller_tax_id and LABEL_TAX_ID.search(joined):
            pairs = extract_labeled_pairs(frags, LABEL_TAX_ID)
            tax_ids = [
                m.group()
                for p in pairs
                if (m := re.search(r"[0-9A-Za-z]{15,20}", normalize_code_text(p)))
            ]
            if len(tax_ids) >= 2:
                result.buyer_tax_id = tax_ids[0]
                result.seller_tax_id = tax_ids[1]
                conf["buyer_tax_id"] = CONF_LABELED
                conf["seller_tax_id"] = CONF_LABELED
            elif len(tax_ids) == 1:
                result.seller_tax_id = tax_ids[0]
                conf["seller_tax_id"] = CONF_GUESSED

    # ---------- 明细 + 合计 ----------
    items, amount_total, tax_total, total_idx = parse_items(rows)
    result.items = items
    if items:
        conf["items"] = CONF_ITEM

    # ---------- 发票备注栏 ----------
    result.remark = parse_remark(rows)
    if result.remark:
        conf["remark"] = CONF_LABELED

    result.amount_without_tax = amount_total
    result.tax_amount = tax_total
    if amount_total is not None:
        conf["amount_without_tax"] = CONF_ITEM if items else CONF_DERIVED
    if tax_total is not None:
        conf["tax_amount"] = CONF_ITEM if items else CONF_DERIVED

    # ---------- 价税合计 ----------
    total = parse_total_amount(rows)
    if total is not None:
        result.total_amount = total
        conf["total_amount"] = CONF_LABELED
    elif amount_total is not None and tax_total is not None:
        result.total_amount = round(amount_total + tax_total, 2)
        conf["total_amount"] = CONF_DERIVED
        result.warnings.append("票面未读到价税合计，已按「不含税金额 + 税额」推导")

    # ---------- 缺项互相补齐 ----------
    if result.amount_without_tax is None and total is not None and tax_total is not None:
        result.amount_without_tax = round(total - tax_total, 2)
        conf["amount_without_tax"] = CONF_DERIVED
    if result.tax_amount is None and total is not None and amount_total is not None:
        result.tax_amount = round(total - amount_total, 2)
        conf["tax_amount"] = CONF_DERIVED

    result.field_confidence = conf
    result.raw = {
        "provider": provider,
        "row_count": len(rows),
        "fragment_count": len(fragments),
        "text_preview": full_text[:2000],
    }
    return result
