"""extensions/docx 单元测试: mammoth html → html2md markdown。

覆盖:
- Section 5.1: mammoth.convert_to_html 调用
- Section 5.2: ignore_empty_paragraphs=False 选项
- Section 5.5: mammoth 失败 → RAGError with "Can not read doc file, please convert to PDF"

测试:
1. minimal: 真实 sample.docx (无图) → raw_text 含文本, meta 字段完整
2. corrupted_buffer: 损坏 docx → RAGError (Section 5.5)
3. ignore_empty_paragraphs_false: mammoth 选项对齐 Section 5.2
4. error_chain_preserved: 原始异常通过 ``raise ... from e`` 保留链路
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import mammoth
import pytest

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.extensions.docx import DOCX_MIME, docx_adapter

# tests/data/sample.docx 路径 (与 conftest.py 的 SAMPLE_DOCX 一致)
SAMPLE_DOCX = Path(__file__).resolve().parents[2] / "data" / "sample.docx"


# ── 1. minimal: 真实 sample.docx (无图) ──────────────────────────────────


@pytest.mark.asyncio
async def test_docx_extension_adapter_minimal() -> None:
    """真实 sample.docx (无图) → 走完整 mammoth → html2md 链路, raw_text 含中文段落。"""
    buf = SAMPLE_DOCX.read_bytes()
    result = await docx_adapter(buf)

    # Section 5.1: mammoth → html → markdown
    assert "Sample DOCX Document" in result.raw_text
    assert "Content of section A" in result.raw_text
    assert "测试 python-docx reader" in result.raw_text
    # meta 完整
    assert result.meta.mime == DOCX_MIME
    # docx 适配器只返 raw_text, 不返 format_text
    assert result.format_text is None


# ── 2. corrupted buffer → RAGError (Section 5.5 验收) ────────────────────


@pytest.mark.asyncio
async def test_docx_extension_adapter_corrupted_buffer() -> None:
    """损坏 docx (非合法 zip) → RAGError(READER_PARSE), message 含
    'Can not read doc file, please convert to PDF' (Section 5.5)。"""
    with pytest.raises(RAGError) as exc_info:
        await docx_adapter(b"this is definitely not a docx")
    assert exc_info.value.code == ReaderErrorCode.PARSE
    assert "Can not read doc file, please convert to PDF" in exc_info.value.message


# ── 3. ignore_empty_paragraphs=False 选项 (Section 5.2 验收) ─────────────


@pytest.mark.asyncio
async def test_docx_extension_adapter_ignore_empty_paragraphs_false() -> None:
    """mammoth.convert_to_html 收到的 options 至少含 ``ignore_empty_paragraphs=False`` (Section 5.2)。

    用 fake mammoth 捕获 kwargs 验证选项传得对。
    """
    fake_result = MagicMock()
    fake_result.value = "<p>hello</p>"

    captured_kwargs: dict[str, object] = {}

    def fake_convert_to_html(*args: object, **kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return fake_result

    with patch.object(
        mammoth,
        "convert_to_html",
        side_effect=fake_convert_to_html,
    ):
        result = await docx_adapter(b"x")

        # Section 5.2: ignore_empty_paragraphs 必传, 且 = False
        assert "ignore_empty_paragraphs" in captured_kwargs
        assert captured_kwargs["ignore_empty_paragraphs"] is False
        # 跑通
        assert "hello" in result.raw_text


# ── 4. 错误链路保留 (cause) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_docx_extension_adapter_error_chain_preserved() -> None:
    """mammoth 抛原始异常 → RAGError 通过 ``raise ... from e`` 保留链路。"""
    raw_err = ValueError("zip corrupt inside")
    with patch.object(
        mammoth,
        "convert_to_html",
        side_effect=raw_err,
    ):
        with pytest.raises(RAGError) as exc_info:
            await docx_adapter(b"x")
        assert exc_info.value.code == ReaderErrorCode.PARSE
        # 链式原因保留
        assert exc_info.value.__cause__ is raw_err