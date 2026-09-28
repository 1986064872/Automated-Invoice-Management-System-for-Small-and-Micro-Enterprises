"""百度智能云增值税发票识别 Provider（项目书第八章首选方案）。

免费额度：实名认证后个人 1000 次/月、企业 2000 次/月（以控制台为准）。
接口文档：https://cloud.baidu.com/doc/OCR/s/ik3h7xv2x

安全约定：
- API Key / Secret Key 从环境变量读，不进代码、不进前端、不进日志。
- 服务端做「每日调用上限」计数，防止自动后付费产生意外账单。
"""

from __future__ import annotations

import base64
import threading
import time
from datetime import date
from pathlib import Path

import httpx

from ..config import settings
from .base import (
    OCRCode,
    InvoiceItemData,
    InvoiceOCRProvider,
    InvoiceResult,
    OCRProviderError,
    normalize_date,
    to_float,
)

TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
VAT_INVOICE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/vat_invoice"


# --------------------------------------------------------------------------
# 每日调用计数（进程内）。V1 用内存计数即可，重启会清零，接生产时换成 Redis。
# --------------------------------------------------------------------------
_usage_lock = threading.Lock()
_usage: dict[str, int] = {"date": "", "count": 0}


def _reserve_quota() -> int:
    """占用一次额度；超限抛 QUOTA_EXCEEDED。返回今日已用次数。"""
    today = date.today().isoformat()
    with _usage_lock:
        if _usage["date"] != today:
            _usage["date"] = today
            _usage["count"] = 0
        if _usage["count"] >= settings.daily_ocr_limit:
            raise OCRProviderError(
                OCRCode.QUOTA_EXCEEDED,
                f"今日云 OCR 调用已达上限（{settings.daily_ocr_limit} 次），"
                "为防产生费用已停止调用。可明天再试，或改用本地解析。",
            )
        _usage["count"] += 1
        return _usage["count"]


def today_usage() -> int:
    today = date.today().isoformat()
    with _usage_lock:
        return _usage["count"] if _usage["date"] == today else 0


class BaiduInvoiceProvider(InvoiceOCRProvider):
    name = "baidu"
    display_name = "百度智能云增值税发票识别"

    def __init__(self) -> None:
        self.api_key = settings.baidu_api_key.strip()
        self.secret_key = settings.baidu_secret_key.strip()
        self._token: str = ""
        self._token_expire_at: float = 0.0

    # ---------------- 鉴权 ----------------
    def _access_token(self) -> str:
        if not self.api_key or not self.secret_key:
            raise OCRProviderError(
                OCRCode.PROVIDER_NOT_CONFIGURED,
                "未配置百度 OCR 密钥。请在 backend/.env 填写 BAIDU_API_KEY 与 "
                "BAIDU_SECRET_KEY，或把 OCR_PROVIDER 设为 local 使用本地解析。",
            )
        if self._token and time.time() < self._token_expire_at:
            return self._token

        try:
            resp = httpx.post(
                TOKEN_URL,
                params={
                    "grant_type": "client_credentials",
                    "client_id": self.api_key,
                    "client_secret": self.secret_key,
                },
                timeout=settings.baidu_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise OCRProviderError(OCRCode.TIMEOUT, "获取百度访问令牌超时，请稍后重试。") from exc
        except Exception as exc:
            raise OCRProviderError(OCRCode.PROVIDER_ERROR, f"获取百度访问令牌失败：{exc}") from exc

        token = data.get("access_token")
        if not token:
            # 常见原因：密钥填错 / 应用未开通该接口
            reason = data.get("error_description") or data.get("error") or "未知原因"
            raise OCRProviderError(
                OCRCode.PROVIDER_NOT_CONFIGURED, f"百度鉴权未通过：{reason}"
            )

        self._token = token
        # 官方有效期 30 天，这里保守按 25 天缓存
        self._token_expire_at = time.time() + 25 * 24 * 3600
        return token

    # ---------------- 识别 ----------------
    def recognize(self, file_path: Path, file_type: str) -> InvoiceResult:
        token = self._access_token()
        _reserve_quota()

        raw_bytes = Path(file_path).read_bytes()
        if len(raw_bytes) > 8 * 1024 * 1024:
            raise OCRProviderError(
                OCRCode.PROVIDER_ERROR, "文件超过 8MB，百度接口无法直接识别，请先压缩。"
            )

        payload: dict[str, str] = {
            "accuracy": "normal",
        }
        if file_type.lower() == "pdf":
            payload["pdf_file"] = base64.b64encode(raw_bytes).decode()
            payload["pdf_file_num"] = "1"
        else:
            payload["image"] = base64.b64encode(raw_bytes).decode()

        try:
            resp = httpx.post(
                VAT_INVOICE_URL,
                params={"access_token": token},
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=settings.baidu_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise OCRProviderError(OCRCode.TIMEOUT, "百度 OCR 识别超时，可重试。") from exc
        except Exception as exc:
            raise OCRProviderError(OCRCode.PROVIDER_ERROR, f"百度 OCR 请求失败：{exc}") from exc

        # 百度用 error_code 表达业务错误
        if data.get("error_code"):
            code = data.get("error_code")
            msg = data.get("error_msg", "")
            if code in (17, 18, 19):  # 额度相关
                raise OCRProviderError(
                    OCRCode.QUOTA_EXCEEDED, f"百度 OCR 额度不足（{code}）：{msg}"
                )
            raise OCRProviderError(
                OCRCode.PROVIDER_ERROR, f"百度 OCR 返回错误 {code}：{msg}"
            )

        words = data.get("words_result")
        if not words:
            raise OCRProviderError(
                OCRCode.PARSE_FAILED, "百度 OCR 未返回发票字段，可能不是发票或版式不受支持。"
            )

        return self.normalize_response(data, file_type)

    # ---------------- 原始响应 → 标准结构 ----------------
    def normalize_response(self, raw: dict, file_type: str = "") -> InvoiceResult:  # noqa: ARG002
        words = raw.get("words_result", {}) or {}
        result = InvoiceResult(provider=self.name)
        conf: dict[str, float] = {}

        def pick(*keys: str) -> tuple[str, float]:
            """按候选键取值；百度有的字段会返回 {word, probability} 结构。"""
            for key in keys:
                if key not in words:
                    continue
                value = words[key]
                prob = 0.95
                if isinstance(value, dict):
                    prob = float(value.get("probability", 0.95))
                    value = value.get("word", "")
                if isinstance(value, list):
                    continue
                text = str(value).strip()
                if text:
                    return text, prob
            return "", 0.0

        # 票种 / 号码
        value, prob = pick("InvoiceType")
        result.invoice_type = value or "未知票种"
        result.invoice_code = pick("InvoiceCode")[0]
        if result.invoice_code:
            conf["invoice_code"] = 0.95
        value, prob = pick("InvoiceNum", "InvoiceNumber")
        result.invoice_number = value
        if value:
            conf["invoice_number"] = prob
        value, _ = pick("InvoiceDate")
        result.invoice_date = normalize_date(value)
        if result.invoice_date:
            conf["invoice_date"] = 0.95

        # 购销方
        value, prob = pick("SellerName")
        result.seller_name = value
        if value:
            conf["seller_name"] = prob
        value, _ = pick("SellerRegisterNum")
        result.seller_tax_id = value
        if value:
            conf["seller_tax_id"] = 0.95
        value, prob = pick("PurchaserName")
        result.buyer_name = value
        if value:
            conf["buyer_name"] = prob
        value, _ = pick("PurchaserRegisterNum")
        result.buyer_tax_id = value
        if value:
            conf["buyer_tax_id"] = 0.95

        # 金额（注意百度把「价税合计」拼成了 AmountInFiguers）
        value, prob = pick("TotalAmount")
        result.amount_without_tax = to_float(value)
        if result.amount_without_tax is not None:
            conf["amount_without_tax"] = prob
        value, prob = pick("TotalTax")
        result.tax_amount = to_float(value)
        if result.tax_amount is not None:
            conf["tax_amount"] = prob
        value, prob = pick("AmountInFiguers", "AmountInFigures")
        result.total_amount = to_float(value)
        if result.total_amount is not None:
            conf["total_amount"] = prob

        # 明细
        result.items = self._parse_items(words)

        # 补齐缺失金额
        if result.total_amount is None and None not in (
            result.amount_without_tax,
            result.tax_amount,
        ):
            result.total_amount = round(
                (result.amount_without_tax or 0) + (result.tax_amount or 0), 2
            )
            conf["total_amount"] = 0.8

        result.field_confidence = conf
        result.raw = raw
        return result

    @staticmethod
    def _parse_items(words: dict) -> list[InvoiceItemData]:
        """百度把明细拆成 CommodityName / CommodityAmount / ... 多个平行数组。"""

        def column(key: str) -> list[str]:
            raw = words.get(key)
            if not raw:
                return []
            out = []
            if isinstance(raw, list):
                for entry in raw:
                    if isinstance(entry, dict):
                        out.append(str(entry.get("word", "")).strip())
                    else:
                        out.append(str(entry).strip())
            elif isinstance(raw, dict):
                out.append(str(raw.get("word", "")).strip())
            else:
                out.append(str(raw).strip())
            return out

        names = column("CommodityName")
        specs = column("CommodityType")
        units = column("CommodityUnit")
        nums = column("CommodityNum")
        prices = column("CommodityPrice")
        amounts = column("CommodityAmount")
        rates = column("CommodityTaxRate")
        taxes = column("CommodityTax")

        def at(seq: list[str], i: int) -> str:
            return seq[i] if i < len(seq) else ""

        items = []
        for i, name in enumerate(names):
            items.append(
                InvoiceItemData(
                    item_name=name,
                    specification=at(specs, i),
                    unit=at(units, i),
                    quantity=to_float(at(nums, i)),
                    unit_price=to_float(at(prices, i)),
                    tax_rate=at(rates, i),
                    amount=to_float(at(amounts, i)),
                    tax_amount=to_float(at(taxes, i)),
                )
            )
        return items

    def health_check(self) -> tuple[bool, str]:
        if not self.api_key or not self.secret_key:
            return False, "未配置 BAIDU_API_KEY / BAIDU_SECRET_KEY"
        try:
            self._access_token()
        except OCRProviderError as exc:
            return False, exc.message
        used = today_usage()
        return True, f"鉴权正常，今日已用 {used}/{settings.daily_ocr_limit} 次"
