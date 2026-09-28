"""OCR 适配层：统一接口 + 多供应商实现。

业务层只 import `get_provider` 和 `InvoiceResult`，不碰任何供应商细节。

供应商一览：
    local     本地 PDF 文本层解析（默认，零成本，最准）
    paddleocr 本地 PaddleOCR（GPU，处理图片与扫描件 PDF）
    rapidocr  本地 RapidOCR（CPU，可选安装）
    baidu     百度智能云增值税发票识别（云端，需密钥）
"""

from .base import (
    InvoiceItemData,
    InvoiceOCRProvider,
    InvoiceResult,
    OCRCode,
    OCRProviderError,
)
from .paddle_ocr import paddle_env_ready, shutdown_paddle_workers
from .registry import (
    available_providers,
    get_fallback_provider,
    get_provider,
    health_check_all,
    paddle_available,
    rapidocr_available,
)

__all__ = [
    "InvoiceItemData",
    "InvoiceOCRProvider",
    "InvoiceResult",
    "OCRCode",
    "OCRProviderError",
    "available_providers",
    "get_fallback_provider",
    "get_provider",
    "health_check_all",
    "paddle_available",
    "paddle_env_ready",
    "rapidocr_available",
    "shutdown_paddle_workers",
]
