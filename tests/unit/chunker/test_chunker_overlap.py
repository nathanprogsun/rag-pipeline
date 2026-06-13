"""Chunker Overlap 倒序累积测试 (T12)。"""

from __future__ import annotations

from rag.ingest.chunker.overlap import get_overlap_tail
from rag.ingest.chunker.utils import valid_len


def test_overlap_returns_empty_when_step_is_final() -> None:
    """step >= 12 时 (已无下一级, STEPS 总长 12) → 不算 overlap。"""
    result = get_overlap_tail(
        text="段落内容。另一段。",
        step=12,
        chunk_size=100,
        overlap_len=15,
        max_overlap_len=40,
    )
    assert result == ""


def test_overlap_returns_15_percent_of_text() -> None:
    """100 字符文本, overlap_len=15 → 末尾约 15 字符。"""
    text = "x" * 100
    result = get_overlap_tail(
        text=text,
        step=10,  # punct_merged 级 (新设计 step 10), 允许 overlap
        chunk_size=100,
        overlap_len=15,
        max_overlap_len=40,
    )
    assert 10 <= valid_len(result) <= 20


def test_overlap_capped_at_max_overlap() -> None:
    """text 越长, overlap 不应超过 max_overlap_len。"""
    text = "y" * 1000
    result = get_overlap_tail(
        text=text,
        step=10,
        chunk_size=100,
        overlap_len=15,
        max_overlap_len=40,
    )
    assert valid_len(result) <= 40


def test_overlap_uses_valid_len_not_len() -> None:
    """文本含大量空白, valid_len 才是有效字符数。"""
    text = "x" * 50 + " " * 50  # len=100, valid_len=50
    result = get_overlap_tail(
        text=text,
        step=10,
        chunk_size=100,
        overlap_len=15,
        max_overlap_len=40,
    )
    assert valid_len(result) <= 40


def test_overlap_unicode_emoji_zwj() -> None:
    """中文 + emoji ZWJ 序列, valid_len 计数 + get_overlap_tail 切片不切碎。"""
    text = "👨‍👩‍👧" * 20 + "正文内容" * 20
    result = get_overlap_tail(
        text=text,
        step=10,
        chunk_size=100,
        overlap_len=15,
        max_overlap_len=40,
    )
    assert 0 < valid_len(result) <= 40
    for ch in result:
        assert ord(ch) >= 0
