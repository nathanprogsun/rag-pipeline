"""Unit tests for ``rag.search.orchestrator`` (multi-dataset pipeline).

Tests use AsyncMock for subgraphs / callbacks (no DB, no network, no LLM).
Validates:
- Multi-dataset fan-out (asyncio.gather)
- Failed subgraph graceful handling
- All 10 stages of Contract 8 wired (with NoOp defaults)
- Filter chain (dedup + score + token budget)
- SearchResult shape (Contract 4-6 invariants)
- Optional callbacks (rerank / parent_doc / cite / gen) invoked when set
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.domain.search import Citation, SearchRequest, SearchResult
from rag.search.orchestrator import (
    SearchPipeline,
    _dedup_by_chunk_id,
)

# ---------- Fixtures ----------


def _meta(ds: uuid.UUID | None = None) -> ChunkMetadata:
    return ChunkMetadata(
        dataset_id=ds or uuid.uuid4(),
        datasource="file",
    )


def _doc(
    chunk_id_str: str,
    *,
    dataset_id: uuid.UUID | None = None,
    score: float = 0.5,
    source: str = "vector",
    text: str | None = None,
) -> ScoredDocument:
    return ScoredDocument(
        chunk_id=uuid.UUID(chunk_id_str),
        dataset_id=dataset_id or uuid.uuid4(),
        text=text or f"text for {chunk_id_str}",
        score=score,
        rank=0,
        source=source,  # type: ignore[arg-type]
        metadata=_meta(),
    )


A = "00000000-0000-0000-0000-000000000001"
B = "00000000-0000-0000-0000-000000000002"
C = "00000000-0000-0000-0000-000000000003"


def _make_subgraph(
    *, dataset_id: uuid.UUID, hits_by_query: dict[str, list[ScoredDocument]]
) -> MagicMock:
    """Mock SearchSubgraph. ``hits_by_query`` maps query → hits."""
    sg = MagicMock()
    sg.dataset_id = dataset_id

    async def _ainvoke(query: str) -> list[ScoredDocument]:
        return list(hits_by_query.get(query, []))

    sg.ainvoke = AsyncMock(side_effect=_ainvoke)
    return sg


def _make_query_ext(*, variants: list[str]) -> MagicMock:
    """Mock QueryExtensionRunnable returning deduped_variants."""
    qe = MagicMock()
    result = MagicMock()
    result.deduped_variants = variants
    qe.return_value = result

    def _call(
        query: str, *, chat_bg: str = "", histories: list[str] | None = None
    ) -> MagicMock:
        return result

    qe.side_effect = _call
    return qe


def _req(
    *,
    query: str = "q",
    dataset_ids: list[uuid.UUID] | None = None,
) -> SearchRequest:
    return SearchRequest(
        query=query,
        dataset_ids=dataset_ids or [uuid.uuid4()],
    )


# ---------- __init__ ----------


def test_init_raises_on_empty_subgraphs() -> None:
    with pytest.raises(ValueError, match="subgraphs must be a non-empty"):
        SearchPipeline(subgraphs={})


def test_init_default_rrf_k_is_60() -> None:
    ds_id = uuid.uuid4()
    sg = _make_subgraph(dataset_id=ds_id, hits_by_query={})
    orch = SearchPipeline(subgraphs={ds_id: sg})
    assert orch.rrf_k == 60


# ---------- ainvoke: identity / variants ----------


async def test_ainvoke_identity_when_query_ext_is_none() -> None:
    """Stage 1: query_ext=None → only original query is used."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={"python": [_doc(A, dataset_id=ds_id, score=0.9)]},
    )
    orch = SearchPipeline(subgraphs={ds_id: sg})
    req = _req(query="python", dataset_ids=[ds_id])

    await orch.ainvoke(req)

    # Subgraph called only once with the original query
    assert sg.ainvoke.await_count == 1
    assert sg.ainvoke.call_args.args[0] == "python"


async def test_ainvoke_uses_query_ext_variants() -> None:
    """Stage 1: query_ext with 3 variants → subgraph called 3x per dataset."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(dataset_id=ds_id, hits_by_query={})
    qe = _make_query_ext(variants=["q1", "q2", "q3"])
    orch = SearchPipeline(subgraphs={ds_id: sg}, query_ext=qe)
    req = _req(query="original", dataset_ids=[ds_id])

    await orch.ainvoke(req)

    # Subgraph called once per variant (3 total)
    assert sg.ainvoke.await_count == 3
    called_queries = {c.args[0] for c in sg.ainvoke.call_args_list}
    assert called_queries == {"q1", "q2", "q3"}


async def test_ainvoke_query_ext_failure_falls_back_to_original() -> None:
    """Stage 1: query_ext raises → [original] used, no crash."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={"original": [_doc(A, dataset_id=ds_id)]},
    )
    qe = MagicMock()
    qe.side_effect = RuntimeError("LLM down")
    orch = SearchPipeline(subgraphs={ds_id: sg}, query_ext=qe)
    req = _req(query="original", dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    # Subgraph called once with original (fallback)
    assert sg.ainvoke.await_count == 1
    assert sg.ainvoke.call_args.args[0] == "original"
    # Result still valid
    assert isinstance(result, SearchResult)


# ---------- Multi-dataset fan-out ----------


async def test_ainvoke_fans_out_to_all_datasets_in_parallel() -> None:
    """Stage 2: asyncio.gather across all datasets in single variant."""
    ds_a = uuid.uuid4()
    ds_b = uuid.uuid4()
    sg_a = _make_subgraph(
        dataset_id=ds_a,
        hits_by_query={"q": [_doc(A, dataset_id=ds_a)]},
    )
    sg_b = _make_subgraph(
        dataset_id=ds_b,
        hits_by_query={"q": [_doc(B, dataset_id=ds_b)]},
    )
    orch = SearchPipeline(subgraphs={ds_a: sg_a, ds_b: sg_b})
    req = _req(query="q", dataset_ids=[ds_a, ds_b])

    await orch.ainvoke(req)

    assert sg_a.ainvoke.await_count == 1
    assert sg_b.ainvoke.await_count == 1


async def test_ainvoke_failed_subgraph_returns_empty_for_that_dataset() -> None:
    """One dataset subgraph raises → other datasets still contribute."""
    ds_a = uuid.uuid4()
    ds_b = uuid.uuid4()
    sg_a = MagicMock()
    sg_a.dataset_id = ds_a
    sg_a.ainvoke = AsyncMock(side_effect=RuntimeError("DB down"))
    sg_b = _make_subgraph(
        dataset_id=ds_b,
        hits_by_query={"q": [_doc(B, dataset_id=ds_b, score=0.9)]},
    )
    orch = SearchPipeline(subgraphs={ds_a: sg_a, ds_b: sg_b})
    req = _req(query="q", dataset_ids=[ds_a, ds_b])

    result = await orch.ainvoke(req)

    # sg_b's hit survives; failed_dataset_ids NOT populated (sg existed)
    assert len(result._intermediate_hits) == 1
    assert result._intermediate_hits[0].chunk_id == uuid.UUID(B)
    assert result.failed_dataset_ids == []


async def test_ainvoke_failed_dataset_id_tracked() -> None:
    """dataset_id requested but no subgraph registered → failed_dataset_ids."""
    ds_registered = uuid.uuid4()
    ds_missing = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_registered,
        hits_by_query={"q": [_doc(A, dataset_id=ds_registered)]},
    )
    orch = SearchPipeline(subgraphs={ds_registered: sg})
    req = _req(query="q", dataset_ids=[ds_registered, ds_missing])

    result = await orch.ainvoke(req)

    assert ds_missing in result.failed_dataset_ids
    assert ds_registered not in result.failed_dataset_ids


# ---------- Filter chain ----------


async def test_ainvoke_dedupes_by_chunk_id() -> None:
    """Stage 7 dedup: same chunk from multiple variants → kept once."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={
            "v1": [_doc(A, dataset_id=ds_id, score=0.9)],
            "v2": [_doc(A, dataset_id=ds_id, score=0.5)],
        },
    )
    qe = _make_query_ext(variants=["v1", "v2"])
    orch = SearchPipeline(subgraphs={ds_id: sg}, query_ext=qe)
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    # Even though A appears in 2 variants, dedup keeps 1
    assert len(result._intermediate_hits) == 1
    assert result._intermediate_hits[0].chunk_id == uuid.UUID(A)


async def test_ainvoke_filter_by_score_applied() -> None:
    """filter_score_threshold drops low-score hits."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={
            "q": [
                _doc(A, dataset_id=ds_id, score=0.9, source="vector"),
                _doc(B, dataset_id=ds_id, score=0.2, source="vector"),
            ]
        },
    )
    orch = SearchPipeline(
        subgraphs={ds_id: sg},
        filter_score_threshold=0.5,
    )
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    # score_breakdown[vector] for B is 0.2 → dropped
    assert len(result._intermediate_hits) == 1
    assert result._intermediate_hits[0].chunk_id == uuid.UUID(A)


async def test_ainvoke_no_filter_when_threshold_is_none() -> None:
    """filter_score_threshold=None → no threshold filter."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={
            "q": [
                _doc(A, dataset_id=ds_id, score=0.1),
                _doc(B, dataset_id=ds_id, score=0.05),
            ]
        },
    )
    orch = SearchPipeline(subgraphs={ds_id: sg}, filter_score_threshold=None)
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    assert len(result._intermediate_hits) == 2


async def test_ainvoke_token_budget_filters_long_hits() -> None:
    """Token budget: short docs kept, long docs dropped."""
    ds_id = uuid.uuid4()
    long_text = "x " * 5000  # > 1000 tokens
    short_text = "short"
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={
            "q": [
                _doc(A, dataset_id=ds_id, score=0.9, text=long_text),
                _doc(B, dataset_id=ds_id, score=0.5, text=short_text),
            ]
        },
    )
    orch = SearchPipeline(subgraphs={ds_id: sg}, token_budget=1000)
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    # Only short doc fits in 1000-token budget
    assert len(result._intermediate_hits) == 1
    assert result._intermediate_hits[0].chunk_id == uuid.UUID(B)


# ---------- Optional stage callbacks ----------


async def test_ainvoke_rerank_callback_invoked() -> None:
    """Stage 4: rerank callback called with fused hits."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={"q": [_doc(A, dataset_id=ds_id), _doc(B, dataset_id=ds_id)]},
    )
    rerank = AsyncMock(return_value=[_doc(B, dataset_id=ds_id, score=0.99)])
    orch = SearchPipeline(subgraphs={ds_id: sg}, rerank=rerank)
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    rerank.assert_awaited_once()
    # rerank's output (single doc B) is what survives
    assert len(result._intermediate_hits) == 1
    assert result._intermediate_hits[0].chunk_id == uuid.UUID(B)


async def test_ainvoke_parent_doc_callback_invoked() -> None:
    """Stage 8: parent_doc callback called with filtered hits."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={"q": [_doc(A, dataset_id=ds_id), _doc(B, dataset_id=ds_id)]},
    )
    parent_doc = AsyncMock(return_value=[_doc(C, dataset_id=ds_id, text="parent")])
    orch = SearchPipeline(subgraphs={ds_id: sg}, parent_doc=parent_doc)
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    parent_doc.assert_awaited_once()
    assert len(result._intermediate_hits) == 1
    assert result._intermediate_hits[0].chunk_id == uuid.UUID(C)


async def test_ainvoke_cite_callback_invoked() -> None:
    """Stage 9: cite callback called with final hits; output → result.citations."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={"q": [_doc(A, dataset_id=ds_id)]},
    )
    citation = Citation(
        chunk_id=uuid.UUID(A),
        dataset_id=ds_id,
        source_name="t",
        content="x",
        score=0.5,
    )
    cite = MagicMock(return_value=[citation])
    orch = SearchPipeline(subgraphs={ds_id: sg}, cite=cite)
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    cite.assert_called_once()
    assert result.citations == [citation]


async def test_ainvoke_gen_callback_invoked() -> None:
    """Stage 10: gen callback returns LLM answer → result.response."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={"q": [_doc(A, dataset_id=ds_id)]},
    )
    gen = AsyncMock(return_value="The answer is 42.")
    orch = SearchPipeline(subgraphs={ds_id: sg}, gen=gen)
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    gen.assert_awaited_once()
    assert result.response == "The answer is 42."


async def test_ainvoke_empty_response_when_gen_is_none() -> None:
    """Stage 10: gen=None → response="" (Contract 4: response holds LLM answer; empty here)."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={"q": [_doc(A, dataset_id=ds_id)]},
    )
    orch = SearchPipeline(subgraphs={ds_id: sg})
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    assert result.response == ""


async def test_ainvoke_empty_citations_when_cite_is_none() -> None:
    """Stage 9: cite=None → citations=[]."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={"q": [_doc(A, dataset_id=ds_id)]},
    )
    orch = SearchPipeline(subgraphs={ds_id: sg})
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    assert result.citations == []


# ---------- SearchResult shape (Contract 4-6) ----------


async def test_ainvoke_result_has_intermediate_hits_populated() -> None:
    """Contract 6: _intermediate_hits accessible programmatically."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={"q": [_doc(A, dataset_id=ds_id), _doc(B, dataset_id=ds_id)]},
    )
    orch = SearchPipeline(subgraphs={ds_id: sg})
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    assert isinstance(result._intermediate_hits, list)
    assert len(result._intermediate_hits) == 2


async def test_ainvoke_intermediate_hits_excluded_from_json() -> None:
    """Contract 6: _intermediate_hits Field(exclude=True) → not in model_dump_json."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={"q": [_doc(A, dataset_id=ds_id)]},
    )
    orch = SearchPipeline(subgraphs={ds_id: sg})
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    json_str = result.model_dump_json()
    assert "_intermediate_hits" not in json_str
    assert "intermediate_hits" not in json_str
    # But accessible programmatically
    assert len(result._intermediate_hits) == 1


async def test_ainvoke_warnings_capture_internal_failures() -> None:
    """Internal failures (e.g., query_ext) accumulate into result.warnings."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={"original": [_doc(A, dataset_id=ds_id)]},
    )
    qe = MagicMock()
    qe.side_effect = RuntimeError("LLM down")
    orch = SearchPipeline(subgraphs={ds_id: sg}, query_ext=qe)
    req = _req(query="original", dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    # query_ext failure recorded as warning
    assert any("query_ext_failed" in w for w in result.warnings)


async def test_ainvoke_warnings_capture_subgraph_failures() -> None:
    """Subgraph failures accumulate into result.warnings."""
    ds_a = uuid.uuid4()
    ds_b = uuid.uuid4()
    sg_a = MagicMock()
    sg_a.dataset_id = ds_a
    sg_a.ainvoke = AsyncMock(side_effect=RuntimeError("DB down"))
    sg_b = _make_subgraph(
        dataset_id=ds_b,
        hits_by_query={"q": [_doc(B, dataset_id=ds_b)]},
    )
    orch = SearchPipeline(subgraphs={ds_a: sg_a, ds_b: sg_b})
    req = _req(query="q", dataset_ids=[ds_a, ds_b])

    result = await orch.ainvoke(req)

    # Subgraph failure recorded as warning (one per failure)
    assert any("subgraph_failed" in w for w in result.warnings)


async def test_ainvoke_empty_warnings_on_clean_run() -> None:
    """No internal failures → empty result.warnings."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(
        dataset_id=ds_id,
        hits_by_query={"q": [_doc(A, dataset_id=ds_id)]},
    )
    orch = SearchPipeline(subgraphs={ds_id: sg})
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    assert result.warnings == []


async def test_ainvoke_empty_subgraph_returns_empty_hits() -> None:
    """Empty retrieval → empty _intermediate_hits, no crash."""
    ds_id = uuid.uuid4()
    sg = _make_subgraph(dataset_id=ds_id, hits_by_query={})
    orch = SearchPipeline(subgraphs={ds_id: sg})
    req = _req(dataset_ids=[ds_id])

    result = await orch.ainvoke(req)

    assert result._intermediate_hits == []


# ---------- Helpers ----------


def test_dedup_by_chunk_id_preserves_order() -> None:
    docs = [_doc(A), _doc(B), _doc(A), _doc(C), _doc(B)]
    deduped = _dedup_by_chunk_id(docs)
    assert [d.chunk_id for d in deduped] == [
        uuid.UUID(A),
        uuid.UUID(B),
        uuid.UUID(C),
    ]


def test_dedup_by_chunk_id_empty() -> None:
    assert _dedup_by_chunk_id([]) == []
