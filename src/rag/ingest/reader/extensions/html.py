"""html 格式适配器: ``read_raw_text`` 解码 + ``html_to_md`` 转 markdown。"""

from __future__ import annotations

import logging
from typing import Final

from rag.ingest.reader.extensions.base import wrap_encoding_error, wrap_parse_error
from rag.ingest.reader.html2md import html_to_md
from rag.ingest.reader.raw_text import read_raw_text
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

HTML_MIME: Final[str] = "text/html"


async def html_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",
) -> FormatReaderResult:
    """将 html 字节内容解析为 markdown 的 ``FormatReaderResult``。

    Args:
        buffer: html 二进制内容。
        encoding: 文本编码, 默认 ``utf-8``。

    Returns:
        ``FormatReaderResult``:
        - ``raw_text``: html → markdown 转换结果。
        - ``format_text=None``。
        - ``meta.mime = "text/html"``。

    Raises:
        RAGError: ``code=READER_ENCODING`` 解码失败; ``code=READER_PARSE``
            ``html_to_md`` 异常。
    """
    try:
        html = await read_raw_text(buffer, encoding=encoding)
        markdown = await html_to_md(html)
    except UnicodeDecodeError as e:
        raise wrap_encoding_error("<buffer:html>", e, "html") from e
    except Exception as e:
        raise wrap_parse_error("<buffer:html>", e, "html") from e

    return FormatReaderResult(
        raw_text=markdown,
        format_text=None,
        meta=DocMeta(mime=HTML_MIME),
    )