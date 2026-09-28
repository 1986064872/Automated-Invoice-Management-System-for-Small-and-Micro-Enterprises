"""处理流水线：把「上传的文件」变成「待复核的发票」。

流程（对应项目书第三章的 3~4 阶段）：
    预处理（校验）→ OCR 识别 → 写结构化字段 → 分类推荐 → 风险校验 → 待复核

为什么用 BackgroundTasks 而不是同步？
    20 张票逐个调 OCR 要几十秒，如果在上传接口里同步跑，用户浏览器会一直转圈。
    项目书第七章也写了「V1 先用 FastAPI BackgroundTasks，量大了再上 Celery」。
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import (
    FileStatus,
    Invoice,
    InvoiceFile,
    InvoiceItem,
    InvoiceStatus,
    JobStatus,
    LedgerEntry,
    ProcessingJob,
    RiskLevel,
    new_id,
)
from ..ocr import InvoiceResult, OCRCode, OCRProviderError, get_fallback_provider, get_provider
from .classify import classify
from .fingerprint import business_dedupe_key
from .validate import validate_invoice

# 这些错误意味着「机器搞不定，但人可以补」→ 仍然进入待复核，让用户人工录入
MANUAL_ENTRY_CODES = {
    OCRCode.UNSUPPORTED_FORMAT,
    OCRCode.NO_TEXT_LAYER,
    OCRCode.NEEDS_MANUAL,
    OCRCode.PARSE_FAILED,
}

# OCR 原始响应入库时的长度上限，防止把数据库撑爆
RAW_JSON_LIMIT = 200_000


# --------------------------------------------------------------------------
# 供应商调用（含云端失败自动回落本地）
# --------------------------------------------------------------------------
def _recognize(
    inv_file: InvoiceFile,
) -> tuple[InvoiceResult | None, OCRProviderError | None]:
    errors: list[OCRProviderError] = []
    candidates = []

    try:
        candidates.append(get_provider(inv_file.file_type))
    except OCRProviderError as exc:
        errors.append(exc)

    # 云端挂了就用本地兜底：PDF 至少还能靠文本层读出字段
    fallback = get_fallback_provider(inv_file.file_type)
    if fallback is not None and all(c.name != fallback.name for c in candidates):
        candidates.append(fallback)

    path = Path(inv_file.storage_path)
    if not path.exists():
        return None, OCRProviderError(
            OCRCode.UNREADABLE, "原文件在磁盘上找不到了，请重新上传该文件。"
        )

    for provider in candidates:
        try:
            result = provider.recognize(path, inv_file.file_type)
            if provider is not candidates[0]:
                result.warnings.append(
                    f"主供应商不可用，已自动改用「{provider.display_name}」完成识别。"
                )
            return result, None
        except OCRProviderError as exc:
            errors.append(exc)
        except Exception as exc:  # 供应商实现里的意外错误
            errors.append(
                OCRProviderError(OCRCode.PROVIDER_ERROR, f"{provider.display_name} 识别异常：{exc}")
            )

    # 全部失败：优先返回「用户可以人工补救」的那条，否则返回第一条
    for exc in errors:
        if exc.code in MANUAL_ENTRY_CODES:
            return None, exc
    if errors:
        return None, errors[0]
    return None, OCRProviderError(OCRCode.UNREADABLE, "没有任何可用的识别方案。")


# --------------------------------------------------------------------------
# 落库
# --------------------------------------------------------------------------
def _replace_invoice(db: Session, inv_file: InvoiceFile, result: InvoiceResult) -> Invoice:
    """写入（或覆盖）一张发票及其明细、分类、风险标记。"""
    old = db.query(Invoice).filter(Invoice.file_id == inv_file.id).first()
    if old is not None:
        db.delete(old)
        db.flush()

    invoice = Invoice(
        id=new_id(),
        file_id=inv_file.id,
        invoice_type=result.invoice_type,
        invoice_code=result.invoice_code,
        invoice_number=result.invoice_number,
        invoice_date=result.invoice_date,
        seller_name=result.seller_name,
        seller_tax_id=result.seller_tax_id,
        buyer_name=result.buyer_name,
        buyer_tax_id=result.buyer_tax_id,
        # 票面备注栏的原文（工程类发票会写开户银行/账号/工程名称/工程地址）
        invoice_remark=result.remark or None,
        amount_without_tax=result.amount_without_tax,
        tax_amount=result.tax_amount,
        total_amount=result.total_amount,
        currency=result.currency or "CNY",
        confidence=result.confidence,
        field_confidence=result.field_confidence,
        status=InvoiceStatus.PENDING_REVIEW,
        edit_history=[],
        items=[
            InvoiceItem(
                id=new_id(),
                item_name=item.item_name,
                specification=item.specification,
                unit=item.unit,
                quantity=item.quantity,
                unit_price=item.unit_price,
                tax_rate=item.tax_rate,
                amount=item.amount,
                tax_amount=item.tax_amount,
                sort_order=idx,
            )
            for idx, item in enumerate(result.items)
        ],
    )
    db.add(invoice)
    db.flush()

    # ---- 分类推荐 ----
    picked = classify(
        db,
        seller_name=invoice.seller_name,
        item_names=[it.item_name for it in result.items],
    )
    invoice.expense_category = picked["expense_category"]
    invoice.account_subject = picked["account_subject"]
    invoice.rule_source = picked["rule_source"]

    # ---- 业务去重指纹 ----
    invoice.dedupe_key = business_dedupe_key(
        invoice_code=invoice.invoice_code,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        total_amount=invoice.total_amount,
        seller_tax_id=invoice.seller_tax_id,
    )

    # ---- 风险校验 ----
    flags = validate_invoice(db, invoice, source_file=inv_file)
    for note in result.warnings:
        flags.append(
            {"code": "ocr_note", "level": RiskLevel.WARNING, "message": note, "field": ""}
        )
    invoice.risk_flags = flags

    # ---- 审计字段 ----
    inv_file.ocr_provider = result.provider
    inv_file.ocr_raw_json = json.dumps(
        result.raw, ensure_ascii=False, default=str
    )[:RAW_JSON_LIMIT]
    db.flush()
    return invoice


def _create_blank_invoice(db: Session, inv_file: InvoiceFile, message: str) -> Invoice:
    """OCR 搞不定时，先建一张空票待人工录入，绝不丢原文件。"""
    old = db.query(Invoice).filter(Invoice.file_id == inv_file.id).first()
    if old is not None:
        db.delete(old)
        db.flush()

    invoice = Invoice(
        id=new_id(),
        file_id=inv_file.id,
        invoice_type="",
        status=InvoiceStatus.PENDING_REVIEW,
        currency="CNY",
        edit_history=[],
        source_note=message,
    )
    db.add(invoice)
    db.flush()

    flags = validate_invoice(db, invoice, source_file=inv_file)
    flags.insert(
        0,
        {
            "code": "manual_entry_required",
            "level": RiskLevel.WARNING,
            "message": f"未能自动识别，需要人工录入：{message}",
            "field": "",
        },
    )
    invoice.risk_flags = flags
    db.flush()
    return invoice


# --------------------------------------------------------------------------
# 单文件
# --------------------------------------------------------------------------
def process_file(db: Session, inv_file: InvoiceFile) -> InvoiceFile:
    inv_file.status = FileStatus.PROCESSING
    inv_file.error_code = None
    inv_file.error_message = None
    db.flush()

    result, error = _recognize(inv_file)

    if result is None:
        code = error.code if error else OCRCode.UNREADABLE
        text = error.message if error else "识别失败"
        if code in MANUAL_ENTRY_CODES:
            # 软失败：进待复核，等人工录入
            _create_blank_invoice(db, inv_file, text)
            inv_file.status = FileStatus.PENDING_REVIEW
        else:
            # 硬失败：OCR 不可用/额度用尽/文件损坏
            inv_file.status = FileStatus.FAILED
        inv_file.error_code = code
        inv_file.error_message = text
        inv_file.updated_at = datetime.now()
        db.flush()
        return inv_file

    _replace_invoice(db, inv_file, result)
    inv_file.status = FileStatus.PENDING_REVIEW
    inv_file.updated_at = datetime.now()
    db.flush()
    return inv_file


def recount_job(db: Session, job: ProcessingJob) -> None:
    files = db.query(InvoiceFile).filter(InvoiceFile.job_id == job.id).all()
    job.total = len(files)
    job.succeeded = sum(
        1 for f in files if f.status in (FileStatus.PENDING_REVIEW, FileStatus.COMPLETED)
    )
    job.failed = sum(1 for f in files if f.status == FileStatus.FAILED)
    job.pending_review = sum(1 for f in files if f.status == FileStatus.PENDING_REVIEW)
    job.completed = sum(1 for f in files if f.status == FileStatus.COMPLETED)
    db.flush()


# --------------------------------------------------------------------------
# 整批
# --------------------------------------------------------------------------
def process_job(job_id: str) -> None:
    """后台任务入口。自己管理 session，异常兜底，保证状态不卡在「识别中」。"""
    db = SessionLocal()
    try:
        job = db.get(ProcessingJob, job_id)
        if job is None:
            return

        job.status = JobStatus.PROCESSING
        db.commit()

        files = (
            db.query(InvoiceFile)
            .filter(InvoiceFile.job_id == job_id)
            .order_by(InvoiceFile.created_at.asc())
            .all()
        )

        for inv_file in files:
            # 已经在处理中/已完成的跳过，避免任务被重复触发时产生重复账目
            if inv_file.status != FileStatus.QUEUED:
                continue
            try:
                process_file(db, inv_file)
            except Exception as exc:  # 单张票崩了不能拖垮整批
                db.rollback()
                inv_file = db.get(InvoiceFile, inv_file.id)
                if inv_file is not None:
                    inv_file.status = FileStatus.FAILED
                    inv_file.error_code = OCRCode.PROVIDER_ERROR
                    inv_file.error_message = f"处理时发生异常：{exc}"
            recount_job(db, job)
            db.commit()

        recount_job(db, job)
        job.status = (
            JobStatus.FAILED
            if job.total and job.failed == job.total
            else (JobStatus.PARTIAL if job.failed else JobStatus.DONE)
        )
        job.finished_at = datetime.now()
        db.commit()

    except Exception:
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        if job is not None:
            job.status = JobStatus.FAILED
            job.error_message = traceback.format_exc()[-1500:]
            job.finished_at = datetime.now()
            for inv_file in job.files:
                if inv_file.status in (FileStatus.QUEUED, FileStatus.PROCESSING):
                    inv_file.status = FileStatus.FAILED
                    inv_file.error_code = OCRCode.PROVIDER_ERROR
                    inv_file.error_message = "批次异常中断，请重试该文件。"
            db.commit()
    finally:
        db.close()


def retry_file(db: Session, inv_file: InvoiceFile) -> InvoiceFile:
    """重试单个文件（OCR 失败可重试，项目书第十三章可靠性要求）。"""
    old = db.query(Invoice).filter(Invoice.file_id == inv_file.id).first()
    if old is not None:
        db.delete(old)
        db.flush()
    inv_file.status = FileStatus.QUEUED
    inv_file.error_code = None
    inv_file.error_message = None
    db.commit()

    process_file(db, inv_file)
    if inv_file.job is not None:
        recount_job(db, inv_file.job)
    db.commit()
    return inv_file


# --------------------------------------------------------------------------
# 确认入账
# --------------------------------------------------------------------------
def create_ledger_entry(
    db: Session, invoice: Invoice, confirmed_by: str = "本机用户"
) -> LedgerEntry:
    """把发票固化成一条账目快照。已存在则更新，保证不会重复入账。"""
    inv_file = db.get(InvoiceFile, invoice.file_id)
    names = [it.item_name for it in invoice.items if it.item_name]
    item_text = "；".join(dict.fromkeys(names))[:512]

    entry = invoice.ledger_entry
    if entry is None:
        entry = LedgerEntry(id=new_id(), invoice_id=invoice.id)
        db.add(entry)

    entry.entry_date = invoice.invoice_date
    entry.invoice_type = invoice.invoice_type
    entry.invoice_code = invoice.invoice_code
    entry.invoice_number = invoice.invoice_number
    entry.seller_name = invoice.seller_name
    entry.seller_tax_id = invoice.seller_tax_id
    entry.buyer_name = invoice.buyer_name
    entry.item_name = item_text
    entry.amount_without_tax = invoice.amount_without_tax
    entry.tax_amount = invoice.tax_amount
    entry.total_amount = invoice.total_amount
    entry.expense_category = invoice.expense_category
    entry.account_subject = invoice.account_subject
    entry.original_name = inv_file.original_name if inv_file else ""
    entry.confirmed_by = confirmed_by
    entry.confirmed_at = datetime.now()

    invoice.status = InvoiceStatus.CONFIRMED
    invoice.confirmed_by = confirmed_by
    invoice.confirmed_at = entry.confirmed_at
    if inv_file is not None:
        inv_file.status = FileStatus.COMPLETED
        inv_file.updated_at = datetime.now()
    if inv_file is not None and inv_file.job is not None:
        recount_job(db, inv_file.job)

    db.flush()
    return entry


def revoke_ledger_entry(db: Session, invoice: Invoice) -> None:
    """撤销入账：账目删除，发票回到待复核。"""
    entry = invoice.ledger_entry
    if entry is not None:
        db.delete(entry)
    invoice.status = InvoiceStatus.PENDING_REVIEW
    invoice.confirmed_by = None
    invoice.confirmed_at = None
    inv_file = db.get(InvoiceFile, invoice.file_id)
    if inv_file is not None:
        inv_file.status = FileStatus.PENDING_REVIEW
        inv_file.updated_at = datetime.now()
        if inv_file.job is not None:
            recount_job(db, inv_file.job)
    db.flush()
