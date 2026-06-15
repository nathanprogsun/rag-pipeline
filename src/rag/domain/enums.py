"""Domain 层枚举: 跨层共享的字面量, 模块级单点定义避免双源不一致。

`Datasource` 按 ingest 与 stored 两个阶段拆分, 避免语义丢失:
同一份内容在两个阶段语义会变 (例如 ingest=url 抓的网页落库归 stored=api)。
拼成一个大枚举会让持久化层查不到 ingest 阶段用 url 抓进来的内容。
- `IngestDatasource`: ingest 阶段, 反映"用什么方式拿到内容" (`file` / `url`)。
- `StoredDatasource`: 持久化阶段, 反映"内容属于哪一类业务来源"
                       (`file` / `manual` / `api`)。
- `ingest_to_stored_datasource`: pipeline 边界唯一合法转换入口。
"""

from __future__ import annotations

from typing import Literal

# Ingest 阶段 datasource: reader 读完 TextDoc 之前看到的来源。
#  - file: 本地文件 (`FileSource` / `BufferSource`: 内存中的字节流视为文件)
#  - url:  HTTP 拉取 (`UrlSource`)
IngestDatasource = Literal["file", "url"]

# Domain 持久化阶段 datasource: 写到 chunk metadata / 数据库 / retriever 读出的来源。
#  - file:    来自本地文件, 入库前保持原值
#  - manual:  用户手动粘贴 / inline (source 前缀 `manual://` 或 `inline://`)
#  - api:     外部拉取 (url 抓的远端内容统一归 api, 区分维度靠 source 字符串)
StoredDatasource = Literal["file", "manual", "api"]

# `inline://` / `manual://` 是约定前缀: 调用方传 `BufferSource(source="manual://xxx")`
# 时 ingest=file 但语义上是用户手填, 落库应归 manual。
_MANUAL_SOURCE_PREFIXES: tuple[str, ...] = ("inline://", "manual://")


def ingest_to_stored_datasource(
    ingest: IngestDatasource,
    source: str | None = None,
) -> StoredDatasource:
    """ingest 阶段到持久化阶段的合法转换入口 (pipeline 边界唯一允许调用的地方)。

    规则:
      - `file` -> `file` (本地文件直接落 file; 若 source 以 `inline://` 或
                 `manual://` 开头则归 `manual`, 语义上调用方标记为手填)。
      - `url`  -> `api` (url 拉取的远端内容统一归 api)。

    Args:
        ingest: ingest 阶段 datasource 字符串, 仅 `file` / `url`。
        source: 可选, 来源 URI; 用于判断 file 是否实际是 manual 输入。

    Returns:
        持久化阶段 datasource 字符串, 取值 `file` / `manual` / `api`。
    """
    if ingest == "file":
        if source and source.startswith(_MANUAL_SOURCE_PREFIXES):
            return "manual"
        return "file"
    # ingest == "url": 远端拉取统一归 api
    return "api"
