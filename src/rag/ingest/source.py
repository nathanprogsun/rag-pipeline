"""IngestSource tagged union: 收敛三段入口 (file / url / buffer) 为单一类型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileSource:
    """本地文件入口。

    Args:
        path: 本地文件路径。
    """

    path: Path


@dataclass(frozen=True)
class UrlSource:
    """URL 入口。

    Args:
        url: HTTP(S) URL 串。
        max_size: 字节上限, 超过则中断读取。
        timeout_s: httpx 超时秒数。
    """

    url: str
    max_size: int = 1_000_000_000
    timeout_s: float = 600.0


@dataclass(frozen=True)
class BufferSource:
    """内存 buffer 入口。

    Args:
        buf: 已读取的字节流。
        file_type: 扩展名 (无前导点), 用于 reader 派发。
        source: 来源标识, 写入 ``DocMeta.source`` / ``filename``。
    """

    buf: bytes
    file_type: str
    source: str


IngestSource = FileSource | UrlSource | BufferSource
