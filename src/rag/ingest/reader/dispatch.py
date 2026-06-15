"""Reader dispatch: bytes + extension -> TextDoc, 补 DocMeta."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from rag.domain.enums import IngestDatasource
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
from rag.ingest.reader.types import FormatReaderResult, UploadFileHandler
from rag.ingest.types import TextDoc

logger = logging.getLogger(__name__)


# 所有 8 个 adapter 都是 ``async def``, 静态上看就是
# ``Callable[..., Awaitable[FormatReaderResult]]``, 不再需要
# ``inspect.iscoroutine`` 判断 sync/async。
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
    source: str,
    *,
    encoding: str = "utf-8",
    datasource: IngestDatasource = "file",
    filename: str | None = None,
    upload_file: UploadFileHandler | None = None,
) -> TextDoc:
    """按 extension 路由到 AsyncFormatAdapter, 构造 TextDoc。

    Args:
        buffer: 二进制内容 (path 读的或 url 拉的)
        extension: 后缀 (无 `.` 前缀, 已 lowercase)
        source: 来源标识 ('file:///abs/path' 或 'https://...')
        encoding: 文本类 adapter 的字符编码
        datasource: 'file' | 'url' (来自 `rag.domain.enums.IngestDatasource`)
        filename: 展示用文件名
        upload_file: 可选, async 上传回调 (docx 内嵌图, html base64 图)

    Returns:
        TextDoc { text=raw_text, format_text=adapter.format_text or None,
                 meta=full DocMeta, images=[...] }

    Raises:
        RAGError: ``code=reader.unsupported`` — 后缀无对应 adapter。
    """
    ext = extension.lower().lstrip(".")
    adapter = EXTENSION_ADAPTERS.get(ext)
    if adapter is None:
        raise RAGError(
            code=ReaderErrorCode.UNSUPPORTED,
            message=(
                f"{source}: only support .txt, .md, .html, .pdf, .docx, .pptx, "
                f".csv, .xlsx. '.{ext}' is not supported."
            ),
        )

    if ext in {"txt", "md", "html", "htm", "csv"}:
        # 文本类 adapter 内部 read_raw_text 会做编码探测 / ascii 降级 / 兜底
        pass

    logger.info("reader.start ext=%s size=%d encoding=%s", ext, len(buffer), encoding)
    # 所有 adapter 都是 async, 统一 ``await`` (不再用 ``inspect.iscoroutine``
    # 判断返回值)。``upload_file`` 仅在 adapter 接受该参数时透传 (xlsx 当前不
    # 接受, 用 ``inspect.signature`` 单点检测, 不再依赖返回值 sync/async)。
    if "upload_file" in inspect.signature(adapter).parameters:
        result = await adapter(buffer, encoding=encoding, upload_file=upload_file)
    else:
        result = await adapter(buffer, encoding=encoding)
    logger.info("reader.done ext=%s text_len=%d", ext, len(result.raw_text))

    full_meta = result.meta.model_copy(
        update={
            "datasource": datasource,
            "filename": filename,
            "source": source,
            "size_bytes": len(buffer),
        }
    )
    return TextDoc(
        text=result.raw_text,
        format_text=result.format_text,
        meta=full_meta,
        images=list(result.images),
    )


def filename_from_url(url: str) -> str:
    """从 URL 提取展示用文件名 (netloc + path)。"""
    parsed = urlparse(url)
    return (parsed.netloc + parsed.path) or url


__all__ = ["EXTENSION_ADAPTERS", "dispatch_bytes", "filename_from_url"]
