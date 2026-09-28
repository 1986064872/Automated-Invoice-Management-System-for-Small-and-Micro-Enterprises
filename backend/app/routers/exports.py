"""Excel 导出（项目书第十二章）。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import EXPORT_DIR
from ..database import get_db
from ..models import Invoice
from ..schemas import ExportRequest
from ..services.export import write_excel
from ..services.ledger import build_query, export_rows

router = APIRouter(tags=["导出"])

MEDIA_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _resolve_rows(db: Session, payload: ExportRequest) -> list[Invoice]:
    status = payload.status
    if payload.only_confirmed:
        status = "confirmed"
    return build_query(
        db,
        status=status,
        month=payload.month,
        date_from=payload.date_from,
        date_to=payload.date_to,
        category=payload.category,
        q=payload.q,
        invoice_ids=payload.invoice_ids,
    ).all()


def _build_file_name(payload: ExportRequest, invoice_count: int, item_count: int) -> str:
    if payload.file_name:
        base = payload.file_name.strip().rstrip("/\\")
    elif payload.invoice_ids is not None:
        # 勾选导出：文件名标明是选中项，别和「按条件导出」混了
        base = f"费用明细_选中{invoice_count}张"
    elif payload.month:
        base = f"费用明细_{payload.month}"
    elif payload.date_from or payload.date_to:
        start = payload.date_from or "开始"
        end = payload.date_to or "至今"
        base = f"费用明细_{start}_{end}"
    else:
        base = f"费用明细_{datetime.now():%Y%m%d}"
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

    rows = export_rows(invoices)
    file_name = _build_file_name(payload, len(invoices), len(rows))
    # 同时落一份到 data/exports，方便追溯每次导出
    stored_path = EXPORT_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{file_name}"
    write_excel(rows, stored_path)

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
        category=category,
        q=q,
        only_confirmed=only_confirmed,
    )
    invoices = _resolve_rows(db, payload)
    rows = export_rows(invoices)
    return {
        # count = 导出的行数（一行一条明细），invoice_count = 涉及几张票
        "count": len(rows),
        "invoice_count": len(invoices),
        "total_amount": round(sum((r.get("total_amount") or 0) for r in rows), 2),
        "file_name": _build_file_name(payload, len(invoices), len(rows)) if rows else "",
    }
