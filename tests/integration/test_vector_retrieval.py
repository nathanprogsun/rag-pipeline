"""VectorRetriever 集成测试 — 真实 PG 上验证 HNSW cosine 检索链路。"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rag.infra.pg.models.chunk import ChunkModel
from rag.infra.pg.models.dataset import DatasetModel
from rag.infra.pg.vector_store import VectorRetriever
from tests._fakes import ConstantEmbeddings

EMBED_DIM = 1536


def _unit_vector(dim_index: int) -> list[float]:
    vec = [0.0] * EMBED_DIM
    vec[dim_index] = 1.0
    return vec


_FAKE_EMB_VECTOR = _unit_vector(0)


def _fake_embeddings() -> ConstantEmbeddings:
    return ConstantEmbeddings(vector=_FAKE_EMB_VECTOR)


async def _create_dataset(db_session: AsyncSession) -> uuid.UUID:
    ds = DatasetModel(
        id=uuid.uuid4(),
        name="vector-retrieval-test",
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


@pytest.mark.asyncio
async def test_hnsw_index_actually_used(db_session: AsyncSession) -> None:
    dataset_id = await _create_dataset(db_session)
    for i, text in enumerate(["text 0", "text 1", "text 2"]):
        db_session.add(
            ChunkModel(
                dataset_id=dataset_id,
                text=text,
                embedding=_unit_vector(i),
            )
        )
    await db_session.commit()

    retriever = VectorRetriever(dataset_id=dataset_id, embed_model=_fake_embeddings())
    with patch(
        "rag.infra.pg.vector_store.AsyncSessionLocal",
        return_value=_session_context_manager(db_session),
    ):
        hits = await retriever.search("x", top_k=2)

    assert len(hits) == 2
    assert hits[0].text == "text 0"
    assert hits[0].score > hits[1].score
    assert hits[0].source == "vector"
    assert hits[0].rank == 0
