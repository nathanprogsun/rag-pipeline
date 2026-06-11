from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rag.config import settings
from rag.infra.pg.base import Base

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
    命名与 asyncpg pool 保持一致 (init_pool / close_pool)。"""
    await engine.dispose()


async def init_pool() -> None:
    """自动建表。Production 用 Alembic 迁移。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
