"""OCR 适配层 —— 统一数据结构与供应商接口（项目书 9.1）。

设计要点：
- 业务层只认 `InvoiceResult`，不认任何供应商字段名。
- 想换供应商，只需要再写一个 `InvoiceOCRProvider` 子类，业务代码一行不动。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path


# --------------------------------------------------------------------------
# 错误码：前端按 error_code 给不同的引导文案
# --------------------------------------------------------------------------
class OCRCode:
    UNSUPPORTED_FORMAT = "unsupported_format"       # 文件类型不支持
    NO_TEXT_LAYER = "no_text_layer"                 # 扫描件/图片 PDF，无文本层
    UNREADABLE = "unreadable_file"                  # 文件损坏或加密
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"  # 云 OCR 没配密钥
    PROVIDER_ERROR = "provider_error"               # 云 OCR 返回错误
    QUOTA_EXCEEDED = "quota_exceeded"               # 当天额度用尽
    TIMEOUT = "timeout"                             # 云 OCR 超时
    NEEDS_MANUAL = "needs_manual_entry"             # 需要人工录入
    PARSE_FAILED = "parse_failed"                   # 拿到文本但没解析出关键字段


class OCRProviderError(Exception):
    """OCR 环节的可预期失败，带机器可读的 code 和给用户看的中文说明。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# 统一数据结构
# --------------------------------------------------------------------------
@dataclass
class InvoiceItemData:
    """一条发票明细行。"""

    item_name: str = ""
    specification: str = ""
    unit: str = ""
    quantity: float | None = None
    unit_price: float | None = None
    tax_rate: str = ""
    amount: float | None = None
    tax_amount: float | None = None


@dataclass
class InvoiceResult:
    """所有 OCR 供应商输出的统一结构。"""

    invoice_type: str = ""
    invoice_code: str = ""
    invoice_number: str = ""
    invoice_date: str = ""            # 统一 yyyy-mm-dd
    seller_name: str = ""
    seller_tax_id: str = ""
    buyer_name: str = ""
    buyer_tax_id: str = ""

    amount_without_tax: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    currency: str = "CNY"

    items: list[InvoiceItemData] = field(default_factory=list)

    # 发票备注栏的原文（工程类发票会写开户银行/账号/工程名称/工程地址）
    remark: str = ""

    # 逐字段置信度 0~1；用于「低置信字段」高亮
    field_confidence: dict[str, float] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)          # 供应商原始响应
    warnings: list[str] = field(default_factory=list)
    provider: str = ""

    # 报销入账必须齐全的字段
    REQUIRED_FIELDS = ("invoice_number", "invoice_date", "seller_name", "total_amount")

    # ---------------- 派生指标 ----------------
    @property
    def confidence(self) -> float:
        """关键字段的平均置信度。"""
        vals = [
            self.field_confidence.get(f, 0.0) for f in self.REQUIRED_FIELDS
        ] or [0.0]
        return round(sum(vals) / len(vals), 4)

    def missing_required(self) -> list[str]:
        return [f for f in self.REQUIRED_FIELDS if not getattr(self, f)]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["confidence"] = self.confidence
        return data


# --------------------------------------------------------------------------
# 供应商接口
# --------------------------------------------------------------------------
class InvoiceOCRProvider(ABC):
    """所有 OCR 供应商的统一接口（项目书 9.1）。"""

    name: str = "base"
    display_name: str = "基础供应商"

    @abstractmethod
    def recognize(self, file_path: Path, file_type: str) -> InvoiceResult:
        """识别一个票据文件，返回标准 InvoiceResult。

        file_type 形如 'pdf' / 'jpg' / 'png'。
        失败时抛 OCRProviderError。
        """

    def normalize_response(self, raw: dict, file_type: str) -> InvoiceResult:
        """把供应商原始 JSON 转成标准结构。默认实现交给 recognize 内部处理。"""
        raise NotImplementedError

    def health_check(self) -> tuple[bool, str]:
        """供应商是否可用。返回 (可用, 说明)。"""
        return True, "ok"


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
_ALNUM_RUN = re.compile(r"[0-9A-Za-z]+")


def first_alnum(text: str, min_len: int = 1) -> str:
    """从 '123456789012345678 其他' 里取出前导的字母数字串。"""
    if not text:
        return ""
    m = _ALNUM_RUN.search(text.strip())
    if not m or len(m.group()) < min_len:
        return ""
    return m.group()


def to_float(value) -> float | None:
    """把 '¥1,035.40' / '1035.4' / 1035.4 统一转成 float。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[¥￥,\s元]", "", str(value))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


_DATE_CN = re.compile(r"(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})")
_DATE_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def normalize_date(text: str) -> str:
    """把各种写法统一成 yyyy-mm-dd；解析不出来返回空串。"""
    if not text:
        return ""
    s = str(text).strip()
    m = _DATE_COMPACT.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _DATE_CN.search(s)
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        return f"{y}-{mo:02d}-{d:02d}"
    return ""
