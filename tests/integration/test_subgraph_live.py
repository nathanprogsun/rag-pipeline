"""SearchSubgraph 集成测试 — 真实 PG 上验证 per-dataset retrieval + intra-fuse。

使用 ``pg_session_factory`` fixture (conftest) 创建独立 sessions per retriever
(避免 asyncio.gather 下共享 session 的 transaction abort 冲突)。

通过 ``RepoRetriever`` Runnable adapter 桥接:仍然按 Runnable 契约
接收 ``ainvoke({"query", "top_k"})``,但内部用 ChunkRepository。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from rag.infra.pg.models.chunk import ChunkModel
from rag.search.retrieve.subgraph import (
    SearchRequestValidationError,
    SearchSubgraph,
)
from tests._fakes import ConstantEmbeddings
from tests.integration._db_helpers import (
    EMBED_DIM,
    create_dataset,
    create_document,
    set_ts_tokens,
)
from tests.integration._retriever import RepoRetriever


def _unit_vector(dim_index: int) -> list[float]:
    vec = [0.0] * EMBED_DIM
    vec[dim_index] = 1.0
    return vec


_FAKE_EMB_VECTOR = _unit_vector(0)


def _fake_embeddings() -> ConstantEmbeddings:
    return ConstantEmbeddings(vector=_FAKE_EMB_VECTOR)


# ---------- Helpers ----------


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
    dataset_id = await create_dataset(db_session, "subgraph-fuse-test")
    doc_id = await create_document(
        db_session, dataset_id, filename="fuse.md", total_chunks=3
    )

    chunks = []
    for i, chunk_text in enumerate(["Python 教程", "Python 入门", "Python 进阶"]):
        chunk = ChunkModel(
            dataset_id=dataset_id,
            document_id=doc_id,
            text=chunk_text,
            embedding=_unit_vector(0),
            chunk_index=i,
        )
        db_session.add(chunk)
        chunks.append(chunk)
    await db_session.flush()
    for c in chunks:
        await set_ts_tokens(db_session, c)
    await db_session.commit()

    vector_retriever = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=dataset_id,
        mode="vector",
        embed_model=_fake_embeddings(),
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
    dataset_id = await create_dataset(db_session, "subgraph-vector-only-test")
    doc_id = await create_document(db_session, dataset_id, filename="vo.md")

    # Embedding matches, but fulltext content is different
    chunk = ChunkModel(
        dataset_id=dataset_id,
        document_id=doc_id,
        text="zzz unmatched text",
        embedding=_unit_vector(0),
    )
    db_session.add(chunk)
    await db_session.flush()
    await set_ts_tokens(db_session, chunk)
    await db_session.commit()

    vector_retriever = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=dataset_id,
        mode="vector",
        embed_model=_fake_embeddings(),
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
    dataset_id = await create_dataset(db_session, "subgraph-empty-test")

    vector_retriever = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=dataset_id,
        mode="vector",
        embed_model=_fake_embeddings(),
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
    dataset_id = await create_dataset(db_session, "subgraph-validation-test")

    vector_retriever = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=dataset_id,
        mode="vector",
        embed_model=_fake_embeddings(),
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
    ds_a = await create_dataset(db_session, "subgraph-isolation-a")
    ds_b = await create_dataset(db_session, "subgraph-isolation-b")
    doc_a = await create_document(db_session, ds_a, filename="a.md")
    doc_b = await create_document(db_session, ds_b, filename="b.md")

    chunk_a = ChunkModel(
        dataset_id=ds_a, document_id=doc_a, text="in A", embedding=_unit_vector(0)
    )
    db_session.add(chunk_a)
    chunk_b = ChunkModel(
        dataset_id=ds_b, document_id=doc_b, text="in B", embedding=_unit_vector(0)
    )
    db_session.add(chunk_b)
    await db_session.flush()
    await set_ts_tokens(db_session, chunk_a)
    await set_ts_tokens(db_session, chunk_b)
    await db_session.commit()

    # Subgraph for dataset A only
    vector_retriever_a = RepoRetriever(
        session_factory=pg_session_factory,
        dataset_id=ds_a,
        mode="vector",
        embed_model=_fake_embeddings(),
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
