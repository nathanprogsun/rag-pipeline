"""PDF 格式适配器: pypdf 抽文本 + 简化版后处理。"""

from __future__ import annotations

import logging
from io import BytesIO

from pypdf import PdfReader

from rag.ingest.reader.extensions.base import wrap_parse_error
from rag.ingest.reader.pdf_text_postprocess import postprocess_lite_parse_pages
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

# PDF 的标准 mime。
PDF_MIME = "application/pdf"

_BATCH_SIZE = 100


async def pdf_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",  # noqa: ARG001 — 保留签名 (PDF 是二进制)
    upload_file: object | None = None,  # noqa: ARG001 — PDF 不抽图, 保留签名
) -> FormatReaderResult:
    """将 PDF 字节内容解析为 ``FormatReaderResult``。

    Args:
        buffer: PDF 二进制内容。
        encoding: 保留签名; PDF 是二进制, 无编码概念。
        upload_file: 保留签名; PDF 不抽图。

    Returns:
        ``FormatReaderResult { raw_text, format_text=None, meta, images=[] }``。

    Raises:
        RAGError: ``code=READER_PARSE`` —— pypdf 解析失败 (损坏 / 加密) 时包装。
    """
    try:
        reader = PdfReader(BytesIO(buffer))
        all_pages = list(reader.pages)
        page_texts: list[str] = []
        for start in range(0, len(all_pages), _BATCH_SIZE):
            batch = all_pages[start : start + _BATCH_SIZE]
            for page in batch:
                text = page.extract_text() or ""
                page_texts.append(text)
    except Exception as e:
        # pypdf 抛 PdfReadError / EmptyFileError / extract_text 失败等统一包装
        raise wrap_parse_error("<buffer:pdf>", e, "pypdf") from e

    raw_text = postprocess_lite_parse_pages(page_texts)

    paragraph_count = sum(1 for line in raw_text.split("\n") if line.strip())

    return FormatReaderResult(
        raw_text=raw_text,
        format_text=None,
        images=[],
        extras={},
        meta=DocMeta(
            datasource="file",  # 占位, dispatch 覆盖
            mime=PDF_MIME,
            encoding=encoding,  # 保留传入, postprocess 按 utf-8 处理
            size_bytes=len(buffer),
            page_count=len(all_pages),
            paragraph_count=paragraph_count,
        ),
    )
