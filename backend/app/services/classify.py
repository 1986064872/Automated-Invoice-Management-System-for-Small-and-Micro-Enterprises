"""费用分类推荐引擎（项目书 9.2）。

命中顺序：
    1) 企业自定义规则（rule_type=custom）—— 按 priority 升序，数字越小越优先
    2) 系统关键词规则（rule_type=system）—— 同上
    3) 都没命中 → 「待分类」，产生一条黄色警告，由用户在复核工作台手选

为什么不用 LLM 分类？项目书明确「V1 不强制接 LLM」。关键词规则可解释、可修正、零成本，
用户在复核时改一次可以「保存为规则」，下次同类票自动命中 —— 准确率随使用自然上升。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import CategoryRule, MatchField, RuleType

UNCLASSIFIED = "待分类"

# --------------------------------------------------------------------------
# 系统内置规则（首次启动时由 seed.py 写入数据库，之后可在设置页停用/调整）
# 结构：(匹配字段, 关键词, 费用分类, 会计科目, 优先级)
# 优先级数字越小越先命中；越具体的规则给越小的数字，避免被泛词抢走。
# --------------------------------------------------------------------------
SYSTEM_RULES: list[tuple[str, str, str, str, int]] = [
    # ---- 云服务 / 研发 ----
    (MatchField.ITEM_NAME, "云服务", "研发费用", "研发费用—云服务", 10),
    (MatchField.SELLER_NAME, "阿里云", "研发费用", "研发费用—云服务", 10),
    (MatchField.SELLER_NAME, "腾讯云", "研发费用", "研发费用—云服务", 10),
    (MatchField.SELLER_NAME, "华为云", "研发费用", "研发费用—云服务", 10),
    # ---- 交通运输 ----
    (MatchField.SELLER_NAME, "滴滴", "差旅费", "管理费用—差旅费", 20),
    (MatchField.ITEM_NAME, "网约车", "差旅费", "管理费用—差旅费", 20),
    (MatchField.ITEM_NAME, "客运服务", "差旅费", "管理费用—差旅费", 20),
    (MatchField.ITEM_NAME, "航空运输", "差旅费", "管理费用—差旅费", 20),
    (MatchField.ITEM_NAME, "铁路旅客运输", "差旅费", "管理费用—差旅费", 20),
    (MatchField.ITEM_NAME, "住宿", "差旅费", "管理费用—差旅费", 22),
    (MatchField.ITEM_NAME, "运输服务", "运输费", "销售费用—运输费", 25),
    (MatchField.ITEM_NAME, "快递", "运输费", "销售费用—运输费", 25),
    (MatchField.ITEM_NAME, "物流", "运输费", "销售费用—运输费", 25),
    # ---- 车辆 ----
    (MatchField.ITEM_NAME, "加油", "汽车费用", "管理费用—车辆使用费", 30),
    (MatchField.ITEM_NAME, "汽油", "汽车费用", "管理费用—车辆使用费", 30),
    (MatchField.ITEM_NAME, "停车", "汽车费用", "管理费用—车辆使用费", 30),
    (MatchField.ITEM_NAME, "通行费", "汽车费用", "管理费用—车辆使用费", 30),
    (MatchField.ITEM_NAME, "维修", "汽车费用", "管理费用—车辆使用费", 32),
    # ---- 电子设备 / 办公 ----
    (MatchField.ITEM_NAME, "电子工业设备", "电子设备", "固定资产—电子设备", 40),
    (MatchField.ITEM_NAME, "家用音视频设备", "电子设备", "固定资产—电子设备", 40),
    (MatchField.ITEM_NAME, "计算机", "电子设备", "固定资产—电子设备", 40),
    (MatchField.ITEM_NAME, "服务器", "电子设备", "固定资产—电子设备", 40),
    (MatchField.ITEM_NAME, "电视机", "电子设备", "固定资产—电子设备", 42),
    (MatchField.ITEM_NAME, "LED液晶", "电子设备", "固定资产—电子设备", 42),
    (MatchField.ITEM_NAME, "办公用品", "办公费", "管理费用—办公费", 45),
    (MatchField.ITEM_NAME, "文具", "办公费", "管理费用—办公费", 45),
    (MatchField.ITEM_NAME, "纸张", "办公费", "管理费用—办公费", 45),
    (MatchField.ITEM_NAME, "硒鼓", "办公费", "管理费用—办公费", 45),
    (MatchField.ITEM_NAME, "打印", "办公费", "管理费用—办公费", 46),
    # ---- 软件与信息技术 ----
    (MatchField.ITEM_NAME, "信息技术服务", "服务费", "管理费用—服务费", 50),
    (MatchField.ITEM_NAME, "技术服务", "服务费", "管理费用—服务费", 50),
    (MatchField.ITEM_NAME, "软件开发", "软件服务费", "管理费用—软件服务费", 50),
    (MatchField.ITEM_NAME, "软件", "软件服务费", "管理费用—软件服务费", 52),
    (MatchField.ITEM_NAME, "鉴证咨询服务", "服务费", "管理费用—服务费", 54),
    # ---- 场地与能耗 ----
    (MatchField.ITEM_NAME, "租赁", "房租物业", "管理费用—租赁费", 60),
    (MatchField.ITEM_NAME, "物业", "房租物业", "管理费用—物业费", 60),
    (MatchField.ITEM_NAME, "电费", "水电费", "管理费用—水电费", 62),
    (MatchField.ITEM_NAME, "水费", "水电费", "管理费用—水电费", 62),
    # ---- 通信 ----
    (MatchField.ITEM_NAME, "电信服务", "通讯费", "管理费用—通讯费", 65),
    (MatchField.ITEM_NAME, "话费", "通讯费", "管理费用—通讯费", 65),
    # ---- 销售与推广 ----
    (MatchField.ITEM_NAME, "广告", "广告宣传费", "销售费用—广告宣传费", 70),
    (MatchField.ITEM_NAME, "推广", "广告宣传费", "销售费用—广告宣传费", 70),
    # ---- 其他 ----
    (MatchField.ITEM_NAME, "会议", "会议费", "管理费用—会议费", 75),
    (MatchField.ITEM_NAME, "培训", "培训费", "管理费用—职工教育经费", 75),
    (MatchField.ITEM_NAME, "餐饮", "业务招待费", "管理费用—业务招待费", 80),
]


def _normalize(text: str) -> str:
    """去掉空格和 * 分隔符，统一小写，让 '阿里 云' 也能命中 '阿里云'。"""
    return (text or "").replace(" ", "").replace("*", "").replace("\u3000", "").lower()


def _rule_hit(rule: CategoryRule, seller: str, items_text: str) -> bool:
    keyword = _normalize(rule.keyword)
    if not keyword:
        return False
    haystack = seller if rule.match_field == MatchField.SELLER_NAME else items_text
    return keyword in haystack


def classify(db: Session, *, seller_name: str, item_names: list[str]) -> dict:
    """给一张票推荐费用分类。

    返回 {expense_category, account_subject, rule_source, confidence,
          matched_rule_id, matched_keyword, is_fallback}
    """
    seller = _normalize(seller_name)
    items_text = _normalize(" ".join(n for n in item_names if n))

    rules = (
        db.query(CategoryRule)
        .filter(CategoryRule.enabled.is_(True))
        .order_by(CategoryRule.priority.asc(), CategoryRule.created_at.asc())
        .all()
    )

    # 自定义规则整体优先于系统规则
    ordered = sorted(
        rules, key=lambda r: (0 if r.rule_type == RuleType.CUSTOM else 1, r.priority)
    )

    for rule in ordered:
        if _rule_hit(rule, seller, items_text):
            rule.hits = (rule.hits or 0) + 1
            tag = "自定义规则" if rule.rule_type == RuleType.CUSTOM else "系统规则"
            return {
                "expense_category": rule.expense_category,
                "account_subject": rule.account_subject or "",
                "rule_source": f"{tag}：{rule.keyword} → {rule.expense_category}",
                "confidence": 0.9 if rule.rule_type == RuleType.CUSTOM else 0.8,
                "matched_rule_id": rule.id,
                "matched_keyword": rule.keyword,
                "is_fallback": False,
            }

    return {
        "expense_category": UNCLASSIFIED,
        "account_subject": "",
        "rule_source": "未命中任何规则，请人工选择",
        "confidence": 0.0,
        "matched_rule_id": None,
        "matched_keyword": "",
        "is_fallback": True,
    }


def item_keyword(item_name: str) -> str:
    """从 '*电子工业设备*LED液晶电视机' 里取出 'LED液晶电视机' 当关键词。

    带 * 前缀的是税收分类简称，用后半段做关键词匹配度更高。
    """
    text = (item_name or "").strip()
    if not text:
        return ""
    if "*" in text:
        text = text.rsplit("*", 1)[-1].strip()
    return text[:64]


def save_rules_from_invoice(db: Session, invoice) -> list[CategoryRule]:
    """把人工确认过的分类沉淀成企业自定义规则（项目书 9.2 最后一条）。

    生成两条（已存在则跳过）：
        1) 项目名称规则（priority 20）—— 更具体，先命中
        2) 销售方规则（priority 50）—— 兜底，同一家供应商默认同类
    """
    category = (invoice.expense_category or "").strip()
    if not category or category == UNCLASSIFIED:
        return []

    created: list[CategoryRule] = []

    def ensure(match_field: str, keyword: str, priority: int, note: str) -> None:
        keyword = (keyword or "").strip()
        if not keyword:
            return
        exists = (
            db.query(CategoryRule)
            .filter(
                CategoryRule.rule_type == RuleType.CUSTOM,
                CategoryRule.match_field == match_field,
                CategoryRule.keyword == keyword,
                CategoryRule.expense_category == category,
            )
            .first()
        )
        if exists is not None:
            return
        rule = CategoryRule(
            rule_type=RuleType.CUSTOM,
            match_field=match_field,
            keyword=keyword,
            expense_category=category,
            account_subject=invoice.account_subject or None,
            priority=priority,
            enabled=True,
            note=note,
        )
        db.add(rule)
        created.append(rule)

    first_item = next((it.item_name for it in invoice.items if it.item_name), "")
    ensure(
        MatchField.ITEM_NAME,
        item_keyword(first_item),
        20,
        f"由发票 {invoice.invoice_number or ''} 复核时保存",
    )
    ensure(
        MatchField.SELLER_NAME,
        (invoice.seller_name or "").strip(),
        50,
        f"由发票 {invoice.invoice_number or ''} 复核时保存（销售方默认分类）",
    )

    if created:
        db.flush()
    return created
