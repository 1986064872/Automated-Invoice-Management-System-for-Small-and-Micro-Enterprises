"""按当前企业身份判断一张发票是进项还是销项。

规则很简单，但必须保守：
- 税号优先于名称；名称只做别名兜底。
- 购买方匹配当前企业 = 进项；
- 销售方匹配当前企业 = 销项；
- 双方都匹配或都不匹配时返回 unknown，交给人工判断。
"""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

DIRECTION_INPUT = "input"
DIRECTION_OUTPUT = "output"
DIRECTION_UNKNOWN = "unknown"
DIRECTION_ORDER = (DIRECTION_INPUT, DIRECTION_OUTPUT, DIRECTION_UNKNOWN)

DIRECTION_TEXT = {
    DIRECTION_INPUT: "进项",
    DIRECTION_OUTPUT: "销项",
    DIRECTION_UNKNOWN: "待判断",
}

SHEET_BY_DIRECTION = {
    DIRECTION_INPUT: "进项发票",
    DIRECTION_OUTPUT: "销项发票",
    DIRECTION_UNKNOWN: "待判断",
}


@dataclass(frozen=True)
class DirectionResult:
    direction: str
    direction_text: str
    company_role: str
    counterparty_name: str
    counterparty_tax_id: str
    reason: str


def normalize_tax_id(value: str | None) -> str:
    """税号仅保留字母数字，统一成大写。"""
    text = unicodedata.normalize("NFKC", value or "")
    return "".join(ch for ch in text.upper() if ch.isalnum())


def normalize_name(value: str | None) -> str:
    """公司名去掉空格、全角/半角符号后再做保守完全匹配。"""
    text = unicodedata.normalize("NFKC", value or "").lower()
    return "".join(ch for ch in text if ch.isalnum())


def _tax_match(left: str | None, right: str | None) -> bool:
    a = normalize_tax_id(left)
    b = normalize_tax_id(right)
    return bool(a and b and a == b)


def _name_matches(value: str | None, company_name: str | None, aliases: list[str] | None) -> bool:
    needle = normalize_name(value)
    if not needle:
        return False
    names = {normalize_name(company_name)}
    names.update(normalize_name(alias) for alias in (aliases or []))
    names.discard("")
    return needle in names


def classify_direction(
    *,
    seller_name: str | None,
    seller_tax_id: str | None,
    buyer_name: str | None,
    buyer_tax_id: str | None,
    company_name: str | None,
    company_tax_id: str | None,
    company_aliases: list[str] | None = None,
) -> DirectionResult:
    """返回票据相对当前企业的方向。

    名称和税号都匹配时优先信税号；如果税号缺失，才降级到名称/别名匹配。
    """
    if not normalize_name(company_name) and not normalize_tax_id(company_tax_id):
        return DirectionResult(
            DIRECTION_UNKNOWN,
            DIRECTION_TEXT[DIRECTION_UNKNOWN],
            "",
            "",
            "",
            "尚未设置当前企业档案",
        )

    buyer_tax_match = _tax_match(buyer_tax_id, company_tax_id)
    seller_tax_match = _tax_match(seller_tax_id, company_tax_id)

    if buyer_tax_match and seller_tax_match:
        return DirectionResult(
            DIRECTION_UNKNOWN,
            DIRECTION_TEXT[DIRECTION_UNKNOWN],
            "",
            "",
            "",
            "票面购买方和销售方都匹配当前企业",
        )
    if buyer_tax_match:
        return DirectionResult(
            DIRECTION_INPUT,
            DIRECTION_TEXT[DIRECTION_INPUT],
            "购买方",
            seller_name or "",
            seller_tax_id or "",
            "购买方税号匹配当前企业",
        )
    if seller_tax_match:
        return DirectionResult(
            DIRECTION_OUTPUT,
            DIRECTION_TEXT[DIRECTION_OUTPUT],
            "销售方",
            buyer_name or "",
            buyer_tax_id or "",
            "销售方税号匹配当前企业",
        )

    # 税号没匹配上时，才用名称/别名兜底。
    buyer_name_match = _name_matches(buyer_name, company_name, company_aliases)
    seller_name_match = _name_matches(seller_name, company_name, company_aliases)

    if buyer_name_match and seller_name_match:
        return DirectionResult(
            DIRECTION_UNKNOWN,
            DIRECTION_TEXT[DIRECTION_UNKNOWN],
            "",
            "",
            "",
            "票面购买方和销售方都匹配当前企业名称",
        )
    if buyer_name_match:
        return DirectionResult(
            DIRECTION_INPUT,
            DIRECTION_TEXT[DIRECTION_INPUT],
            "购买方",
            seller_name or "",
            seller_tax_id or "",
            "购买方名称匹配当前企业或别名",
        )
    if seller_name_match:
        return DirectionResult(
            DIRECTION_OUTPUT,
            DIRECTION_TEXT[DIRECTION_OUTPUT],
            "销售方",
            buyer_name or "",
            buyer_tax_id or "",
            "销售方名称匹配当前企业或别名",
        )

    return DirectionResult(
        DIRECTION_UNKNOWN,
        DIRECTION_TEXT[DIRECTION_UNKNOWN],
        "",
        "",
        "",
        "票面双方均未匹配当前企业，请检查企业档案或人工确认",
    )
