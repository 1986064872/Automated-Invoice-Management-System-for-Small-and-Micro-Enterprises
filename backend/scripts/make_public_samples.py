"""把真实发票「重建」成可以公开的脱敏样本（方案 B · 重建版）。

为什么是「重建」而不是「涂抹」
------------------------------
先走的是 PyMuPDF `add_redact_annot` + `apply_redactions` 那条路，**行不通**：
涂抹后输出文件的 /Contents 变成 20+ 元素的流数组，原始文字仍在里面 ——
**PyMuPDF 读不到，pypdf 读得到**（实测 14/20 个文件仍能提取真实开票人姓名）。
而且文件从 230KB 涨到 9.5MB，还有一张票的明细行 24 → 0。

所以改成**从零造一个新 PDF**：
    新页面（同尺寸）→ 重绘表格线 → 重新嵌入税局监制章 → 只写匿名化后的文字 → 画假二维码
结果**不含任何原始对象**，不管用哪个库、哪种方式读，都只能读到假值。

文字位置怎么保证和原版一样
--------------------------
每个 span 都带 `origin`（基线起点）和 `size`（字号），直接照抄；
字体也按原 span 的字体族选（票面解出来是 宋体 / 楷体 / CourierNew），
字宽就能对上，不会出现「用 Courier 写宋体数字导致超出页面被裁」那种问题。

真实值放哪
----------
真实值放 `anonymize_map.local.json`（已被 .gitignore 忽略），模板见 `anonymize_map.example.json`。
脚本本身不含任何真实信息。

用法
----
    python backend/scripts/make_public_samples.py --limit 2   # 先试 2 张
    python backend/scripts/make_public_samples.py            # 全部
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pymupdf
import pypdf

sys.stdout.reconfigure(encoding="utf-8")

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
SRC_DIR = PROJECT_DIR / "素材" / "测试发票"
OUT_DIR = BACKEND_DIR / "testdata" / "测试发票公开版"

MAP_LOCAL = Path(__file__).with_name("anonymize_map.local.json")
MAP_EXAMPLE = Path(__file__).with_name("anonymize_map.example.json")

# 左上角二维码的固定位置（本批 20 张版式一致，实测 bbox）
QR_REGION = (18.0, 17.0, 75.0, 74.0)
# 税局监制章（政府印章，不含企业信息，保留）
SEAL_MIN_WIDTH = 50

COMPANY_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9]{2,40}?(?:有限责任公司|股份有限公司|有限公司)")
NUMBER20_RE = re.compile(r"(?<!\d)\d{20}(?!\d)")
TAXID_RE = re.compile(r"(?<![0-9A-Za-z])[0-9A-Z]{15,20}(?![0-9A-Za-z])")

FONT_DIR = Path(os.environ.get("PUBLIC_SAMPLE_FONT_DIR") or r"C:\Windows\Fonts")
CJK_FONTS = {
    "宋体": FONT_DIR / "simsun.ttc",
    "楷体": FONT_DIR / "simkai.ttf",
    "微软雅黑": FONT_DIR / "msyh.ttc",
}


def load_rules() -> dict:
    if not MAP_LOCAL.exists():
        raise SystemExit(
            f"缺少映射表：{MAP_LOCAL}\n"
            f"请复制 {MAP_EXAMPLE.name} 为 {MAP_LOCAL.name} 并填入真实值。"
        )
    data = json.loads(MAP_LOCAL.read_text(encoding="utf-8"))
    for key in ("company_name", "tax_id", "person_name", "remark_lines"):
        if key not in data:
            raise SystemExit(f"映射表缺少字段：{key}")
    return data


class NumberAllocator:
    """给发票号码分配「前 N 位保留 + 后 8 位唯一序号」。

    为什么不能直接把后 8 位全归零：前 12 位相同的票会变成同一个号码。
    本批 20 张里实测有 4 组撞车（9 张票），去重逻辑会把它们判成「重复票」。
    """

    def __init__(self, cfg: dict):
        self.keep = int(cfg.get("keep_prefix", 12))
        self.digits = int(cfg.get("suffix_digits", 8))
        self.start = int(cfg.get("start_at", 1))
        self._map: dict[str, str] = {}

    def __call__(self, real: str) -> str:
        if real not in self._map:
            prefix = real[: self.keep]
            seq = self.start + len(self._map)
            self._map[real] = f"{prefix}{seq:0{self.digits}d}"
        return self._map[real]

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._map)


# ==========================================================================
# 版面工具
# ==========================================================================
def real_font_name(raw: str) -> str:
    """PyMuPDF 给的字体名是 UTF-8 字节被按 latin-1 解出来的，还原一下。"""
    try:
        return raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw


def page_spans(page) -> list[dict]:
    out: list[dict] = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                text = span["text"].strip()
                if not text:
                    continue
                out.append(
                    {
                        "id": len(out),
                        "text": text,
                        "bbox": tuple(span["bbox"]),
                        "origin": tuple(span["origin"]),
                        "size": float(span["size"]),
                        "font": real_font_name(span["font"]),
                    }
                )
    return out


# 自己按 y 聚行，**不要用 PyMuPDF 的 line 分组**：
# 实测它会把同一行的 span 拆进不同的 line 对象（y 差 0.3pt 就分家），
# 于是「南京」+「aa」+「电器有限公司」拼不出完整公司名，替换会漏。
ROW_TOL = 2.5


def group_rows(spans: list[dict]) -> list[list[dict]]:
    rows: list[dict] = []
    for s in sorted(spans, key=lambda s: (s["bbox"][1], s["bbox"][0])):
        for row in rows:
            if abs(row["y"] - s["bbox"][1]) <= ROW_TOL:
                row["spans"].append(s)
                break
        else:
            rows.append({"y": s["bbox"][1], "spans": [s]})
    for row in rows:
        row["spans"].sort(key=lambda s: s["bbox"][0])
    return [row["spans"] for row in rows]


def remark_band(spans: list[dict]) -> tuple[float, float] | None:
    """备注区上下界：价税合计行的底 → 开票人行的顶（和 parser.parse_remark 同一套结构判据）。"""
    total = next((s for s in spans if "价税合计" in s["text"]), None)
    issuer = next((s for s in spans if "开票人" in s["text"]), None)
    if total is None or issuer is None:
        return None
    return total["bbox"][3], issuer["bbox"][1]


def font_for_span(span: dict) -> tuple[str, str | None]:
    """按原 span 的字体族选字体，保证字宽一致。

    票面解出来是三种：宋体（正文）、楷体（标题）、CourierNew（税号）。
    ASCII 数字也走原字体（宋体的数字是半角，和票面宽度一致）——
    之前用 Courier 写宋体数字，20 位会宽出 18pt 直接超出页面被裁掉。
    """
    name = span["font"]
    if "ourier" in name:
        return "cour", None
    for cn, path in CJK_FONTS.items():
        if cn in name and path.exists():
            return cn, str(path)
    for cn, path in CJK_FONTS.items():
        if path.exists():
            return cn, str(path)
    return "china-s", None


def write_text(page, origin, text: str, size: float, font: tuple[str, str | None]) -> None:
    fontname, fontfile = font
    kwargs = {"fontsize": size, "fontname": fontname}
    if fontfile:
        kwargs["fontfile"] = fontfile
    page.insert_text(origin, text, **kwargs)


# ==========================================================================
# 假二维码
# ==========================================================================
def draw_fake_qr(page, rect: tuple[float, float, float, float], seed: str) -> None:
    """画一张「看着像二维码、但扫不出任何东西」的假码。"""
    x0, y0, x1, y1 = rect
    n = 25
    cell = (x1 - x0) / n
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    bits: list[int] = []
    i = 0
    while len(bits) < n * n:
        byte = digest[i % len(digest)]
        bits.extend((byte >> k) & 1 for k in range(8))
        i += 1

    finders = [(0, 0), (0, n - 7), (n - 7, 0)]

    def is_finder(r: int, c: int) -> bool:
        return any(br <= r < br + 7 and bc <= c < bc + 7 for br, bc in finders)

    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(x0, y0, x1, y1))
    shape.finish(color=None, fill=(1, 1, 1))
    for row in range(n):
        for col in range(n):
            if is_finder(row, col) or not bits[row * n + col]:
                continue
            shape.draw_rect(
                pymupdf.Rect(x0 + col * cell, y0 + row * cell,
                             x0 + (col + 1) * cell, y0 + (row + 1) * cell)
            )
    for br, bc in finders:
        shape.draw_rect(pymupdf.Rect(x0 + bc * cell, y0 + br * cell,
                                     x0 + (bc + 7) * cell, y0 + (br + 7) * cell))
        shape.draw_rect(pymupdf.Rect(x0 + (bc + 1) * cell, y0 + (br + 1) * cell,
                                     x0 + (bc + 6) * cell, y0 + (br + 6) * cell))
        shape.draw_rect(pymupdf.Rect(x0 + (bc + 2) * cell, y0 + (br + 2) * cell,
                                     x0 + (bc + 5) * cell, y0 + (br + 5) * cell))
    shape.finish(color=None, fill=(0, 0, 0))
    shape.commit()


# ==========================================================================
# 匿名化「计划」：算出每个 span 该写什么
# ==========================================================================
def plan_page(page, rules: dict, alloc: NumberAllocator) -> tuple[list[dict], list[str], list[str]]:
    """返回 (要写的文字列表, 替换说明, 本页用到的假票号)。

    被替换的 span 不会原样出现；其余 span 一律原样保留。
    """
    spans = page_spans(page)
    if not spans:
        return [], [], []

    band = remark_band(spans)
    consumed: set[int] = set()
    plan: list[dict] = []
    notes: list[str] = []
    numbers: list[str] = []

    def emit(span: dict, text: str) -> None:
        plan.append(
            {"origin": span["origin"], "text": text, "size": span["size"],
             "font": font_for_span(span)}
        )

    # ---- 1) 备注区整块替换（结构判据）----
    if band is not None:
        top, bottom = band
        band_spans = [
            s for s in spans
            if s["bbox"][1] >= top - 1 and s["bbox"][1] <= bottom - 1 and s["bbox"][0] >= 27
        ]
        if band_spans:
            for s in band_spans:
                consumed.add(s["id"])
            first = min(band_spans, key=lambda s: (s["bbox"][1], s["bbox"][0]))
            for i, text in enumerate(list(rules["remark_lines"])[:2]):
                plan.append(
                    {
                        "origin": (first["origin"][0], first["origin"][1] + i * 9.4),
                        "text": text,
                        "size": first["size"],
                        "font": font_for_span(first),
                    }
                )
            notes.append(f"备注区 {len(band_spans)} 段 → 假内容 {len(list(rules['remark_lines'])[:2])} 行")

    # ---- 2) 行级精确替换（公司名 / 税号 / 人名 / 票号）----
    pairs: list[tuple[str, str]] = []
    pairs += list(rules["company_name"].items())
    pairs += list(rules["tax_id"].items())
    pairs += list(rules["person_name"].items())

    for row in group_rows(spans):
        live = [s for s in row if s["id"] not in consumed]
        if not live:
            continue
        joined = "".join(s["text"] for s in live)
        index_of: list[tuple[int, int, dict]] = []
        pos = 0
        for s in live:
            index_of.append((pos, pos + len(s["text"]), s))
            pos += len(s["text"])

        local_pairs = list(pairs)
        for m in NUMBER20_RE.finditer(joined):
            real = m.group()
            if any(real == r for r, _ in pairs):
                continue
            fake = alloc(real)
            local_pairs.append((real, fake))
            numbers.append(fake)

        for real, fake in local_pairs:
            start = 0
            while True:
                hit = joined.find(real, start)
                if hit < 0:
                    break
                start = hit + 1
                covered = [s for a, b, s in index_of if a >= hit and b <= hit + len(real)]
                # 要求「被覆盖的 span 拼起来正好等于目标串」——挡掉跨边界假匹配
                if not covered or "".join(s["text"] for s in covered) != real:
                    continue
                if any(s["id"] in consumed for s in covered):
                    continue
                for s in covered:
                    consumed.add(s["id"])
                emit(min(covered, key=lambda s: s["bbox"][0]), fake)
                notes.append(f"{real} → {fake}")

    # ---- 3) 其余 span 原样保留 ----
    # 「下载次数：N」是第三方工具盖的章，不属于发票内容，丢掉
    for s in spans:
        if s["id"] in consumed or "下载次数" in s["text"]:
            continue
        emit(s, s["text"])

    return plan, notes, numbers


# ==========================================================================
# 重建单页
# ==========================================================================
def rebuild_page(src_page, out_doc, rules: dict, alloc: NumberAllocator):
    plan, notes, numbers = plan_page(src_page, rules, alloc)
    page = out_doc.new_page(width=src_page.rect.width, height=src_page.rect.height)

    # 1) 重绘表格线等矢量图元
    for drawing in src_page.get_drawings():
        shape = page.new_shape()
        for item in drawing["items"]:
            kind = item[0]
            try:
                if kind == "l":
                    shape.draw_line(item[1], item[2])
                elif kind == "re":
                    shape.draw_rect(item[1])
                elif kind == "qu":
                    shape.draw_quad(item[1])
                elif kind == "c":
                    shape.draw_bezier(item[1], item[2], item[3], item[4])
            except Exception:  # 单个图元坏了不能拖垮整页
                continue
        cap = drawing.get("lineCap")
        join = drawing.get("lineJoin")
        kwargs = {
            "color": drawing.get("color"),
            "fill": drawing.get("fill"),
            "width": drawing.get("width") or 0.5,
            "closePath": bool(drawing.get("closePath")),
            "lineCap": cap[0] if isinstance(cap, (tuple, list)) else (cap or 0),
            "lineJoin": join if isinstance(join, (int, float)) else 0,
        }
        if drawing.get("dashes"):
            kwargs["dashes"] = drawing["dashes"]
        try:
            shape.finish(**kwargs)
        except Exception:
            shape.finish(color=drawing.get("color"), width=drawing.get("width") or 0.5)
        shape.commit()

    # 2) 重新嵌入税局监制章（政府印章，不含企业信息）
    #    注意 xref_object 是 Document 的方法，不是 pymupdf 的模块函数
    #    （写错会被下面的 except 吞掉，表现成「印章静默消失」）
    src_doc = src_page.parent
    for annot in src_page.annots() or []:
        if annot.type[1] != "Stamp" or annot.rect.width < SEAL_MIN_WIDTH:
            continue
        try:
            ap = src_doc.xref_object(annot.xref, compressed=False)
            form = re.search(r"/N (\d+) 0 R", ap)
            if not form:
                continue
            inner = src_doc.xref_object(int(form.group(1)), compressed=False)
            ref = re.search(r"/Im\d+ (\d+) 0 R", inner)
            if not ref:
                continue
            img = src_doc.extract_image(int(ref.group(1)))
            page.insert_image(annot.rect, stream=img["image"])
        except Exception as exc:  # noqa: BLE001
            print(f"      [警告] 印章嵌入失败：{type(exc).__name__}: {exc}")

    # 3) 写文字（匿名化后的）
    for item in plan:
        write_text(page, item["origin"], item["text"], item["size"], item["font"])

    # 4) 假二维码
    if src_page.get_image_info() or src_page.get_drawings():
        draw_fake_qr(page, QR_REGION, seed="public-sample")

    return notes, numbers


# ==========================================================================
# 校验：必须用「独立于生成工具的读取器」
# ==========================================================================
def verify_pdf(path: Path, rules: dict, alloc: NumberAllocator) -> list[str]:
    """用 pypdf（独立读取器）+ 原始字节 + 白名单，三重校验。

    ⚠️ 不能只用 PyMuPDF 校验 —— 它是生成工具，属于自证清白。
    上一版就是这么翻车的：PyMuPDF 看全干净，pypdf 读出 14/20 个真实姓名。
    """
    allow_company = set(rules["company_name"].values())
    allow_tax = set(rules["tax_id"].values())
    allow_number = set(alloc.mapping.values())
    problems: list[str] = []

    raw = path.read_bytes()
    reader = pypdf.PdfReader(str(path))
    text = "\n".join(p.extract_text() for p in reader.pages)

    # 1) 白名单：出现的任何公司名 / 税号 / 票号都必须是允许的假值
    for name in COMPANY_RE.findall(text):
        if name not in allow_company:
            problems.append(f"未授权的公司名：{name}")
    for token in TAXID_RE.findall(text):
        if len(token) == 18 and token not in allow_tax:
            problems.append(f"未授权的 18 位码：{token}")
    for token in NUMBER20_RE.findall(text):
        if token not in allow_number:
            problems.append(f"未授权的 20 位号码：{token}")

    # 2) 黑名单：映射表里的真实值一个都不许出现（pypdf 视图）
    for bucket in ("company_name", "tax_id", "person_name"):
        for real in rules[bucket]:
            if real in text:
                problems.append(f"真实值残留（pypdf）：{real}")

    # 3) 原始字节
    for real in list(rules["company_name"]) + list(rules["tax_id"]):
        if real.encode("utf-16-be") in raw:
            problems.append(f"真实值残留（字节）：{real}")

    # 4) 二维码必须换成假码
    for pno, pg in enumerate(pymupdf.open(str(path))):
        for info in pg.get_image_info():
            x0, y0, x1, y1 = info["bbox"]
            if x0 < QR_REGION[2] and y0 < QR_REGION[3] and x1 > QR_REGION[0] and y1 > QR_REGION[1]:
                problems.append(f"第 {pno+1} 页二维码位置仍有原始图片")

    return problems


# ==========================================================================
# 主流程
# ==========================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="生成脱敏公开版发票样本（重建版）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 张（调试用）")
    args = ap.parse_args()

    rules = load_rules()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*.pdf"):
        old.unlink()

    pdfs = sorted(SRC_DIR.glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]

    alloc = NumberAllocator(rules["invoice_number"])

    print(f"源目录：{SRC_DIR}")
    print(f"输出目录：{OUT_DIR}")
    print(f"待处理 PDF：{len(pdfs)} 个")
    print()
    for idx, src in enumerate(pdfs, start=1):
        src_doc = pymupdf.open(str(src))
        out_doc = pymupdf.open()
        notes: list[str] = []
        numbers: list[str] = []
        for src_page in src_doc:
            page_notes, page_numbers = rebuild_page(src_page, out_doc, rules, alloc)
            notes += page_notes
            numbers += page_numbers

        out_doc.set_metadata(
            {
                "title": "示例电子发票",
                "author": "示例数据",
                "subject": "",
                "keywords": "",
                "creator": "示例数据生成器",
                "producer": "示例数据生成器",
            }
        )
        out_doc.subset_fonts()  # 不做子集化的话字体整个嵌进去，文件会涨到 9MB
        stem = f"{idx:02d}_" + (numbers[0] if numbers else "无号码")
        dst = OUT_DIR / f"{stem}.pdf"
        out_doc.save(str(dst), garbage=4, deflate=True)
        out_doc.close()
        src_doc.close()
        print(f"  [{idx:02d}] {src.name[:42]:44} → {dst.name}  （{dst.stat().st_size//1024} KB，{len(notes)} 处替换）")

    print()
    print(f"发票号码映射 {len(alloc.mapping)} 个，唯一：{len(set(alloc.mapping.values())) == len(alloc.mapping)}")

    # ---- 校验 ----
    print()
    print("=" * 74)
    print("校验：pypdf（独立读取器）+ 原始字节 + 白名单，三重")
    print("=" * 74)
    outs = sorted(OUT_DIR.glob("*.pdf"))
    if not outs:
        print("输出目录里没有 PDF。")
        return 1
    bad = 0
    for path in outs:
        problems = verify_pdf(path, rules, alloc)
        if problems:
            bad += 1
            print(f"  ✘ {path.name}")
            for p in problems[:5]:
                print(f"      {p}")
        else:
            print(f"  ✔ {path.name}")
    print()
    if bad:
        print(f"❌ {bad}/{len(outs)} 个文件未通过校验")
        return 1
    print(f"✅ 全部 {len(outs)} 个文件通过三重校验")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
