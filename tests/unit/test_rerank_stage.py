"""Unit tests for ``rag.search.retrieve.rerank`` (stage 4+5).

Tests use AsyncMock / MagicMock for the underlying reranker (no network).
Validates Contract 8 stage 4+5 invariants:
- Text-only rerank (image_caption bypasses)
- score_breakdown["rerank"] populated (Contract 2)
- Re-fuse via intra_fusion with weights [rerank_weight, 1-rerank_weight]
- Image hits appended at weight 1.0
- Reranker failure → graceful fallback to original order
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.domain.search import SearchRequest
from rag.search.retrieve.rerank import (
    NoOpRerankStage,
    RerankStageAdapter,
)

# ---------- Fixtures ----------


def _meta() -> ChunkMetadata:
    return ChunkMetadata(datasource="file")


def _doc(
    chunk_id_str: str,
    *,
    text: str | None = None,
    modality: str = "text",
    score: float = 0.5,
    rerank_score: float | None = None,
    breakdown: dict[str, float] | None = None,
) -> ScoredDocument:
    return ScoredDocument(
        chunk_id=uuid.UUID(chunk_id_str),
        dataset_id=uuid.uuid4(),
        text=text or f"text for {chunk_id_str}",
        score=score,
        rank=0,
        source="vector",
        modality=modality,  # type: ignore[arg-type]
        metadata=_meta(),
        rerank_score=rerank_score,
        score_breakdown=breakdown or {"vector": score},
    )


A = "00000000-0000-0000-0000-000000000001"
B = "00000000-0000-0000-0000-000000000002"
C = "00000000-0000-0000-0000-000000000003"
D = "00000000-0000-0000-0000-000000000004"
E = "00000000-0000-0000-0000-000000000005"


def _req(query: str = "test") -> SearchRequest:
    return SearchRequest(query=query, dataset_ids=[uuid.uuid4()])


def _make_reranker(
    *,
    results: list[tuple[int, float]] | None = None,
    side_effect: BaseException | None = None,
) -> MagicMock:
    """Mock reranker. Returns (idx, score) pairs in relevance-desc order."""
    r = MagicMock()
    if side_effect is not None:
        r.rerank = AsyncMock(side_effect=side_effect)
    else:
        r.rerank = AsyncMock(return_value=results or [])
    return r


# ---------- NoOpRerankStage ----------


async def test_noop_returns_input_unchanged() -> None:
    docs = [_doc(A), _doc(B), _doc(C)]
    stage = NoOpRerankStage()
    result = await stage(docs, _req())
    assert [d.chunk_id for d in result] == [
        uuid.UUID(A),
        uuid.UUID(B),
        uuid.UUID(C),
    ]


async def test_noop_empty_input() -> None:
    stage = NoOpRerankStage()
    result = await stage([], _req())
    assert result == []


async def test_noop_does_not_populate_rerank_score() -> None:
    docs = [_doc(A, rerank_score=None)]
    stage = NoOpRerankStage()
    result = await stage(docs, _req())
    assert result[0].rerank_score is None
    assert "rerank" not in result[0].score_breakdown


# ---------- RerankStageAdapter: __init__ ----------


def test_adapter_init_validates_weight_negative() -> None:
    reranker = _make_reranker()
    with pytest.raises(ValueError, match="rerank_weight must be in"):
        RerankStageAdapter(reranker=reranker, rerank_weight=-0.1)


def test_adapter_init_validates_weight_too_large() -> None:
    reranker = _make_reranker()
    with pytest.raises(ValueError, match="rerank_weight must be in"):
        RerankStageAdapter(reranker=reranker, rerank_weight=1.5)


def test_adapter_default_weight_is_0_7() -> None:
    reranker = _make_reranker()
    adapter = RerankStageAdapter(reranker=reranker)
    assert adapter.rerank_weight == 0.7


# ---------- RerankStageAdapter: empty / no text ----------


async def test_adapter_empty_input_returns_empty() -> None:
    reranker = _make_reranker()
    adapter = RerankStageAdapter(reranker=reranker)
    result = await adapter([], _req())
    assert result == []
    # Reranker should NOT have been called
    assert reranker.rerank.await_count == 0


async def test_adapter_only_image_hits_passes_through() -> None:
    """无 text 可 rerank → image hits 原样返回, reranker 不被调用。"""
    reranker = _make_reranker()
    adapter = RerankStageAdapter(reranker=reranker)
    docs = [
        _doc(A, modality="image_caption"),
        _doc(B, modality="image_caption"),
    ]
    result = await adapter(docs, _req())
    assert len(result) == 2
    assert all(d.modality == "image_caption" for d in result)
    assert reranker.rerank.await_count == 0


# ---------- RerankStageAdapter: text rerank ----------


async def test_adapter_reorders_by_rerank_score() -> None:
    """Reranker 返回 [B, A, C] 顺序 → 输出按 rerank 顺序。"""
    docs = [_doc(A), _doc(B), _doc(C)]
    reranker = _make_reranker(
        results=[(1, 0.99), (0, 0.5), (2, 0.1)]  # B > A > C
    )
    adapter = RerankStageAdapter(reranker=reranker, rerank_weight=1.0)
    result = await adapter(docs, _req())
    assert [d.chunk_id for d in result] == [
        uuid.UUID(B),
        uuid.UUID(A),
        uuid.UUID(C),
    ]


async def test_adapter_populates_rerank_score() -> None:
    docs = [_doc(A), _doc(B)]
    reranker = _make_reranker(results=[(0, 0.9), (1, 0.7)])
    adapter = RerankStageAdapter(reranker=reranker, rerank_weight=1.0)
    result = await adapter(docs, _req())
    a_in_result = next(d for d in result if d.chunk_id == uuid.UUID(A))
    assert a_in_result.rerank_score == 0.9


async def test_adapter_populates_score_breakdown_rerank_key() -> None:
    """Contract 2: rerank 后 score_breakdown['rerank'] 被填充。"""
    docs = [_doc(A, breakdown={"vector": 0.5})]
    reranker = _make_reranker(results=[(0, 0.88)])
    adapter = RerankStageAdapter(reranker=reranker, rerank_weight=1.0)
    result = await adapter(docs, _req())
    assert result[0].score_breakdown.get("rerank") == 0.88
    # 原始 breakdown 通过 intra_fusion per-source max merge 保留
    assert result[0].score_breakdown.get("vector") == 0.5


async def test_adapter_image_hits_not_passed_to_reranker() -> None:
    """Contract 8: image_caption modality 不进 reranker。"""
    text_a = _doc(A, modality="text", text="python text")
    img_b = _doc(B, modality="image_caption", text="python image")
    reranker = MagicMock()

    async def _rerank(
        query: str, documents: list[str], top_k: int
    ) -> list[tuple[int, float]]:
        # documents 列表只应包含 text 内容
        assert len(documents) == 1
        assert documents[0] == "python text"
        return [(0, 0.9)]

    reranker.rerank = AsyncMock(side_effect=_rerank)
    adapter = RerankStageAdapter(reranker=reranker, rerank_weight=1.0)
    result = await adapter([text_a, img_b], _req())

    chunk_ids = {d.chunk_id for d in result}
    assert uuid.UUID(A) in chunk_ids
    assert uuid.UUID(B) in chunk_ids


# ---------- RerankStageAdapter: re-fuse 权重 ----------


async def test_adapter_rerank_weight_zero_preserves_original() -> None:
    """rerank_weight=0.0 → 只看原始 RRF, rerank 顺序被忽略。"""
    docs = [_doc(A, score=0.9), _doc(B, score=0.7)]
    reranker = _make_reranker(results=[(1, 0.99), (0, 0.5)])
    adapter = RerankStageAdapter(reranker=reranker, rerank_weight=0.0)
    result = await adapter(docs, _req())
    assert result[0].chunk_id == uuid.UUID(A)


async def test_adapter_rerank_weight_one_uses_rerank_order() -> None:
    """rerank_weight=1.0 → 完全信任 rerank 顺序。"""
    docs = [_doc(A, score=0.9), _doc(B, score=0.7)]
    reranker = _make_reranker(results=[(1, 0.99), (0, 0.5)])
    adapter = RerankStageAdapter(reranker=reranker, rerank_weight=1.0)
    result = await adapter(docs, _req())
    assert result[0].chunk_id == uuid.UUID(B)


async def test_adapter_rerank_weight_middle_balanced() -> None:
    """rerank_weight=0.5 → 两源都贡献, RRF 合并。"""
    docs = [_doc(A, score=0.9), _doc(B, score=0.7)]
    reranker = _make_reranker(results=[(1, 0.99), (0, 0.5)])
    adapter = RerankStageAdapter(reranker=reranker, rerank_weight=0.5)
    result = await adapter(docs, _req())
    assert len(result) == 2


# ---------- RerankStageAdapter: image hits append ----------


async def test_adapter_image_hits_appended_after_text() -> None:
    """Image hits 绕过 rerank, 附在 re-fused list 后面。"""
    docs = [
        _doc(A, modality="text", score=0.9),
        _doc(B, modality="image_caption", score=0.5),
    ]
    reranker = _make_reranker(results=[(0, 0.9)])
    adapter = RerankStageAdapter(reranker=reranker, rerank_weight=1.0)
    result = await adapter(docs, _req())
    assert len(result) == 2
    img = next(d for d in result if d.modality == "image_caption")
    assert img.rerank_score is None


async def test_adapter_text_rerank_failure_falls_back() -> None:
    """Reranker 抛错 → 返回原始列表, 不崩溃。"""
    docs = [_doc(A), _doc(B), _doc(C)]
    reranker = _make_reranker(side_effect=RuntimeError("API down"))
    adapter = RerankStageAdapter(reranker=reranker)
    result = await adapter(docs, _req())
    assert [d.chunk_id for d in result] == [
        uuid.UUID(A),
        uuid.UUID(B),
        uuid.UUID(C),
    ]


async def test_adapter_calls_on_error_on_failure() -> None:
    docs = [_doc(A), _doc(B)]
    reranker = _make_reranker(side_effect=RuntimeError("oops"))
    on_error = AsyncMock()
    adapter = RerankStageAdapter(reranker=reranker, on_error=on_error)
    await adapter(docs, _req())
    on_error.assert_awaited_once()
    assert on_error.await_args is not None
    args = on_error.await_args.args
    assert args[0] == docs
    assert isinstance(args[1], RuntimeError)


# ---------- RerankStageAdapter: bad reranker index ----------


async def test_adapter_skips_bad_rerank_index() -> None:
    """Defensive: reranker 返越界 idx → 跳过该 pair。"""
    docs = [_doc(A), _doc(B)]
    reranker = _make_reranker(results=[(0, 0.9), (5, 0.8), (1, 0.7)])
    adapter = RerankStageAdapter(reranker=reranker, rerank_weight=1.0)
    result = await adapter(docs, _req())
    chunk_ids = {d.chunk_id for d in result}
    assert {uuid.UUID(A), uuid.UUID(B)} == chunk_ids


async def test_adapter_uses_query_in_rerank_call() -> None:
    """req.query 作为 reranker.rerank 第一参数。"""
    docs = [_doc(A)]
    reranker = _make_reranker(results=[(0, 0.9)])
    adapter = RerankStageAdapter(reranker=reranker, rerank_weight=1.0)
    await adapter(docs, _req(query="hello world"))
    call_args = reranker.rerank.await_args.args
    assert call_args[0] == "hello world"
