"""业务去重指纹（项目书第六章）。

规则：
- 有发票代码 → 「发票代码 + 发票号码」（传统的增值税普通/专用发票）
- 没有发票代码（全电发票）→ 「发票号码 + 开票日期 + 价税合计 + 销方税号」

为什么不直接用文件哈希做去重？
    同一张发票可能被下载两次（文件名不同）、拍照两次（图像不同），
    文件哈希不同但业务上是同一张票。文件哈希只能识别「同一个文件」重复上传。
"""

from __future__ import annotations


def _norm(value) -> str:
    return str(value or "").strip()


def business_dedupe_key(
    *,
    invoice_code: str | None,
    invoice_number: str | None,
    invoice_date: str | None,
    total_amount: float | None,
    seller_tax_id: str | None,
) -> str:
    code = _norm(invoice_code)
    number = _norm(invoice_number)
    if not number:
        return ""
    if code:
        return f"{code}-{number}"
    amount = "" if total_amount is None else f"{total_amount:.2f}"
    return "|".join([number, _norm(invoice_date), amount, _norm(seller_tax_id)])
