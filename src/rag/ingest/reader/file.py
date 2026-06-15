"""read_file: 本地文件 -> TextDoc (同步包装 async dispatch)。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.dispatch import dispatch_bytes
from rag.ingest.types import TextDoc

logger = logging.getLogger(__name__)


def read_file(path: str | Path) -> TextDoc:
    """本地文件 -> TextDoc。

    Args:
        path: 本地文件路径。

    Returns:
        解析后的 TextDoc。

    Raises:
        RAGError: ``code=reader.not_found`` — 文件不存在或非普通文件。
        RAGError: ``code=reader.permission`` — 无读权限。
        RAGError: ``code=reader.parse`` — 读盘 OSError。
        RAGError: ``code=reader.unsupported`` — 后缀无对应 adapter。
    """
    p = Path(path)
    logger.debug("reader.file.start path=%s", p)
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
        buffer = p.read_bytes()
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

    logger.debug("reader.file.done path=%s bytes=%d", p, len(buffer))
    # dispatch_bytes 是 async, 同步入口用 asyncio.run 包
    return asyncio.run(
        dispatch_bytes(
            buffer=buffer,
            extension=p.suffix,
            source=f"file://{p.resolve()}",
            datasource="file",
            filename=p.name,
        )
    )
