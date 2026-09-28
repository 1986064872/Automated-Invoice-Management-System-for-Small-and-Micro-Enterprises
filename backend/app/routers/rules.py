"""费用分类规则的增删改查（项目书第四章「规则设置」P1 模块）。"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CategoryRule, MatchField, RuleType
from ..schemas import RuleCreate, RuleOut, RuleUpdate
from ..services.classify import SYSTEM_RULES, UNCLASSIFIED

router = APIRouter(tags=["规则"])

# 给前端的下拉候选，避免用户手打错别字
DEFAULT_CATEGORIES = [
    "办公费",
    "差旅费",
    "业务招待费",
    "电子设备",
    "研发费用",
    "服务费",
    "软件服务费",
    "房租物业",
    "水电费",
    "通讯费",
    "运输费",
    "汽车费用",
    "广告宣传费",
    "会议费",
    "培训费",
    "劳保用品",
    "职工福利费",
    "低值易耗品",
]


@router.get("/rules", response_model=list[RuleOut], summary="读取分类规则")
def list_rules(
    rule_type: str | None = Query(default=None, description="custom / system"),
    enabled: bool | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
) -> list[CategoryRule]:
    query = db.query(CategoryRule)
    if rule_type:
        query = query.filter(CategoryRule.rule_type == rule_type)
    if enabled is not None:
        query = query.filter(CategoryRule.enabled.is_(enabled))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            CategoryRule.keyword.like(like) | CategoryRule.expense_category.like(like)
        )
    return (
        query.order_by(
            CategoryRule.rule_type.desc(),  # custom 在前
            CategoryRule.priority.asc(),
            CategoryRule.created_at.asc(),
        )
        .all()
    )


@router.get("/rules/options", summary="规则可选值")
def rule_options() -> dict:
    categories = list(DEFAULT_CATEGORIES)
    for _field, _kw, category, _subject, _prio in SYSTEM_RULES:
        if category not in categories:
            categories.append(category)
    return {
        "categories": sorted(set(categories)),
        "pending_category": UNCLASSIFIED,
        "match_fields": [
            {"value": MatchField.ITEM_NAME, "label": "项目名称"},
            {"value": MatchField.SELLER_NAME, "label": "销售方名称"},
        ],
        "rule_types": [
            {"value": RuleType.CUSTOM, "label": "企业自定义（优先生效）"},
            {"value": RuleType.SYSTEM, "label": "系统内置"},
        ],
    }


def _validated_keyword(keyword: str) -> str:
    text = (keyword or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="关键词不能为空")
    if len(text) > 128:
        raise HTTPException(status_code=400, detail="关键词太长（最多 128 字）")
    return text


@router.post("/rules", response_model=RuleOut, status_code=201, summary="新增规则")
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)) -> CategoryRule:
    keyword = _validated_keyword(payload.keyword)
    duplicate = (
        db.query(CategoryRule)
        .filter(
            CategoryRule.match_field == payload.match_field,
            CategoryRule.keyword == keyword,
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail=f"已存在同样的规则：「{payload.match_field} 含 {keyword} → "
            f"{duplicate.expense_category}」。请直接修改现有规则。",
        )

    rule = CategoryRule(
        rule_type=RuleType.CUSTOM if payload.rule_type != RuleType.SYSTEM else RuleType.SYSTEM,
        match_field=payload.match_field,
        keyword=keyword,
        expense_category=payload.expense_category.strip(),
        account_subject=(payload.account_subject or "").strip() or None,
        priority=payload.priority,
        enabled=payload.enabled,
        note=payload.note,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/rules/{rule_id}", response_model=RuleOut, summary="修改规则")
def update_rule(
    rule_id: str, payload: RuleUpdate, db: Session = Depends(get_db)
) -> CategoryRule:
    rule = db.get(CategoryRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")

    data = payload.model_dump(exclude_none=True)
    if "keyword" in data:
        data["keyword"] = _validated_keyword(data["keyword"])
    for field, value in data.items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", summary="删除规则")
def delete_rule(rule_id: str, db: Session = Depends(get_db)) -> dict:
    rule = db.get(CategoryRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    if rule.rule_type == RuleType.SYSTEM:
        # 系统规则只允许停用，保留可追溯的内置基线
        rule.enabled = False
        db.commit()
        return {
            "ok": True,
            "message": "系统内置规则不能删除，已改为「停用」。",
            "disabled": True,
        }
    db.delete(rule)
    db.commit()
    return {"ok": True, "message": "规则已删除。", "disabled": False}


@router.post("/rules/{rule_id}/toggle", response_model=RuleOut, summary="启用/停用")
def toggle_rule(rule_id: str, db: Session = Depends(get_db)) -> CategoryRule:
    rule = db.get(CategoryRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    rule.enabled = not rule.enabled
    db.commit()
    db.refresh(rule)
    return rule


@router.post("/rules/{rule_id}/move", response_model=list[RuleOut], summary="调整优先级（上移/下移）")
def move_rule(
    rule_id: str,
    direction: str = Query(default="up", pattern="^(up|down)$"),
    db: Session = Depends(get_db),
) -> list[CategoryRule]:
    """同一类型内按 priority 排序，交换相邻两条的优先级数字。"""
    rule = db.get(CategoryRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")

    siblings = (
        db.query(CategoryRule)
        .filter(CategoryRule.rule_type == rule.rule_type)
        .order_by(CategoryRule.priority.asc())
        .all()
    )
    index = next((i for i, r in enumerate(siblings) if r.id == rule.id), None)
    if index is None:
        return siblings
    target_index = index - 1 if direction == "up" else index + 1
    if target_index < 0 or target_index >= len(siblings):
        return siblings

    other = siblings[target_index]
    rule.priority, other.priority = other.priority, rule.priority
    # 数字撞了就用位置重排一遍，保证顺序稳定可解释
    if rule.priority == other.priority:
        for position, sibling in enumerate(sorted(siblings, key=lambda r: r.priority)):
            sibling.priority = (position + 1) * 10

    db.commit()
    return (
        db.query(CategoryRule)
        .filter(CategoryRule.rule_type == rule.rule_type)
        .order_by(CategoryRule.priority.asc())
        .all()
    )


@router.get("/rules/test", summary="试跑规则：看某个销售方/项目会命中哪条")
def test_rule(
    seller_name: str = "",
    item_name: str = "",
    db: Session = Depends(get_db),
) -> dict:
    from ..services.classify import classify

    picked = classify(db, seller_name=seller_name, item_names=[item_name] if item_name else [])
    db.rollback()  # 试跑不记录命中次数
    return picked


# 输入校验提示用的正则（供前端展示规则语法限制）
KEYWORD_PATTERN = re.compile(r"^.{1,128}$").pattern
