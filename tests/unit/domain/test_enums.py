"""``rag.domain.enums`` 单元测试: Datasource 拆分 + 映射函数。"""

from __future__ import annotations

from rag.domain.enums import (
    Datasource,
    IngestDatasource,
    StoredDatasource,
    ingest_to_stored_datasource,
)

# ── Literal 类型约束: mypy 静态保证; 这里只 spot-check 字面值。──


def test_ingest_datasource_file_url_api() -> None:
    """IngestDatasource 仅包含 'file' / 'url' / 'api' 三种 ingest 阶段值。"""
    ingest_values = ("file", "url", "api")
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


def test_datasource_alias_is_ingest_datasource() -> None:
    """旧 ``Datasource`` 是 ``IngestDatasource`` 的 deprecated alias, 值集合一致。"""
    # type-check: Datasource 可被当作 IngestDatasource 用 (兼容旧引用)
    typed: IngestDatasource = "file"
    aliased: Datasource = typed
    assert aliased == "file"


# ── ingest_to_stored_datasource 映射规则 ──


def test_map_file_to_file() -> None:
    """ingest='file' → stored='file', 与 source 无关。"""
    assert ingest_to_stored_datasource("file") == "file"
    assert ingest_to_stored_datasource("file", source="file:///x.pdf") == "file"
    assert ingest_to_stored_datasource("file", source="inline://x") == "file"


def test_map_url_default_to_api() -> None:
    """ingest='url' 默认 → stored='api' (外部拉取)。"""
    assert ingest_to_stored_datasource("url") == "api"
    assert ingest_to_stored_datasource("url", source="https://x.com/a") == "api"


def test_map_url_manual_prefix_to_manual() -> None:
    """ingest='url' + source 以 'manual://' 或 'inline://' 开头 → stored='manual'。"""
    assert ingest_to_stored_datasource("url", source="manual://user-note") == "manual"
    assert ingest_to_stored_datasource("url", source="inline://chat-msg") == "manual"
    # 大小写敏感: 不带前缀的 inline 不是 manual
    assert ingest_to_stored_datasource("url", source="Inline://x") == "api"
    # 相似前缀不是 manual (例如 'manual-note://')
    assert ingest_to_stored_datasource("url", source="manual-note://x") == "api"


def test_map_api_to_api() -> None:
    """ingest='api' → stored='api', 与 source 无关。"""
    assert ingest_to_stored_datasource("api") == "api"
    assert ingest_to_stored_datasource("api", source="https://api.x/v1") == "api"
    # 即使 source 是 manual 前缀, api 阶段直接保留 api 语义
    assert ingest_to_stored_datasource("api", source="inline://x") == "api"


def test_map_url_empty_source_treated_as_api() -> None:
    """ingest='url' + source='' / None → 视为 api (无法识别为 manual)。"""
    assert ingest_to_stored_datasource("url", source="") == "api"
    assert ingest_to_stored_datasource("url", source=None) == "api"
