"""账本：列表、搜索、筛选、汇总。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Invoice
from ..schemas import LedgerPage
from ..services.ledger import build_query, ledger_row, summarise

router = APIRouter(tags=["账本"])


@router.get("/ledger", response_model=LedgerPage, summary="读取账本")
def get_ledger(
    status: str | None = Query(default="all", description="all / pending_review / confirmed"),
    month: str | None = Query(default=None, description="YYYY-MM"),
    date_from: str | None = None,
    date_to: str | None = None,
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

    start = (page - 1) * page_size
    return {
        "items": [ledger_row(row) for row in rows[start : start + page_size]],
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
