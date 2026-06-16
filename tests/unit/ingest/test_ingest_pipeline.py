from __future__ import annotations

import asyncio
from asyncio import CancelledError
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest import fixture

from ingest_helpers import run_ingest
from rag.ingest.chunker import Chunker, ChunkSettings
from rag.ingest.normalizer import NoOpNormalizer, StructureMode, StructureNormalizer
from rag.ingest.pipeline import IngestPipeline
from rag.ingest.types import IngestResult


def test_pipeline_txt_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("段落一。\n\n段落二。\n\n段落三。", encoding="utf-8")

    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=50)))
    result = run_ingest(pipeline, str(path))

    assert len(result.chunks) >= 1
    assert all(c.text.strip() for c in result.chunks)
    assert result.title == "a.txt"
    assert result.doc_meta.filename == "a.txt"


def test_pipeline_ingest_directory_expands(tmp_path: Path) -> None:
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text("# A\n\nbody a", encoding="utf-8")
    (d / "b.md").write_text("# B\n\nbody b", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=50)))
    outcome = asyncio.run(pipeline.ingest_many([str(d)]))
    assert len(outcome.items) == 2
    titles = {item.title for item in outcome.items}
    assert titles == {"A", "B"}


def test_pipeline_returns_chunk_objects(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text("# 标题\n\n内容。", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    result = run_ingest(pipeline, str(path))
    assert len(result.chunks) >= 1
    assert result.chunks[0].metadata.chunk_index == 0
    assert result.title == "标题"


def test_pipeline_populates_markdown_structure_metadata(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text(
        "# H1\n\n## H2\n\n正文含```python\nx=1\n``` 代码块。\n\n正文继续。",
        encoding="utf-8",
    )
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=200)))
    result = run_ingest(pipeline, str(path))
    assert len(result.chunks) >= 1
    chunks = result.chunks
    assert any(
        any("# H1" in h for h in c.metadata.heading_stack)
        and any("## H2" in h for h in c.metadata.heading_stack)
        for c in chunks
    )
    assert any(c.metadata.has_code is True for c in chunks)
    assert result.title == "H1"


def test_pipeline_txt_has_no_structure_metadata(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("plain text content", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    result = run_ingest(pipeline, str(path))
    assert len(result.chunks) >= 1
    for c in result.chunks:
        assert c.metadata.heading_stack == []
        assert c.metadata.has_code is False
        assert c.metadata.has_table is False
    assert result.title == "a.txt"


def _assert_doc_meta_injected(chunks: list, file_type: str, filename: str) -> None:
    for c in chunks:
        assert c.metadata.file_type == file_type
        assert c.metadata.source == filename
        assert c.metadata.encoding in ("utf-8", "utf8")
        assert c.metadata.chunk_index < c.metadata.total_chunks


def test_pipeline_injects_doc_meta_into_chunks(
    pipeline_e2e: IngestPipeline, sample_md: Path
) -> None:
    result = run_ingest(pipeline_e2e, str(sample_md))
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "md", "sample.md")
    assert any(
        any("Sample Markdown Document" in h for h in c.metadata.heading_stack)
        for c in chunks
    )
    assert any(c.metadata.has_code for c in chunks)
    assert any(c.metadata.has_table for c in chunks)
    assert result.title == "Sample Markdown Document"
    assert result.doc_meta.filename == "sample.md"


def test_pipeline_txt_no_structure_metadata(
    pipeline_e2e: IngestPipeline, sample_txt: Path
) -> None:
    result = run_ingest(pipeline_e2e, str(sample_txt))
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "txt", "sample.txt")
    for c in chunks:
        assert c.metadata.heading_stack == []
        assert c.metadata.has_code is False
        assert c.metadata.has_table is False
    assert result.title == "sample.txt"


def test_pipeline_pdf_injects_page_count(
    pipeline_e2e: IngestPipeline, sample_pdf: Path
) -> None:
    result = run_ingest(pipeline_e2e, str(sample_pdf))
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "pdf", "sample.pdf")
    for c in chunks:
        assert c.metadata.page_count == 3
    assert result.doc_meta.page_count == 3


def test_pipeline_html_extracts_headings(
    pipeline_e2e: IngestPipeline, sample_html: Path
) -> None:
    result = run_ingest(pipeline_e2e, str(sample_html))
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "html", "sample.html")
    assert result.title in ("Sample HTML Document", "sample.html")
    full = "\n".join(c.text for c in chunks)
    assert "Sample HTML Document" in full


def test_pipeline_without_normalizer_uses_noop() -> None:
    p = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=200)))
    assert isinstance(p.normalizer, NoOpNormalizer)


def test_pipeline_with_forbid_normalizer_does_not_call_llm(tmp_path: Path) -> None:
    from langchain_core.runnables import Runnable

    fake_model = MagicMock(spec=Runnable)
    p = IngestPipeline(
        chunker=Chunker(ChunkSettings(chunk_size=200)),
        normalizer=StructureNormalizer(
            chat_model=fake_model, mode=StructureMode.FORBID
        ),
    )
    f = tmp_path / "doc.txt"
    f.write_text(
        "hello content for testing pipeline normalizer integration", encoding="utf-8"
    )
    result = run_ingest(p, str(f))
    assert result.chunks
    fake_model.ainvoke.assert_not_called()
    fake_model.invoke.assert_not_called()


def test_pipeline_ingest_result_doc_meta_passthrough(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text("# Title\n\nbody", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    result = run_ingest(pipeline, str(path))
    assert result.doc_meta.filename == "a.md"
    assert result.doc_meta.encoding in ("utf-8", "utf8")


def test_pipeline_ingest_result_title_fallback_filename(tmp_path: Path) -> None:
    path = tmp_path / "no_heading.txt"
    path.write_text("plain content", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    result = run_ingest(pipeline, str(path))
    assert result.title == "no_heading.txt"
    assert result.warnings == []


@fixture
def pipeline_e2e() -> IngestPipeline:
    return IngestPipeline(
        chunker=Chunker(
            ChunkSettings(chunk_size=200, max_chunk_size=800, min_chunk_size=50)
        ),
        normalizer=NoOpNormalizer(),
    )


@pytest.mark.asyncio
async def test_ingest_many_propagates_cancelled_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CancelledError 是 BaseException, 不应被吞。"""
    f = tmp_path / "a.txt"
    f.write_text("hello")

    async def cancel(_: Path) -> IngestResult:
        raise CancelledError()

    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    monkeypatch.setattr(pipeline, "_process", cancel)
    with pytest.raises(CancelledError):
        await pipeline.ingest_many([str(f)])
