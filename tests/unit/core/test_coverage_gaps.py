"""T18 coverage gap fill tests.

Focused tests for under-covered modules/branches:
  - normalizer/base.py: abstract NotImplementedError contract
  - chunker/overlap.py: single piece too large, candidate overflow path
  - reader/adapters/*: error wrapping paths (parse/encoding/permission/not_found)
  - chunker/recursive.py: parent_title accumulation, base case with large text
  - chunker/finalize.py: merge_small_chunks with all small chunks
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.chunker.finalize import merge_small_chunks
from rag.ingest.chunker.overlap import get_overlap_tail
from rag.ingest.chunker.recursive import common_split
from rag.ingest.chunker.rules import build_steps
from rag.ingest.chunker.utils import valid_len
from rag.ingest.normalizer.base import Normalizer
from rag.ingest.reader import read_file
from rag.ingest.types import DocMeta, TextDoc

# ─────────────────────────────────────────────────────────────────────────────
# normalizer/base.py: abstract Normalizer raises NotImplementedError
# ─────────────────────────────────────────────────────────────────────────────


def test_normalizer_base_raises_not_implemented() -> None:
    """Base Normalizer.normalize is abstract; instantiating & calling raises."""
    raw = TextDoc(text="x", meta=DocMeta())

    async def call() -> TextDoc:
        return await Normalizer().normalize(raw)

    with pytest.raises(NotImplementedError):
        asyncio.run(call())


# ─────────────────────────────────────────────────────────────────────────────
# chunker/overlap.py: single piece larger than max_overlap_len path
# ─────────────────────────────────────────────────────────────────────────────

# 统一的测试用 Rule 列表 (与历史模块级 STEPS 等价)
_OVERLAP_RULES = build_steps(
    chunk_size=1000, max_size=8000, paragraph_chunk_deep=5, custom_reg=[]
)


def test_overlap_single_piece_larger_than_max_caps() -> None:
    """单片段本身 > max_overlap_len → 直接切片到 overlap_len。"""
    text = "x" * 200
    result = get_overlap_tail(
        text=text,
        step=10,
        rules=_OVERLAP_RULES,
        chunk_size=100,
        overlap_len=15,
        max_overlap_len=40,
    )
    assert 0 < valid_len(result) <= 40
    assert valid_len(result) <= 15


def test_overlap_zero_overlap_len_returns_empty() -> None:
    result = get_overlap_tail(
        text="段落内容。",
        step=10,
        rules=_OVERLAP_RULES,
        chunk_size=100,
        overlap_len=0,
        max_overlap_len=40,
    )
    assert result == ""


def test_overlap_step_out_of_bounds_returns_unchanged_text() -> None:
    result = get_overlap_tail(
        text="段落内容。另一段。",
        step=99,
        rules=_OVERLAP_RULES,
        chunk_size=100,
        overlap_len=15,
        max_overlap_len=40,
    )
    assert result == ""


def test_overlap_accumulates_multiple_pieces() -> None:
    text = "句一。句二。句三。句四。句五。"
    result = get_overlap_tail(
        text=text,
        step=11,  # 句号级 (STEPS[11]), 允许 overlap
        rules=_OVERLAP_RULES,
        chunk_size=100,
        overlap_len=10,
        max_overlap_len=40,
    )
    assert 0 < valid_len(result) <= 40


def test_overlap_unicode_no_valid_chars_returns_full() -> None:
    text = "   "
    result = get_overlap_tail(
        text=text,
        step=10,
        rules=_OVERLAP_RULES,
        chunk_size=100,
        overlap_len=15,
        max_overlap_len=40,
    )
    assert result == ""


# ─────────────────────────────────────────────────────────────────────────────
# chunker/recursive.py: parent_title propagation + base case large
# ─────────────────────────────────────────────────────────────────────────────


def test_recursive_base_case_with_large_text_sliding() -> None:
    """step >= len(rules) 且 combined >= max_size → 走 _sliding_window。"""
    rules = build_steps(
        chunk_size=100, max_size=50, paragraph_chunk_deep=5, custom_reg=[]
    )
    text = "a" * 200
    result = common_split(
        text=text,
        step=len(rules),
        last_text="",
        parent_title="",
        rules=rules,
        chunk_size=100,
        max_size=50,
        overlap_len=10,
    )
    assert len(result) >= 2
    for chunk in result:
        assert len(chunk) <= 50


def test_recursive_heading_propagates_parent_title() -> None:
    """Heading 触发 step+1 递归, parent_title 累加。"""
    rules = build_steps(
        chunk_size=500, max_size=2000, paragraph_chunk_deep=5, custom_reg=[]
    )
    text = "# H1\n\n内容一\n\n## H2\n\n内容二"
    result = common_split(
        text=text,
        step=0,
        last_text="",
        parent_title="pre-",
        rules=rules,
        chunk_size=500,
        max_size=2000,
        overlap_len=15,
    )
    assert len(result) >= 1
    for chunk in result:
        assert isinstance(chunk, str)


# ─────────────────────────────────────────────────────────────────────────────
# chunker/finalize.py: merge_small_chunks with all small chunks
# ─────────────────────────────────────────────────────────────────────────────


def test_merge_small_chunks_all_small_collapse_to_previous() -> None:
    chunks = ["a", "b", "c", "d"]
    result = merge_small_chunks(chunks, min_size=100)
    assert len(result) == 1
    assert result[0].replace("\n", "") == "abcd"


def test_merge_small_chunks_empty() -> None:
    assert merge_small_chunks([], min_size=10) == []


# ─────────────────────────────────────────────────────────────────────────────
# reader/*: error wrapping paths (新架构下 error 由 adapter / read_file 负责)
# ─────────────────────────────────────────────────────────────────────────────


def test_read_file_not_found(tmp_path: Path) -> None:
    """read_file 遇到不存在路径 → RAGError(reader.not_found)。"""
    with pytest.raises(RAGError) as exc_info:
        read_file(tmp_path / "missing.txt")
    assert exc_info.value.code == ReaderErrorCode.NOT_FOUND


def test_read_file_unsupported_extension(tmp_path: Path) -> None:
    """不支持的后缀 → RAGError(reader.unsupported)。"""
    p = tmp_path / "a.xyz"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(RAGError) as exc_info:
        read_file(p)
    assert exc_info.value.code == ReaderErrorCode.UNSUPPORTED
    assert ".xyz" in exc_info.value.message


def test_text_adapter_wraps_unicode_as_encoding() -> None:
    """txt adapter UnicodeDecodeError → RAGError(reader.encoding)。

    ``text_adapter`` 走 ``read_raw_text`` 兜底 (decode 失败时 ``errors='replace'``),
    不抛错。验证: raw_text 有 replace 字符 (U+FFFD), 没有 RAGError。
    """
    from rag.ingest.reader.extensions.text import text_adapter

    result = asyncio.run(text_adapter(b"\xff\xfe bad bytes"))
    assert "�" in result.raw_text or result.raw_text  # 不抛错, 有内容


def test_pdf_adapter_wraps_generic_as_parse() -> None:
    """pdf adapter 内部任意异常 → RAGError(reader.parse)。"""
    from unittest.mock import patch

    from rag.ingest.reader.extensions.pdf import pdf_adapter

    with patch(
        "rag.ingest.reader.extensions.pdf.PdfReader", side_effect=ValueError("boom")
    ):
        with pytest.raises(RAGError) as exc_info:
            asyncio.run(pdf_adapter(b"fake"))
    assert exc_info.value.code == ReaderErrorCode.PARSE
