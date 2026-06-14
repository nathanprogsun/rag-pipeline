"""Unit tests for ``rag.pipeline.parent_doc`` (stage 8).

Tests cover Contract 8 stage 8 invariants:
- NoOpParentDoc identity passthrough
- ParentDocExpander: window size from req.context.parent_doc_window
- Sibling score decay
- image_caption modality bypass
- Empty parent_title handling
- Overlapping window dedup
- chunk_repo exception → fallback to original order
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from rag.domain.document import Chunk as DomainChunk
from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.domain.search import SearchRequest
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.pipeline.parent_doc import (
    NoOpParentDoc,
    ParentDocExpander,
)

# ---------- Fixtures ----------


def _meta(
    parent_title: str = "doc",
    chunk_index: int = 0,
) -> ChunkMetadata:
    return ChunkMetadata(
        dataset_id=uuid.uuid4(),
        datasource="file",
        parent_title=parent_title,
        chunk_index=chunk_index,
    )


def _doc(
    chunk_id_str: str,
    *,
    parent_title: str = "doc",
    chunk_index: int = 0,
    score: float = 0.5,
    modality: str = "text",
    text: str | None = None,
    dataset_id: uuid.UUID | None = None,
) -> ScoredDocument:
    return ScoredDocument(
        chunk_id=uuid.UUID(chunk_id_str),
        dataset_id=dataset_id or uuid.uuid4(),
        text=text or f"text {chunk_index} for {chunk_id_str}",
        score=score,
        rank=0,
        source="vector",
        modality=modality,  # type: ignore[arg-type]
        metadata=_meta(parent_title=parent_title, chunk_index=chunk_index),
    )


def _domain_chunk(
    chunk_id_str: str,
    *,
    parent_title: str = "doc",
    chunk_index: int = 0,
    text: str | None = None,
    dataset_id: uuid.UUID | None = None,
    modality: str = "text",
) -> DomainChunk:
    """Domain Chunk shape returned by ChunkRepository.get_siblings."""
    return DomainChunk(
        id=uuid.UUID(chunk_id_str),
        dataset_id=dataset_id or uuid.uuid4(),
        text=text or f"text {chunk_index} for {chunk_id_str}",
        modality=modality,  # type: ignore[arg-type]
        metadata=_meta(parent_title=parent_title, chunk_index=chunk_index),
    )


A = "00000000-0000-0000-0000-000000000001"
B = "00000000-0000-0000-0000-000000000002"
C = "00000000-0000-0000-0000-000000000003"
D = "00000000-0000-0000-0000-000000000004"
E = "00000000-0000-0000-0000-000000000005"


def _req(*, parent_doc_window: int = 0) -> SearchRequest:
    """req.context.parent_doc_window 控制实际 window size。"""
    from rag.domain.search import ContextConfig

    return SearchRequest(
        query="q",
        dataset_ids=[uuid.uuid4()],
        context=ContextConfig(parent_doc_window=parent_doc_window),
    )


def _make_chunk_repo(
    *, siblings_by_doc: dict | None = None,
    side_effect: BaseException | None = None,
) -> MagicMock:
    """Mock ChunkRepository. ``siblings_by_doc`` maps chunk_id_str -> list of DomainChunk."""
    repo = MagicMock(spec=ChunkRepository)
    if side_effect is not None:
        repo.get_siblings = AsyncMock(side_effect=side_effect)
    else:
        siblings_by_doc = siblings_by_doc or {}
        chunks_by_id = {
            uuid.UUID(k): v for k, v in (siblings_by_doc or {}).items()
        }

        async def _get_siblings(
            dataset_id: uuid.UUID,
            parent_title: str,
            lo: int,
            hi: int,
        ) -> list[DomainChunk]:
            # In real impl we'd filter by chunk_index. For tests, return
            # whatever was associated with the (parent_title, dataset_id)
            # tuple. Simplified.
            return []

        repo.get_siblings = AsyncMock(side_effect=_get_siblings)
        repo._chunks_by_id = chunks_by_id  # type: ignore[attr-defined]
    return repo


# ---------- NoOpParentDoc ----------


async def test_noop_returns_input_unchanged() -> None:
    docs = [_doc(A), _doc(B)]
    stage = NoOpParentDoc()
    result = await stage(docs, _req())
    assert [d.chunk_id for d in result] == [uuid.UUID(A), uuid.UUID(B)]


async def test_noop_does_not_dedup() -> None:
    docs = [_doc(A), _doc(A)]
    stage = NoOpParentDoc()
    result = await stage(docs, _req())
    assert len(result) == 2  # NoOp 不去重


async def test_noop_empty_input() -> None:
    stage = NoOpParentDoc()
    result = await stage([], _req())
    assert result == []


# ---------- ParentDocExpander: init ----------


def test_expander_init_validates_negative_default_window() -> None:
    repo = _make_chunk_repo()
    with pytest.raises(ValueError, match="default_window must be >= 0"):
        ParentDocExpander(chunk_repo=repo, default_window=-1)


def test_expander_init_validates_decay_negative() -> None:
    repo = _make_chunk_repo()
    with pytest.raises(ValueError, match="sibling_decay must be in"):
        ParentDocExpander(chunk_repo=repo, sibling_decay=-0.1)


def test_expander_init_validates_decay_too_large() -> None:
    repo = _make_chunk_repo()
    with pytest.raises(ValueError, match="sibling_decay must be in"):
        ParentDocExpander(chunk_repo=repo, sibling_decay=1.5)


def test_expander_default_decay_is_0_5() -> None:
    repo = _make_chunk_repo()
    expander = ParentDocExpander(chunk_repo=repo)
    assert expander.sibling_decay == 0.5


# ---------- ParentDocExpander: window=0 / empty ----------


async def test_expander_window_zero_returns_input() -> None:
    """req.context.parent_doc_window=0 AND default_window=0 → 原样返回。"""
    repo = _make_chunk_repo()
    expander = ParentDocExpander(chunk_repo=repo, default_window=0)
    docs = [_doc(A), _doc(B)]
    result = await expander(docs, _req(parent_doc_window=0))
    assert [d.chunk_id for d in result] == [uuid.UUID(A), uuid.UUID(B)]
    # chunk_repo 没被调用
    assert repo.get_siblings.await_count == 0


async def test_expander_empty_input_returns_empty() -> None:
    repo = _make_chunk_repo()
    expander = ParentDocExpander(chunk_repo=repo, default_window=3)
    result = await expander([], _req(parent_doc_window=3))
    assert result == []
    assert repo.get_siblings.await_count == 0


async def test_expander_default_window_used_when_req_window_zero() -> None:
    """req.context.parent_doc_window=0 但 expander 有 default_window=3 → 用 3。"""
    repo = _make_chunk_repo()
    expander = ParentDocExpander(chunk_repo=repo, default_window=3)
    docs = [_doc(A, chunk_index=5, parent_title="t")]
    await expander(docs, _req(parent_doc_window=0))
    # chunk_repo.get_siblings 调用过, lo=5-3=2, hi=5+3=8
    assert repo.get_siblings.await_count == 1
    call = repo.get_siblings.await_args
    assert call.kwargs["lo"] == 2
    assert call.kwargs["hi"] == 8


# ---------- ParentDocExpander: window expansion ----------


async def test_expander_window_uses_lo_hi_from_chunk_index() -> None:
    """lo = max(0, chunk_index - window), hi = chunk_index + window。"""
    repo = _make_chunk_repo()
    expander = ParentDocExpander(chunk_repo=repo, default_window=2)
    docs = [_doc(A, chunk_index=3, parent_title="t")]
    await expander(docs, _req(parent_doc_window=0))
    call = repo.get_siblings.await_args
    assert call.kwargs["lo"] == 1  # max(0, 3-2)
    assert call.kwargs["hi"] == 5  # 3+2


async def test_expander_lo_clamped_to_zero() -> None:
    """chunk_index < window 时 lo 钳到 0, 不会变负数。"""
    repo = _make_chunk_repo()
    expander = ParentDocExpander(chunk_repo=repo, default_window=10)
    docs = [_doc(A, chunk_index=2, parent_title="t")]
    await expander(docs, _req(parent_doc_window=0))
    call = repo.get_siblings.await_args
    assert call.kwargs["lo"] == 0  # max(0, 2-10)
    assert call.kwargs["hi"] == 12


# ---------- ParentDocExpander: score decay ----------


async def test_expander_matched_chunk_keeps_original_score() -> None:
    """被命中的 chunk (sibling 列表中第一个) 保留原 score。"""
    repo = MagicMock(spec=ChunkRepository)
    matched = _domain_chunk(A, chunk_index=5, parent_title="t")
    matched_id = matched.id

    async def _get_siblings(
            dataset_id: uuid.UUID, parent_title: str, lo: int, hi: int
        ) -> list[DomainChunk]:
        return [matched]

    repo.get_siblings = AsyncMock(side_effect=_get_siblings)
    expander = ParentDocExpander(chunk_repo=repo, default_window=2)
    docs = [_doc(A, chunk_index=5, score=0.88, parent_title="t")]

    result = await expander(docs, _req(parent_doc_window=0))

    # 命中的 chunk 保留原 score
    matched_in_result = next(d for d in result if d.chunk_id == matched_id)
    assert matched_in_result.score == 0.88


async def test_expander_siblings_get_decay_score() -> None:
    """非命中的 sibling (context chunk) 分数 = 原 score * decay。"""
    matched = _domain_chunk(A, chunk_index=5, parent_title="t")
    sibling_b = _domain_chunk(B, chunk_index=4, parent_title="t")
    sibling_c = _domain_chunk(C, chunk_index=6, parent_title="t")
    repo = MagicMock(spec=ChunkRepository)

    async def _get_siblings(
            dataset_id: uuid.UUID, parent_title: str, lo: int, hi: int
        ) -> list[DomainChunk]:
        return [matched, sibling_b, sibling_c]

    repo.get_siblings = AsyncMock(side_effect=_get_siblings)
    expander = ParentDocExpander(
        chunk_repo=repo, default_window=2, sibling_decay=0.5
    )
    docs = [_doc(A, chunk_index=5, score=0.8, parent_title="t")]

    result = await expander(docs, _req(parent_doc_window=0))

    by_id = {d.chunk_id: d for d in result}
    # A 保留 0.8
    assert by_id[matched.id].score == 0.8
    # B/C 是 sibling → 0.8 * 0.5 = 0.4
    assert by_id[sibling_b.id].score == 0.4
    assert by_id[sibling_c.id].score == 0.4


async def test_expander_decay_zero_zeros_sibling_score() -> None:
    """decay=0 → sibling score=0 (保留但不入 RRF 排名)。"""
    matched = _domain_chunk(A, chunk_index=5, parent_title="t")
    sibling = _domain_chunk(B, chunk_index=4, parent_title="t")
    repo = MagicMock(spec=ChunkRepository)

    async def _get_siblings(
            dataset_id: uuid.UUID, parent_title: str, lo: int, hi: int
        ) -> list[DomainChunk]:
        return [matched, sibling]

    repo.get_siblings = AsyncMock(side_effect=_get_siblings)
    expander = ParentDocExpander(
        chunk_repo=repo, default_window=2, sibling_decay=0.0
    )
    docs = [_doc(A, chunk_index=5, score=0.8, parent_title="t")]

    result = await expander(docs, _req(parent_doc_window=0))

    by_id = {d.chunk_id: d for d in result}
    assert by_id[matched.id].score == 0.8
    assert by_id[sibling.id].score == 0.0


# ---------- ParentDocExpander: image_caption bypass ----------


async def test_expander_image_caption_bypasses_expansion() -> None:
    """image_caption modality 不进 get_siblings, 原样保留。"""
    repo = _make_chunk_repo()
    expander = ParentDocExpander(chunk_repo=repo, default_window=3)
    img = _doc(
        A, chunk_index=5, parent_title="t", modality="image_caption"
    )
    docs = [img]
    result = await expander(docs, _req(parent_doc_window=0))

    # image_caption 没触发 get_siblings
    assert repo.get_siblings.await_count == 0
    # 原样保留
    assert len(result) == 1
    assert result[0].chunk_id == uuid.UUID(A)
    assert result[0].modality == "image_caption"


# ---------- ParentDocExpander: missing parent_title ----------


async def test_expander_missing_parent_title_keeps_original() -> None:
    """parent_title 为空 → 跳过 expansion, 原 chunk 保留。"""
    repo = _make_chunk_repo()
    expander = ParentDocExpander(chunk_repo=repo, default_window=3)
    doc = _doc(A, parent_title="", chunk_index=5)
    result = await expander([doc], _req(parent_doc_window=0))

    assert repo.get_siblings.await_count == 0
    assert len(result) == 1
    assert result[0].chunk_id == uuid.UUID(A)


# ---------- ParentDocExpander: overlapping windows dedup ----------


async def test_expander_overlapping_windows_dedup() -> None:
    """两个 matched chunk 在同一 parent window 内, sibling 只出现一次。"""
    matched_a = _domain_chunk(A, chunk_index=5, parent_title="t")
    sibling_b = _domain_chunk(B, chunk_index=4, parent_title="t")
    sibling_c = _domain_chunk(C, chunk_index=6, parent_title="t")
    matched_d = _domain_chunk(D, chunk_index=6, parent_title="t")  # 同 chunk_index as C

    # D 也会命中 C 这个 chunk (D 的 sibling 包含 C)
    async def _get_siblings(
            dataset_id: uuid.UUID, parent_title: str, lo: int, hi: int
        ) -> list[DomainChunk]:
        # 简化: 总是返回所有 siblings (测试不关心 window 过滤)
        return [matched_a, sibling_b, sibling_c, matched_d]

    repo = MagicMock(spec=ChunkRepository)
    repo.get_siblings = AsyncMock(side_effect=_get_siblings)
    expander = ParentDocExpander(chunk_repo=repo, default_window=2)

    docs = [
        _doc(A, chunk_index=5, score=0.9, parent_title="t"),
        _doc(D, chunk_index=6, score=0.8, parent_title="t"),
    ]
    result = await expander(docs, _req(parent_doc_window=0))

    # 4 个 unique chunk_ids, 不重复
    unique_ids = {d.chunk_id for d in result}
    assert unique_ids == {uuid.UUID(A), uuid.UUID(B), uuid.UUID(C), uuid.UUID(D)}
    # A 保留原 score 0.9, D 保留 0.8
    by_id = {d.chunk_id: d for d in result}
    assert by_id[uuid.UUID(A)].score == 0.9
    assert by_id[uuid.UUID(D)].score == 0.8


# ---------- ParentDocExpander: chunk_repo failure ----------


async def test_expander_chunk_repo_failure_falls_back() -> None:
    """chunk_repo 抛错 → 返回原始列表, 不崩溃。"""
    repo = MagicMock(spec=ChunkRepository)
    repo.get_siblings = AsyncMock(side_effect=RuntimeError("DB down"))
    expander = ParentDocExpander(chunk_repo=repo, default_window=3)
    docs = [_doc(A), _doc(B)]
    result = await expander(docs, _req(parent_doc_window=0))
    assert [d.chunk_id for d in result] == [uuid.UUID(A), uuid.UUID(B)]


async def test_expander_calls_on_error_on_failure() -> None:
    repo = MagicMock(spec=ChunkRepository)
    repo.get_siblings = AsyncMock(side_effect=RuntimeError("oops"))
    on_error = AsyncMock()
    expander = ParentDocExpander(
        chunk_repo=repo, default_window=3, on_error=on_error
    )
    docs = [_doc(A)]
    await expander(docs, _req(parent_doc_window=0))
    on_error.assert_awaited_once()
    args = on_error.await_args.args
    assert args[0] == docs
    assert isinstance(args[1], RuntimeError)