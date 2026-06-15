"""IngestSource tagged union: 收敛三段入口 (file / url / buffer) 为单一类型。

设计:
- 三个 ``frozen=True`` dataclass 表示三段入口:
  * ``FileSource``    本地文件 path
  * ``UrlSource``     任意公网 URL, 内部走 read_url
  * ``BufferSource``  内存 bytes + file_type, 用于把外部已读取的字节流送进 pipeline
- ``IngestSource = FileSource | UrlSource | BufferSource`` 作为
  ``pipeline.ingest`` 的入参类型, 在 ``isinstance`` 分发后决定调 ``read_file`` /
  ``read_url`` / ``dispatch_bytes``。

为什么用 tagged union 而非枚举:
- dataclass 携带的实际参数 (path / url / buffer) 各不相同, 单一 enum 字段塞不下。
- 顶层类型用 ``|`` 让 mypy 在 ``isinstance`` 分支后能自动收窄到具体子类, 减少
  运行时 dispatch 成本。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileSource:
    """本地文件入口: 持有 Path。"""

    path: Path


@dataclass(frozen=True)
class UrlSource:
    """URL 入口: 持有 URL 串 + 字节上限 + httpx 超时 (秒)。"""

    url: str
    max_size: int = 1_000_000_000
    timeout_s: float = 600.0


@dataclass(frozen=True)
class BufferSource:
    """内存 buffer 入口: 持有 bytes + file_type (无点) + 来源标识 (用于 DocMeta.source / filename)。"""

    buf: bytes
    file_type: str
    source: str


IngestSource = FileSource | UrlSource | BufferSource
