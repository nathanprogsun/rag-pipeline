"""Reader 扩展层: 格式适配器协议与错误包装工具。"""

from __future__ import annotations

from typing import Protocol

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.types import FormatReaderResult


class FormatAdapter(Protocol):
    """格式适配器协议: 字节 + 编码 → ``FormatReaderResult``。"""

    async def __call__(
        self,
        buffer: bytes,
        *,
        encoding: str = "utf-8",
    ) -> FormatReaderResult: ...


def wrap_encoding_error(source: str, exc: Exception, parser: str) -> RAGError:
    """将编码失败包装为 ``RAGError(code=READER_ENCODING)``。

    Args:
        source: 失败来源标识, 例如 ``"<buffer:csv>"``。
        exc: 底层异常。
        parser: 解析器名称, 用于错误信息定位。

    Returns:
        包装后的 ``RAGError`` 实例。
    """
    return RAGError(
        code=ReaderErrorCode.ENCODING,
        message=f"{source}: {parser} encoding failed: {exc}",
    )


def wrap_parse_error(source: str, exc: Exception, parser: str) -> RAGError:
    """将解析失败包装为 ``RAGError(code=READER_PARSE)``。

    Args:
        source: 失败来源标识, 例如 ``"<buffer:pdf>"``。
        exc: 底层异常。
        parser: 解析器名称, 用于错误信息定位。

    Returns:
        包装后的 ``RAGError`` 实例。
    """
    return RAGError(
        code=ReaderErrorCode.PARSE,
        message=f"{source}: {parser} parse failed: {exc}",
    )