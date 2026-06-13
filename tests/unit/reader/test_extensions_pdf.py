"""``extensions/pdf.pdf_adapter`` 单元测试。

覆盖:
  - real_sample: 走真实 ``tests/data/sample.pdf`` 全链路
  - error_wrap: 损坏 buffer → RAGError(READER_PARSE)
  - postprocess_invocation: mock 后处理, 验证 PDF 文本传入
  - meta_fields: meta.mime / page_count / size_bytes / datasource
  - batch_100: 100+ 页分批 (mock 出 >= 100 页)
  - encoding_passed: encoding 参数传到 DocMeta
  - empty_pdf: 0 页 PDF → 正常返回空文本
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from data import SAMPLE_PDF
from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.extensions.pdf import PDF_MIME, pdf_adapter

# ── 真实 fixture ──


def test_pdf_extension_adapter_against_real_sample() -> None:
    """真实 sample.pdf (3 页) → meta 全字段 + raw_text 非空 + images=[]。"""
    buf = SAMPLE_PDF.read_bytes()
    result = asyncio.run(pdf_adapter(buf))

    assert result.meta.size_bytes == len(buf)
    assert result.meta.mime == PDF_MIME
    assert result.meta.mime == "application/pdf"
    assert result.meta.encoding == "utf-8"
    assert result.meta.datasource == "api"
    assert result.meta.page_count == 3
    assert isinstance(result.meta.page_count, int)
    assert isinstance(result.raw_text, str)
    # raw_text 应含 sample.pdf 文本内容
    assert len(result.raw_text) > 0
    # 薄封装: format_text/images 全空
    assert result.format_text is None
    assert result.images == []
    assert result.extras == {}


# ── 错误包装 ──


def test_pdf_extension_adapter_error_wrap_corrupt_buffer() -> None:
    """损坏 buffer → RAGError(READER_PARSE); message 含 'pypdf'。"""
    with pytest.raises(RAGError) as exc_info:
        asyncio.run(pdf_adapter(b"this is not a pdf"))
    assert exc_info.value.code == ReaderErrorCode.PARSE
    assert "pypdf" in exc_info.value.message


def test_pdf_extension_adapter_error_chain_preserved() -> None:
    """错误链路保留 (``__cause__`` 是底层异常)。"""
    try:
        asyncio.run(pdf_adapter(b"definitely not a pdf"))
    except RAGError as e:
        assert e.__cause__ is not None
    else:  # pragma: no cover
        pytest.fail("expected RAGError")


# ── mock postprocess invocation ──


def test_pdf_extension_adapter_postprocess_invocation() -> None:
    """mock 后处理函数, 验证 PDF 文本传入 (简化版, 字符串列表)。"""
    with (
        patch("rag.ingest.reader.extensions.pdf.PdfReader") as mock_reader_cls,
        patch(
            "rag.ingest.reader.extensions.pdf.postprocess_lite_parse_pages"
        ) as mock_postprocess,
    ):
        # 构造 2 页 mock PDF
        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Page 1 content"
        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = "Page 2 content"
        mock_instance = MagicMock()
        mock_instance.pages = [mock_page1, mock_page2]
        mock_reader_cls.return_value = mock_instance
        # postprocess 返回固定字符串
        mock_postprocess.return_value = "POSTPROCESSED TEXT"

        result = asyncio.run(pdf_adapter(b"%PDF-1.4\n"))

    # 验证 postprocess 被调用
    mock_postprocess.assert_called_once()
    # 验证传入参数: list[str] 形式, 长度 2
    call_args = mock_postprocess.call_args
    pages_arg = call_args.args[0]
    assert isinstance(pages_arg, list)
    assert len(pages_arg) == 2
    assert pages_arg[0] == "Page 1 content"
    assert pages_arg[1] == "Page 2 content"
    # 验证返回值
    assert result.raw_text == "POSTPROCESSED TEXT"
    assert result.meta.page_count == 2


# ── 100 页分批 ──


def test_pdf_extension_adapter_batch_100_pages() -> None:
    """≥100 页时仍能完整抽文本, postprocess 收到的 list 长度 = page_count。"""
    with (
        patch("rag.ingest.reader.extensions.pdf.PdfReader") as mock_reader_cls,
        patch(
            "rag.ingest.reader.extensions.pdf.postprocess_lite_parse_pages"
        ) as mock_postprocess,
    ):
        # 250 页, 触发 3 批 (100, 100, 50)
        pages: list[MagicMock] = []
        for i in range(250):
            mock_page = MagicMock()
            mock_page.extract_text.return_value = f"page {i}"
            pages.append(mock_page)
        mock_instance = MagicMock()
        mock_instance.pages = pages
        mock_reader_cls.return_value = mock_instance
        mock_postprocess.return_value = "out"

        result = asyncio.run(pdf_adapter(b"%PDF-1.4\n"))

    # page_count = 250
    assert result.meta.page_count == 250
    # postprocess 收到 250 个 string
    call_args = mock_postprocess.call_args
    pages_arg = call_args.args[0]
    assert len(pages_arg) == 250
    assert pages_arg[0] == "page 0"
    assert pages_arg[249] == "page 249"


# ── meta 字段 ──


def test_pdf_extension_adapter_meta_datasource_placeholder() -> None:
    """datasource='api' 占位, dispatch 层会覆盖。"""
    with (
        patch("rag.ingest.reader.extensions.pdf.PdfReader") as mock_reader_cls,
        patch(
            "rag.ingest.reader.extensions.pdf.postprocess_lite_parse_pages"
        ) as mock_postprocess,
    ):
        mock_instance = MagicMock()
        mock_instance.pages = []
        mock_reader_cls.return_value = mock_instance
        mock_postprocess.return_value = ""

        result = asyncio.run(pdf_adapter(b"%PDF-1.4\n"))
    assert result.meta.datasource == "api"


# ── encoding 参数 ──


def test_pdf_extension_adapter_encoding_passed_through() -> None:
    """encoding 参数传到 DocMeta.encoding (PDF 无编码, 保留签名)。"""
    with (
        patch("rag.ingest.reader.extensions.pdf.PdfReader") as mock_reader_cls,
        patch(
            "rag.ingest.reader.extensions.pdf.postprocess_lite_parse_pages"
        ) as mock_postprocess,
    ):
        mock_instance = MagicMock()
        mock_instance.pages = []
        mock_reader_cls.return_value = mock_instance
        mock_postprocess.return_value = ""

        result = asyncio.run(pdf_adapter(b"%PDF-1.4\n", encoding="gbk"))
    assert result.meta.encoding == "gbk"


# ── 空 PDF ──


def test_pdf_extension_adapter_empty_pdf() -> None:
    """0 页 PDF → page_count=0, raw_text="", 正常返回。"""
    with (
        patch("rag.ingest.reader.extensions.pdf.PdfReader") as mock_reader_cls,
        patch(
            "rag.ingest.reader.extensions.pdf.postprocess_lite_parse_pages"
        ) as mock_postprocess,
    ):
        mock_instance = MagicMock()
        mock_instance.pages = []
        mock_reader_cls.return_value = mock_instance
        mock_postprocess.return_value = ""

        result = asyncio.run(pdf_adapter(b"%PDF-1.4\n"))
    assert result.meta.page_count == 0
    assert result.raw_text == ""


# ── extract_text 抛错 → 错误包装 ──


def test_pdf_extension_adapter_extract_text_error_wrap() -> None:
    """``page.extract_text()`` 抛错 → 仍走 RAGError(READER_PARSE)。

    实际 pypdf 抽文本失败通常静默返回空串, 但万一抛错仍要包装。
    """
    with patch("rag.ingest.reader.extensions.pdf.PdfReader") as mock_reader_cls:
        mock_page = MagicMock()
        mock_page.extract_text.side_effect = RuntimeError("decode boom")
        mock_instance = MagicMock()
        mock_instance.pages = [mock_page]
        mock_reader_cls.return_value = mock_instance

        with pytest.raises(RAGError) as exc_info:
            asyncio.run(pdf_adapter(b"%PDF-1.4\n"))
    assert exc_info.value.code == ReaderErrorCode.PARSE
