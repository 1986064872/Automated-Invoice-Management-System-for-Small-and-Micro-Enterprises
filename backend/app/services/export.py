"""Excel 导出：默认按进项/销项/待判断拆分工作表。

规范：
- 一张工作表 = 一个票据方向；另附「汇总」「导出说明」
- 一行 = 一条发票明细行，避免丢掉单位/数量/单价
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

from .direction import (
    DIRECTION_ORDER,
    DIRECTION_TEXT,
    SHEET_BY_DIRECTION,
)

SUMMARY_SHEET_NAME = "汇总"
META_SHEET_NAME = "导出说明"

# (表头, 列宽, 水平对齐)
# 前 10 列是发票级字段，中间 8 列是明细行级字段。
COLUMNS: list[tuple[str, int, str]] = [
    ("开票日期", 12, "center"),
    ("发票类型", 22, "left"),
    ("发票代码", 14, "center"),
    ("发票号码", 22, "center"),
    ("往来单位", 30, "left"),
    ("往来单位税号", 22, "left"),
    ("销售方名称", 30, "left"),
    ("销售方税号", 22, "left"),
    ("购买方名称", 30, "left"),
    ("购买方税号", 22, "left"),
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
    14: "#,##0.####",      # 数量：整数不显示小数点，有小数才显示
    15: "#,##0.00####",    # 单价：保留票面原始精度（不含税单价常常很长）
    16: "#,##0.00",
    17: "#,##0.00",
    18: "#,##0.00",
}

DATE_FORMAT = "yyyy-mm-dd"

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
REMARK_FILL = PatternFill("solid", fgColor="FFE699")
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


COUNTERPARTY_HEADER = {
    "input": "往来单位（销售方）",
    "output": "往来单位（购买方）",
    "unknown": "往来单位（待判断）",
}


def _style_header(ws: Worksheet, *, direction: str | None = None) -> None:
    for idx, (title, width, _align) in enumerate(COLUMNS, start=1):
        if idx == 5 and direction in COUNTERPARTY_HEADER:
            title = COUNTERPARTY_HEADER[direction]
        cell = ws.cell(row=1, column=idx, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.row_dimensions[1].height = 24


def fill_sheet(ws: Worksheet, rows: list[dict], *, direction: str | None = None) -> None:
    """把账目数据写进工作表。rows 是已经展开到明细行的字典列表。"""
    _style_header(ws, direction=direction)

    for r, row in enumerate(rows, start=2):
        values = [
            _to_date(row.get("entry_date")),
            row.get("invoice_type") or "",
            row.get("invoice_code") or "",
            row.get("invoice_number") or "",
            row.get("counterparty_name") or "",
            row.get("counterparty_tax_id") or "",
            row.get("seller_name") or "",
            row.get("seller_tax_id") or "",
            row.get("buyer_name") or "",
            row.get("buyer_tax_id") or "",
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
                if isinstance(value, (int, float)) and value < 0:
                    cell.font = NEGATIVE_FONT
            elif c == 1 and isinstance(value, (date, datetime)):
                cell.number_format = DATE_FORMAT

        if row.get("note_highlight"):
            ws.cell(row=r, column=NOTE_COLUMN).fill = REMARK_FILL

    last_col = get_column_letter(len(COLUMNS))
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{last_col}{max(len(rows) + 1, 1)}"


def _invoice_count(rows: list[dict]) -> int:
    ids = {r.get("_invoice_id") for r in rows if r.get("_invoice_id")}
    if ids:
        return len(ids)
    return len({(r.get("invoice_code"), r.get("invoice_number")) for r in rows})


def _direction_stats(rows: list[dict]) -> list[dict]:
    stats = []
    for direction in DIRECTION_ORDER:
        subset = [r for r in rows if r.get("direction") == direction]
        stats.append(
            {
                "direction": direction,
                "label": DIRECTION_TEXT[direction],
                "invoice_count": _invoice_count(subset),
                "item_count": len(subset),
                "amount_without_tax": round(
                    sum((r.get("amount_without_tax") or 0) for r in subset), 2
                ),
                "tax_amount": round(sum((r.get("tax_amount") or 0) for r in subset), 2),
                "total_amount": round(sum((r.get("total_amount") or 0) for r in subset), 2),
            }
        )
    return stats


def _create_summary_sheet(wb: Workbook, rows: list[dict]) -> Worksheet:
    ws = wb.create_sheet(SUMMARY_SHEET_NAME)
    headers = [
        "票据方向",
        "发票数",
        "明细行数",
        "不含税金额",
        "税额",
        "价税合计",
    ]
    for idx, title in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=idx, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
    widths = (14, 10, 12, 16, 14, 16)
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    stats = _direction_stats(rows)
    for r, stat in enumerate(stats, start=2):
        values = [
            stat["label"],
            stat["invoice_count"],
            stat["item_count"],
            stat["amount_without_tax"],
            stat["tax_amount"],
            stat["total_amount"],
        ]
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.border = BORDER
            cell.alignment = Alignment(
                horizontal="left" if c == 1 else "right",
                vertical="center",
            )
            if c in (4, 5, 6):
                cell.number_format = "#,##0.00"

    total_row = len(stats) + 2
    total_values = [
        "合计",
        _invoice_count(rows),
        len(rows),
        round(sum((r.get("amount_without_tax") or 0) for r in rows), 2),
        round(sum((r.get("tax_amount") or 0) for r in rows), 2),
        round(sum((r.get("total_amount") or 0) for r in rows), 2),
    ]
    for c, value in enumerate(total_values, start=1):
        cell = ws.cell(row=total_row, column=c, value=value)
        cell.border = BORDER
        cell.font = Font(bold=True)
        cell.alignment = Alignment(
            horizontal="left" if c == 1 else "right",
            vertical="center",
        )
        if c in (4, 5, 6):
            cell.number_format = "#,##0.00"
    ws.freeze_panes = "A2"
    return ws


def _create_meta_sheet(
    wb: Workbook,
    rows: list[dict],
    *,
    company_name: str = "",
    company_tax_id: str = "",
) -> Worksheet:
    meta = wb.create_sheet(META_SHEET_NAME)
    meta.column_dimensions["A"].width = 20
    meta.column_dimensions["B"].width = 72

    meta_rows = [
        ("导出时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("当前企业", company_name or "未设置"),
        ("纳税人识别号", company_tax_id or "未设置"),
        ("明细行数", len(rows)),
        ("涉及发票数", _invoice_count(rows)),
        ("数量合计", round(sum((r.get("quantity") or 0) for r in rows), 4)),
        ("不含税金额合计", round(sum((r.get("amount_without_tax") or 0) for r in rows), 2)),
        ("税额合计", round(sum((r.get("tax_amount") or 0) for r in rows), 2)),
        ("价税合计合计", round(sum((r.get("total_amount") or 0) for r in rows), 2)),
        ("来源", "企业智能票据记账助手 V1 —— 账本页按筛选条件导出"),
        (
            "分类口径",
            "购买方是当前企业的发票归入「进项发票」；销售方是当前企业的发票归入"
            "「销项发票」；双方都匹配或都无法匹配时归入「待判断」。",
        ),
        (
            "金额口径",
            "一行 = 一条发票明细。「不含税金额/税额/价税合计」为该明细行的值，"
            "同一张发票的多行相加正好等于票面合计，不会重复计数；"
            "没有解析出明细的票据退化为票面合计并只占一行。",
        ),
        (
            "备注口径",
            "「备注」列 = 本行备注（折扣/退货行没写就填「票面折扣/退货行」）"
            "+ 【票面备注】（识别自发票备注栏的工程名称/开户银行等，同一张票的每行都带）"
            "+ 识别来源说明。",
        ),
        ("用法", "每个方向 sheet 首行已冻结并启用筛选，可直接按日期/往来单位/项目筛选。"),
    ]
    for r, (key, value) in enumerate(meta_rows, start=1):
        kc = meta.cell(row=r, column=1, value=key)
        kc.font = Font(bold=True)
        kc.alignment = Alignment(vertical="center")
        vc = meta.cell(row=r, column=2, value=value)
        vc.alignment = Alignment(vertical="center", wrap_text=True)
    return meta


def build_workbook(
    rows: list[dict],
    *,
    company_name: str = "",
    company_tax_id: str = "",
) -> Workbook:
    wb = Workbook()
    # 默认 sheet 不要，后面只保留有内容的方向 sheet。
    wb.remove(wb.active)

    _create_summary_sheet(wb, rows)
    for direction in DIRECTION_ORDER:
        subset = [r for r in rows if r.get("direction") == direction]
        if not subset:
            continue
        ws = wb.create_sheet(SHEET_BY_DIRECTION[direction])
        fill_sheet(ws, subset, direction=direction)

    _create_meta_sheet(
        wb,
        rows,
        company_name=company_name,
        company_tax_id=company_tax_id,
    )
    return wb


def write_excel(
    rows: list[dict],
    out_path: Path,
    *,
    company_name: str = "",
    company_tax_id: str = "",
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb = build_workbook(
        rows,
        company_name=company_name,
        company_tax_id=company_tax_id,
    )
    wb.save(out_path)
    return out_path
