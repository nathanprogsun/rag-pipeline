"""html 格式适配器: ``read_raw_text`` 解码 + ``html_to_md`` 转 markdown。"""

from __future__ import annotations

import logging
from typing import Final, cast

from rag.ingest.reader.extensions.base import wrap_encoding_error, wrap_parse_error
from rag.ingest.reader.html2md import html_to_md
from rag.ingest.reader.raw_text import UploadFileHandler as _RawUploadHandler
from rag.ingest.reader.raw_text import read_raw_text
from rag.ingest.reader.types import FormatReaderResult, UploadFileHandler
from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

# html 的标准 mime。
HTML_MIME: Final[str] = "text/html"


async def html_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",
    upload_file: UploadFileHandler | None = None,
) -> FormatReaderResult:
    """将 html 字节内容解析为 markdown 的 ``FormatReaderResult``。

    Args:
        buffer: html 二进制内容。
        encoding: 文本编码, 默认 ``utf-8``。
        upload_file: 透传给 ``read_raw_text`` 与 ``html_to_md`` 的异步上传回调。

    Returns:
        ``FormatReaderResult``:
        - ``raw_text``: html → markdown 转换结果。
        - ``format_text=None``。
        - ``meta.mime = "text/html"``。

    Raises:
        RAGError: ``code=READER_ENCODING`` 解码失败; ``code=READER_PARSE``
            ``html_to_md`` 在有 ``upload_file`` 时异常。
    """
    try:
        # ``read_raw_text`` 的 ``upload_file`` 类型与本模块的 ``types.UploadFileHandler``
        # 名义不同但 TypedDict 兼容, 显式 cast
        raw_upload: _RawUploadHandler | None = (
            cast("_RawUploadHandler", upload_file) if upload_file is not None else None
        )
        html = await read_raw_text(buffer, encoding=encoding, upload_file=raw_upload)
        # html_to_md 已是 async, 直接 await
        markdown = await html_to_md(html, upload_file=upload_file)
    except UnicodeDecodeError as e:
        # ``read_raw_text`` 内部已兜底, 此处防御性捕获
        raise wrap_encoding_error("<buffer:html>", e, "html") from e
    except Exception as e:
        # ``html_to_md`` 在有 upload_file 时异常会向上抛,
        # 无 upload_file 时吞掉返回空字符串
        raise wrap_parse_error("<buffer:html>", e, "html") from e

    return FormatReaderResult(
        raw_text=markdown,
        format_text=None,
        meta=DocMeta(
            datasource="file",  # 占位, dispatch 覆盖
            mime=HTML_MIME,
            encoding=encoding,
            size_bytes=len(buffer),
        ),
        images=[],
        extras={},
    )
