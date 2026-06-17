"""集成测试 conftest — 共享 fixtures。

DB 集成（真实 PG）:
- ``db_session``: 真实库会话：装扩展 → 建表 → 交给测试做 CRUD / 软删除验证。
- ``chunk_repo``: 基于 db_session 的 ChunkRepository。

Ingest 端到端（真实 LLM + 真实 fixture）:
- ``sample_data_dir``: tests/data 目录（10 个内置 sample.*）
- ``real_llm_chat_model``: langchain ChatOpenAI（OPENAI_API_KEY 缺则 skip）
- ``pipeline_with_llm``: 预装 StructureNormalizer(mode=FORCE) + Chunker 的 IngestPipeline
- ``live_embed_model``: 真实 DashScope-compatible embedding（OPENAI_EMBEDDING_API_KEY 缺则 skip）

``live_llm`` 用例在 OPENAI_API_KEY 缺时 pytest.skip，不会污染 CI。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.runnables import Runnable
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rag.config import settings
from rag.infra.llm.chat import get_structured_chat_model
from rag.infra.llm.embed import get_embed_model
from rag.infra.pg.base import Base
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.ingest.chunker import Chunker, ChunkSettings
from rag.ingest.normalizer import StructureMode, StructureNormalizer
from rag.ingest.normalizer.structure import StructuredText
from rag.ingest.pipeline import IngestPipeline

# ─────────────────────────────────────────────────────────────────────────────
# DB 集成
# ─────────────────────────────────────────────────────────────────────────────


# @pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
# async def reset_schema() -> AsyncGenerator[None]:
#     """会话级 schema 重置：drop_all + create_all。

#     仅作用于测试 DB；生产环境使用 ``scripts/backfill_documents.sql`` 演进 schema。
#     ``Base.metadata.create_all`` 是幂等的但不会为已有数据的表添加新 NOT NULL 列
#     (如 ``chunks.document_id``),因此测试前必须 drop_all 重建。
#     """
#     engine = create_async_engine(str(settings.database_url))
#     async with engine.begin() as conn:
#         await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
#         await conn.run_sync(Base.metadata.drop_all)
#         await conn.run_sync(Base.metadata.create_all)
#     await engine.dispose()
#     yield


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


@pytest.fixture
async def pg_session_factory() -> AsyncGenerator:
    """Yields an ``async_sessionmaker`` bound to a fresh engine on the test's loop.

    Use this when you need MULTIPLE separate sessions (e.g. for parallel
    retrievers in subgraph tests, to avoid shared-transaction conflicts).

    Yields the ``async_sessionmaker``; engine is disposed at teardown.
    """
    engine = create_async_engine(str(settings.database_url))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# Ingest 端到端（live_llm）
# ─────────────────────────────────────────────────────────────────────────────


def _require_api_key() -> str:
    raw = settings.openai_api_key.get_secret_value().strip()
    if not raw:
        pytest.skip("OPENAI_API_KEY not configured")
    return raw


def _require_embedding_api_key() -> str:
    raw = settings.openai_embedding_api_key.get_secret_value().strip()
    if not raw:
        pytest.skip("OPENAI_EMBEDDING_API_KEY not configured")
    return raw


@pytest.fixture(scope="session")
def real_llm_chat_model() -> Runnable:
    """从 .env 读 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL，构造 ChatOpenAI。

    缺 key 立即 skip，不抛异常污染测试报告。

    返回的是 **带 StructuredText schema 的 chat model**（用
    ``get_structured_chat_model``），而非裸 ``ChatOpenAI``：这样 ``ainvoke``
    返回 Pydantic ``StructuredText`` 实例，与 ``StructureNormalizer._call_llm``
    的 ``parsed.result_text`` 访问契约对齐。
    """
    _require_api_key()
    return get_structured_chat_model(StructuredText, temperature=0.1)


@pytest.fixture(scope="session")
def pipeline_with_llm(real_llm_chat_model: Runnable) -> IngestPipeline:
    """预装 StructureNormalizer(mode=FORCE) + Chunker 的端到端 Pipeline。"""
    return IngestPipeline(
        chunker=Chunker(ChunkSettings()),
        normalizer=StructureNormalizer(
            chat_model=real_llm_chat_model,
            mode=StructureMode.FORCE,
        ),
    )


@pytest.fixture(scope="session")
def live_embed_model() -> Embeddings:
    """真实 DashScope-compatible embedding (text-embedding-v3 / 1536 dim)。

    用于 5d2 orchestrator 集成测试：跨 dataset fan-out、真实 cosine 排序、
    score_breakdown 阈值过滤等都依赖真实 embedding 的语义距离。
    缺 OPENAI_EMBEDDING_API_KEY 立即 skip。
    """
    _require_embedding_api_key()
    return get_embed_model()
