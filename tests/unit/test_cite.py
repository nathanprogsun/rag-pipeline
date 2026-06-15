"""Unit tests for ``rag.search.post.cite`` (stage 9) and
``rag.infra.text.citation_check`` (marker parsing + validation).

Tests cover Contract 5:
- SimpleCite numbers docs 1-based and builds Citation DTOs
- Custom source_name_fn override
- parse_inline_citations extracts [id](CITE) markers correctly
- resolve_citation_positions populates Citation.position from response
"""

from __future__ import annotations

import uuid

from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.domain.search import Citation, SearchRequest
from rag.infra.text.citation_check import (
    parse_inline_citations,
    resolve_citation_positions,
)
from rag.search.post.cite import SimpleCite

# ---------- Fixtures ----------


def _meta() -> ChunkMetadata:
    return ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file")


def _doc(
    chunk_id_str: str,
    *,
    text: str | None = None,
    image_path: str | None = None,
    score: float = 0.5,
    modality: str = "text",
    dataset_id: uuid.UUID | None = None,
) -> ScoredDocument:
    return ScoredDocument(
        chunk_id=uuid.UUID(chunk_id_str),
        dataset_id=dataset_id or uuid.uuid4(),
        text=text or f"text for {chunk_id_str}",
        score=score,
        rank=0,
        source="vector",
        modality=modality,  # type: ignore[arg-type]
        image_path=image_path,
        metadata=_meta(),
    )


A = "00000000-0000-0000-0000-000000000001"
B = "00000000-0000-0000-0000-000000000002"
C = "00000000-0000-0000-0000-000000000003"


def _req(query: str = "test") -> SearchRequest:
    return SearchRequest(query=query, dataset_ids=[uuid.uuid4()])


# ---------- SimpleCite.__call__ ----------


def test_simple_cite_empty_returns_empty_list() -> None:
    cite = SimpleCite()
    assert cite([], _req()) == []


def test_simple_cite_single_doc_indexed_1() -> None:
    """1 doc → 1 citation with source_name='src-1'."""
    cite = SimpleCite()
    docs = [_doc(A)]
    result = cite(docs, _req())
    assert len(result) == 1
    assert result[0].source_name == "src-1"


def test_simple_cite_preserves_doc_order() -> None:
    """3 docs → citations in same order, numbered 1, 2, 3."""
    cite = SimpleCite()
    docs = [_doc(A), _doc(B), _doc(C)]
    result = cite(docs, _req())
    assert len(result) == 3
    assert [c.source_name for c in result] == ["src-1", "src-2", "src-3"]
    assert [c.chunk_id for c in result] == [
        uuid.UUID(A),
        uuid.UUID(B),
        uuid.UUID(C),
    ]


def test_simple_cite_copies_chunk_and_dataset_id() -> None:
    cite = SimpleCite()
    ds = uuid.uuid4()
    docs = [_doc(A, dataset_id=ds)]
    result = cite(docs, _req())
    assert result[0].chunk_id == uuid.UUID(A)
    assert result[0].dataset_id == ds


def test_simple_cite_copies_text_to_content() -> None:
    cite = SimpleCite()
    docs = [_doc(A, text="specific content")]
    result = cite(docs, _req())
    assert result[0].content == "specific content"


def test_simple_cite_copies_score() -> None:
    cite = SimpleCite()
    docs = [_doc(A, score=0.88)]
    result = cite(docs, _req())
    assert result[0].score == 0.88


def test_simple_cite_text_chunk_has_no_image_path() -> None:
    cite = SimpleCite()
    docs = [_doc(A, modality="text", image_path=None)]
    result = cite(docs, _req())
    assert result[0].image_path is None


def test_simple_cite_image_caption_keeps_image_path() -> None:
    """image_caption modality 保留 image_path, 不丢。"""
    cite = SimpleCite()
    docs = [_doc(A, modality="image_caption", image_path="/img/foo.png")]
    result = cite(docs, _req())
    assert result[0].image_path == "/img/foo.png"


def test_simple_cite_default_position_is_none() -> None:
    """resolve_citation_positions 之前, position=None。"""
    cite = SimpleCite()
    docs = [_doc(A)]
    result = cite(docs, _req())
    assert result[0].position is None


# ---------- SimpleCite: custom source_name_fn ----------


def test_simple_cite_custom_source_name_fn() -> None:
    """用户可注入 source_name_fn 定制命名。"""

    def name_fn(doc: ScoredDocument, idx: int) -> str:
        return f"chunk-{idx}-{doc.chunk_id.hex[:6]}"

    cite = SimpleCite(source_name_fn=name_fn)
    docs = [_doc(A), _doc(B)]
    result = cite(docs, _req())
    assert result[0].source_name.startswith("chunk-1-")
    assert result[1].source_name.startswith("chunk-2-")


def test_simple_cite_source_name_fn_receives_correct_index() -> None:
    captured: list[tuple[ScoredDocument, int]] = []

    def name_fn(doc: ScoredDocument, idx: int) -> str:
        captured.append((doc, idx))
        return f"src-{idx}"

    cite = SimpleCite(source_name_fn=name_fn)
    docs = [_doc(A), _doc(B), _doc(C)]
    cite(docs, _req())
    assert [idx for _, idx in captured] == [1, 2, 3]


# ---------- parse_inline_citations ----------


def test_parse_inline_citations_single_marker() -> None:
    assert parse_inline_citations("a [1](CITE) b") == [1]


def test_parse_inline_citations_multiple_distinct() -> None:
    assert parse_inline_citations("a [1](CITE) b [2](CITE) c") == [1, 2]


def test_parse_inline_citations_repeated_id_kept() -> None:
    """同一 id 多次出现, 全部捕获 (不 dedup)。"""
    assert parse_inline_citations("[3](CITE) and [3](CITE) again") == [3, 3]


def test_parse_inline_citations_no_markers() -> None:
    assert parse_inline_citations("no citations here") == []


def test_parse_inline_citations_empty_string() -> None:
    assert parse_inline_citations("") == []


def test_parse_inline_citations_multi_digit_id() -> None:
    """多位数 id 也支持 ([10], [100])。"""
    assert parse_inline_citations("[10](CITE) and [100](CITE)") == [10, 100]


def test_parse_inline_citations_ignores_other_patterns() -> None:
    """其他 markdown 链接 (非 CITE) 不被识别。"""
    assert parse_inline_citations("[1](http://example.com) [2](CITE)") == [2]


# ---------- resolve_citation_positions ----------


def test_resolve_positions_empty_response() -> None:
    citations = [
        Citation(
            chunk_id=uuid.UUID(A),
            dataset_id=uuid.uuid4(),
            source_name="src-1",
            content="x",
            score=0.5,
        )
    ]
    result = resolve_citation_positions("", citations)
    # No markers → position stays None
    assert result[0].position is None


def test_resolve_positions_empty_citations() -> None:
    result = resolve_citation_positions("anything [1](CITE)", [])
    assert result == []


def test_resolve_positions_single_marker_first_offset() -> None:
    citations = [
        Citation(
            chunk_id=uuid.UUID(A),
            dataset_id=uuid.uuid4(),
            source_name="src-1",
            content="x",
            score=0.5,
        )
    ]
    response = "a [1](CITE) b"
    # '[1](CITE)' starts at offset 2
    result = resolve_citation_positions(response, citations)
    assert result[0].position == 2


def test_resolve_positions_multiple_markers() -> None:
    """[1](CITE) at offset 2, [2](CITE) at offset 14 (after 'a ' + '[1](CITE) b ')."""
    citations = [
        Citation(
            chunk_id=uuid.UUID(A),
            dataset_id=uuid.uuid4(),
            source_name="src-1",
            content="x",
            score=0.5,
        ),
        Citation(
            chunk_id=uuid.UUID(B),
            dataset_id=uuid.uuid4(),
            source_name="src-2",
            content="y",
            score=0.5,
        ),
    ]
    response = "a [1](CITE) b [2](CITE) c"
    result = resolve_citation_positions(response, citations)
    assert result[0].position == 2
    assert result[1].position == 14


def test_resolve_positions_first_occurrence_wins() -> None:
    """同一 id 多次出现, position 取首次 offset。"""
    citations = [
        Citation(
            chunk_id=uuid.UUID(A),
            dataset_id=uuid.uuid4(),
            source_name="src-1",
            content="x",
            score=0.5,
        )
    ]
    response = "first [1](CITE) middle again [1](CITE) end"
    # First [1](CITE) at offset 6
    result = resolve_citation_positions(response, citations)
    assert result[0].position == 6


def test_resolve_positions_unreferenced_citation() -> None:
    """未在 response 出现的 citation → position=None。"""
    citations = [
        Citation(
            chunk_id=uuid.UUID(A),
            dataset_id=uuid.uuid4(),
            source_name="src-1",
            content="x",
            score=0.5,
        ),
        Citation(
            chunk_id=uuid.UUID(B),
            dataset_id=uuid.uuid4(),
            source_name="src-2",
            content="y",
            score=0.5,
        ),
    ]
    response = "only [1](CITE) mentioned"
    result = resolve_citation_positions(response, citations)
    assert result[0].position == 5  # [1] at offset 5 (after "only ")
    assert result[1].position is None  # [2] not referenced


def test_resolve_positions_id_out_of_range() -> None:
    """id 超过 len(citations) 不会被识别 (regex 找不到对应 Citation)。"""
    citations = [
        Citation(
            chunk_id=uuid.UUID(A),
            dataset_id=uuid.uuid4(),
            source_name="src-1",
            content="x",
            score=0.5,
        )
    ]
    response = "[1](CITE) and [99](CITE)"
    result = resolve_citation_positions(response, citations)
    assert result[0].position == 0
    # [99] has no Citation at index 98 — silently ignored


def test_resolve_positions_does_not_mutate_input() -> None:
    """Input citations 不被修改, 返回新 list。"""
    citations = [
        Citation(
            chunk_id=uuid.UUID(A),
            dataset_id=uuid.uuid4(),
            source_name="src-1",
            content="x",
            score=0.5,
        )
    ]
    response = "[1](CITE)"
    result = resolve_citation_positions(response, citations)
    # Original unchanged
    assert citations[0].position is None
    # Result has position
    assert result[0].position == 0
    # Different identity (Pydantic model_copy)
    assert result[0] is not citations[0]


# ---------- Round-trip: SimpleCite → resolve_citation_positions ----------


def test_simple_cite_then_resolve_round_trip() -> None:
    """SimpleCite 出来的 citations 顺序与 [id](CITE) 一一对应。"""
    cite = SimpleCite()
    docs = [_doc(A), _doc(B), _doc(C)]
    citations = cite(docs, _req())
    # LLM 引用 [2](CITE) 和 [3](CITE) (跳过了 [1])
    response = "see [2](CITE) and [3](CITE) for more"
    result = resolve_citation_positions(response, citations)
    # [2] is src-2 (B), [3] is src-3 (C)
    assert result[1].source_name == "src-2"
    assert result[1].position is not None
    assert result[2].source_name == "src-3"
    assert result[2].position is not None
    # [1] (src-1, A) 未引用
    assert result[0].position is None
