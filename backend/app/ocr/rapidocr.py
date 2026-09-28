"""本地图片 OCR Provider（可选，基于 RapidOCR / PaddleOCR ONNX 版）。

用途：识别 JPG/JPEG/PNG 拍票、截图。票据不出本机，无调用额度限制。
前提：pip install rapidocr-onnxruntime （约 80MB，含 onnxruntime）

实现思路：RapidOCR 返回「文字框四点坐标 + 文本 + 置信度」，
正好能复用 parser.parse_invoice_fragments 做版面解析，无需为图片重写一套逻辑。
"""

from __future__ import annotations

from pathlib import Path

from .base import OCRCode, InvoiceOCRProvider, InvoiceResult, OCRProviderError
from .parser import parse_invoice_fragments


class RapidOCRImageProvider(InvoiceOCRProvider):
    name = "rapidocr"
    display_name = "本地图片 OCR（RapidOCR）"

    def __init__(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover - 取决于本机是否安装
            raise OCRProviderError(
                OCRCode.PROVIDER_NOT_CONFIGURED,
                "未安装本地图片 OCR。执行 pip install rapidocr-onnxruntime 后再试，"
                "或把图片票据交给百度 OCR / 人工录入。",
            ) from exc
        self._engine = RapidOCR()

    def recognize(self, file_path: Path, file_type: str) -> InvoiceResult:
        if file_type.lower() == "pdf":
            raise OCRProviderError(
                OCRCode.UNSUPPORTED_FORMAT, "图片 OCR 不处理 PDF，请用本地文本层解析。"
            )

        try:
            raw, _elapse = self._engine(str(file_path))
        except Exception as exc:
            raise OCRProviderError(OCRCode.UNREADABLE, f"图片识别失败：{exc}") from exc

        if not raw:
            raise OCRProviderError(OCRCode.NO_TEXT_LAYER, "图片里没有识别出任何文字。")

        frags: list[tuple[float, float, str]] = []
        for entry in raw:
            box, text, _score = entry[0], entry[1], entry[2]
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            # 图像原点在左上、y 向下；取负号换成 PDF 约定（y 越大越靠上），
            # 这样就能和 PDF 版解析器共用同一套聚行逻辑
            frags.append((sum(xs) / len(xs), -sum(ys) / len(ys), str(text)))

        result = parse_invoice_fragments(frags, provider=self.name)
        if len(result.missing_required()) == len(InvoiceResult.REQUIRED_FIELDS):
            raise OCRProviderError(
                OCRCode.PARSE_FAILED, "图片 OCR 出了文字，但没解析出发票关键字段。"
            )
        return result

    def health_check(self) -> tuple[bool, str]:
        return True, "本地图片 OCR 可用（无需联网）"
