"""Excel 导出（项目书第十二章）。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import EXPORT_DIR
from ..database import get_db
from ..models import CompanyProfile, Invoice
from ..schemas import ExportRequest
from ..services.export import write_excel
from ..services.direction import DIRECTION_ORDER, DIRECTION_TEXT, classify_direction
from ..services.ledger import build_query, export_rows

router = APIRouter(tags=["导出"])

MEDIA_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _resolve_rows(db: Session, payload: ExportRequest) -> list[Invoice]:
    status = payload.status
    if payload.only_confirmed:
        status = "confirmed"
    invoices = build_query(
        db,
        status=status,
        month=payload.month,
        date_from=payload.date_from,
        date_to=payload.date_to,
        category=payload.category,
        q=payload.q,
        invoice_ids=payload.invoice_ids,
    ).all()
    if payload.direction in DIRECTION_ORDER:
        company = _company_profile(db)
        invoices = [
            invoice
            for invoice in invoices
            if classify_direction(
                seller_name=invoice.seller_name,
                seller_tax_id=invoice.seller_tax_id,
                buyer_name=invoice.buyer_name,
                buyer_tax_id=invoice.buyer_tax_id,
                company_name=company.name if company else None,
                company_tax_id=company.tax_id if company else None,
                company_aliases=list(company.aliases or []) if company else None,
            ).direction
            == payload.direction
        ]
    return invoices


def _company_profile(db: Session) -> CompanyProfile | None:
    return (
        db.query(CompanyProfile)
        .order_by(CompanyProfile.updated_at.desc())
        .first()
    )


def _build_file_name(payload: ExportRequest, invoice_count: int, item_count: int) -> str:
    prefix = (
        f"票据台账_{DIRECTION_TEXT[payload.direction]}"
        if payload.direction in DIRECTION_ORDER
        else "票据台账_按方向"
    )
    if payload.file_name:
        base = payload.file_name.strip().rstrip("/\\")
    elif payload.invoice_ids is not None:
        # 勾选导出：文件名标明是选中项，别和「按条件导出」混了
        base = f"{prefix}_选中{invoice_count}张"
    elif payload.month:
        base = f"{prefix}_{payload.month}"
    elif payload.date_from or payload.date_to:
        start = payload.date_from or "开始"
        end = payload.date_to or "至今"
        base = f"{prefix}_{start}_{end}"
    else:
        base = f"{prefix}_{datetime.now():%Y%m%d}"
    # 文件名里体现「几张票 / 几行明细」，核对时一眼能对上
    return f"{base}({invoice_count}张票_{item_count}行明细).xlsx"


@router.post("/exports/excel", summary="按筛选条件生成 Excel")
def export_excel(payload: ExportRequest | None = None, db: Session = Depends(get_db)):
    payload = payload or ExportRequest()
    invoices = _resolve_rows(db, payload)

    if not invoices:
        raise HTTPException(
            status_code=404,
            detail="当前筛选条件下没有数据，导不出任何记录。请放宽筛选条件后重试。",
        )

    company = _company_profile(db)
    rows = export_rows(
        invoices,
        company_name=company.name if company else None,
        company_tax_id=company.tax_id if company else None,
        company_aliases=list(company.aliases or []) if company else None,
    )
    file_name = _build_file_name(payload, len(invoices), len(rows))
    # 同时落一份到 data/exports，方便追溯每次导出
    stored_path = EXPORT_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{file_name}"
    write_excel(
        rows,
        stored_path,
        company_name=company.name if company else "",
        company_tax_id=(company.tax_id or "") if company else "",
    )

    return FileResponse(
        path=str(stored_path),
        media_type=MEDIA_XLSX,
        filename=file_name,
        content_disposition_type="attachment",
    )


@router.get("/exports/preview", summary="导出前预览条数（前端确认用）")
def export_preview(
    status: str = "all",
    month: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    direction: str | None = None,
    category: str | None = None,
    q: str | None = None,
    only_confirmed: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    payload = ExportRequest(
        status=status,
        month=month,
        date_from=date_from,
        date_to=date_to,
        direction=direction,
        category=category,
        q=q,
        only_confirmed=only_confirmed,
    )
    invoices = _resolve_rows(db, payload)
    company = _company_profile(db)
    rows = export_rows(
        invoices,
        company_name=company.name if company else None,
        company_tax_id=company.tax_id if company else None,
        company_aliases=list(company.aliases or []) if company else None,
    )
    by_direction = []
    for direction in DIRECTION_ORDER:
        subset = [r for r in rows if r.get("direction") == direction]
        by_direction.append(
            {
                "direction": direction,
                "label": DIRECTION_TEXT[direction],
                "invoice_count": len({r.get("_invoice_id") for r in subset if r.get("_invoice_id")}),
                "item_count": len(subset),
                "total_amount": round(sum((r.get("total_amount") or 0) for r in subset), 2),
            }
        )
    return {
        # count = 导出的行数（一行一条明细），invoice_count = 涉及几张票
        "count": len(rows),
        "invoice_count": len(invoices),
        "total_amount": round(sum((r.get("total_amount") or 0) for r in rows), 2),
        "file_name": _build_file_name(payload, len(invoices), len(rows)) if rows else "",
        "company_ready": company is not None,
        "by_direction": by_direction,
    }
