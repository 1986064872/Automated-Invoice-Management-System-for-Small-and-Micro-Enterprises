"""风险校验（项目书第十章）。

两档级别：
    红色 error   —— 阻止确认入账，必须改到合格才能提交
    黄色 warning —— 高亮提示，允许人工确认

设计原则：规则只管「确定性」的事（字段缺没缺、金额对不对、票号重没重），
         判断层面的事（这笔费用该不该报）交给人工。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Invoice, InvoiceFile, RiskLevel

# 允许的浮点误差（元）
AMOUNT_TOLERANCE = 0.01


def _flag(code: str, level: str, message: str, field: str = "") -> dict:
    return {"code": code, "level": level, "message": message, "field": field}


def has_blocking_error(flags: list[dict] | None) -> bool:
    return any((f or {}).get("level") == RiskLevel.ERROR for f in (flags or []))


def error_count(flags: list[dict] | None) -> int:
    return sum(1 for f in (flags or []) if (f or {}).get("level") == RiskLevel.ERROR)


def warning_count(flags: list[dict] | None) -> int:
    return sum(1 for f in (flags or []) if (f or {}).get("level") == RiskLevel.WARNING)


def validate_invoice(
    db: Session,
    invoice: Invoice,
    *,
    source_file: InvoiceFile | None = None,
) -> list[dict]:
    """跑一遍全部校验规则，返回风险标记列表。"""
    flags: list[dict] = []

    # ---------- 规则 1：关键字段缺失（error） ----------
    required_labels = {
        "invoice_number": "发票号码",
        "invoice_date": "开票日期",
        "seller_name": "销售方名称",
        "total_amount": "价税合计",
    }
    missing = [
        label
        for field, label in required_labels.items()
        if getattr(invoice, field) in (None, "", 0)
    ]
    if missing:
        flags.append(
            _flag(
                "missing_required",
                RiskLevel.ERROR,
                f"关键字段缺失：{'、'.join(missing)}。补齐后才能确认入账。",
                "、".join(missing),
            )
        )

    # ---------- 规则 1b：发票号码格式（error） ----------
    # 位数不是随手定的：全电发票号码固定 20 位、没有发票代码；
    # 老式增值税发票号码 8 位，一定配 10~12 位的发票代码。
    # 靠这个规律就能判断用户填的位数对不对。
    number = (invoice.invoice_number or "").strip()
    if number:
        if not number.isdigit():
            flags.append(
                _flag(
                    "invoice_number_format",
                    RiskLevel.ERROR,
                    "发票号码只能填数字，当前含有非数字字符。",
                    "invoice_number",
                )
            )
        else:
            has_code = bool((invoice.invoice_code or "").strip())
            expect = 8 if has_code else 20
            kind = "老式发票，配发票代码" if has_code else "全电发票"
            if len(number) != expect:
                flags.append(
                    _flag(
                        "invoice_number_format",
                        RiskLevel.ERROR,
                        f"发票号码应为 {expect} 位（{kind}），当前 {len(number)} 位。",
                        "invoice_number",
                    )
                )

    # ---------- 规则 2：金额关系（error） ----------
    a, t, total = (
        invoice.amount_without_tax,
        invoice.tax_amount,
        invoice.total_amount,
    )
    if None not in (a, t, total):
        diff = round((a or 0) + (t or 0) - (total or 0), 2)
        if abs(diff) > AMOUNT_TOLERANCE:
            flags.append(
                _flag(
                    "amount_mismatch",
                    RiskLevel.ERROR,
                    f"金额关系不平：不含税金额 {a:.2f} + 税额 {t:.2f} = "
                    f"{a + t:.2f}，与价税合计 {total:.2f} 相差 {diff:+.2f} 元，"
                    f"超过 {AMOUNT_TOLERANCE} 元容差。",
                    "total_amount",
                )
            )
    else:
        flags.append(
            _flag(
                "amount_incomplete",
                RiskLevel.WARNING,
                "金额字段不完整（不含税金额/税额/价税合计 至少缺一项），无法自动勾稽。",
                "total_amount",
            )
        )

    # ---------- 规则 2b：明细合计与票面合计是否一致（warning） ----------
    if invoice.items and invoice.amount_without_tax is not None:
        item_sum = round(
            sum(it.amount for it in invoice.items if it.amount is not None), 2
        )
        if abs(item_sum - invoice.amount_without_tax) > AMOUNT_TOLERANCE:
            flags.append(
                _flag(
                    "item_sum_mismatch",
                    RiskLevel.WARNING,
                    f"明细行金额合计 {item_sum:.2f} 与票面不含税金额 "
                    f"{invoice.amount_without_tax:.2f} 不一致。",
                    "items",
                )
            )

    # ---------- 规则 3：疑似重复（error） ----------
    # 只拦「后来者」：同一张票重复入库时，最早的记录是主记录，应当允许正常入账；
    # 后进来的副本才标红，并指向主记录让用户去删。
    if invoice.dedupe_key:
        others = (
            db.query(Invoice)
            .filter(Invoice.dedupe_key == invoice.dedupe_key, Invoice.id != invoice.id)
            .all()
        )
        mine = (invoice.created_at or datetime.min, invoice.id or "")
        earlier = [o for o in others if (o.created_at or datetime.min, o.id or "") < mine]
        if earlier:
            duplicate = min(
                earlier, key=lambda o: (o.created_at or datetime.min, o.id or "")
            )
            dup_file = db.get(InvoiceFile, duplicate.file_id)
            dup_name = dup_file.original_name if dup_file else "（原文件已删除）"
            status_text = "已入账" if duplicate.status == "confirmed" else "待复核"
            flags.append(
                _flag(
                    "duplicate_invoice",
                    RiskLevel.ERROR,
                    f"疑似重复票据：系统里已存在同一张发票（发票号码 "
                    f"{duplicate.invoice_number or '未知'}，{status_text}，"
                    f"来源文件「{dup_name}」）。"
                    f"请核对后删除其中一条，只保留一张。",
                    "invoice_number",
                )
            )

    # ---------- 规则 3b：同一个文件重复上传（warning） ----------
    if source_file and source_file.file_hash:
        same_file = (
            db.query(InvoiceFile)
            .filter(
                InvoiceFile.file_hash == source_file.file_hash,
                InvoiceFile.id != source_file.id,
            )
            .first()
        )
        if same_file:
            flags.append(
                _flag(
                    "duplicate_file",
                    RiskLevel.WARNING,
                    f"该文件此前已上传过（原文件名「{same_file.original_name}」）。",
                    "file",
                )
            )

    # ---------- 规则 4：日期异常（error） ----------
    if invoice.invoice_date:
        try:
            parsed = datetime.strptime(invoice.invoice_date, "%Y-%m-%d").date()
            if parsed > date.today():
                flags.append(
                    _flag(
                        "date_in_future",
                        RiskLevel.ERROR,
                        f"开票日期 {invoice.invoice_date} 晚于今天，属于未来日期。",
                        "invoice_date",
                    )
                )
        except ValueError:
            flags.append(
                _flag(
                    "date_unparsable",
                    RiskLevel.ERROR,
                    f"开票日期「{invoice.invoice_date}」无法解析，请按 yyyy-mm-dd 填写。",
                    "invoice_date",
                )
            )

    # ---------- 规则 5：低置信度（warning） ----------
    threshold = settings.confidence_threshold
    low_fields = []
    label_map = {
        "invoice_number": "发票号码",
        "invoice_date": "开票日期",
        "seller_name": "销售方名称",
        "total_amount": "价税合计",
        "buyer_name": "购买方名称",
        "seller_tax_id": "销售方税号",
        "buyer_tax_id": "购买方税号",
    }
    confidences: dict = invoice.field_confidence or {}
    for field, label in label_map.items():
        value = getattr(invoice, field, None)
        if not value:
            continue
        score = confidences.get(field)
        if score is None:
            continue
        if score < threshold:
            low_fields.append(f"{label}({score:.0%})")
    if low_fields:
        flags.append(
            _flag(
                "low_confidence",
                RiskLevel.WARNING,
                f"以下字段识别置信度低于 {threshold:.0%}，请重点核对："
                f"{'、'.join(low_fields)}。",
                "、".join(low_fields),
            )
        )

    # ---------- 规则 6：类别未命中（warning） ----------
    from .classify import UNCLASSIFIED

    if not invoice.expense_category or invoice.expense_category == UNCLASSIFIED:
        flags.append(
            _flag(
                "category_unmatched",
                RiskLevel.WARNING,
                "没有规则能确定费用类别，请手动选择。选好后可「保存为规则」，下次自动命中。",
                "expense_category",
            )
        )

    # ---------- 规则 6b：会计科目缺失（warning） ----------
    if invoice.expense_category and not invoice.account_subject:
        flags.append(
            _flag(
                "account_subject_missing",
                RiskLevel.WARNING,
                "缺少会计科目，建议补充后再入账。",
                "account_subject",
            )
        )

    return flags
