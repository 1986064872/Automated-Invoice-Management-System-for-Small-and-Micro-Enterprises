"""Excel 导出（项目书第十二章 + 用户实际操作习惯）。

规范：
- 工作表名「费用明细」，另附「导出说明」
- 一行 = **一条明细行**（不是一张发票），这样「单位 / 数量 / 单价」才有地方放
- 首行冻结 + 自动筛选
- 日期统一 yyyy-mm-dd，金额保留两位小数
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

SHEET_NAME = "费用明细"

# (表头, 列宽, 水平对齐)
# 前 6 列 + 分类/科目/状态/文件名是「发票级」字段；中间 8 列是「明细行级」字段
COLUMNS: list[tuple[str, int, str]] = [
    ("开票日期", 12, "center"),
    ("发票类型", 22, "left"),
    ("发票代码", 14, "center"),
    ("发票号码", 22, "center"),
    ("销售方名称", 30, "left"),
    ("销售方税号", 22, "left"),
    ("项目名称", 34, "left"),
    ("规格型号", 20, "left"),
    ("单位", 7, "center"),
    ("数量", 9, "right"),
    ("单价", 18, "right"),
    ("不含税金额", 13, "right"),
    ("税额", 11, "right"),
    ("价税合计", 13, "right"),
    ("费用分类", 14, "left"),
    ("会计科目", 20, "left"),
    ("状态", 10, "center"),
    ("原文件名", 38, "left"),
    ("备注", 24, "left"),
]

# 数字列（1 基）→ 格式
NUMBER_FORMATS: dict[int, str] = {
    10: "#,##0.####",      # 数量：整数不显示小数点，有小数才显示
    11: "#,##0.00####",    # 单价：保留票面原始精度（不含税单价常常很长）
    12: "#,##0.00",
    13: "#,##0.00",
    14: "#,##0.00",
}

DATE_FORMAT = "yyyy-mm-dd"

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
# 「本行有明细备注」时高亮备注单元格（浅黄）。
# 用途：折扣/退货这类金额为负的行，用户会在备注里说明原因，
# 高亮一下对账时不容易看漏。只染备注这一格，不去动其他列的颜色语义。
REMARK_FILL = PatternFill("solid", fgColor="FFE699")
# 负数（折扣/退货）用红色字体，会计惯例
NEGATIVE_FONT = Font(color="C00000")
NOTE_COLUMN = len(COLUMNS)  # 备注是最后一列
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

STATUS_TEXT = {
    "pending_review": "待复核",
    "confirmed": "已入账",
}


def _to_date(value):
    """能转成 date 就转，转不了就原样返回字符串。"""
    if isinstance(value, (date, datetime)):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return text


def _style_header(ws: Worksheet) -> None:
    for idx, (title, width, _align) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=idx, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.row_dimensions[1].height = 24


def fill_sheet(ws: Worksheet, rows: list[dict]) -> None:
    """把账目数据写进工作表。rows 是已经展开到明细行的字典列表。"""
    _style_header(ws)

    for r, row in enumerate(rows, start=2):
        values = [
            _to_date(row.get("entry_date")),
            row.get("invoice_type") or "",
            row.get("invoice_code") or "",
            row.get("invoice_number") or "",
            row.get("seller_name") or "",
            row.get("seller_tax_id") or "",
            row.get("item_name") or "",
            row.get("specification") or "",
            row.get("unit") or "",
            row.get("quantity"),
            row.get("unit_price"),
            row.get("amount_without_tax"),
            row.get("tax_amount"),
            row.get("total_amount"),
            row.get("expense_category") or "",
            row.get("account_subject") or "",
            STATUS_TEXT.get(row.get("status", ""), row.get("status") or ""),
            row.get("original_name") or "",
            row.get("note") or "",
        ]
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.border = BORDER
            align = COLUMNS[c - 1][2]
            cell.alignment = Alignment(horizontal=align, vertical="center")
            if c in NUMBER_FORMATS:
                cell.number_format = NUMBER_FORMATS[c]
                # 负数（票面的折扣/退货）用红色字体 —— 会计惯例，扫一眼就能看出是哪一行
                if isinstance(value, (int, float)) and value < 0:
                    cell.font = NEGATIVE_FONT
            elif c == 1 and isinstance(value, (date, datetime)):
                cell.number_format = DATE_FORMAT

        # 备注格高亮：写了备注的行，以及**金额为负**的折扣/退货行
        # （后者不依赖用户有没有写备注 —— 实测用户导出后发现什么都没标）
        if row.get("note_highlight"):
            ws.cell(row=r, column=NOTE_COLUMN).fill = REMARK_FILL

    last_col = get_column_letter(len(COLUMNS))
    # 首行冻结 + 自动筛选
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{last_col}{max(len(rows) + 1, 1)}"


def build_workbook(rows: list[dict]) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    fill_sheet(ws, rows)

    # 加一个说明表，方便财务知道这份表的来源和口径
    meta = wb.create_sheet("导出说明")
    meta.column_dimensions["A"].width = 20
    meta.column_dimensions["B"].width = 62
    invoice_count = len({(r.get("invoice_code"), r.get("invoice_number")) for r in rows})
    meta_rows = [
        ("导出时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("明细行数", len(rows)),
        ("涉及发票数", invoice_count),
        ("数量合计", round(sum((r.get("quantity") or 0) for r in rows), 4)),
        ("不含税金额合计", round(sum((r.get("amount_without_tax") or 0) for r in rows), 2)),
        ("税额合计", round(sum((r.get("tax_amount") or 0) for r in rows), 2)),
        ("价税合计合计", round(sum((r.get("total_amount") or 0) for r in rows), 2)),
        ("来源", "企业智能票据记账助手 V1 —— 账本页按筛选条件导出"),
        (
            "口径说明",
            "一行 = 一条发票明细。「不含税金额/税额/价税合计」为该明细行的值，"
            "同一张发票的多行相加正好等于票面合计，不会重复计数；"
            "没有解析出明细的票据退化为票面合计并只占一行。"
            "「备注」列 = 本行备注（折扣/退货行没写就填「票面折扣/退货行」）"
            "+ 【票面备注】（识别自发票备注栏的工程名称/开户银行等，同一张票的每行都带）"
            "+ 识别来源说明。",
        ),
        ("用法", "首行已冻结并启用筛选，可直接按费用分类/日期/销售方做透视。"),
    ]
    remark_rows = sum(1 for r in rows if r.get("note_highlight"))
    negative_rows = sum(
        1 for r in rows if isinstance(r.get("amount_without_tax"), (int, float))
        and r["amount_without_tax"] < 0
    )
    if remark_rows or negative_rows:
        bits = []
        if negative_rows:
            bits.append(
                f"有 {negative_rows} 行的不含税金额为负（票面的**折扣/退货行**），已用红色字体标出"
            )
        if remark_rows:
            bits.append(
                f"有 {remark_rows} 行的备注单元格填了浅黄底色（写过备注的行，以及折扣/退货行）"
            )
        meta_rows.append(("标注说明", "；".join(bits) + "。对账时请留意这些行。"))
    for r, (key, value) in enumerate(meta_rows, start=1):
        kc = meta.cell(row=r, column=1, value=key)
        kc.font = Font(bold=True)
        kc.alignment = Alignment(vertical="center")
        vc = meta.cell(row=r, column=2, value=value)
        vc.alignment = Alignment(vertical="center", wrap_text=True)
    return wb


def write_excel(rows: list[dict], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb = build_workbook(rows)
    wb.save(out_path)
    return out_path
