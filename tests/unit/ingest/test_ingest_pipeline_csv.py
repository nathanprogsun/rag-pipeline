"""IngestPipeline 端到端: csv 走完整链路。"""

from __future__ import annotations

from pathlib import Path

from ingest_helpers import run_ingest
from rag.ingest.chunker import Chunker, ChunkSettings
from rag.ingest.pipeline import IngestPipeline


def test_pipeline_csv_via_file_source(sample_csv: Path) -> None:
    pipeline = IngestPipeline(
        chunker=Chunker(
            ChunkSettings(chunk_size=200, max_chunk_size=800, min_chunk_size=50)
        )
    )
    result = run_ingest(pipeline, str(sample_csv))

    assert result.chunks
    assert result.doc_meta.filename == "sample.csv"
    assert result.doc_meta.mime == "text/csv"
    for c in result.chunks:
        assert c.metadata.heading_stack == []


def test_pipeline_csv_with_large_rows(tmp_path: Path) -> None:
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
    result = run_ingest(pipeline, str(p))

    assert len(result.chunks) >= 1
    assert result.doc_meta.filename == "big.csv"


def test_pipeline_csv_single_row(tmp_path: Path) -> None:
    p = tmp_path / "header_only.csv"
    p.write_text("a,b,c\n", encoding="utf-8")

    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=200)))
    result = run_ingest(pipeline, str(p))

    assert result.chunks
    assert result.doc_meta.filename == "header_only.csv"
    # get_format_text=True → Chunk.text 取 format_text (markdown 表格)
    full = "\n".join(c.text for c in result.chunks)
    assert "| a | b | c |" in full
