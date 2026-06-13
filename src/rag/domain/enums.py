"""Domain 层枚举: cross-layer 共享的字面量。

只放稳定的、跨 layer 引用的枚举。模块级单点定义,避免双源不一致。

Datasource 拆分:
    原单一 ``Datasource = Literal['file', 'url', 'api', 'manual']`` 故意把
    ingest 阶段 ('file' / 'url' / 'api') 与 domain 持久化阶段 ('file' /
    'manual' / 'api') 拼在一起, 实际语义丢失: 一个 ingest 的 url 来源入库
    时变成 manual, 后续按 datasource='url' 永远查不到。

    修复拆成两个 Literal + 一个显式映射函数:
      - ``IngestDatasource``: ingest 阶段三个来源, 不可用于持久化。
      - ``StoredDatasource``: domain 持久化层三个来源, 不可在 ingest 中使用。
      - ``ingest_to_stored_datasource``: pipeline 边界唯一合法转换入口。

    旧 ``Datasource`` 保留为 ``IngestDatasource`` 的 deprecated alias,
    保证 ``src/rag/ingest/reader/dispatch.py`` 不破。
"""

from __future__ import annotations

from typing import Literal

# Ingest 阶段 datasource: reader 读完 TextDoc 之前看到的来源。
#  - file: 本地文件 (FileSource / BufferSource)
#  - url:  HTTP 拉取 (UrlSource)
#  - api:  第三方 API JSON 抽取 (ApiSource), 或 dispatch 内部默认占位
IngestDatasource = Literal["file", "url", "api"]

# Domain 持久化阶段 datasource: 写到 chunk metadata / 数据库 / retriever 读出的来源。
#  - file:    来自本地文件, 入库前保持原值
#  - manual:  用户手动粘贴 / inline (source 前缀 'manual://' 或 'inline://')
#  - api:     外部拉取 (url 或第三方 api, 统一归到 api, 区分维度靠 source 字符串)
StoredDatasource = Literal["file", "manual", "api"]

# 旧名兼容: dispatch.py 还在用, reader 重构完成后会替换。
# 新代码请直接用 IngestDatasource / StoredDatasource。
Datasource = IngestDatasource

# 'inline://' / 'manual://' 是约定前缀: 调用方传 BufferSource(source="manual://xxx")
# 时 ingest=api 但语义上是用户手填, 落库应归 manual。
_MANUAL_SOURCE_PREFIXES: tuple[str, ...] = ("inline://", "manual://")


def ingest_to_stored_datasource(
    ingest: IngestDatasource,
    source: str | None = None,
) -> StoredDatasource:
    """ingest 阶段 → 持久化阶段的合法转换入口 (pipeline 边界唯一允许调用的地方)。

    规则:
      - 'file' → 'file' (直接透传, 文件就是文件)。
      - 'url'  → 'api' (默认, 外部拉取一律归 api); 若 source 以 'inline://' 或
                 'manual://' 开头则归 'manual' (语义上调用方标记为手填)。
      - 'api'  → 'api' (第三方 API 拉取本身就是 api 语义)。

    Args:
        ingest: ingest 阶段 datasource 字符串。
        source: 可选, 来源 URI; 用于判断 url/api 是否实际是 manual 输入。

    Returns:
        持久化阶段 datasource 字符串。
    """
    if ingest == "file":
        return "file"
    if ingest == "url":
        if source and source.startswith(_MANUAL_SOURCE_PREFIXES):
            return "manual"
        return "api"
    # ingest == "api": 第三方 API 拉取统一归 api
    return "api"
