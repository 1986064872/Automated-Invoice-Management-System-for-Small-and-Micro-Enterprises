"""账本：列表、搜索、筛选、汇总。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CompanyProfile, Invoice
from ..schemas import LedgerPage
from ..services.direction import DIRECTION_ORDER, classify_direction
from ..services.ledger import build_query, ledger_row, summarise

router = APIRouter(tags=["账本"])


@router.get("/ledger", response_model=LedgerPage, summary="读取账本")
def get_ledger(
    status: str | None = Query(default="all", description="all / pending_review / confirmed"),
    month: str | None = Query(default=None, description="YYYY-MM"),
    date_from: str | None = None,
    date_to: str | None = None,
    direction: str | None = None,
    category: str | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    rows: list[Invoice] = build_query(
        db,
        status=status,
        month=month,
        date_from=date_from,
        date_to=date_to,
        category=category,
        q=q,
    ).all()

    company = (
        db.query(CompanyProfile)
        .order_by(CompanyProfile.updated_at.desc())
        .first()
    )

    direction_map = {
        row.id: classify_direction(
            seller_name=row.seller_name,
            seller_tax_id=row.seller_tax_id,
            buyer_name=row.buyer_name,
            buyer_tax_id=row.buyer_tax_id,
            company_name=company.name if company else None,
            company_tax_id=company.tax_id if company else None,
            company_aliases=list(company.aliases or []) if company else None,
        )
        for row in rows
    }
    if direction in DIRECTION_ORDER:
        rows = [row for row in rows if direction_map[row.id].direction == direction]

    start = (page - 1) * page_size
    return {
        "items": [
            ledger_row(row, direction=direction_map[row.id])
            for row in rows[start : start + page_size]
        ],
        "summary": summarise(rows),
        "total": len(rows),
        "page": page,
        "page_size": page_size,
    }


@router.get("/ledger/categories", summary="账本里出现过的费用分类")
def list_categories(db: Session = Depends(get_db)) -> dict:
    rows = (
        db.query(Invoice.expense_category, Invoice.account_subject)
        .filter(Invoice.expense_category.isnot(None))
        .distinct()
        .all()
    )
    seen: dict[str, str] = {}
    for category, subject in rows:
        if category and category not in seen:
            seen[category] = subject or ""
    return {
        "categories": sorted(seen.keys()),
        "map": seen,
    }
