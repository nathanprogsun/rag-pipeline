"""IngestSource tagged union: 收敛四段入口 (file / url / buffer / api) 为单一类型。

设计:
- 四个 ``frozen=True`` dataclass 表示四段入口:
  * ``FileSource``  本地文件 path
  * ``UrlSource``   任意公网 URL, 内部走 read_url
  * ``BufferSource`` 内存 bytes + file_type, 用于把外部已读取的字节流送进 pipeline
  * ``ApiSource``  第三方 API 请求配置 (server + endpoint), 内部拉 JSON 并按
                   field_priority 抽字段
- ``IngestSource = FileSource | UrlSource | BufferSource | ApiSource`` 作为
  ``pipeline.ingest`` 的入参类型, 在 ``isinstance`` 分发后决定调 ``read_file`` /
  ``read_url`` / ``dispatch_bytes`` / 内部 inline 的 httpx fetch + JSON 抽取。

为什么用 tagged union 而非枚举:
- dataclass 携带的实际参数 (path / url / buffer / server+endpoint) 各不相同,
  单一 enum 字段塞不下。
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


@dataclass(frozen=True)
class ApiSource:
    """第三方 API JSON 响应入口: 拉取响应 → 抽字段 → 转 TextDoc。

    与 FileSource / UrlSource / BufferSource 不同, ApiSource 持有的是 *请求配置*
    (server + endpoint) 而非内容本身; 内容由 pipeline.ingest 内部拉取。

    字段抽取遵循 ``field_priority`` 顺序 (默认 ``text`` → ``content`` → ``data``
    → ``message``), 命中首个非空字符串值即返回; list 响应按相同优先级逐项抽字段
    并用空行拼接。
    """

    server_url: str  # 第三方 API base URL (e.g. "https://api.example.com")
    endpoint: str  # 路径 (e.g. "/v1/files")
    auth_token: str | None = None  # 鉴权 token (Bearer)
    timeout_s: float = 30.0
    max_size: int = 1_000_000_000
    field_priority: tuple[str, ...] = ("text", "content", "data", "message")
    # 上层 HTTP 客户端可注入 (用于测试 mock); None = 内部新建 httpx.AsyncClient
    http_client: object | None = None


IngestSource = FileSource | UrlSource | BufferSource | ApiSource
