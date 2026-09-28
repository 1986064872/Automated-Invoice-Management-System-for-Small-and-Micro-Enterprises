"""本地 PDF 文本层解析 Provider。

适用：文本型电子发票 PDF（全电发票、增值税电子普通/专用发票下载件）。
不适用：扫描件、拍照 JPG/PNG —— 这类没有文本层，会抛 NO_TEXT_LAYER 让上层引导用户
        去接百度 OCR 或人工录入。

零成本、不联网、票据不出本机，是隐私场景下的默认方案。
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from .base import OCRCode, InvoiceOCRProvider, InvoiceResult, OCRProviderError
from .parser import parse_invoice_fragments


def _page_fragments(page) -> list[tuple[float, float, str]]:
    """用 visitor 回调拿到每个文字块的 (x, y, 文本)。

    关键点：pypdf 给的 tm 是「文字矩阵」，只是一个很小的相对偏移；
    真实页面坐标必须和当前变换矩阵 cm 合成，否则整页文字会挤成一行。
        X = cm[0]*tm[4] + cm[2]*tm[5] + cm[4]
        Y = cm[1]*tm[4] + cm[3]*tm[5] + cm[5]
    有些 PDF 把绝对坐标写在 tm 里（此时 cm 为单位阵），下面这套公式两种情况都成立。
    """
    frags: list[tuple[float, float, str]] = []

    def visitor(text, cm, tm, font_dict, font_size):  # noqa: ARG001
        if not text:
            return
        stripped = text.strip()
        if not stripped or tm is None or cm is None:
            return
        x = cm[0] * tm[4] + cm[2] * tm[5] + cm[4]
        y = cm[1] * tm[4] + cm[3] * tm[5] + cm[5]
        frags.append((float(x), float(y), stripped))

    page.extract_text(visitor_text=visitor)
    return frags


class LocalTextPdfProvider(InvoiceOCRProvider):
    name = "local"
    display_name = "本地 PDF 文本层解析"

    def recognize(self, file_path: Path, file_type: str) -> InvoiceResult:
        if file_type.lower() != "pdf":
            raise OCRProviderError(
                OCRCode.UNSUPPORTED_FORMAT,
                "本地解析只支持带文本层的 PDF。图片票据请接入云 OCR（百度）或改走人工录入。",
            )

        try:
            reader = PdfReader(str(file_path))
        except Exception as exc:  # 加密、损坏、非 PDF
            raise OCRProviderError(OCRCode.UNREADABLE, f"PDF 打不开：{exc}") from exc

        if not reader.pages:
            raise OCRProviderError(OCRCode.UNREADABLE, "PDF 没有任何页面。")

        best: InvoiceResult | None = None
        best_score = -1
        total_frags = 0

        # 一页一页解析，取「字段最全」的那页当结果（有的 PDF 首页是封面）
        for page in reader.pages:
            frags = _page_fragments(page)
            total_frags += len(frags)
            if len(frags) < 5:
                continue
            candidate = parse_invoice_fragments(frags, provider=self.name)
            score = 4 - len(candidate.missing_required())
            if candidate.invoice_number:
                score += 1
            if score > best_score:
                best, best_score = candidate, score

        if best is None:
            raise OCRProviderError(
                OCRCode.NO_TEXT_LAYER,
                "这份 PDF 没有文本层（属于扫描件或图片版），"
                "请接入云 OCR（百度）或改走人工录入。",
            )

        best.raw.setdefault("pages", len(reader.pages))
        best.raw["text_fragments"] = total_frags

        # 关键字段一个都没解出来 —— 说明版面和预期差太远，宁可如实报错
        if len(best.missing_required()) == len(InvoiceResult.REQUIRED_FIELDS):
            raise OCRProviderError(
                OCRCode.PARSE_FAILED,
                "拿到了 PDF 文本但没有解析出可用的发票字段，"
                "可能是非发票文档或票面版式未支持。",
            )

        return best

    def health_check(self) -> tuple[bool, str]:
        return True, "本地解析可用（无需联网、无调用额度）"
