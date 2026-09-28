"""票据：上传、批次进度、列表、详情、复核保存、确认入账、重试、删除。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ..config import UPLOAD_DIR, settings
from ..database import get_db
from ..models import (
    FileStatus,
    Invoice,
    InvoiceFile,
    InvoiceItem,
    InvoiceStatus,
    JobStatus,
    ProcessingJob,
    RiskLevel,
    new_id,
)
from ..schemas import (
    BatchDeleteRequest,
    ConfirmIn,
    InvoiceListOut,
    InvoiceOut,
    InvoiceUpdate,
    JobOut,
    UploadResult,
    file_to_dict,
    invoice_to_dict,
    job_to_dict,
)
from ..services.classify import classify as classify_recommend
from ..services.classify import save_rules_from_invoice
from ..services.fingerprint import business_dedupe_key
from ..services.ledger import build_query, summarise
from ..services.pipeline import (
    create_ledger_entry,
    process_job,
    recount_job,
    retry_file,
    revoke_ledger_entry,
)
from ..services.storage import (
    UploadRejected as UploadRejectedError,
)
from ..services.storage import (
    count_pages,
    move_to_trash,
    safe_filename,
    save_upload,
    sha256_of,
    validate_upload,
)
from ..services.validate import validate_invoice

router = APIRouter(tags=["票据"])

# 允许人工修改的票面字段
UPDATABLE_FIELDS = [
    "invoice_type",
    "invoice_code",
    "invoice_number",
    "invoice_date",
    "seller_name",
    "seller_tax_id",
    "buyer_name",
    "buyer_tax_id",
    # 票面备注栏原文（识别自票面左下角，允许人工修正）
    "invoice_remark",
    "amount_without_tax",
    "tax_amount",
    "total_amount",
    "expense_category",
    "account_subject",
]

MONEY_FIELDS = {"amount_without_tax", "tax_amount", "total_amount"}

FIELD_LABELS = {
    "invoice_type": "发票类型",
    "invoice_code": "发票代码",
    "invoice_number": "发票号码",
    "invoice_date": "开票日期",
    "seller_name": "销售方名称",
    "seller_tax_id": "销售方税号",
    "buyer_name": "购买方名称",
    "buyer_tax_id": "购买方税号",
    "invoice_remark": "票面备注",
    "amount_without_tax": "不含税金额",
    "tax_amount": "税额",
    "total_amount": "价税合计",
    "expense_category": "费用分类",
    "account_subject": "会计科目",
}


def _get_invoice_or_404(db: Session, invoice_id: str) -> Invoice:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="票据不存在或已被删除")
    return invoice


# ==========================================================================
# 上传
# ==========================================================================
@router.post("/invoices/upload", response_model=UploadResult, summary="上传一个或多个票据")
async def upload_invoices(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(..., description="PDF / JPG / JPEG / PNG"),
    db: Session = Depends(get_db),
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="没有收到任何文件")
    if len(files) > settings.max_batch_files:
        raise HTTPException(
            status_code=400,
            detail=f"单批最多 {settings.max_batch_files} 个文件，本次收到 {len(files)} 个，请分批上传。",
        )

    job = ProcessingJob(
        id=new_id(),
        total=0,
        status=JobStatus.QUEUED,
        ocr_provider=settings.resolved_ocr_provider,
    )
    db.add(job)
    db.flush()

    batch_dir = UPLOAD_DIR / f"{datetime.now():%Y%m%d}" / job.id
    accepted: list[InvoiceFile] = []
    rejected: list[dict] = []

    for upload in files:
        original = safe_filename(upload.filename or "unnamed")
        content = await upload.read()
        try:
            file_type = validate_upload(original, content)
        except UploadRejectedError as exc:
            rejected.append({"name": original, "reason": exc.message})
            continue

        path, stored_name = save_upload(batch_dir, original, content)
        record = InvoiceFile(
            id=new_id(),
            job_id=job.id,
            original_name=original,
            stored_name=stored_name,
            storage_path=str(path),
            file_type=file_type,
            file_size=len(content),
            file_hash=sha256_of(content),
            page_count=count_pages(path, file_type),
            status=FileStatus.QUEUED,
        )
        db.add(record)
        accepted.append(record)

    recount_job(db, job)
    db.commit()

    if accepted:
        # 交给后台跑，上传接口立刻返回，前端轮询批次进度
        background_tasks.add_task(process_job, job.id)

    if not accepted:
        db.delete(job)
        db.commit()
        return {
            "job_id": None,
            "accepted": [],
            "rejected": rejected,
            "message": f"全部 {len(rejected)} 个文件都没通过校验，请按提示调整后重传。",
        }

    for record in accepted:
        db.refresh(record)

    message = f"已接收 {len(accepted)} 个文件，正在后台识别。"
    if rejected:
        message += f" 其中 {len(rejected)} 个未通过校验。"

    return {
        "job_id": job.id,
        "accepted": [file_to_dict(f) for f in accepted],
        "rejected": rejected,
        "message": message,
    }


# ==========================================================================
# 批次进度
# ==========================================================================
@router.get("/jobs/{job_id}", response_model=JobOut, summary="查询批次进度")
def get_job(
    job_id: str,
    with_files: bool = Query(default=True, description="是否带上每个文件的状态"),
    db: Session = Depends(get_db),
) -> dict:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    return job_to_dict(job, with_files=with_files)


@router.get("/jobs", response_model=list[JobOut], summary="最近批次列表")
def list_jobs(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[dict]:
    jobs = (
        db.query(ProcessingJob)
        .order_by(ProcessingJob.created_at.desc())
        .limit(limit)
        .all()
    )
    return [job_to_dict(j) for j in jobs]


# ==========================================================================
# 列表 / 详情
# ==========================================================================
@router.get("/invoices", response_model=InvoiceListOut, summary="筛选票据列表")
def list_invoices(
    status: str | None = Query(default=None, description="pending_review / confirmed / all"),
    month: str | None = Query(default=None, description="YYYY-MM"),
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    q: str | None = None,
    abnormal_only: bool = Query(default=False, description="只看有红色错误的"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    query = build_query(
        db,
        status=status,
        month=month,
        date_from=date_from,
        date_to=date_to,
        category=category,
        q=q,
    )
    all_rows = query.all()

    if abnormal_only:
        all_rows = [
            row
            for row in all_rows
            if any((f or {}).get("level") == RiskLevel.ERROR for f in (row.risk_flags or []))
        ]

    summary = summarise(all_rows)
    total = len(all_rows)
    start = (page - 1) * page_size
    page_rows = all_rows[start : start + page_size]

    return {
        "items": [invoice_to_dict(row) for row in page_rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "summary": summary,
    }


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut, summary="票据详情")
def get_invoice(invoice_id: str, db: Session = Depends(get_db)) -> dict:
    return invoice_to_dict(_get_invoice_or_404(db, invoice_id))


@router.get("/invoices/{invoice_id}/raw-response", summary="查看 OCR 原始响应（可追溯）")
def get_raw_response(invoice_id: str, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    inv_file = db.get(InvoiceFile, invoice.file_id)
    if inv_file is None or not inv_file.ocr_raw_json:
        raise HTTPException(status_code=404, detail="这张票没有留存的 OCR 原始响应")
    try:
        parsed = json.loads(inv_file.ocr_raw_json)
    except json.JSONDecodeError:
        parsed = {"raw": inv_file.ocr_raw_json}
    return {
        "provider": inv_file.ocr_provider,
        "captured_at": inv_file.updated_at,
        "raw": parsed,
    }


# ==========================================================================
# 复核保存
# ==========================================================================
def _apply_items(invoice: Invoice, payload_items: list) -> list[dict]:
    """按 id 更新明细行，并**真的删除**前端移除掉的行。

    注意：只做「更新+新增」是不够的 —— 前端删掉一行后提交的 payload 里就没有它了，
    如果这里不删，界面上「删除本行」会变成假操作（刷新后又回来了）。
    """
    changes: list[dict] = []
    existing = {item.id: item for item in invoice.items}
    kept_ids: set[str] = set()

    for index, incoming in enumerate(payload_items):
        item = existing.get(incoming.id) if incoming.id else None
        if item is not None:
            kept_ids.add(item.id)
        else:
            item = InvoiceItem(id=new_id(), invoice_id=invoice.id)
            invoice.items.append(item)

        for field in (
            "item_name",
            "specification",
            "unit",
            "tax_rate",
            "remark",
        ):
            new_value = getattr(incoming, field)
            if new_value is None:
                continue
            old_value = getattr(item, field)
            if old_value != new_value:
                changes.append(
                    {
                        "at": datetime.now().isoformat(timespec="seconds"),
                        "field": f"明细{index + 1}.{field}",
                        "old": old_value,
                        "new": new_value,
                    }
                )
                setattr(item, field, new_value)
        for field in ("quantity", "unit_price", "amount", "tax_amount"):
            new_value = getattr(incoming, field)
            if new_value is None:
                continue
            old_value = getattr(item, field)
            if old_value is None or abs((old_value or 0) - new_value) > 1e-9:
                changes.append(
                    {
                        "at": datetime.now().isoformat(timespec="seconds"),
                        "field": f"明细{index + 1}.{field}",
                        "old": old_value,
                        "new": new_value,
                    }
                )
                setattr(item, field, new_value)
        item.sort_order = index

    # 清理被移除的行
    for item_id, item in list(existing.items()):
        if item_id not in kept_ids:
            changes.append(
                {
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "field": "明细行删除",
                    "old": item.item_name or item_id[:8],
                    "new": None,
                }
            )
            invoice.items.remove(item)

    return changes


@router.patch("/invoices/{invoice_id}", response_model=InvoiceOut, summary="保存人工修改")
def update_invoice(
    invoice_id: str,
    payload: InvoiceUpdate,
    db: Session = Depends(get_db),
) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    changes: list[dict] = []
    now = datetime.now().isoformat(timespec="seconds")

    for field in UPDATABLE_FIELDS:
        new_value = getattr(payload, field)
        if new_value is None:
            continue
        old_value = getattr(invoice, field)
        if field in MONEY_FIELDS:
            if old_value is not None and abs(float(old_value) - float(new_value)) < 1e-9:
                continue
        elif old_value == new_value:
            continue
        setattr(invoice, field, new_value)
        changes.append(
            {
                "at": now,
                "field": FIELD_LABELS.get(field, field),
                "field_key": field,
                "old": old_value,
                "new": new_value,
                "by": payload.edited_by,
            }
        )

    touched_classify_fields = any(
        c.get("field_key") in ("seller_name", "invoice_type") or c["field"].startswith("明细")
        for c in changes
    )

    if payload.items is not None:
        changes.extend(_apply_items(invoice, payload.items))

    # 销售方或明细变了、且用户没手动指定类别 → 重新跑一次规则推荐
    if touched_classify_fields and payload.expense_category is None:
        picked = classify_recommend(
            db,
            seller_name=invoice.seller_name,
            item_names=[it.item_name or "" for it in invoice.items],
        )
        if picked["expense_category"] != invoice.expense_category:
            invoice.expense_category = picked["expense_category"]
            invoice.account_subject = picked["account_subject"]
            invoice.rule_source = picked["rule_source"]

    # 指纹、风险标记都跟着刷新
    invoice.dedupe_key = business_dedupe_key(
        invoice_code=invoice.invoice_code,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        total_amount=invoice.total_amount,
        seller_tax_id=invoice.seller_tax_id,
    )
    db.flush()
    invoice.risk_flags = validate_invoice(db, invoice, source_file=invoice.file)

    if payload.save_as_rule:
        created = save_rules_from_invoice(db, invoice)
        if created:
            changes.append(
                {
                    "at": now,
                    "field": "规则沉淀",
                    "old": None,
                    "new": f"新增 {len(created)} 条自定义规则："
                    + "、".join(f"{r.match_field}={r.keyword}" for r in created),
                    "by": payload.edited_by,
                }
            )

    if changes:
        invoice.edit_history = (invoice.edit_history or []) + changes

    # 已入账的票被改了，账目快照要同步
    if invoice.status == InvoiceStatus.CONFIRMED:
        create_ledger_entry(db, invoice, invoice.confirmed_by or payload.edited_by)

    db.commit()
    db.refresh(invoice)
    return invoice_to_dict(invoice)


# ==========================================================================
# 确认入账 / 撤销
# ==========================================================================
@router.post("/invoices/{invoice_id}/confirm", response_model=InvoiceOut, summary="确认入账")
def confirm_invoice(
    invoice_id: str,
    payload: ConfirmIn | None = None,
    db: Session = Depends(get_db),
) -> dict:
    payload = payload or ConfirmIn()
    invoice = _get_invoice_or_404(db, invoice_id)

    # 幂等：同一任务重复提交不会产生第二条账目
    if invoice.status == InvoiceStatus.CONFIRMED:
        return invoice_to_dict(invoice)

    # 确认前重跑一次校验，避免用的是过期标记
    db.flush()
    invoice.risk_flags = validate_invoice(db, invoice, source_file=invoice.file)
    blockers = [
        f for f in (invoice.risk_flags or []) if (f or {}).get("level") == RiskLevel.ERROR
    ]
    if blockers:
        db.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "message": "存在红色错误，不能确认入账。请先按提示修改。",
                "errors": blockers,
            },
        )

    create_ledger_entry(db, invoice, payload.confirmed_by)
    db.commit()
    db.refresh(invoice)
    return invoice_to_dict(invoice)


@router.post("/invoices/{invoice_id}/revoke", response_model=InvoiceOut, summary="撤销入账")
def revoke_invoice(invoice_id: str, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    if invoice.status != InvoiceStatus.CONFIRMED:
        raise HTTPException(status_code=400, detail="这张票还没有入账，无需撤销")
    revoke_ledger_entry(db, invoice)
    db.commit()
    db.refresh(invoice)
    return invoice_to_dict(invoice)


# ==========================================================================
# 重试 / 删除
# ==========================================================================
@router.post("/invoices/{invoice_id}/retry", response_model=InvoiceOut, summary="重新识别")
def retry_invoice(invoice_id: str, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    if invoice.status == InvoiceStatus.CONFIRMED:
        raise HTTPException(status_code=400, detail="已入账的票据不能重新识别，请先撤销入账")
    inv_file = db.get(InvoiceFile, invoice.file_id)
    if inv_file is None:
        raise HTTPException(status_code=404, detail="原文件记录不存在")

    retry_file(db, inv_file)
    refreshed = db.query(Invoice).filter(Invoice.file_id == inv_file.id).first()
    if refreshed is None:
        raise HTTPException(status_code=500, detail="重新识别失败，请查看文件状态")
    return invoice_to_dict(refreshed)


def _delete_invoice_row(
    db: Session, invoice: Invoice, *, delete_file: bool
) -> str | None:
    """删一条票据记录（连带明细/账目），原票**移进回收站**。

    单个删除和批量删除都走这里 —— 保证行为完全一致。
    批量删除最容易被人顺手写成循环 `unlink()`，那样误删就捞不回来了。

    返回被移入回收站的路径（没移返回 None）。
    """
    inv_file = db.get(InvoiceFile, invoice.file_id)
    file_path = Path(inv_file.storage_path) if inv_file else None
    job = inv_file.job if inv_file else None

    db.delete(invoice)
    db.flush()
    if inv_file is not None:
        db.delete(inv_file)
        db.flush()

    # 移进 data/trash/ 而不是直接 unlink —— 误删还能捞回来
    trashed = None
    if delete_file and file_path is not None:
        trashed = move_to_trash(file_path)

    if job is not None:
        recount_job(db, job)
        if job.total == 0:
            db.delete(job)
    return str(trashed) if trashed is not None else None


@router.delete("/invoices/{invoice_id}", summary="删除票据（原票进回收站，不硬删）")
def delete_invoice(
    invoice_id: str,
    delete_file: bool = Query(default=True, description="是否同时把原票移出 uploads"),
    db: Session = Depends(get_db),
) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    trashed = _delete_invoice_row(db, invoice, delete_file=delete_file)
    db.commit()

    if trashed is not None:
        path = Path(trashed)
        return {
            "ok": True,
            "message": f"票据已删除。原票已移入回收站：{path.relative_to(path.parents[1])}",
            "trashed_path": trashed,
        }
    return {"ok": True, "message": "票据已删除（原票文件不存在或未移动）。"}


# 一次最多删多少张。防止误点「全选 + 删除」把整个库清空时还毫无阻力；
# 真需要批量清理就分批来，多一道手续。
MAX_BATCH_DELETE = 200


@router.post("/invoices/batch-delete", summary="批量删除票据（原票进回收站，不硬删）")
def batch_delete_invoices(
    payload: BatchDeleteRequest,
    db: Session = Depends(get_db),
) -> dict:
    """批量删除。逐张提交，单张失败不影响其他张。

    ⚠️ 安全设计：
      · 原票一律走 `move_to_trash()`，和单个删除同一条路径
      · 一次最多 MAX_BATCH_DELETE 张
      · 逐张独立提交 —— 中间某张出错，已删的不会回滚、失败的会如实报告
    """
    ids = list(dict.fromkeys(payload.ids))  # 去重，保持传入顺序
    if not ids:
        return {"ok": True, "deleted": 0, "failed": [], "message": "没有选中任何票据。"}
    if len(ids) > MAX_BATCH_DELETE:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多删除 {MAX_BATCH_DELETE} 张，当前选中 {len(ids)} 张。请分批删除。",
        )

    deleted = 0
    failed: list[dict] = []
    trashed: list[str] = []

    for invoice_id in ids:
        invoice = db.get(Invoice, invoice_id)
        if invoice is None:
            failed.append({"id": invoice_id, "reason": "票据不存在（可能已被删除）"})
            continue
        try:
            path = _delete_invoice_row(db, invoice, delete_file=payload.delete_file)
            db.commit()
            deleted += 1
            if path is not None:
                trashed.append(path)
        except Exception as exc:  # 单张失败不能拖垮整批
            db.rollback()
            failed.append({"id": invoice_id, "reason": str(exc)[:120]})

    parts = [f"已删除 {deleted} 张"]
    if trashed:
        parts.append(f"原票移入回收站 {len(trashed)} 个文件")
    if failed:
        parts.append(f"失败 {len(failed)} 张")
    return {
        "ok": not failed,
        "deleted": deleted,
        "failed": failed,
        "trashed_paths": trashed,
        "message": "，".join(parts) + "。",
    }


@router.delete("/files/{file_id}", summary="删除一个上传文件及其票据")
def delete_file(file_id: str, db: Session = Depends(get_db)) -> dict:
    inv_file = db.get(InvoiceFile, file_id)
    if inv_file is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    invoice = db.query(Invoice).filter(Invoice.file_id == file_id).first()
    if invoice is not None:
        return delete_invoice(invoice.id, delete_file=True, db=db)

    job = inv_file.job
    path = Path(inv_file.storage_path)
    db.delete(inv_file)
    db.flush()
    if job is not None:
        recount_job(db, job)
    trashed = move_to_trash(path)
    db.commit()
    if trashed is not None:
        return {
            "ok": True,
            "message": f"文件已删除，原票已移入回收站：{trashed.relative_to(trashed.parents[1])}",
            "trashed_path": str(trashed),
        }
    return {"ok": True, "message": "文件已删除。"}


def _cleanup_empty_dirs(root: Path) -> None:  # pragma: no cover - 辅助
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            shutil.rmtree(path, ignore_errors=True)
