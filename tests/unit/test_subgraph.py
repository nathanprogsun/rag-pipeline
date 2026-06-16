"""Unit tests for ``rag.search.retrieve.subgraph`` (per-dataset retrieval).

Tests use AsyncMock for retrievers (no DB, no network).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.search.retrieve.subgraph import (
    SearchRequestValidationError,
    SearchSubgraph,
    validate_subgraph_request,
)

# ---------- Fixtures ----------


def _meta() -> ChunkMetadata:
    return ChunkMetadata(datasource="file")


def _doc(
    chunk_id_str: str,
    *,
    score: float = 0.5,
    source: str = "vector",
) -> ScoredDocument:
    return ScoredDocument(
        chunk_id=uuid.UUID(chunk_id_str),
        dataset_id=uuid.uuid4(),
        text=f"text for {chunk_id_str}",
        score=score,
        rank=0,
        source=source,  # type: ignore[arg-type]
        metadata=_meta(),
    )


A = "00000000-0000-0000-0000-000000000001"
B = "00000000-0000-0000-0000-000000000002"
C = "00000000-0000-0000-0000-000000000003"


def _make_retriever(hits: list[ScoredDocument]) -> AsyncMock:
    """Mock LangChain Runnable with .ainvoke(input_dict) -> list[ScoredDocument]."""
    r = MagicMock()
    r.ainvoke = AsyncMock(return_value=hits)
    return r


# ---------- Request validation ----------


def test_validate_query_empty_raises() -> None:
    with pytest.raises(SearchRequestValidationError, match="query must be"):
        validate_subgraph_request(query="", dataset_id=uuid.uuid4(), top_k=10)


def test_validate_query_whitespace_only_raises() -> None:
    with pytest.raises(SearchRequestValidationError, match="query must be"):
        validate_subgraph_request(query="   ", dataset_id=uuid.uuid4(), top_k=10)


def test_validate_dataset_id_not_uuid_raises() -> None:
    with pytest.raises(SearchRequestValidationError, match="dataset_id must be UUID"):
        validate_subgraph_request(
            query="hello",
            dataset_id="not-a-uuid",  # type: ignore[arg-type]
            top_k=10,
        )


def test_validate_top_k_zero_raises() -> None:
    with pytest.raises(SearchRequestValidationError, match="top_k must be"):
        validate_subgraph_request(query="hello", dataset_id=uuid.uuid4(), top_k=0)


def test_validate_top_k_negative_raises() -> None:
    with pytest.raises(SearchRequestValidationError, match="top_k must be"):
        validate_subgraph_request(query="hello", dataset_id=uuid.uuid4(), top_k=-5)


def test_validate_request_passes() -> None:
    # No exception
    validate_subgraph_request(query="hello", dataset_id=uuid.uuid4(), top_k=10)


# ---------- SearchSubgraph.__init__ ----------


def test_subgraph_init_validates_negative_weight() -> None:
    vec = _make_retriever([])
    ft = _make_retriever([])
    with pytest.raises(SearchRequestValidationError, match="weights must be"):
        SearchSubgraph(
            dataset_id=uuid.uuid4(),
            vector_retriever=vec,
            fulltext_retriever=ft,
            vector_weight=-0.1,
        )


def test_subgraph_default_weights() -> None:
    vec = _make_retriever([])
    ft = _make_retriever([])
    sg = SearchSubgraph(
        dataset_id=uuid.uuid4(), vector_retriever=vec, fulltext_retriever=ft
    )
    assert sg.vector_weight == 0.7
    assert sg.fulltext_weight == 0.3
    assert sg.rrf_k == 60
    assert sg.top_k == 10


# ---------- SearchSubgraph.ainvoke: per-dataset retrieval ----------


async def test_subgraph_fuses_vector_and_fulltext() -> None:
    """Both retrievers return hits → intra_fuse yields merged list."""
    vec_hits = [
        _doc(A, source="vector", score=0.9),
        _doc(B, source="vector", score=0.7),
    ]
    ft_hits = [
        _doc(B, source="fulltext", score=0.8),
        _doc(C, source="fulltext", score=0.6),
    ]
    vec = _make_retriever(vec_hits)
    ft = _make_retriever(ft_hits)
    sg = SearchSubgraph(
        dataset_id=uuid.uuid4(), vector_retriever=vec, fulltext_retriever=ft
    )
    result = await sg.ainvoke("query")
    # B is in both -> RRF sum; A and C only in one
    assert len(result) == 3
    # Verify B is the top (rank 1 in both groups)
    assert result[0].chunk_id == uuid.UUID(B)


async def test_subgraph_handles_empty_vector_hits() -> None:
    """Vector retriever returns empty → fulltext results only."""
    ft_hits = [
        _doc(A, source="fulltext", score=0.9),
        _doc(B, source="fulltext", score=0.7),
    ]
    vec = _make_retriever([])
    ft = _make_retriever(ft_hits)
    sg = SearchSubgraph(
        dataset_id=uuid.uuid4(), vector_retriever=vec, fulltext_retriever=ft
    )
    result = await sg.ainvoke("query")
    assert len(result) == 2
    assert {d.chunk_id for d in result} == {uuid.UUID(A), uuid.UUID(B)}


async def test_subgraph_handles_empty_fulltext_hits() -> None:
    """Fulltext retriever returns empty → vector results only."""
    vec_hits = [_doc(A, source="vector", score=0.9)]
    vec = _make_retriever(vec_hits)
    ft = _make_retriever([])
    sg = SearchSubgraph(
        dataset_id=uuid.uuid4(), vector_retriever=vec, fulltext_retriever=ft
    )
    result = await sg.ainvoke("query")
    assert len(result) == 1
    assert result[0].chunk_id == uuid.UUID(A)


async def test_subgraph_handles_both_empty() -> None:
    """Both retrievers return empty → empty list."""
    vec = _make_retriever([])
    ft = _make_retriever([])
    sg = SearchSubgraph(
        dataset_id=uuid.uuid4(), vector_retriever=vec, fulltext_retriever=ft
    )
    result = await sg.ainvoke("query")
    assert result == []


async def test_subgraph_continues_when_vector_fails() -> None:
    """Vector retriever raises → return fulltext results."""
    ft_hits = [_doc(A, source="fulltext", score=0.9)]
    vec = MagicMock()
    vec.ainvoke = AsyncMock(side_effect=RuntimeError("DB down"))
    ft = _make_retriever(ft_hits)
    sg = SearchSubgraph(
        dataset_id=uuid.uuid4(), vector_retriever=vec, fulltext_retriever=ft
    )
    result = await sg.ainvoke("query")
    assert len(result) == 1
    assert result[0].chunk_id == uuid.UUID(A)


async def test_subgraph_continues_when_fulltext_fails() -> None:
    """Fulltext retriever raises → return vector results."""
    vec_hits = [_doc(A, source="vector", score=0.9)]
    vec = _make_retriever(vec_hits)
    ft = MagicMock()
    ft.ainvoke = AsyncMock(side_effect=RuntimeError("DB down"))
    sg = SearchSubgraph(
        dataset_id=uuid.uuid4(), vector_retriever=vec, fulltext_retriever=ft
    )
    result = await sg.ainvoke("query")
    assert len(result) == 1
    assert result[0].chunk_id == uuid.UUID(A)


async def test_subgraph_returns_empty_when_both_fail() -> None:
    """Both retrievers raise → empty list (no crash)."""
    vec = MagicMock()
    vec.ainvoke = AsyncMock(side_effect=RuntimeError("vec down"))
    ft = MagicMock()
    ft.ainvoke = AsyncMock(side_effect=RuntimeError("ft down"))
    sg = SearchSubgraph(
        dataset_id=uuid.uuid4(), vector_retriever=vec, fulltext_retriever=ft
    )
    result = await sg.ainvoke("query")
    assert result == []


async def test_subgraph_calls_retrievers_in_parallel() -> None:
    """Both retrievers invoked (in parallel via asyncio.gather)."""
    vec = _make_retriever([_doc(A, source="vector")])
    ft = _make_retriever([_doc(B, source="fulltext")])
    sg = SearchSubgraph(
        dataset_id=uuid.uuid4(), vector_retriever=vec, fulltext_retriever=ft
    )
    await sg.ainvoke("query")
    # Both ainvoke methods called once
    assert vec.ainvoke.await_count == 1
    assert ft.ainvoke.await_count == 1
    # Both called with {"query": "query", "top_k": 10}
    vec_call = vec.ainvoke.call_args.args[0]
    ft_call = ft.ainvoke.call_args.args[0]
    assert vec_call == {"query": "query", "top_k": 10}
    assert ft_call == {"query": "query", "top_k": 10}


async def test_subgraph_uses_custom_top_k() -> None:
    """top_k is passed through to retrievers."""
    vec = _make_retriever([_doc(A, source="vector")])
    ft = _make_retriever([_doc(B, source="fulltext")])
    sg = SearchSubgraph(
        dataset_id=uuid.uuid4(),
        vector_retriever=vec,
        fulltext_retriever=ft,
        top_k=5,
    )
    await sg.ainvoke("query")
    assert vec.ainvoke.call_args.args[0] == {"query": "query", "top_k": 5}


async def test_subgraph_custom_weights_change_fusion() -> None:
    """vector_weight=1, fulltext_weight=0 → only vector scores count in RRF."""
    vec_hits = [
        _doc(A, source="vector", score=0.9),
        _doc(B, source="vector", score=0.7),
    ]
    ft_hits = [_doc(C, source="fulltext", score=0.99)]  # higher raw but weight=0
    vec = _make_retriever(vec_hits)
    ft = _make_retriever(ft_hits)
    sg = SearchSubgraph(
        dataset_id=uuid.uuid4(),
        vector_retriever=vec,
        fulltext_retriever=ft,
        vector_weight=1.0,
        fulltext_weight=0.0,
    )
    result = await sg.ainvoke("query")
    # C is in result (weight 0 doesn't filter), but vector items have higher RRF
    assert len(result) == 3
    # A is rank 1 in vector (score 1/61), C is rank 0 (score 0/61 = 0)
    assert result[0].chunk_id == uuid.UUID(A)  # A has highest RRF


async def test_subgraph_ainvoke_raises_on_empty_query() -> None:
    """Invalid query → SearchRequestValidationError before retrievers called."""
    vec = _make_retriever([])
    ft = _make_retriever([])
    sg = SearchSubgraph(
        dataset_id=uuid.uuid4(), vector_retriever=vec, fulltext_retriever=ft
    )
    with pytest.raises(SearchRequestValidationError, match="query must be"):
        await sg.ainvoke("")
    # Retrievers should NOT be called (validation happens first)
    assert vec.ainvoke.await_count == 0
    assert ft.ainvoke.await_count == 0
