"""read_url: URL -> TextDoc (httpx 拉 bytes + dispatch)。"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.dispatch import (
    EXTENSION_ADAPTERS,
    dispatch_bytes,
    filename_from_url,
)
from rag.ingest.types import TextDoc

logger = logging.getLogger(__name__)


async def read_url(
    url: str,
    *,
    max_size: int = 1_000_000_000,
    timeout_s: float = 600.0,
    encoding: str = "utf-8",
) -> TextDoc:
    """URL -> TextDoc。

    Args:
        url: 完整 http(s) URL。
        max_size: 字节上限, 超过抛 too_large。
        timeout_s: httpx 超时 (秒)。
        encoding: 文本类 adapter 的字符编码。

    Returns:
        解析后的 TextDoc。

    Raises:
        RAGError: ``code=reader.too_large`` — content-length 超过 max_size。
        RAGError: ``code=reader.parse`` — httpx 失败或状态码非 2xx。
    """
    timeout = httpx.Timeout(connect=10.0, read=timeout_s, write=10.0, pool=10.0)
    logger.info("reader.url.start url=%s", url)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        # HEAD 预检 (失败不阻塞, 让 GET 自己跑)
        try:
            head = await client.head(url, timeout=10.0)
            cl = head.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > max_size:
                raise RAGError(
                    code=ReaderErrorCode.TOO_LARGE,
                    message=f"{url}: file too large: {cl} > {max_size}",
                )
        except httpx.HTTPError:
            pass
        except RAGError:
            raise
        except Exception:
            pass

        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("reader.url.fail url=%s err=%s", url, e)
            raise RAGError(
                code=ReaderErrorCode.PARSE,
                message=f"{url}: httpx failed: {e}",
            ) from e
        except Exception as e:
            logger.warning("reader.url.fail url=%s err=%s", url, e)
            raise RAGError(
                code=ReaderErrorCode.PARSE,
                message=f"{url}: unexpected: {e}",
            ) from e

        buffer = resp.content
        content_type = resp.headers.get("content-type")
        final_url = str(resp.url)

    if len(buffer) > max_size:
        logger.warning("reader.url.fail url=%s err=too_large", url)
        raise RAGError(
            code=ReaderErrorCode.TOO_LARGE,
            message=f"{url}: file too large: {len(buffer)} > {max_size}",
        )

    extension = _infer_extension(final_url, content_type, buffer)
    logger.info(
        "reader.url.done url=%s bytes=%d ext=%s content_type=%s",
        final_url,
        len(buffer),
        extension,
        content_type,
    )
    return await dispatch_bytes(
        buffer=buffer,
        extension=extension,
        source=final_url,
        datasource="url",
        filename=filename_from_url(final_url),
        encoding=encoding,
    )


_CONTENT_TYPE_TO_EXT: dict[str, str] = {
    "text/plain": "txt",
    "text/markdown": "md",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/csv": "csv",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}


def _ext_from_content_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    ct = content_type.split(";", 1)[0].strip().lower()
    for mime, ext in _CONTENT_TYPE_TO_EXT.items():
        if ct == mime:
            return ext
    return None


def _ext_from_path(url: str) -> str | None:
    """路径后缀仅作 hint: 必须在 dispatch 已注册 adapter 时才采用。"""
    path = urlparse(url).path
    segment = path.rsplit("/", 1)[-1]
    if "." not in segment:
        return None
    ext = segment.rsplit(".", 1)[-1].lower()
    if ext in EXTENSION_ADAPTERS:
        return ext
    return None


def _sniff_extension(buffer: bytes) -> str | None:
    """按响应体 magic / 标签前缀推断格式 (URL 无可靠后缀时的兜底)。"""
    if not buffer:
        return None
    head = buffer[:512].lstrip()
    lower = head.lower()
    if head.startswith(b"%PDF"):
        return "pdf"
    if lower.startswith(b"<!doctype html") or lower.startswith(b"<html"):
        return "html"
    if lower.startswith(b"<?xml") and b"<html" in lower:
        return "html"
    return None


def _infer_extension(url: str, content_type: str | None, buffer: bytes) -> str:
    """URL 响应格式推断: Content-Type > body 嗅探 > 已知路径后缀 > txt。

    与本地 ``read_file`` 不同, URL 不依赖 ``.shtml`` / ``.php`` 等路径后缀;
    服务器返回 ``text/html`` 或 body 为 HTML 即走 html adapter。
    """
    ext = _ext_from_content_type(content_type)
    if ext is not None:
        return ext
    ext = _sniff_extension(buffer)
    if ext is not None:
        return ext
    ext = _ext_from_path(url)
    if ext is not None:
        return ext
    return "txt"
