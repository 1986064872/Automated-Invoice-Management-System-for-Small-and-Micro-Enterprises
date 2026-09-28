"""账本查询：列表页和 Excel 导出共用同一套筛选，保证「导出的和看到的一致」。

注意：账本直接以 invoice 为主体（而不是只读 ledger_entry），
这样「待复核」的票也能出现在账本里接受筛选 —— 项目书第五章账本页要求「状态筛选」。
已确认入账的票才带 ledger_entry 快照。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session, joinedload

from ..models import Invoice, InvoiceFile, InvoiceStatus, LedgerEntry

STATUS_ALL = "all"


def month_bounds(month: str) -> tuple[str, str]:
    """'2026-08' → ('2026-08-01', '2026-08-31')。"""
    year, mon = month.split("-")
    y, m = int(year), int(mon)
    first = date(y, m, 1)
    last = date(y + (m == 12), (m % 12) + 1, 1)
    end = date.fromordinal(last.toordinal() - 1)
    return first.isoformat(), end.isoformat()


def build_query(
    db: Session,
    *,
    month: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    invoice_ids: list[str] | None = None,
) -> Query:
    query = (
        db.query(Invoice)
        .options(joinedload(Invoice.file), joinedload(Invoice.items), joinedload(Invoice.ledger_entry))
        .join(InvoiceFile, Invoice.file_id == InvoiceFile.id)
    )

    # 只导出勾选的那些票（账本批量导出用）。传空列表 = 什么都没选，直接返回空结果 ——
    # 千万别把空列表当成「不过滤」，否则会静默导出全部，属于严重误操作。
    if invoice_ids is not None:
        if not invoice_ids:
            return query.filter(Invoice.id.is_(None))
        query = query.filter(Invoice.id.in_(invoice_ids))

    if month:
        try:
            start, end = month_bounds(month)
            query = query.filter(Invoice.invoice_date >= start, Invoice.invoice_date <= end)
        except (ValueError, IndexError):
            pass

    if date_from:
        query = query.filter(Invoice.invoice_date >= date_from)
    if date_to:
        query = query.filter(Invoice.invoice_date <= date_to)

    if status and status != STATUS_ALL:
        query = query.filter(Invoice.status == status)

    if category:
        query = query.filter(Invoice.expense_category == category)

    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Invoice.invoice_number.like(like),
                Invoice.seller_name.like(like),
                Invoice.buyer_name.like(like),
                InvoiceFile.original_name.like(like),
                Invoice.expense_category.like(like),
            )
        )

    return query.order_by(
        Invoice.invoice_date.desc().nullslast(),
        Invoice.created_at.desc(),
    )


def summarise(invoices: list[Invoice]) -> dict:
    """汇总卡片上的数字。"""
    by_category: dict[str, dict] = {}
    total = untaxed = tax = 0.0

    for inv in invoices:
        total += inv.total_amount or 0
        untaxed += inv.amount_without_tax or 0
        tax += inv.tax_amount or 0
        key = inv.expense_category or "待分类"
        bucket = by_category.setdefault(key, {"category": key, "count": 0, "total_amount": 0.0})
        bucket["count"] += 1
        bucket["total_amount"] = round(bucket["total_amount"] + (inv.total_amount or 0), 2)

    return {
        "count": len(invoices),
        "total_amount": round(total, 2),
        "amount_without_tax": round(untaxed, 2),
        "tax_amount": round(tax, 2),
        "by_category": sorted(
            by_category.values(), key=lambda x: x["total_amount"], reverse=True
        ),
    }


def ledger_row(inv: Invoice) -> dict:
    """账本表格的一行（Excel 导出也用同一份数据）。"""
    entry: LedgerEntry | None = inv.ledger_entry
    inv_file = inv.file
    names = [it.item_name for it in inv.items if it.item_name]
    item_text = "；".join(dict.fromkeys(names))[:512]
    return {
        "id": inv.id,
        "invoice_id": inv.id,
        "entry_date": inv.invoice_date,
        "invoice_type": inv.invoice_type,
        "invoice_code": inv.invoice_code,
        "invoice_number": inv.invoice_number,
        "seller_name": inv.seller_name,
        "seller_tax_id": inv.seller_tax_id,
        "buyer_name": inv.buyer_name,
        "item_name": item_text,
        "amount_without_tax": inv.amount_without_tax,
        "tax_amount": inv.tax_amount,
        "total_amount": inv.total_amount,
        "expense_category": inv.expense_category,
        "account_subject": inv.account_subject,
        "original_name": inv_file.original_name if inv_file else "",
        "status": inv.status,
        "file_id": inv.file_id,
        "confirmed_by": inv.confirmed_by,
        "confirmed_at": (entry.confirmed_at if entry else inv.confirmed_at),
        "confidence": inv.confidence,
        "error_count": sum(
            1 for f in (inv.risk_flags or []) if (f or {}).get("level") == "error"
        ),
        "warning_count": sum(
            1 for f in (inv.risk_flags or []) if (f or {}).get("level") == "warning"
        ),
        "raw_url": f"/api/v1/files/{inv.file_id}/raw" if inv.file_id else "",
    }


def export_rows(invoices: list[Invoice]) -> list[dict]:
    """导出用：把发票**展开到明细行**，一行一条明细。

    为什么按明细行展开？
        用户要的台账要能看到「单位 / 数量 / 单价」。一张票可能有 N 行明细，
        压成一行就装不下了。参考 `素材/招远核电项目.xlsx` 的排版思路。

    金额口径（重要）：
        不含税金额 / 税额 / 价税合计 取**明细行**的值，
        所以同一张票的多行相加 == 票面合计，不会重复计数。
        没有明细行的票（人工录入的图片票等）退化为票面合计，只出一行。
    """
    rows: list[dict] = []

    for inv in invoices:
        inv_file = inv.file
        items = list(inv.items) if inv.items else [None]
        item_count = len([it for it in items if it is not None])

        for index, item in enumerate(items):
            has_item = item is not None
            amount = (item.amount if has_item else None)
            tax = (item.tax_amount if has_item else None)

            if not has_item:
                # 没有明细：用票面合计兜底，至少别丢金额
                amount = inv.amount_without_tax
                tax = inv.tax_amount

            total = None
            if amount is not None or tax is not None:
                total = round((amount or 0) + (tax or 0), 2)

            rows.append(
                {
                    "entry_date": inv.invoice_date,
                    "invoice_type": inv.invoice_type,
                    "invoice_code": inv.invoice_code,
                    "invoice_number": inv.invoice_number,
                    "seller_name": inv.seller_name,
                    "seller_tax_id": inv.seller_tax_id,
                    "buyer_name": inv.buyer_name,
                    "item_name": (item.item_name if has_item else "") or "",
                    "specification": (item.specification if has_item else "") or "",
                    "unit": (item.unit if has_item else "") or "",
                    "quantity": (item.quantity if has_item else None),
                    "unit_price": (item.unit_price if has_item else None),
                    "amount_without_tax": amount,
                    "tax_amount": tax,
                    "total_amount": total,
                    "expense_category": inv.expense_category,
                    "account_subject": inv.account_subject,
                    "status": inv.status,
                    "original_name": inv_file.original_name if inv_file else "",
                    "note": _compose_note(inv, item, item_count, index),
                    # 备注格要不要高亮（导出时用）。见 _should_highlight_note
                    "note_highlight": _should_highlight_note(inv, item),
                    # 供前端/脚本判断分组，导出时不会写成列
                    "_first_of_invoice": index == 0,
                    "_item_count": item_count,
                }
            )

    return rows


# 负数行没写备注时，自动填的说明
NEGATIVE_NOTE = "票面折扣/退货行"


def _is_negative(item) -> bool:
    return item is not None and item.amount is not None and item.amount < 0


def _invoice_remark_text(inv: Invoice) -> str:
    """票面备注压成一行 —— 存的时候保留换行（编辑框里好读），
    导出成一个单元格时不能带换行，否则那一行会变得很高。"""
    return " ".join((inv.invoice_remark or "").split())


def _should_highlight_note(inv: Invoice, item) -> bool:
    """这一行的备注格要不要高亮。

    三种情况都高亮：
      ① 用户写了明细备注（他特意留的说明）
      ② **金额为负**（票面的折扣/退货行）—— 这种行必须一眼看得见，
         不能依赖「用户记得去写备注」
      ③ 这张票有**票面备注**（工程名称/开户银行等），备注格里确实有内容

    ⚠️ 注意别把 `source_note`（识别来源说明，每行都有）算进来，
    否则所有图片票的每一行都会被高亮，高亮就失去意义了。
    """
    if item is not None and (item.remark or "").strip():
        return True
    if _is_negative(item):
        return True
    return bool(_invoice_remark_text(inv))


def _compose_note(inv: Invoice, item, item_count: int, index: int) -> str:
    """拼出这一行的「备注」。

    按顺序拼四部分（最相关的放最前）：
      ① 本行明细的备注（用户手写）；负数行没写就填「票面折扣/退货行」兜底
      ② 票面备注（发票级，每行都带 —— 这样按工程名称/账号做透视才筛得出来）
      ③ 发票级来源说明（如「由本地 PaddleOCR 识别…」，每行都有）
      ④ 「本票共 N 行明细」只写在第一行，避免每行重复
    """
    parts: list[str] = []

    remark = (item.remark or "").strip() if item is not None else ""
    if remark:
        parts.append(f"【本行备注】{remark}")
    elif _is_negative(item):
        # 兜底：折扣/退货行不写原因也得看得懂，否则高亮一格空白反而让人猜
        parts.append(NEGATIVE_NOTE)

    invoice_remark = _invoice_remark_text(inv)
    if invoice_remark:
        parts.append(f"【票面备注】{invoice_remark}")

    source = (inv.source_note or "").strip()
    if source:
        parts.append(source)

    if item_count > 1 and index == 0:
        parts.append(f"（本票共 {item_count} 行明细）")

    return "  ".join(parts)


def current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def invoice_status_text(status: str) -> str:
    return "已入账" if status == InvoiceStatus.CONFIRMED else "待复核"
