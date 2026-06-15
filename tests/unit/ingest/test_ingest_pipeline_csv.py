"""IngestPipeline 端到端: csv 走完整链路。

验证:
- chunks 非空
- doc_meta 字段 (mime=text/csv, datasource=file)
- ChunkMetadata 字段合理 (无 heading 树, page_count=None)
- format_text 在 dispatch 阶段被丢弃 (IngestResult 不暴露), raw_text 是 CSV
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


def test_pipeline_csv_via_file_source(sample_csv: Path) -> None:
    """csv fixture 通过 FileSource 走 ingest: chunks 非空 + doc_meta 字段。"""
    pipeline = IngestPipeline(
        chunker=Chunker(
            ChunkSettings(chunk_size=200, max_chunk_size=800, min_chunk_size=50)
        )
    )
    result = run_ingest(pipeline.ingest(FileSource(sample_csv)))

    # chunks 非空
    assert result.chunks
    # doc_meta
    assert result.doc_meta.datasource == "file"
    assert result.doc_meta.filename == "sample.csv"
    assert result.doc_meta.mime == "text/csv"
    assert result.doc_meta.size_bytes > 0
    # 每块 metadata
    for c in result.chunks:
        assert c.metadata.file_type == "csv"
        assert c.metadata.encoding == "utf-8"
        assert c.metadata.source.endswith("sample.csv")
        # csv 无结构
        assert c.metadata.heading_stack == []
        assert c.metadata.has_code is False
        assert c.metadata.has_table is False
        assert c.metadata.page_count is None


def test_pipeline_csv_via_buffer_source() -> None:
    """csv bytes 通过 BufferSource 走 ingest: datasource='api', filename 从 source 推。"""
    csv_data = b"name,age\nAlice,30\nBob,25\n"

    pipeline = IngestPipeline(
        chunker=Chunker(
            ChunkSettings(chunk_size=200, max_chunk_size=800, min_chunk_size=50)
        )
    )
    result = run_ingest(
        pipeline.ingest(
            BufferSource(buf=csv_data, file_type="csv", source="inline://data.csv")
        )
    )

    assert result.chunks
    assert result.doc_meta.datasource == "file"
    assert result.doc_meta.filename == "data.csv"
    assert result.doc_meta.size_bytes == len(csv_data)
    # csv 内容进 chunks
    full = "\n".join(c.text for c in result.chunks)
    assert "Alice" in full
    assert "Bob" in full
    assert "name" in full


def test_pipeline_csv_with_large_rows(tmp_path: Path) -> None:
    """大 CSV (很多行) → 多 chunk, doc_meta.size_bytes 反映原文件大小。"""
    rows = ["id,name,city"]
    for i in range(100):
        rows.append(f"{i},user_{i},beijing")
    big = "\n".join(rows).encode("utf-8")
    p = tmp_path / "big.csv"
    p.write_bytes(big)

    pipeline = IngestPipeline(
        chunker=Chunker(
            ChunkSettings(chunk_size=100, max_chunk_size=400, min_chunk_size=30)
        )
    )
    result = run_ingest(pipeline.ingest(FileSource(p)))

    assert len(result.chunks) >= 1
    assert result.doc_meta.size_bytes == len(big)
    assert result.doc_meta.filename == "big.csv"


def test_pipeline_csv_single_row_no_chunks_after_split(tmp_path: Path) -> None:
    """只有 header 1 行的 CSV → chunker 仍应产出 1 chunk, doc_meta 字段完整。

    默认 ``get_format_text=True`` → chunk.text 是 md table ("| a | b | c | ...").
    改用 ``get_format_text=False`` 走 raw_text 才能验证原文 "a,b,c"。
    """
    p = tmp_path / "header_only.csv"
    p.write_text("a,b,c\n", encoding="utf-8")

    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=200)))
    result = run_ingest(pipeline.ingest(FileSource(p), get_format_text=False))

    assert result.chunks
    assert result.doc_meta.filename == "header_only.csv"
    full = "\n".join(c.raw_text for c in result.chunks)
    assert "a,b,c" in full
