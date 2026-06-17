"""纯文本适配器: 同时支持 txt 与 md, 共享 ``read_raw_text`` 解码逻辑。"""

from __future__ import annotations

import logging

from rag.ingest.reader.extensions.base import wrap_encoding_error, wrap_parse_error
from rag.ingest.reader.raw_text import read_raw_text
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

TEXT_MIME = "text/plain"


async def text_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",
) -> FormatReaderResult:
    """将 txt/md 字节内容解析为 ``FormatReaderResult``。

    Args:
        buffer: 文件二进制内容。
        encoding: 文本编码, 默认 ``utf-8``。

    Returns:
        ``FormatReaderResult``:
        - ``raw_text``: 解码后的文本。
        - ``format_text=None``。
        - ``meta``: ``mime='text/plain'``。

    Raises:
        RAGError: 编码异常 → ``code=READER_ENCODING``;
            其它意外 → ``code=READER_PARSE``。
    """
    try:
        raw_text = await read_raw_text(buffer, encoding=encoding)
    except UnicodeDecodeError as e:
        # ``read_raw_text`` 内部已兜底, 此处防御性捕获
        raise wrap_encoding_error("<buffer:text>", e, "text/md") from e
    except Exception as e:
        raise wrap_parse_error("<buffer:text>", e, "text/md") from e

    return FormatReaderResult(
        raw_text=raw_text,
        format_text=None,
        meta=DocMeta(mime=TEXT_MIME),
    )
