"""read_file: 本地文件 -> TextDoc (同步包装 async dispatch)。

同时提供 ``read_to_buffer`` 供 ``IngestPipeline._read_file`` 复用读盘逻辑
(避免 `ingest/pipeline.py:119-154` 的重复)。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.dispatch import dispatch_bytes
from rag.ingest.types import TextDoc

logger = logging.getLogger(__name__)


def read_to_buffer(p: Path) -> bytes:
    """读本地文件到 bytes, 含路径校验与异常包装。

    纯文件 I/O, 不含 dispatch 逻辑, 供 ``read_file`` (sync) 与
    ``IngestPipeline._read_file`` (async) 复用。

    Args:
        p: 本地文件 ``Path``。

    Returns:
        文件字节内容。

    Raises:
        RAGError(code=reader.not_found): 路径不存在或非普通文件。
        RAGError(code=reader.permission): 无读权限。
        RAGError(code=reader.parse): 读盘 OSError。
    """
    if not p.exists():
        raise RAGError(
            code=ReaderErrorCode.NOT_FOUND,
            message=f"{p}: file does not exist",
        )
    if not p.is_file():
        raise RAGError(
            code=ReaderErrorCode.NOT_FOUND,
            message=f"{p}: not a regular file",
        )
    try:
        return p.read_bytes()
    except PermissionError as e:
        logger.warning("reader.file.fail path=%s err=%s", p, e)
        raise RAGError(
            code=ReaderErrorCode.PERMISSION,
            message=f"{p}: {e}",
        ) from e
    except OSError as e:
        logger.warning("reader.file.fail path=%s err=%s", p, e)
        raise RAGError(
            code=ReaderErrorCode.PARSE,
            message=f"{p}: {e}",
        ) from e


def read_file(path: str | Path) -> TextDoc:
    """本地文件 -> ``TextDoc`` (同步接口, 内部 ``asyncio.run`` 包装 async dispatch)。

    Args:
        path: 本地文件路径。

    Returns:
        解析后的 ``TextDoc``。

    Raises:
        RAGError: 见 ``read_to_buffer``; 外加 ``reader.unsupported`` (后缀无 adapter)。
    """
    p = Path(path)
    logger.debug("reader.file.start path=%s", p)
    buffer = read_to_buffer(p)
    logger.debug("reader.file.done path=%s bytes=%d", p, len(buffer))
    return asyncio.run(
        dispatch_bytes(
            buffer=buffer,
            extension=p.suffix,
            source=f"file://{p.resolve()}",
            datasource="file",
            filename=p.name,
        )
    )
