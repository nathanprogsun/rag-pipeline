"""IngestPipeline 端到端: docx 走完整链路 (reader + chunker + IngestResult)。

验证:
- chunks 非空
- doc_meta 字段正确 (mime / size / filename / datasource=file)
- ChunkMetadata 字段合理 (page_count=None)
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path

from rag.ingest.chunker import Chunker, ChunkSettings
from rag.ingest.pipeline import IngestPipeline
from rag.ingest.source import BufferSource, FileSource
from rag.ingest.types import IngestResult


def run_ingest(coro: Coroutine[object, object, IngestResult]) -> IngestResult:
    return asyncio.run(coro)


def test_pipeline_docx_via_file_source(sample_docx: Path) -> None:
    """docx fixture 通过 FileSource 走 ingest: chunks 非空 + doc_meta 字段。"""
    pipeline = IngestPipeline(
        chunker=Chunker(
            ChunkSettings(chunk_size=200, max_chunk_size=800, min_chunk_size=50)
        )
    )
    result = run_ingest(pipeline.ingest(FileSource(sample_docx)))

    # chunks 非空
    assert result.chunks
    # doc_meta
    assert result.doc_meta.datasource == "file"
    assert result.doc_meta.filename == "sample.docx"
    assert result.doc_meta.mime == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert result.doc_meta.size_bytes > 0
    # 每块 metadata
    for c in result.chunks:
        assert c.metadata.file_type == "docx"
        assert c.metadata.encoding == "utf-8"
        assert c.metadata.source.endswith("sample.docx")
        # docx 无 page_count (page_count 是 PDF 的概念)
        assert c.metadata.page_count is None


def test_pipeline_docx_chunk_metadata_has_no_heading(
    sample_docx: Path,
) -> None:
    """docx 通过 mammoth adapter 抽 markdown: heading 由 chunker per-chunk regex 兜底。

    这里正文较短, 不产生 heading 匹配; heading_stack 默认为空列表。
    验证 metadata 字段一致且全 chunks 结构合理。
    """
    pipeline = IngestPipeline(
        chunker=Chunker(
            ChunkSettings(chunk_size=200, max_chunk_size=800, min_chunk_size=50)
        )
    )
    result = run_ingest(pipeline.ingest(FileSource(sample_docx)))

    assert result.chunks
    # 所有 chunk 的 metadata 一致: file_type / encoding / source
    for c in result.chunks:
        assert c.metadata.file_type == "docx"
        assert c.metadata.encoding == "utf-8"
        assert c.metadata.source.endswith("sample.docx")
        # heading_stack 可能为空 (短文档不产生 heading 匹配)
        assert isinstance(c.metadata.heading_stack, list)
        assert c.metadata.has_code is False
        assert c.metadata.has_table is False
    # 至少 chunk_index / total_chunks 字段一致
    for i, c in enumerate(result.chunks):
        assert c.metadata.chunk_index == i
        assert c.metadata.total_chunks == len(result.chunks)
    # doc content 至少含 docx fixture 文本
    full = "\n".join(c.text for c in result.chunks)
    assert "sample docx" in full.lower() or "DOCX" in full


def test_pipeline_docx_via_buffer_source(sample_docx: Path) -> None:
    """docx bytes 通过 BufferSource 走 ingest: datasource='api', filename 从 source 推。"""
    pipeline = IngestPipeline(
        chunker=Chunker(
            ChunkSettings(chunk_size=200, max_chunk_size=800, min_chunk_size=50)
        )
    )
    buffer = sample_docx.read_bytes()
    result = run_ingest(
        pipeline.ingest(
            BufferSource(buf=buffer, file_type="docx", source="inline://doc.docx")
        )
    )

    assert result.chunks
    assert result.doc_meta.datasource == "file"
    assert result.doc_meta.filename == "doc.docx"
    # doc_meta.size_bytes 应等于 buffer 长度
    assert result.doc_meta.size_bytes == len(buffer)
    for c in result.chunks:
        assert c.metadata.file_type == "docx"
        assert c.metadata.source == "inline://doc.docx"


def test_pipeline_docx_short_text_no_chunks_warning(tmp_path: Path) -> None:
    """非常短的 docx (一句话) → 仍应至少 1 chunk, doc_meta 字段完整。"""
    import docx as pydocx

    p = tmp_path / "tiny.docx"
    d = pydocx.Document()
    d.add_heading("Tiny", level=1)
    d.add_paragraph("One short paragraph.")
    d.save(str(p))

    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=200)))
    result = run_ingest(pipeline.ingest(FileSource(p)))

    assert result.chunks
    assert result.doc_meta.filename == "tiny.docx"
    assert result.doc_meta.mime == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
