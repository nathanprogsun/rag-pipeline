"""Chunk 切分质量回归: 对话导出型 Markdown fixture before/after 对比。"""

from __future__ import annotations

from pathlib import Path

import pytest

from data import (  # noqa: E402
    SAMPLE_CHAT_EXPORT_MD,
    SAMPLE_MD,
)
from rag.ingest.chunker import Chunker  # noqa: E402
from rag.ingest.chunker.quality import measure_chunks  # noqa: E402
from rag.ingest.chunker.settings import ChunkSettings  # noqa: E402

# 优化前实测 (约 4000 字对话导出 Markdown) 的参考基线
_LEGACY_BASELINE_CHUNK_COUNT = 38
_LEGACY_BASELINE_AVG_VALID_LEN = 100
_LEGACY_BASELINE_HEADING_COVERAGE = 0.15
_LEGACY_BASELINE_BAD_BOUNDARY_RATE = 0.40


def _split_file(path: Path, settings: ChunkSettings | None = None) -> list:
    text = path.read_text(encoding="utf-8")
    return Chunker(settings or ChunkSettings()).split(text)


def test_chat_export_optimized_chunk_count() -> None:
    chunks = _split_file(SAMPLE_CHAT_EXPORT_MD)
    metrics = measure_chunks(chunks, ChunkSettings().chunk_size)
    assert 2 <= metrics.chunk_count <= 12
    assert metrics.chunk_count < _LEGACY_BASELINE_CHUNK_COUNT


def test_chat_export_optimized_avg_valid_len() -> None:
    chunks = _split_file(SAMPLE_CHAT_EXPORT_MD)
    metrics = measure_chunks(chunks, ChunkSettings().chunk_size)
    assert metrics.avg_valid_len >= 400
    assert metrics.avg_valid_len > _LEGACY_BASELINE_AVG_VALID_LEN


def test_chat_export_heading_stack_coverage() -> None:
    chunks = _split_file(SAMPLE_CHAT_EXPORT_MD)
    metrics = measure_chunks(chunks, ChunkSettings().chunk_size)
    assert metrics.heading_stack_coverage >= 0.5
    assert metrics.heading_stack_coverage > _LEGACY_BASELINE_HEADING_COVERAGE


def test_chat_export_bad_boundary_rate() -> None:
    chunks = _split_file(SAMPLE_CHAT_EXPORT_MD)
    metrics = measure_chunks(chunks, ChunkSettings().chunk_size)
    assert metrics.bad_boundary_rate <= 0.15
    assert metrics.bad_boundary_rate < _LEGACY_BASELINE_BAD_BOUNDARY_RATE


def test_short_sample_md_does_not_over_merge() -> None:
    """短文档不应被 merge-to-target 膨胀成巨型单块。"""
    chunks = _split_file(SAMPLE_MD)
    assert 1 <= len(chunks) <= 8


@pytest.mark.parametrize(
    "path",
    [SAMPLE_CHAT_EXPORT_MD],
)
def test_code_fence_intact_in_chunk(path: Path) -> None:
    """fenced code block 的 ``` 开闭应在同一 chunk 内。"""
    chunks = _split_file(path)
    for c in chunks:
        if "```" in c.text:
            assert c.text.count("```") % 2 == 0
