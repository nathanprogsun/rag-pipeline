"""SearchSubgraph 集成测试 — 真实 PG 上验证 per-dataset retrieval + intra-fuse。

使用 ``pg_session_factory`` fixture (conftest) 创建独立 sessions per retriever
(避免 asyncio.gather 下共享 session 的 transaction abort 冲突)。

通过 ``RepoRetriever`` Runnable adapter 桥接:仍然按 Runnable 契约
接收 ``ainvoke({"query", "top_k"})``,但内部用 ChunkRepository。
"""

from __future__ import annotations

import uuid

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from rag.infra.pg.chinese_tokenizer import ChineseTokenizer
from rag.infra.pg.models.chunk import ChunkModel
from rag.infra.pg.models.dataset import DatasetModel
from rag.search.retrieve.subgraph import (
    SearchRequestValidationError,
    SearchSubgraph,
)
from tests.integration._retriever import RepoRetriever

EMBED_DIM = 1536


def _unit_vector(dim_index: int) -> list[float]:
    vec = [0.0] * EMBED_DIM
    vec[dim_index] = 1.0
    return vec


class FakeEmbeddings(Embeddings):
    """Real embedder interface. Returns unit vector (index 0)."""

    async def aembed_query(self, text: str) -> list[float]:
        return _unit_vector(0)

    def embed_query(self, text: str) -> list[float]:
        return _unit_vector(0)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_unit_vector(0) for _ in texts]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_unit_vector(0) for _ in texts]


# ---------- Helpers ----------


async def _create_dataset(db_session: AsyncSession, name: str) -> uuid.UUID:
    ds = DatasetModel(
        id=uuid.uuid4(),
        name=name,
        embed_model="fake",
        embed_dim=EMBED_DIM,
    )
    db_session.add(ds)
    await db_session.flush()
    return ds.id


async def _set_tsvector(
    db_session: AsyncSession, chunk_id: uuid.UUID, content: str
) -> None:
    """Set ts_tokens for fulltext search (Chinese-tokenized)."""
    await db_session.execute(
        text("UPDATE chunks SET ts_tokens = to_tsvector('simple', :t) WHERE id = :id"),
        {"t": content, "id": chunk_id},
    )


# ---------- Tests ----------


@pytest.mark.asyncio
async def test_subgraph_fuses_vector_and_fulltext(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """End-to-end: real DB via ChunkRepository + intra_fuse.

    Setup:
    - 3 chunks with embedding at index 0 (matches FakeEmbeddings)
    - All 3 have fulltext content matching the query
    - SearchSubgraph returns all 3 chunks, top by RRF.
    """
    dataset_id = await _create_dataset(db_session, "subgraph-fuse-test")

    chunks = []
    for chunk_text in ["Python 教程", "Python 入门", "Python 进阶"]:
        chunk = ChunkModel(
            dataset_id=dataset_id, text=chunk_text, embedding=_unit_vector(0)
        )
        db_session.add(chunk)
        chunks.append(chunk)
    await db_session.flush()
    for c in chunks:
        await _set_tsvector(db_session, c.id, ChineseTokenizer().build_tsvector(c.text))
    await db_session.commit()

    vector_retriever = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=dataset_id,
        mode="vector",
        embed_model=FakeEmbeddings(),
    )
    fulltext_retriever = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=dataset_id,
        mode="fulltext",
    )
    subgraph = SearchSubgraph(
        dataset_id=dataset_id,
        vector_retriever=vector_retriever,
        fulltext_retriever=fulltext_retriever,
        top_k=5,
    )

    hits = await subgraph.ainvoke("Python 教程")

    # All 3 chunks should be in result (all have matching embedding + text)
    assert len(hits) == 3
    # All hits are ScoredDocument with proper source
    assert all(h.source in ("vector", "fulltext") for h in hits)
    # Top hit has positive RRF score (≥ 1/61 in either source)
    top_score = max(h.score for h in hits)
    assert top_score > 0
    # All chunk texts present
    texts_in_result = {h.text for h in hits}
    assert "Python 教程" in texts_in_result


@pytest.mark.asyncio
async def test_subgraph_vector_only_match(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Vector match exists, fulltext does not -> still returns results."""
    dataset_id = await _create_dataset(db_session, "subgraph-vector-only-test")

    # Embedding matches, but fulltext content is different
    chunk = ChunkModel(
        dataset_id=dataset_id,
        text="zzz unmatched text",
        embedding=_unit_vector(0),
    )
    db_session.add(chunk)
    await db_session.flush()
    await _set_tsvector(
        db_session,
        chunk.id,
        ChineseTokenizer().build_tsvector("zzz unmatched text"),
    )
    await db_session.commit()

    vector_retriever = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=dataset_id,
        mode="vector",
        embed_model=FakeEmbeddings(),
    )
    fulltext_retriever = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=dataset_id,
        mode="fulltext",
    )
    subgraph = SearchSubgraph(
        dataset_id=dataset_id,
        vector_retriever=vector_retriever,
        fulltext_retriever=fulltext_retriever,
    )

    hits = await subgraph.ainvoke("Python 教程")

    # Vector matched, fulltext did not; chunk still present
    assert len(hits) >= 1
    assert any("zzz" in h.text for h in hits)


@pytest.mark.asyncio
async def test_subgraph_empty_result_when_no_chunks(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Empty dataset -> no chunks -> empty result (no crash).

    This test passes even with pre-existing chunk_repo bugs because the
    retrievers return empty (no chunks to find) before hitting the bugs.
    """
    dataset_id = await _create_dataset(db_session, "subgraph-empty-test")

    vector_retriever = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=dataset_id,
        mode="vector",
        embed_model=FakeEmbeddings(),
    )
    fulltext_retriever = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=dataset_id,
        mode="fulltext",
    )
    subgraph = SearchSubgraph(
        dataset_id=dataset_id,
        vector_retriever=vector_retriever,
        fulltext_retriever=fulltext_retriever,
    )

    hits = await subgraph.ainvoke("any query")

    assert hits == []


@pytest.mark.asyncio
async def test_subgraph_raises_on_empty_query(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Empty query -> SearchRequestValidationError, retrievers not called.

    This test passes regardless of chunk_repo bugs because the validation
    happens before any retriever is invoked.
    """
    dataset_id = await _create_dataset(db_session, "subgraph-validation-test")

    vector_retriever = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=dataset_id,
        mode="vector",
        embed_model=FakeEmbeddings(),
    )
    fulltext_retriever = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=dataset_id,
        mode="fulltext",
    )
    subgraph = SearchSubgraph(
        dataset_id=dataset_id,
        vector_retriever=vector_retriever,
        fulltext_retriever=fulltext_retriever,
    )

    with pytest.raises(SearchRequestValidationError, match="query must be"):
        await subgraph.ainvoke("")


@pytest.mark.asyncio
async def test_subgraph_per_dataset_isolation(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two datasets; each subgraph returns only its own dataset's chunks."""
    ds_a = await _create_dataset(db_session, "subgraph-isolation-a")
    ds_b = await _create_dataset(db_session, "subgraph-isolation-b")

    chunk_a = ChunkModel(dataset_id=ds_a, text="in A", embedding=_unit_vector(0))
    db_session.add(chunk_a)
    chunk_b = ChunkModel(dataset_id=ds_b, text="in B", embedding=_unit_vector(0))
    db_session.add(chunk_b)
    await db_session.flush()
    await _set_tsvector(
        db_session, chunk_a.id, ChineseTokenizer().build_tsvector("in A")
    )
    await _set_tsvector(
        db_session, chunk_b.id, ChineseTokenizer().build_tsvector("in B")
    )
    await db_session.commit()

    # Subgraph for dataset A only
    vector_retriever_a = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=ds_a,
        mode="vector",
        embed_model=FakeEmbeddings(),
    )
    fulltext_retriever_a = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=ds_a,
        mode="fulltext",
    )
    subgraph_a = SearchSubgraph(
        dataset_id=ds_a,
        vector_retriever=vector_retriever_a,
        fulltext_retriever=fulltext_retriever_a,
    )

    hits_a = await subgraph_a.ainvoke("A")

    # Per-dataset isolation: all hits should be from dataset A
    assert len(hits_a) >= 1
    assert all(h.dataset_id == ds_a for h in hits_a)
    assert any("in A" in h.text for h in hits_a)
    assert not any("in B" in h.text for h in hits_a)
