"""docx 格式适配器: mammoth 抽 HTML 后再由 ``html_to_md`` 转 markdown。"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO

import mammoth

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.html2md import html_to_md
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

# docx 文件 mime (OOXML 官方命名)
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_PARSE_ERROR_MSG = "Can not read doc file, please convert to PDF"


async def docx_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",  # noqa: ARG001 — 保留签名 (docx 主体是 XML)
) -> FormatReaderResult:
    """将 docx 字节内容解析为 markdown 格式的 ``FormatReaderResult``。

    Args:
        buffer: docx 二进制内容。
        encoding: 文本编码 (docx 主体是 XML, 此参数保留以对齐 adapter 签名)。

    Returns:
        ``FormatReaderResult``:
        - ``raw_text``: markdown 文本。
        - ``format_text=None``。
        - ``meta.mime`` 为 docx 标准 mime。

    Raises:
        RAGError: ``code=READER_PARSE`` —— mammoth 解析失败。
    """
    def _run_mammoth() -> object:
        """在 worker 线程内同步执行 mammoth, 避免阻塞 event loop。"""
        try:
            return mammoth.convert_to_html(
                BytesIO(buffer), ignore_empty_paragraphs=False
            )
        except Exception as e:
            # 解析失败统一包成 READER_PARSE
            raise RAGError(
                code=ReaderErrorCode.PARSE,
                message=_PARSE_ERROR_MSG,
            ) from e

    mammoth_result: object = await asyncio.to_thread(_run_mammoth)
    # mammoth 返回 ``mammoth.results.Result`` (有 .value 属性)
    html = mammoth_result.value  # type: ignore[attr-defined]

    markdown = await html_to_md(html)

    return FormatReaderResult(
        raw_text=markdown,
        format_text=None,
        meta=DocMeta(mime=DOCX_MIME),
    )