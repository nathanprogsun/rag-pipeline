"""Reader dispatch: bytes + extension -> TextDoc, 补 DocMeta。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.extensions import (
    csv_adapter,
    docx_adapter,
    html_adapter,
    pdf_adapter,
    pptx_adapter,
    text_adapter,
    xlsx_adapter,
)
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import TextDoc

logger = logging.getLogger(__name__)

AsyncFormatAdapter = Callable[..., Awaitable[FormatReaderResult]]

EXTENSION_ADAPTERS: dict[str, AsyncFormatAdapter] = {
    "txt": text_adapter,
    "md": text_adapter,
    "html": html_adapter,
    "htm": html_adapter,
    "pdf": pdf_adapter,
    "docx": docx_adapter,
    "pptx": pptx_adapter,
    "csv": csv_adapter,
    "xlsx": xlsx_adapter,
}


async def dispatch_bytes(
    buffer: bytes,
    extension: str,
    *,
    encoding: str = "utf-8",
    filename: str | None = None,
) -> TextDoc:
    """按 extension 路由到 AsyncFormatAdapter, 构造 TextDoc。

    Args:
        buffer: 二进制内容 (path 读的或 url 拉的)
        extension: 后缀 (无 ``.`` 前缀, 已 lowercase)
        encoding: 文本类 adapter 的字符编码
        filename: 展示用文件名

    Returns:
        TextDoc { text=raw_text, format_text=adapter.format_text or None,
                 meta=DocMeta+filename }

    Raises:
        RAGError: ``code=reader.unsupported`` — 后缀无对应 adapter。
    """
    ext = extension.lower().lstrip(".")
    adapter = EXTENSION_ADAPTERS.get(ext)
    if adapter is None:
        raise RAGError(
            code=ReaderErrorCode.UNSUPPORTED,
            message=(
                f"{filename}: only support .txt, .md, .html, .pdf, .docx, .pptx, "
                f".csv, .xlsx. '.{ext}' is not supported."
            ),
        )

    logger.info("reader.start ext=%s size=%d encoding=%s", ext, len(buffer), encoding)
    result = await adapter(buffer, encoding=encoding)
    logger.info("reader.done ext=%s text_len=%d", ext, len(result.raw_text))

    full_meta = result.meta.model_copy(update={"filename": filename})
    return TextDoc(
        text=result.raw_text,
        format_text=result.format_text,
        meta=full_meta,
    )


def filename_from_url(url: str) -> str:
    """从 URL 提取展示用文件名 (netloc + path)。"""
    parsed = urlparse(url)
    return (parsed.netloc + parsed.path) or url


__all__ = ["EXTENSION_ADAPTERS", "dispatch_bytes", "filename_from_url"]
