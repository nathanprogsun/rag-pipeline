"""Reader extensions: FormatAdapter 协议 + 错误包装。

:
 - 每个 FormatAdapter 是 ``async def (buffer, *, encoding, upload_file) -> FormatReaderResult``
 - 返回值走 ``FormatReaderResult`` (raw_text / format_text / meta / images)
 - 失败抛 ``RAGError`` 子类 (由 ``wrap_parse_error`` / ``wrap_encoding_error`` 包装)

``UploadedFileResult`` / ``UploadFileHandler`` / ``FormatReaderResult``
single-source 统一从 ``rag.ingest.reader.types`` 导入, 避免双份定义飘移。
``structure`` 字段已从 ``FormatReaderResult`` 移除 (doc-level structure
不再由 reader 抽取, chunker 内部 per-chunk regex 现场重算 heading_stack)。
"""

from __future__ import annotations

from typing import Protocol

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.types import (
    FormatReaderResult,
    UploadFileHandler,
)
from rag.ingest.reader.types import (
    UploadedFileResult as UploadedFileResult,
)

# 单源 re-export: 避免下游 `from rag.ingest.reader.extensions.base import`
# FormatReaderResult 时与 reader/types.py 飘移。


class FormatAdapter(Protocol):
    """Adapter 协议: 字节 + 扩展名 → FormatReaderResult."""

    async def __call__(
        self,
        buffer: bytes,
        *,
        encoding: str = "utf-8",
        upload_file: UploadFileHandler | None = None,
    ) -> FormatReaderResult: ...


def wrap_encoding_error(source: str, exc: Exception, parser: str) -> RAGError:
    """编码失败包装 → RAGError(READER_ENCODING)."""
    return RAGError(
        code=ReaderErrorCode.ENCODING,
        message=f"{source}: {parser} encoding failed: {exc}",
    )


def wrap_parse_error(source: str, exc: Exception, parser: str) -> RAGError:
    """解析失败包装 → RAGError(READER_PARSE)."""
    return RAGError(
        code=ReaderErrorCode.PARSE,
        message=f"{source}: {parser} parse failed: {exc}",
    )
