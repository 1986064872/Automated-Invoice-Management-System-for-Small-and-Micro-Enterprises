"""首页仪表盘 + 系统信息。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import (
    CompanyProfile,
    Invoice,
    InvoiceFile,
    InvoiceStatus,
    ProcessingJob,
    RiskLevel,
)
from ..ocr.baidu import today_usage
from ..ocr.registry import (
    available_providers,
    health_check_all,
    paddle_available,
    rapidocr_available,
)
from ..ocr.paddle_ocr import paddle_env_ready
from ..schemas import (
    CompanyProfileIn,
    CompanyProfileOut,
    CompanySuggestionOut,
    DashboardOut,
)
from ..services.direction import normalize_name, normalize_tax_id
from ..services.ledger import month_bounds

router = APIRouter(tags=["首页"])


@router.get("/dashboard/summary", response_model=DashboardOut, summary="本月概览")
def dashboard_summary(
    month: str | None = Query(default=None, description="YYYY-MM，默认本月"),
    db: Session = Depends(get_db),
) -> dict:
    month = month or date.today().strftime("%Y-%m")
    try:
        start, end = month_bounds(month)
    except (ValueError, IndexError):
        start = end = ""

    invoices = (
        db.query(Invoice)
        .filter(Invoice.invoice_date >= start, Invoice.invoice_date <= end)
        .all()
    )

    by_category: dict[str, dict] = {}
    for inv in invoices:
        key = inv.expense_category or "待分类"
        bucket = by_category.setdefault(
            key, {"category": key, "count": 0, "total_amount": 0.0}
        )
        bucket["count"] += 1
        bucket["total_amount"] = round(bucket["total_amount"] + (inv.total_amount or 0), 2)

    recent_jobs = (
        db.query(ProcessingJob).order_by(ProcessingJob.created_at.desc()).limit(5).all()
    )

    # 失败文件数：让用户一眼看到「有东西没处理成功」
    failed_files = (
        db.query(InvoiceFile).filter(InvoiceFile.status == "failed").count()
    )

    from ..schemas import job_to_dict

    return {
        "month": month,
        "invoice_count": len(invoices),
        "total_amount": round(sum(i.total_amount or 0 for i in invoices), 2),
        "pending_review": sum(
            1 for i in invoices if i.status == InvoiceStatus.PENDING_REVIEW
        ),
        # 侧边栏角标要的是「全部时间的待复核」，不能跟着月份走，
        # 否则本月没票时角标就是 0，看着像没有待办
        "pending_review_total": db.query(Invoice)
        .filter(Invoice.status == InvoiceStatus.PENDING_REVIEW)
        .count(),
        "abnormal": sum(
            1
            for i in invoices
            if any((f or {}).get("level") == RiskLevel.ERROR for f in (i.risk_flags or []))
        ),
        "confirmed": sum(1 for i in invoices if i.status == InvoiceStatus.CONFIRMED),
        "failed": failed_files,
        "by_category": sorted(
            by_category.values(), key=lambda x: x["total_amount"], reverse=True
        ),
        "recent_jobs": [job_to_dict(j) for j in recent_jobs],
        "provider": {
            "configured": settings.resolved_ocr_provider,
            "ocr_provider_setting": settings.ocr_provider,
            "baidu_ready": bool(settings.baidu_api_key and settings.baidu_secret_key),
            # 本地图片 OCR（PaddleOCR，GPU）：跨环境子进程方案
            "paddle_ready": paddle_available(),
            "paddle_detail": paddle_env_ready()[1],
            "rapidocr_ready": rapidocr_available(),
            "daily_ocr_limit": settings.daily_ocr_limit,
            "daily_ocr_used": today_usage(),
        },
    }


@router.get("/system/providers", summary="OCR 供应商状态")
def system_providers(deep: bool = Query(default=False, description="true 则真的去调一次鉴权")) -> dict:
    if deep:
        return {"providers": health_check_all(), "active": settings.resolved_ocr_provider}
    return {"providers": available_providers(), "active": settings.resolved_ocr_provider}


@router.get("/system/config", summary="非敏感配置（前端展示用）")
def system_config() -> dict:
    return {
        "app_name": settings.app_name,
        "api_prefix": settings.api_prefix,
        "max_file_size_mb": settings.max_file_size_mb,
        "max_batch_files": settings.max_batch_files,
        "allowed_extensions": list(settings.allowed_extensions),
        "confidence_threshold": settings.confidence_threshold,
        "ocr_provider": settings.resolved_ocr_provider,
        # 注意：密钥一律不返回
    }


@router.get(
    "/system/company",
    response_model=CompanyProfileOut | None,
    summary="读取当前企业档案",
)
def get_company_profile(db: Session = Depends(get_db)) -> dict | None:
    profile = (
        db.query(CompanyProfile)
        .order_by(CompanyProfile.updated_at.desc())
        .first()
    )
    if profile is None:
        return None
    return {
        "name": profile.name,
        "tax_id": profile.tax_id,
        "aliases": list(profile.aliases or []),
        "updated_at": profile.updated_at,
    }


@router.put(
    "/system/company",
    response_model=CompanyProfileOut,
    summary="保存当前企业档案",
)
def save_company_profile(
    payload: CompanyProfileIn,
    db: Session = Depends(get_db),
) -> dict:
    profile = (
        db.query(CompanyProfile)
        .order_by(CompanyProfile.updated_at.desc())
        .first()
    )
    if profile is None:
        profile = CompanyProfile()
        db.add(profile)

    profile.name = payload.name.strip()
    profile.tax_id = (payload.tax_id or "").strip() or None
    aliases = [alias.strip() for alias in payload.aliases if alias.strip()]
    profile.aliases = list(dict.fromkeys(aliases))
    db.commit()
    db.refresh(profile)
    return {
        "name": profile.name,
        "tax_id": profile.tax_id,
        "aliases": list(profile.aliases or []),
        "updated_at": profile.updated_at,
    }


@router.get(
    "/system/company/suggestions",
    response_model=list[CompanySuggestionOut],
    summary="根据已登记票据推荐企业主体",
)
def company_suggestions(
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[dict]:
    """从已识别票据的购销双方里生成候选企业，供导出前快速选择。"""
    rows = db.query(
        Invoice.seller_name,
        Invoice.seller_tax_id,
        Invoice.buyer_name,
        Invoice.buyer_tax_id,
    ).all()
    candidates: dict[str, dict] = {}

    def add_candidate(
        *,
        name: str | None,
        tax_id: str | None,
        role: str,
    ) -> None:
        clean_name = (name or "").strip()
        clean_tax_id = (tax_id or "").strip() or None
        if not clean_name and not clean_tax_id:
            return
        key = f"tax:{normalize_tax_id(clean_tax_id)}" if clean_tax_id else f"name:{normalize_name(clean_name)}"
        bucket = candidates.setdefault(
            key,
            {
                "name": clean_name,
                "tax_id": clean_tax_id,
                "roles": set(),
                "invoice_count": 0,
                "buyer_count": 0,
                "seller_count": 0,
            },
        )
        if not bucket["name"] and clean_name:
            bucket["name"] = clean_name
        if not bucket["tax_id"] and clean_tax_id:
            bucket["tax_id"] = clean_tax_id
        if role not in bucket["roles"]:
            bucket["roles"].add(role)
        bucket["invoice_count"] += 1
        bucket[f"{role}_count"] += 1

    for seller_name, seller_tax_id, buyer_name, buyer_tax_id in rows:
        add_candidate(name=seller_name, tax_id=seller_tax_id, role="seller")
        add_candidate(name=buyer_name, tax_id=buyer_tax_id, role="buyer")

    ordered = sorted(
        candidates.values(),
        key=lambda item: (-item["invoice_count"], item["name"]),
    )
    return [
        {
            "name": item["name"],
            "tax_id": item["tax_id"],
            "roles": sorted(item["roles"]),
            "invoice_count": item["invoice_count"],
            "buyer_count": item["buyer_count"],
            "seller_count": item["seller_count"],
        }
        for item in ordered[:limit]
    ]
