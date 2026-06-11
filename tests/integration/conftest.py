"""集成测试 — 使用 `settings.database_url` 连接真实 PostgreSQL。

本目录测试**不是 mock**，在真实库上跑完整链路：
`CREATE EXTENSION vector` → `create_all` → 表结构 / Repository CRUD / 软删除。

**数据污染**：用例会插入并软删数据。默认 `DATABASE_URL` 指向开发库
（`docker compose up` 的 `localhost:5432/rag`）。跑集成测试前建议切到专用库，例如：

    DATABASE_URL=postgresql://rag:rag@localhost:5432/rag_test

前提：PostgreSQL 已运行且支持 pgvector（`make up` 即可）。
"""

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rag.config import settings
from rag.infra.pg.base import Base
from rag.infra.pg.repositories.chunk_repo import ChunkRepository


@pytest.fixture
def chunk_repo(db_session: AsyncSession) -> ChunkRepository:
    return ChunkRepository(db_session)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """真实库会话：装扩展 → 建表 → 交给测试做 CRUD / 软删除验证。"""
    engine = create_async_engine(str(settings.database_url))

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()
