"""txt + md extension adapter: 共享 ``read_raw_text``。

设计:
    - 同时支持 txt 和 md (没有单独的 md 处理模块);
    本模块沿用同样的合并方式, 单一 ``text_adapter`` 既处理 .txt 也处理 .md,
    mime 统一为 ``text/plain`` (下游 normalizer / chunker 按 mime 路由,
    不区分 txt vs md)。
    - 实际解码由 ``rag.ingest.reader.raw_text.read_raw_text`` 完成, 包括:
      * 多编码白名单 + ascii/非 ASCII 字节降级
      * markdown 内 ``data:image/...;base64,...`` 上传替换
    - 错误包装: ``UnicodeDecodeError`` 极少见 (``read_raw_text`` 已自带兜底),
      但仍保留以对齐其它 adapter 的 ``wrap_*_error`` 约定。
"""

from __future__ import annotations

import logging

from rag.ingest.reader.extensions.base import wrap_encoding_error, wrap_parse_error
from rag.ingest.reader.raw_text import UploadFileHandler, read_raw_text
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

TEXT_MIME = "text/plain"


async def text_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",
    upload_file: UploadFileHandler | None = None,
) -> FormatReaderResult:
    """bytes → ``FormatReaderResult`` (txt + md 共享实现)。

    Args:
        buffer: 文件二进制内容。
        encoding: 文本编码 (默认 utf-8)。
        upload_file: 可选, markdown 内 base64 图的上传回调。

    Returns:
        ``FormatReaderResult``:
        - ``raw_text``: 解码后的文本
        - ``format_text=None``, ``images=[]``
        - ``meta``: ``mime='text/plain'`` + ``encoding`` + ``size_bytes``

    Raises:
        RAGError: 编码异常 → ``code=READER_ENCODING``;
        其它意外 → ``code=READER_PARSE``。
    """
    try:
        raw_text = await read_raw_text(
            buffer, encoding=encoding, upload_file=upload_file
        )
    except UnicodeDecodeError as e:
        # ``read_raw_text`` 内部已兜底, 这里是防御性捕获 (e.g. codecs 内部异常未吞)。
        raise wrap_encoding_error("<buffer:text>", e, "text/md") from e
    except Exception as e:
        raise wrap_parse_error("<buffer:text>", e, "text/md") from e

    return FormatReaderResult(
        raw_text=raw_text,
        format_text=None,
        images=[],
        meta=DocMeta(
            datasource="file",  # 占位, dispatch 覆盖
            mime=TEXT_MIME,
            encoding=encoding,
            size_bytes=len(buffer),
        ),
    )
