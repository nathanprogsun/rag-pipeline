"""Reader 公共 API: read_file / read_url + 类型导出。

架构:
    read_file(path) ─┐
                     ├─> dispatch_bytes(buffer, ext, source) ─> EXTENSION_ADAPTERS
    read_url(url)   ─┘                                          └─> 7 format adapters
"""

from __future__ import annotations

from rag.ingest.reader.dispatch import (
    EXTENSION_ADAPTERS,
    dispatch_bytes,
    filename_from_url,
)
from rag.ingest.reader.file import read_file
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.reader.url import read_url
from rag.ingest.types import DocMeta, TextDoc
