"""供应商注册与选择。

选择顺序（对应 config.OCR_PROVIDER）：
    baidu  -> 百度智能云（必须配密钥）
    local  -> 不调云：PDF 走文本层解析；图片走本地 PaddleOCR（没装则提示人工录入）
    auto   -> 配了百度密钥就走百度，否则等价于 local

兜底链（主供应商失败时依次尝试）：
    PDF：本地文本层（准、快）→ PaddleOCR（扫描件没有文本层时救场）
    图片：PaddleOCR（GPU）→ RapidOCR（CPU，装了才可用）
"""

from __future__ import annotations

import importlib.util

from ..config import settings
from .base import OCRCode, InvoiceOCRProvider, OCRProviderError
from .local_pdf import LocalTextPdfProvider
from .paddle_ocr import paddle_env_ready

IMAGE_TYPES = {"jpg", "jpeg", "png", "bmp", "webp", "tif", "tiff"}


def rapidocr_available() -> bool:
    return importlib.util.find_spec("rapidocr_onnxruntime") is not None


def paddle_available() -> bool:
    ok, _ = paddle_env_ready()
    return ok


def available_providers() -> list[dict]:
    """给「设置」页展示供应商状态用。"""
    paddle_ok, paddle_note = paddle_env_ready()
    return [
        {
            "name": "local",
            "display_name": "本地 PDF 文本层解析",
            "ready": True,
            "note": "零成本、不联网；仅支持带文本层的 PDF",
        },
        {
            "name": "paddleocr",
            "display_name": "本地 PaddleOCR（GPU 加速）",
            "ready": paddle_ok,
            "note": "支持 JPG/PNG 图片与扫描件 PDF，票据不出本机",
            "detail": paddle_note,
        },
        {
            "name": "baidu",
            "display_name": "百度智能云增值税发票识别",
            "ready": bool(settings.baidu_api_key and settings.baidu_secret_key),
            "note": "支持 PDF/JPG/PNG；个人 1000 次/月免费额度，需实名认证",
        },
        {
            "name": "rapidocr",
            "display_name": "本地图片 OCR（RapidOCR，CPU）",
            "ready": rapidocr_available(),
            "note": "轻量 CPU 方案；需 pip install rapidocr-onnxruntime",
        },
    ]


def get_provider(file_type: str) -> InvoiceOCRProvider:
    """按配置和文件类型挑一个主供应商。"""
    ft = (file_type or "").lower().lstrip(".")
    chosen = settings.resolved_ocr_provider

    if chosen == "baidu":
        from .baidu import BaiduInvoiceProvider

        return BaiduInvoiceProvider()

    if ft == "pdf":
        # PDF 优先文本层：直接读字，比 OCR 更准也更快
        return LocalTextPdfProvider()

    if ft in IMAGE_TYPES:
        if paddle_available():
            from .paddle_ocr import PaddleOCRProvider

            return PaddleOCRProvider()
        if rapidocr_available():
            from .rapidocr import RapidOCRImageProvider

            return RapidOCRImageProvider()
        _, note = paddle_env_ready()
        raise OCRProviderError(
            OCRCode.NEEDS_MANUAL,
            "本机没有可用的图片 OCR。三条路：① 安装 paddlepaddle-gpu + paddleocr"
            f"（本机未检测到：{note}）；② 配置百度 OCR 密钥；"
            "③ 安装 pip install rapidocr-onnxruntime。在此之前可以人工录入这张票。",
        )

    raise OCRProviderError(
        OCRCode.UNSUPPORTED_FORMAT, f"暂不支持的文件类型：{file_type}"
    )


def get_fallback_provider(file_type: str) -> InvoiceOCRProvider | None:
    """主供应商失败时的兜底。

    PDF 的文本层解析失败通常意味着「扫描件/图片型 PDF」——正好交给 PaddleOCR。
    """
    ft = (file_type or "").lower().lstrip(".")
    if ft == "pdf":
        if paddle_available():
            from .paddle_ocr import PaddleOCRProvider

            return PaddleOCRProvider()
        return None
    if ft in IMAGE_TYPES and rapidocr_available():
        from .rapidocr import RapidOCRImageProvider

        return RapidOCRImageProvider()
    return None


def health_check_all() -> list[dict]:
    """批量体检，给设置页显示。"""
    out = []
    for row in available_providers():
        if not row["ready"]:
            out.append({**row, "detail": row.get("detail") or row["note"]})
            continue
        try:
            provider = _instantiate(row["name"])
            ok, detail = provider.health_check()
            out.append({**row, "ready": ok, "detail": detail})
        except OCRProviderError as exc:
            out.append({**row, "ready": False, "detail": exc.message})
        except Exception as exc:  # 兜底，避免设置页 500
            out.append({**row, "ready": False, "detail": str(exc)})
    return out


def _instantiate(name: str) -> InvoiceOCRProvider:
    if name == "baidu":
        from .baidu import BaiduInvoiceProvider

        return BaiduInvoiceProvider()
    if name == "paddleocr":
        from .paddle_ocr import PaddleOCRProvider

        return PaddleOCRProvider()
    if name == "rapidocr":
        from .rapidocr import RapidOCRImageProvider

        return RapidOCRImageProvider()
    return LocalTextPdfProvider()
