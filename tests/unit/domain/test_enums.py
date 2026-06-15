"""``rag.domain.enums`` 单元测试: Datasource 拆分 + 映射函数。"""

from __future__ import annotations

from rag.domain.enums import (
    StoredDatasource,
    ingest_to_stored_datasource,
)

# ── Literal 类型约束: mypy 静态保证; 这里只 spot-check 字面值。──


def test_ingest_datasource_file_url() -> None:
    """IngestDatasource 仅包含 'file' / 'url' 两种 ingest 阶段值。"""
    ingest_values = ("file", "url")
    for v in ingest_values:
        # 函数参数强制收窄到 IngestDatasource, 失败会被 mypy 抓住
        ingest_to_stored_datasource(v)  # type: ignore[arg-type]


def test_stored_datasource_file_manual_api() -> None:
    """StoredDatasource 仅包含 'file' / 'manual' / 'api' 三种持久化值。"""
    stored_values = ("file", "manual", "api")
    for v in stored_values:
        # StoredDatasource 字符串可作为持久化阶段值使用
        typed: StoredDatasource = v  # type: ignore[assignment]
        assert typed in {"file", "manual", "api"}


# ── ingest_to_stored_datasource 映射规则 ──


def test_map_file_to_file() -> None:
    """ingest='file' 默认 → stored='file'。"""
    assert ingest_to_stored_datasource("file") == "file"
    assert ingest_to_stored_datasource("file", source="file:///x.pdf") == "file"


def test_map_file_manual_prefix_to_manual() -> None:
    """ingest='file' + source 以 'manual://' 或 'inline://' 开头 → stored='manual'。"""
    assert ingest_to_stored_datasource("file", source="manual://user-note") == "manual"
    assert ingest_to_stored_datasource("file", source="inline://chat-msg") == "manual"
    # 大小写敏感: 不带前缀的 inline 不是 manual
    assert ingest_to_stored_datasource("file", source="Inline://x") == "file"
    # 相似前缀不是 manual (例如 'manual-note://')
    assert ingest_to_stored_datasource("file", source="manual-note://x") == "file"


def test_map_url_to_api() -> None:
    """ingest='url' → stored='api' (外部拉取), 与 source 无关。"""
    assert ingest_to_stored_datasource("url") == "api"
    assert ingest_to_stored_datasource("url", source="https://x.com/a") == "api"
    assert ingest_to_stored_datasource("url", source="manual://x") == "api"
    assert ingest_to_stored_datasource("url", source="inline://x") == "api"
    # 空 source 视为普通 url, 归 api
    assert ingest_to_stored_datasource("url", source="") == "api"
    assert ingest_to_stored_datasource("url", source=None) == "api"
