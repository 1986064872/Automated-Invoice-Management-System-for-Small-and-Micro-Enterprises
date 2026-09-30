"""Pydantic 出入参模型 + ORM → 前端字典的序列化助手。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import FileStatus, InvoiceFile, InvoiceStatus, LedgerEntry, ProcessingJob
from .services.validate import error_count, warning_count

# ==========================================================================
# 请求体
# ==========================================================================


class InvoiceUpdate(BaseModel):
    """复核工作台保存人工修改。所有字段可选，只传改过的。"""

    invoice_type: str | None = None
    invoice_code: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    seller_name: str | None = None
    seller_tax_id: str | None = None
    buyer_name: str | None = None
    buyer_tax_id: str | None = None
    # 发票备注栏原文（识别自票面，可人工修正）
    invoice_remark: str | None = None
    amount_without_tax: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    expense_category: str | None = None
    account_subject: str | None = None
    items: list["InvoiceItemIn"] | None = None
    # 勾上就把「销售方/项目名称 + 费用分类」沉淀成企业自定义规则
    save_as_rule: bool = False
    edited_by: str = "本机用户"


class InvoiceItemIn(BaseModel):
    id: str | None = None
    item_name: str | None = None
    specification: str | None = None
    unit: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    tax_rate: str | None = None
    amount: float | None = None
    tax_amount: float | None = None
    remark: str | None = None


class ConfirmIn(BaseModel):
    confirmed_by: str = "本机用户"
    # 有需要时允许「带警告确认」，但红色错误一律不允许
    override_warnings: bool = False


class RuleCreate(BaseModel):
    rule_type: str = "custom"
    match_field: str = Field(default="item_name", pattern="^(seller_name|item_name)$")
    keyword: str = Field(min_length=1, max_length=128)
    expense_category: str = Field(min_length=1, max_length=64)
    account_subject: str | None = None
    priority: int = 10
    enabled: bool = True
    note: str | None = None


class RuleUpdate(BaseModel):
    match_field: str | None = Field(default=None, pattern="^(seller_name|item_name)$")
    keyword: str | None = None
    expense_category: str | None = None
    account_subject: str | None = None
    priority: int | None = None
    enabled: bool | None = None
    note: str | None = None


class ExportRequest(BaseModel):
    """导出条件。前端把账本页当前的筛选原样传过来，保证「看到的 = 导出的」。"""

    month: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    status: str = "all"
    direction: str | None = None
    category: str | None = None
    q: str | None = None
    only_confirmed: bool = False
    file_name: str | None = None
    # 只导出勾选的这些票（账本批量导出）。None = 不加这个条件；
    # 空列表 = 一张都不导（不是「全部」，别搞反）。
    invoice_ids: list[str] | None = None


class CompanyProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    tax_id: str | None = Field(default=None, max_length=32)
    aliases: list[str] = Field(default_factory=list)


class CompanyProfileOut(BaseModel):
    name: str
    tax_id: str | None = None
    aliases: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class CompanySuggestionOut(BaseModel):
    name: str
    tax_id: str | None = None
    roles: list[str] = []
    invoice_count: int = 0
    buyer_count: int = 0
    seller_count: int = 0


class BatchDeleteRequest(BaseModel):
    """批量删除。原票一样走回收站，不硬删。"""

    ids: list[str]
    delete_file: bool = True


# ==========================================================================
# 响应模型
# ==========================================================================


class RiskFlag(BaseModel):
    code: str = ""
    level: str = "warning"
    message: str = ""
    field: str = ""


class InvoiceItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    item_name: str | None = None
    specification: str | None = None
    unit: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    tax_rate: str | None = None
    amount: float | None = None
    tax_amount: float | None = None
    remark: str | None = None


class InvoiceFileOut(BaseModel):
    id: str
    job_id: str | None = None
    invoice_id: str | None = None
    original_name: str
    file_type: str
    file_size: int = 0
    page_count: int = 1
    status: str
    error_code: str | None = None
    error_message: str | None = None
    ocr_provider: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw_url: str = ""


class InvoiceOut(BaseModel):
    id: str
    file_id: str
    invoice_type: str | None = None
    invoice_code: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    seller_name: str | None = None
    seller_tax_id: str | None = None
    buyer_name: str | None = None
    buyer_tax_id: str | None = None
    amount_without_tax: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    currency: str | None = "CNY"
    expense_category: str | None = None
    account_subject: str | None = None
    confidence: float | None = None
    field_confidence: dict | None = None
    rule_source: str | None = None
    risk_flags: list[RiskFlag] = []
    dedupe_key: str | None = None
    status: str
    edit_history: list | None = None
    source_note: str | None = None
    invoice_remark: str | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[InvoiceItemOut] = []
    file: InvoiceFileOut | None = None
    error_count: int = 0
    warning_count: int = 0
    raw_url: str = ""


class JobOut(BaseModel):
    id: str
    total: int
    succeeded: int
    failed: int
    pending_review: int
    completed: int
    status: str
    ocr_provider: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None
    progress: float = 0.0
    files: list[InvoiceFileOut] = []


class UploadRejected(BaseModel):
    name: str
    reason: str


class UploadResult(BaseModel):
    job_id: str | None = None
    accepted: list[InvoiceFileOut] = []
    rejected: list[UploadRejected] = []
    message: str = ""


class LedgerEntryOut(BaseModel):
    """账本一行。

    注意：这个模型必须把 `ledger_row()` 返回的字段**全部**声明出来。
    FastAPI 的 response_model 会剥离未声明的字段，漏一个前端就静默显示空值。
    """

    id: str
    invoice_id: str
    entry_date: str | None = None
    invoice_type: str | None = None
    invoice_code: str | None = None
    invoice_number: str | None = None
    seller_name: str | None = None
    seller_tax_id: str | None = None
    buyer_name: str | None = None
    direction: str = "unknown"
    direction_text: str = "待判断"
    company_role: str = ""
    counterparty_name: str = ""
    counterparty_tax_id: str = ""
    direction_reason: str = ""
    item_name: str | None = None
    amount_without_tax: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    expense_category: str | None = None
    account_subject: str | None = None
    original_name: str | None = None
    status: str = "pending_review"
    file_id: str | None = None
    confidence: float | None = None
    error_count: int = 0
    warning_count: int = 0
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    raw_url: str = ""


class LedgerSummary(BaseModel):
    count: int = 0
    total_amount: float = 0.0
    amount_without_tax: float = 0.0
    tax_amount: float = 0.0
    by_category: list[dict] = []


class LedgerPage(BaseModel):
    items: list[LedgerEntryOut] = []
    summary: LedgerSummary = LedgerSummary()
    total: int = 0
    page: int = 1
    page_size: int = 50


class InvoiceListOut(BaseModel):
    items: list[InvoiceOut] = []
    total: int = 0
    page: int = 1
    page_size: int = 20
    summary: LedgerSummary = LedgerSummary()


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rule_type: str
    match_field: str
    keyword: str
    expense_category: str
    account_subject: str | None = None
    priority: int
    enabled: bool
    hits: int = 0
    note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DashboardOut(BaseModel):
    month: str = ""
    invoice_count: int = 0
    total_amount: float = 0.0
    pending_review: int = 0
    # 全部时间的待复核数（侧边栏角标用，不受月份筛选影响）
    pending_review_total: int = 0
    abnormal: int = 0
    confirmed: int = 0
    failed: int = 0
    by_category: list[dict] = []
    recent_jobs: list[JobOut] = []
    provider: dict = {}


class DeleteResult(BaseModel):
    ok: bool = True
    message: str = ""


InvoiceUpdate.model_rebuild()


# ==========================================================================
# ORM → 字典
# ==========================================================================


def file_raw_url(file_id: str) -> str:
    return f"/api/v1/files/{file_id}/raw"


def file_to_dict(inv_file: InvoiceFile | None, *, with_children: bool = False) -> dict:
    if inv_file is None:
        return {}
    return {
        "id": inv_file.id,
        "job_id": inv_file.job_id,
        "original_name": inv_file.original_name,
        "file_type": inv_file.file_type,
        "file_size": inv_file.file_size or 0,
        "page_count": inv_file.page_count or 1,
        "status": inv_file.status,
        "error_code": inv_file.error_code,
        "error_message": inv_file.error_message,
        "ocr_provider": inv_file.ocr_provider,
        "created_at": inv_file.created_at,
        "updated_at": inv_file.updated_at,
        "raw_url": file_raw_url(inv_file.id),
    }


def invoice_to_dict(invoice, *, with_file: bool = True) -> dict:
    inv_file = invoice.file if with_file else None
    flags = invoice.risk_flags or []
    return {
        "id": invoice.id,
        "file_id": invoice.file_id,
        "invoice_type": invoice.invoice_type,
        "invoice_code": invoice.invoice_code,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "seller_name": invoice.seller_name,
        "seller_tax_id": invoice.seller_tax_id,
        "buyer_name": invoice.buyer_name,
        "buyer_tax_id": invoice.buyer_tax_id,
        "amount_without_tax": invoice.amount_without_tax,
        "tax_amount": invoice.tax_amount,
        "total_amount": invoice.total_amount,
        "currency": invoice.currency or "CNY",
        "expense_category": invoice.expense_category,
        "account_subject": invoice.account_subject,
        "confidence": invoice.confidence,
        "field_confidence": invoice.field_confidence or {},
        "rule_source": invoice.rule_source,
        "risk_flags": flags,
        "dedupe_key": invoice.dedupe_key,
        "status": invoice.status,
        "edit_history": invoice.edit_history or [],
        "source_note": invoice.source_note,
        "invoice_remark": invoice.invoice_remark,
        "confirmed_by": invoice.confirmed_by,
        "confirmed_at": invoice.confirmed_at,
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
        "items": invoice.items,
        "file": file_to_dict(inv_file) if inv_file is not None else None,
        "error_count": error_count(flags),
        "warning_count": warning_count(flags),
        "raw_url": file_raw_url(invoice.file_id),
    }


def job_to_dict(job: ProcessingJob | None, *, with_files: bool = False) -> dict:
    if job is None:
        return {}
    total = job.total or 0
    done = (job.succeeded or 0) + (job.failed or 0)
    return {
        "id": job.id,
        "total": total,
        "succeeded": job.succeeded or 0,
        "failed": job.failed or 0,
        "pending_review": job.pending_review or 0,
        "completed": job.completed or 0,
        "status": job.status,
        "ocr_provider": job.ocr_provider,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "progress": round(done / total, 4) if total else 0.0,
        "files": [file_to_dict(f) for f in job.files] if with_files else [],
    }


def ledger_to_dict(entry: LedgerEntry, *, invoice=None) -> dict:
    inv_file = invoice.file if invoice is not None else None
    return {
        "id": entry.id,
        "invoice_id": entry.invoice_id,
        "entry_date": entry.entry_date,
        "invoice_type": entry.invoice_type,
        "invoice_code": entry.invoice_code,
        "invoice_number": entry.invoice_number,
        "seller_name": entry.seller_name,
        "seller_tax_id": entry.seller_tax_id,
        "buyer_name": entry.buyer_name,
        "item_name": entry.item_name,
        "amount_without_tax": entry.amount_without_tax,
        "tax_amount": entry.tax_amount,
        "total_amount": entry.total_amount,
        "expense_category": entry.expense_category,
        "account_subject": entry.account_subject,
        "original_name": entry.original_name,
        "confirmed_by": entry.confirmed_by,
        "confirmed_at": entry.confirmed_at,
        "raw_url": file_raw_url(inv_file.id) if inv_file is not None else "",
    }


# 状态中文名，前端也用得上
STATUS_TEXT = {
    InvoiceStatus.PENDING_REVIEW: "待复核",
    InvoiceStatus.CONFIRMED: "已入账",
    FileStatus.QUEUED: "排队中",
    FileStatus.PROCESSING: "识别中",
    FileStatus.PENDING_REVIEW: "待复核",
    FileStatus.COMPLETED: "已完成",
    FileStatus.FAILED: "失败",
}
