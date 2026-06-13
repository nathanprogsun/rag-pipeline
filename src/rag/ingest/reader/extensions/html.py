"""html extension adapter: ``read_raw_text`` → ``html_to_md`` → markdown。

6.1 ``read_raw_text`` 解码 buffer 拿 html 字符串
6.2 ``await html_to_md(html, upload_file)`` 拿 markdown (async 路径, 内部直接 await upload)
6.3 返回 ``FormatReaderResult(raw_text=markdown, format_text=None, ...)``;
**只** ``rawText``, **无** ``formatText`` (Section 6.3 明确)
6.4 mime ``text/html``
6.5 错误: ``wrap_parse_error`` / ``wrap_encoding_error``

设计:
- 与 ``adapters/html`` (旧 BS4 strip) 不同: 本模块走 ``html2md`` (Turndown 等价)
  + ``read_raw_text`` (含 base64 图抽取), 完整保留 html 处理链。
- ``html_to_md`` 为 async, base64 上传直接 await,
  不嵌套 ``asyncio.run``。
- 错误包装: ``html_to_md`` 在无 ``upload_file`` 时返回空字符串而不抛,
  但 ``read_raw_text`` 的 decode / base64 处理可能仍抛。
- ``structure`` / ``images`` / ``extras`` 留空 (html 解析阶段不抽结构,
  文档级结构由 chunker 内部 per-chunk 重算)。
"""

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

# html mime (RFC 2854 + IANA 注册名)。
HTML_MIME: Final[str] = "text/html"


async def html_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",
    upload_file: UploadFileHandler | None = None,
) -> FormatReaderResult:
    """bytes → ``FormatReaderResult`` (html 专属, raw_text = markdown, 无 format_text)。

    Args:
        buffer: html 二进制内容。
        encoding: 文本编码 (默认 utf-8)。
        upload_file: 透传给 ``read_raw_text`` 与 ``html_to_md`` 的异步上传回调。

    Returns:
        ``FormatReaderResult { raw_text, format_text=None, meta,
        images=[], extras={} }``:
        - ``raw_text``: html → markdown 转换结果
        - ``format_text=None`` (Section 6.3 明确)
        - ``meta.mime = "text/html"``

    Raises:
        RAGError: ``code=READER_ENCODING`` (decode 失败) /
            ``code=READER_PARSE`` (html_to_md 在有 upload_file 时异常)。
    """
    try:
        # 6.1: decode + markdown base64 图抽取
        # ``read_raw_text`` 的 ``upload_file`` 类型是 R1-A ``raw_text.UploadFileHandler``
        # (返回 ``UploadedFileResult``), 与本模块的 ``types.UploadFileHandler`` (返回
        # ``dict[str, str]``) 名义不同但 TypedDict 兼容, 显式 cast。
        raw_upload: _RawUploadHandler | None = (
            cast("_RawUploadHandler", upload_file) if upload_file is not None else None
        )
        html = await read_raw_text(buffer, encoding=encoding, upload_file=raw_upload)
        # 6.2: HTML → markdown (含媒体重写、标签删除、Turndown 配置、base64 上传)
        # html_to_md 已是 async, 直接 await 即可。
        markdown = await html_to_md(html, upload_file=upload_file)
    except UnicodeDecodeError as e:
        # ``read_raw_text`` 内部已兜底, 这里是防御性捕获。
        raise wrap_encoding_error("<buffer:html>", e, "html") from e
    except Exception as e:
        # ``html_to_md`` 在有 upload_file 时异常会向上抛, 在无 upload_file 时吞掉返回 ``""``。
        raise wrap_parse_error("<buffer:html>", e, "html") from e

    return FormatReaderResult(
        raw_text=markdown,
        format_text=None,  # Section 6.3 明确: 只 raw_text, 无 format_text
        meta=DocMeta(
            datasource="api",  # 占位, dispatch 覆盖
            mime=HTML_MIME,
            encoding=encoding,
            size_bytes=len(buffer),
        ),
        images=[],
        extras={},
    )
