"""数据模型（项目书第六章「数据字段」+ 第十一章「数据库草案」）。

6 张表：
    processing_job  一次批量上传 = 一个批次任务
    invoice_file    单个票据文件（含处理状态、OCR 原始响应）
    invoice         一张发票的结构化字段 + 智能结果 + 风险标记
    invoice_item    发票明细行（项目名称/规格/数量/单价/税率）
    ledger_entry    确认入账后生成的账目快照（账本页读这张表）
    category_rule   费用分类规则（企业自定义 + 系统内置）
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from .database import Base


def new_id() -> str:
    """统一用 uuid4 字符串做主键，方便前端直接当 key 用。"""
    return str(uuid.uuid4())


# --------------------------------------------------------------------------
# 状态常量（前端要和这些字符串对齐）
# --------------------------------------------------------------------------
class JobStatus:
    QUEUED = "queued"            # 排队中
    PROCESSING = "processing"    # 识别中
    DONE = "done"                # 全部处理完
    PARTIAL = "partial"          # 部分失败
    FAILED = "failed"            # 整批失败


class FileStatus:
    QUEUED = "queued"                    # 排队中
    PROCESSING = "processing"            # 识别中
    PENDING_REVIEW = "pending_review"    # 待复核
    COMPLETED = "completed"              # 已完成（已确认入账）
    FAILED = "failed"                    # 失败


class InvoiceStatus:
    PENDING_REVIEW = "pending_review"
    CONFIRMED = "confirmed"


class RiskLevel:
    ERROR = "error"      # 红色：阻止确认
    WARNING = "warning"  # 黄色：允许确认


class RuleType:
    CUSTOM = "custom"    # 企业自定义规则，优先级高
    SYSTEM = "system"    # 系统内置关键词规则


class MatchField:
    SELLER_NAME = "seller_name"
    ITEM_NAME = "item_name"


# --------------------------------------------------------------------------
# 表 1：批次任务
# --------------------------------------------------------------------------
class ProcessingJob(Base):
    __tablename__ = "processing_job"

    id = Column(String(36), primary_key=True, default=new_id)
    total = Column(Integer, default=0, nullable=False)          # 本批文件总数
    succeeded = Column(Integer, default=0, nullable=False)      # 识别成功数
    failed = Column(Integer, default=0, nullable=False)         # 失败数
    pending_review = Column(Integer, default=0, nullable=False)  # 待复核数
    completed = Column(Integer, default=0, nullable=False)      # 已入账数
    status = Column(String(16), default=JobStatus.QUEUED, nullable=False)
    ocr_provider = Column(String(32))
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    finished_at = Column(DateTime)

    files = relationship(
        "InvoiceFile", back_populates="job", cascade="all, delete-orphan"
    )

    @property
    def processed(self) -> int:
        return self.succeeded + self.failed


# --------------------------------------------------------------------------
# 表 2：票据文件
# --------------------------------------------------------------------------
class InvoiceFile(Base):
    __tablename__ = "invoice_file"

    id = Column(String(36), primary_key=True, default=new_id)
    job_id = Column(String(36), ForeignKey("processing_job.id"), index=True)

    # ---- 文件本身 ----
    original_name = Column(String(255), nullable=False)
    stored_name = Column(String(255), nullable=False)
    storage_path = Column(Text, nullable=False)
    file_type = Column(String(16), nullable=False)      # pdf / jpg / jpeg / png
    file_size = Column(Integer, default=0, nullable=False)
    # sha256：只用来识别「同一个文件重复上传」，不替代业务去重
    file_hash = Column(String(64), index=True)
    page_count = Column(Integer, default=1, nullable=False)

    # ---- 处理状态 ----
    status = Column(String(24), default=FileStatus.QUEUED, index=True, nullable=False)
    error_code = Column(String(48))          # 机器可读错误码
    error_message = Column(Text)             # 给人看的中文错误说明

    # ---- 审计字段 ----
    ocr_provider = Column(String(32))
    ocr_raw_json = Column(Text)              # 保留 OCR 原始响应，便于追溯
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    job = relationship("ProcessingJob", back_populates="files")
    invoice = relationship(
        "Invoice", back_populates="file", uselist=False, cascade="all, delete-orphan"
    )


# --------------------------------------------------------------------------
# 表 3：发票
# --------------------------------------------------------------------------
class Invoice(Base):
    __tablename__ = "invoice"

    id = Column(String(36), primary_key=True, default=new_id)
    file_id = Column(
        String(36), ForeignKey("invoice_file.id"), index=True, nullable=False
    )

    # ---- 票面字段 ----
    invoice_type = Column(String(64))        # 电子发票（增值税专用发票）等
    invoice_code = Column(String(32), index=True)   # 老票有；全电发票为空
    invoice_number = Column(String(32), index=True)
    invoice_date = Column(String(10))        # 统一 yyyy-mm-dd
    seller_name = Column(String(255))
    seller_tax_id = Column(String(32))
    buyer_name = Column(String(255))
    buyer_tax_id = Column(String(32))

    # ---- 金额 ----
    amount_without_tax = Column(Float)       # 不含税金额（合计）
    tax_amount = Column(Float)               # 税额（合计）
    total_amount = Column(Float)             # 价税合计
    currency = Column(String(8), default="CNY")

    # ---- 智能结果 ----
    expense_category = Column(String(64))
    account_subject = Column(String(64))
    confidence = Column(Float)               # 关键字段平均置信度 0~1
    field_confidence = Column(JSON, default=dict)   # 逐字段置信度
    rule_source = Column(String(64))         # 命中规则的说明，如「自定义规则:阿里云」
    risk_flags = Column(JSON, default=list)  # [{code, level, message, field}]

    # ---- 业务去重 ----
    # 有发票代码用「代码+号码」；全电发票用「号码+日期+价税合计+销方税号」
    dedupe_key = Column(String(160), index=True)

    # ---- 流程状态 ----
    status = Column(
        String(24), default=InvoiceStatus.PENDING_REVIEW, index=True, nullable=False
    )
    # 人工修改痕迹：[{at, field, old, new}]
    edit_history = Column(JSON, default=list)
    # 说明性备注：如「未能自动识别，需人工录入」的原因
    source_note = Column(Text)
    # 发票备注栏的原文（识别自票面左下角，可能多行）：
    # 工程类发票会在这里写开户银行/账号/工程名称/工程地址/合同编号。
    # 属于**发票级**信息，不是某一行明细的备注（明细备注见 InvoiceItem.remark）。
    invoice_remark = Column(Text)

    # ---- 审计字段 ----
    confirmed_by = Column(String(64))
    confirmed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    file = relationship("InvoiceFile", back_populates="invoice")
    items = relationship(
        "InvoiceItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceItem.sort_order",
    )
    ledger_entry = relationship(
        "LedgerEntry", back_populates="invoice", uselist=False, cascade="all, delete-orphan"
    )


# --------------------------------------------------------------------------
# 表 4：发票明细行
# --------------------------------------------------------------------------
class InvoiceItem(Base):
    __tablename__ = "invoice_item"

    id = Column(String(36), primary_key=True, default=new_id)
    invoice_id = Column(String(36), ForeignKey("invoice.id"), index=True, nullable=False)

    item_name = Column(String(255))          # 项目名称，如 *电子工业设备*LED液晶电视机
    specification = Column(String(255))      # 规格型号
    unit = Column(String(16))                # 单位
    quantity = Column(Float)                 # 数量
    unit_price = Column(Float)               # 单价
    tax_rate = Column(String(16))            # 税率/征收率，保留 "13%" 原样
    amount = Column(Float)                   # 金额（不含税）
    tax_amount = Column(Float)               # 税额
    # 明细行备注：给「折扣/退货（金额为负）」这类需要解释的行留个说明位置。
    # 导出时和发票级备注合并写进「备注」列，并高亮该单元格。
    remark = Column(String(255))
    sort_order = Column(Integer, default=0)

    invoice = relationship("Invoice", back_populates="items")


# --------------------------------------------------------------------------
# 表 5：账目（确认入账时生成快照）
# --------------------------------------------------------------------------
class LedgerEntry(Base):
    __tablename__ = "ledger_entry"

    id = Column(String(36), primary_key=True, default=new_id)
    invoice_id = Column(
        String(36), ForeignKey("invoice.id"), index=True, nullable=False, unique=True
    )

    entry_date = Column(String(10), index=True)   # 开票日期，账本按它筛选
    invoice_type = Column(String(64))
    invoice_code = Column(String(32))
    invoice_number = Column(String(32), index=True)
    seller_name = Column(String(255))
    seller_tax_id = Column(String(32))
    buyer_name = Column(String(255))
    item_name = Column(String(512))               # 明细行名称汇总
    amount_without_tax = Column(Float)
    tax_amount = Column(Float)
    total_amount = Column(Float)
    expense_category = Column(String(64), index=True)
    account_subject = Column(String(64))
    original_name = Column(String(255))
    confirmed_by = Column(String(64))
    confirmed_at = Column(DateTime, default=datetime.now, nullable=False)

    invoice = relationship("Invoice", back_populates="ledger_entry")


# --------------------------------------------------------------------------
# 表 6：分类规则
# --------------------------------------------------------------------------
class CategoryRule(Base):
    __tablename__ = "category_rule"

    id = Column(String(36), primary_key=True, default=new_id)
    rule_type = Column(String(16), default=RuleType.SYSTEM, nullable=False)
    match_field = Column(String(24), default=MatchField.ITEM_NAME, nullable=False)
    keyword = Column(String(128), nullable=False)   # 命中的关键词
    expense_category = Column(String(64), nullable=False)
    account_subject = Column(String(64))
    priority = Column(Integer, default=100, nullable=False)  # 数字越小越优先
    enabled = Column(Boolean, default=True, nullable=False)
    hits = Column(Integer, default=0, nullable=False)        # 命中次数，便于复盘
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    __table_args__ = (
        Index("ix_rule_lookup", "enabled", "rule_type", "priority"),
    )


# --------------------------------------------------------------------------
# 表 7：当前企业档案
# --------------------------------------------------------------------------
class CompanyProfile(Base):
    """用于判断进项/销项的当前企业。

    V1 只保存一条当前企业记录；后续要支持代账多主体时，可以把这里扩成多行并加
    `is_default` 筛选，不需要改发票表。
    """

    __tablename__ = "company_profile"

    id = Column(String(36), primary_key=True, default=new_id)
    name = Column(String(255), nullable=False)
    tax_id = Column(String(32), index=True)
    aliases = Column(JSON, default=list)
    is_default = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )
