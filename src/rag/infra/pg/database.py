from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rag.config import settings
from rag.infra.pg.base import Base

# 强制导入 models, 让 `Base.metadata` 知道所有 ORM 类
# (SQLAlchemy 在 class 定义时把 Table 注册到 metadata, 仅 import 模型模块即可触发)
from rag.infra.pg.models import chunk as _chunk_model  # noqa: F401
from rag.infra.pg.models import dataset as _dataset_model  # noqa: F401
from rag.infra.pg.models import document as _document_model  # noqa: F401

engine = create_async_engine(
    str(settings.database_url),
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def close_pool() -> None:
    """长时间运行的服务应调用此函数释放连接池。

    命名与 asyncpg pool 保持一致（``init_pool`` / ``close_pool``）。
    """
    await engine.dispose()


async def init_pool() -> None:
    """自动建表。生产环境推荐使用 Alembic 迁移。

    幂等: 重复调用只创建缺失的表, 已有表不受影响。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def truncate_all() -> None:
    """清空所有业务表数据, 保留 schema。

    业务表包括: `chunks`, `documents`, `datasets` (按 FK CASCADE 链一并清空)。
    无 `AuditRecord` 表 (audit 走 NDJSON 文件, 不在 PG 内)。

    Returns:
        被清空的表名列表 (按删除顺序, datasets 在最后以确保 CASCADE 触发)。
    """
    async with engine.begin() as conn:
        # chunks 有 FK 指向 documents/datasets, ON DELETE CASCADE 允许一次 TRUNCATE CASCADE
        # 显式三张表 + CASCADE 兼顾可读性
        await conn.execute(
            text("TRUNCATE TABLE chunks, documents, datasets RESTART IDENTITY CASCADE")
        )


async def drop_all() -> None:
    """删除所有业务表 (schema 一并删除)。

    危险操作: 调用后必须重新 `init_pool()` 才能继续使用。
    适用于: 测试 reset / 完整重建场景。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
