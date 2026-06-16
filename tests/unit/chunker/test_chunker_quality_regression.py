"""Chunk 切分质量回归: 对话导出型 Markdown fixture before/after 对比。

NOTE: measure_chunks (依赖已删除的 ChunkMetadata.total_chunks/valid_len 等字段)
已随 T3.3 删除, 本文件仅保留与字段无关的 basic smoke test。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data import (  # noqa: E402
    SAMPLE_CHAT_EXPORT_MD,
    SAMPLE_MD,
)
from rag.ingest.chunker import Chunker  # noqa: E402
from rag.ingest.chunker.settings import ChunkSettings  # noqa: E402


def _split_file(path: Path, settings: ChunkSettings | None = None) -> list:
    text = path.read_text(encoding="utf-8")
    return Chunker(settings or ChunkSettings()).split(text)


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
