"""FulltextRetriever 集成测试 — 真实 PG 上验证 jieba + tsvector GIN 检索链路。"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag.infra.pg.fulltext_store import FulltextRetriever, build_tsvector
from rag.infra.pg.models.chunk import ChunkModel
from rag.infra.pg.models.dataset import DatasetModel

EMBED_DIM = 1536


def _embedding() -> list[float]:
    return [0.0] * EMBED_DIM


async def _create_dataset(db_session: AsyncSession) -> uuid.UUID:
    ds = DatasetModel(
        id=uuid.uuid4(),
        name="fulltext-retrieval-test",
        embed_model="fake",
        embed_dim=EMBED_DIM,
    )
    db_session.add(ds)
    await db_session.flush()
    return ds.id


def _session_context_manager(session: AsyncSession) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


async def _set_tsvector(
    db_session: AsyncSession, chunk_id: uuid.UUID, content: str
) -> None:
    await db_session.execute(
        text("UPDATE chunks SET ts_tokens = to_tsvector('simple', :t) WHERE id = :id"),
        {"t": content, "id": chunk_id},
    )


@pytest.mark.asyncio
async def test_chinese_tokenization_and_search(db_session: AsyncSession) -> None:
    dataset_id = await _create_dataset(db_session)
    chunk = ChunkModel(
        dataset_id=dataset_id,
        text="Python 教程 入门",
        embedding=_embedding(),
    )
    db_session.add(chunk)
    await db_session.flush()
    await _set_tsvector(db_session, chunk.id, build_tsvector("Python 教程 入门"))
    await db_session.commit()

    retriever = FulltextRetriever(dataset_id=dataset_id)
    with patch(
        "rag.infra.pg.fulltext_store.AsyncSessionLocal",
        return_value=_session_context_manager(db_session),
    ):
        hits = await retriever.search("Python 教程", top_k=5)

    assert len(hits) >= 1
    assert "Python" in hits[0].text
    assert hits[0].source == "fulltext"
    assert hits[0].rank == 0
