"""初始化数据：把系统内置分类规则写进数据库。

命令行用法：
    python -m app.seed          # 只在规则表为空时写入
    python -m app.seed --force  # 清空系统规则后重写（自定义规则不动）
"""

from __future__ import annotations

import sys

from sqlalchemy.orm import Session

from .database import SessionLocal, init_db
from .models import CategoryRule, RuleType
from .services.classify import SYSTEM_RULES


def seed_system_rules(db: Session, *, force: bool = False) -> int:
    """写入系统规则，返回新增条数。"""
    if force:
        db.query(CategoryRule).filter(CategoryRule.rule_type == RuleType.SYSTEM).delete()
        db.commit()

    existing = (
        db.query(CategoryRule).filter(CategoryRule.rule_type == RuleType.SYSTEM).count()
    )
    if existing:
        return 0

    created = 0
    for match_field, keyword, category, subject, priority in SYSTEM_RULES:
        db.add(
            CategoryRule(
                rule_type=RuleType.SYSTEM,
                match_field=match_field,
                keyword=keyword,
                expense_category=category,
                account_subject=subject,
                priority=priority,
                enabled=True,
                note="系统内置规则",
            )
        )
        created += 1
    db.commit()
    return created


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        count = seed_system_rules(db, force="--force" in sys.argv)
        total = db.query(CategoryRule).count()
        print(f"新增系统规则 {count} 条，当前规则总数 {total} 条。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
