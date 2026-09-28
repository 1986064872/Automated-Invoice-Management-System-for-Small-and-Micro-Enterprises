"""SQLite + SQLAlchemy 会话管理。

V1 用单文件 SQLite（项目书第七章），生产环境再换 PostgreSQL；
换库时只改 DATABASE_URL，模型代码不用动。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DATA_DIR

# 允许用环境变量把数据库指到别处。
# 用途：跑自检脚本 / 演示时用**独立的临时库**，绝不碰你正在用的真实账目。
#     set APP_DB_PATH=C:\temp\scratch.db  然后重启后端
_override = os.environ.get("APP_DB_PATH", "").strip()
DB_PATH = Path(_override) if _override else (DATA_DIR / "app.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    # FastAPI 的 BackgroundTasks 会在别的线程里用 session，必须关掉这个检查
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """打开外键约束，并用 WAL 模式提升并发读写体验。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def get_db() -> Iterator[Session]:
    """FastAPI 依赖注入用的会话工厂。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表（幂等）+ 给已有库补上后来新增的列。"""
    from . import models  # noqa: F401  —— 必须导入才能让元数据注册

    Base.metadata.create_all(bind=engine)
    _ensure_columns()


# 后来新增的列：{表名: {列名: 列类型}}
#
# ⚠️ V1 没有 Alembic，create_all 只会**建新表**，不会给已存在的表加列。
# 所以新增字段必须登记在这里，否则老库跑起来会报 "no such column"。
# 这里用 SQLite 的 `ALTER TABLE ... ADD COLUMN` 做**增量、非破坏性**迁移：
# 不重建表、不动已有数据（ALTER ADD COLUMN 是 SQLite 少数几个安全的 DDL）。
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "invoice_item": {"remark": "VARCHAR(255)"},
    "invoice": {"invoice_remark": "TEXT"},
}


def _ensure_columns() -> None:
    """把 _ADDED_COLUMNS 里登记的列补到已有表上（幂等）。"""
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            info = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            if not info:  # 表不存在（理论上不会，create_all 已建）
                continue
            existing = {row[1] for row in info}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
