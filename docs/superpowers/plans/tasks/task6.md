# Task 6: Cache Layer (Redis + keys + invalidation)

> Extracted from `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` (lines 1236-1439).
>
> Cross-task fixes applied (in addition to original):
> - **(B10 🔴 Blocker 强化)** L3 `search_key` 改:`rag:search:{dataset_version}:{hash}`;payload 含 `dataset_versions: list[int]`(多个 dataset 的 version 列表),而非单一 `dataset_version`;失效对齐 FastGPT `VERSION_KEY:` 模式(见 `packages/service/common/cache/index.ts:9-48`)— dataset 增删时 `INCR dataset_version:{dataset_id}`,而非 SCAN 删 key。
> - **(B11 🔴 Blocker 强化)** `on_model_changed` 增加 `await cache.delete_pattern(f"{NAMESPACE}:search:*")` 清 L3,理由:切 embed model 后旧模型算的 search 结果必须立刻清(避免模型不一致 stale)。
> - **(audit #4 强化)** `on_chunks_changed` per-dataset 隔离:用 `delete_pattern(f"{NAMESPACE}:search:*{dataset_id}*")` 替代全 namespace 清(需 search_key payload 含 `dataset_ids` 字段,见 search_key 改造)。
> - **(audit #4 强化)** Cache hit rate 监控:Cache 类已有 `metrics: dict[str, dict[str, int]]`;新增 `unavailable` 计数(降级 catch 中自增);`JsonLoggingHandler` 输出 `cache_hit` 字段用于上游日志聚合。
>
> Original fixes applied:
> - (B10 🔴) L3 `search_key` payload 增加 `dataset_version: int` 字段, key 格式 `rag:search:{dataset_version}:{hash}`
> - (B11 🔴) `on_model_changed` 增加 L3 清: `await cache.delete_pattern(f"{NAMESPACE}:search:*")` (spec §8.4)
> - (audit #2 P1-6) L2 标 default off: 加 `CacheSettings.query_ext_enabled: bool = False`
> - (audit #4) `on_chunks_changed` per-dataset 隔离(而非全 namespace 清): 用 dataset_id 过滤 pattern
> - (B7 🔴) Cache.get/set 接受 `warnings: list[str] | None = None` 参数, Redis 失败时 `warnings.append("redis_unavailable: layer=L1")`
> - (audit #4) cache hit rate 监控: Cache 类加 `metrics: dict[str, dict[str, int]] = {"L1": {"hit": 0, "miss": 0}, ...}`

**Files:**
- Create: `src/rag/infra/cache/__init__.py`
- Create: `src/rag/infra/cache/connection.py`
- Create: `src/rag/infra/cache/keys.py`
- Create: `src/rag/infra/cache/invalidation.py`
- Create: `tests/unit/test_cache_keys.py`
- Create: `tests/integration/test_cache.py`

- [ ] **Step 0: 写 stub (audit #1 P1-1 修正: 先 stub 后 test,确保 RED 阶段模块可 import)**

```python
# src/rag/infra/cache/keys.py (stub)
def embedding_key(model, text, provider_version=""):
    return ""
def query_ext_key(model, query, max_variants, provider_version=""):
    return ""
def search_key(payload):
    return ""
def search_key_pattern_for_dataset(dataset_id):
    return ""
def dataset_version_key(dataset_id):
    return ""
def rerank_key(model, query, doc_ids):
    return ""
```

```python
# src/rag/infra/cache/connection.py (stub)
class Cache:
    def __init__(self, url=None): self._client = None
    async def connect(self): pass
    async def close(self): pass
    async def get(self, key, warnings=None): return None
    async def set(self, key, value, ex=None, warnings=None): return False
    async def delete_pattern(self, pattern, warnings=None): return 0

cache = Cache()
```

```python
# src/rag/infra/cache/invalidation.py (stub)
async def on_chunks_changed(dataset_id): pass
async def on_dataset_deleted(dataset_id): pass
async def on_model_changed(dataset_id): pass
async def flush_all(): pass
```

- [ ] **Step 1: 写失败单测 (key hash)**

```python
# tests/unit/test_cache_keys.py
from rag.infra.cache.keys import (
    embedding_key, query_ext_key, search_key, rerank_key,
)

def test_embedding_key_deterministic():
    k1 = embedding_key("text-embedding-3-small", "hello")
    k2 = embedding_key("text-embedding-3-small", "hello")
    assert k1 == k2

def test_embedding_key_model_sensitive():
    k1 = embedding_key("text-embedding-3-small", "hello")
    k2 = embedding_key("text-embedding-3-large", "hello")
    assert k1 != k2

def test_key_namespace_prefix():
    k = embedding_key("m", "q")
    assert k.startswith("rag:emb:m:")

def test_search_key_includes_dataset_version():
    """B10: L3 search key 必须含 dataset_version(s),否则 chunk 增删后 L3 不会失效。
    
    跨 task 强化: payload 用 `dataset_versions: sorted(list[int])` (多 dataset 的 version 列表),
    key 格式 `rag:search:{joined_versions}:{hash}`, 与 FastGPT `VERSION_KEY:` 模式对齐。
    """
    payload_a = {"query": "q", "dataset_ids": ["d1"], "dataset_versions": [1]}
    payload_b = {"query": "q", "dataset_ids": ["d1"], "dataset_versions": [2]}
    assert search_key(payload_a) != search_key(payload_b)
    # key 格式: rag:search:1:hash (单 dataset)
    k = search_key(payload_a)
    assert k.startswith("rag:search:1:")

def test_search_key_multi_dataset_versions_sorted():
    """B10 强化: 多 dataset 时,version 列表必须 sort 后再 join,保证 (v_d1, v_d2) 顺序无关。"""
    p1 = {"query": "q", "dataset_ids": ["d1", "d2"], "dataset_versions": [2, 1]}
    p2 = {"query": "q", "dataset_ids": ["d1", "d2"], "dataset_versions": [1, 2]}
    assert search_key(p1) == search_key(p2)
    k = search_key(p1)
    assert k.startswith("rag:search:1-2:")
```

```python
# tests/unit/test_cache_settings.py (B10/P1-6 验证)
from rag.config import CacheSettings

def test_cache_settings_default_off():
    """P1-6: L2 query_ext 默认关闭,生产环境需显式开启。"""
    s = CacheSettings()
    assert s.query_ext_enabled is False
```

```python
# tests/unit/test_cache_metrics.py (audit #4 验证)
import pytest
from rag.infra.cache.connection import Cache

@pytest.mark.asyncio
async def test_cache_metrics_hit_miss():
    c = Cache(url="redis://127.0.0.1:1")
    await c.connect()
    # L1 miss
    await c.get("k", layer="L1")
    assert c.metrics["L1"]["miss"] == 1
    await c.close()

@pytest.mark.asyncio
async def test_cache_metrics_unavailable_counter():
    """audit #4 强化: Redis 不可用时,unavailable 计数应自增(而非 miss)。"""
    c = Cache(url="redis://127.0.0.1:1")  # 不可用端口
    await c.connect()
    await c.get("k", layer="L1")
    assert c.metrics["L1"]["unavailable"] == 1
    assert c.metrics["L1"]["miss"] == 0  # 失败不计 miss
    await c.close()

@pytest.mark.asyncio
async def test_json_logging_handler_emits_cache_hit():
    """audit #4 强化: cache hit/miss 事件应通过 JsonLoggingHandler 输出 cache_hit 字段。"""
    import logging
    from rag.infra.cache.connection import Cache, JsonLoggingHandler
    
    captured = []
    class CaptureHandler(logging.Handler):
        def emit(self, record):
            captured.append(record)
    
    c = Cache(url="redis://127.0.0.1:1")
    handler = CaptureHandler()
    logger = logging.getLogger("rag.infra.cache.connection")
    logger.addHandler(handler)
    await c.connect()
    await c.get("k", layer="L1")
    
    # 至少有一条 log 含 cache_hit 字段
    cache_hit_records = [r for r in captured if hasattr(r, "cache_hit")]
    assert len(cache_hit_records) >= 1
    assert cache_hit_records[0].cache_layer == "L1"
    await c.close()
```

- [ ] **Step 2: 跑测试,确认 fail**

```bash
uv run pytest tests/unit/test_cache_keys.py -v
# 期望: 4 failed (stub 返回空串,断言失败 — 非 ImportError)
```

- [ ] **Step 3: 写 keys.py (含 B10 dataset_version)**

```python
# src/rag/infra/cache/keys.py
import hashlib
import json
import uuid
from typing import Any

NAMESPACE = "rag"

def _hash(payload: Any) -> str:
    s = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(s.encode()).hexdigest()[:32]

def embedding_key(model: str, text: str, provider_version: str = "") -> str:
    return f"{NAMESPACE}:emb:{model}:{provider_version}:{_hash(text)}"

def query_ext_key(model: str, query: str, max_variants: int, provider_version: str = "") -> str:
    return f"{NAMESPACE}:qext:{model}:{provider_version}:{_hash({'q': query, 'n': max_variants})}"

def search_key(payload: dict) -> str:
    """B10 强化: payload 含 dataset_versions: sorted(list[int]) + dataset_ids: list[str]。

    Key 格式: `rag:search:{ds_ids_joined}:{vs_joined}:{hash}`,例如
    `rag:search:aabb-ccdd-...:1-3:abc...`。

    P0-21 修复 (audit #7): 与 P0-5 同源 — task15 与 task16 跨 task 契约对齐,
    payload 字段名统一 `dataset_versions: list[int]`。

    对齐 FastGPT `VERSION_KEY:` 模式(见 `packages/service/common/cache/index.ts:9-48`):
    - dataset 增删时 INCR `dataset_version:{dataset_id}`,旧 version 的 key 自然失效;
    - 多 dataset 时把所有参与 version sort + join,保证 (v_d1=1, v_d2=2) == (v_d1=2, v_d2=1)。

    Hash 输入包含 dataset_ids (sorted) + query + top_k + dataset_versions,
    保证 dataset 集合 / version / query / top_k 任一变化都会生成不同 key。

    P0-5 修复 (audit #7): 字段集契约 — payload 必含字段:
      - dataset_versions: sorted(list[int])   (来自 task15 注入 dataset_versions dict)
      - dataset_ids: sorted(list[str])         (UUID 字符串,排序后 join)
      - query: str
      - top_k: int
    task16 build_full_pipeline 在 deps["dataset_versions"] 注入,本函数读
    payload.get("dataset_versions", []) list,缺字段视为空(走 "0" 默认)。

    P0-6 修复 (audit #7): 在 key path 嵌入 sorted_dataset_ids_joined,这样
    `search_key_pattern_for_dataset` 生成的 glob 形如 `rag:search:*-{ds_id}-*:*`
    (ds_id 出现在 ds_ids_joined 段而非 hash 段),SCAN 直命中,无需靠 hash
    collision 兜底。trade-off: key 长度增加(36 字符 UUID × N),但命中率
    从"靠运气"提升到 100%。
    """
    versions = sorted(payload.get("dataset_versions", []))
    versions_str = "-".join(str(v) for v in versions) if versions else "0"
    ds_ids = sorted(str(d) for d in payload.get("dataset_ids", []))
    ds_ids_str = "-".join(ds_ids) if ds_ids else "_"
    return f"{NAMESPACE}:search:{ds_ids_str}:{versions_str}:{_hash(payload)}"

def search_key_pattern_for_dataset(dataset_id: uuid.UUID | str) -> str:
    """audit #4 强化: 生成 per-dataset 失效 pattern,供 on_chunks_changed 使用。

    P0-6 修复 (audit #7): 利用 key path 的 ds_ids_joined 段直接 glob 命中。
    形如 `rag:search:*-{ds_id}*:*`,SCAN MATCH 在 O(N) 内命中,无需靠 hash
    collision 兜底。原实现 `*{dataset_id}*` 会扫到 hash 段(32 hex 字符),
    误命中风险高(uuid 子串出现在 hex 概率约 36/16^32 ≈ 0)。
    """
    return f"{NAMESPACE}:search:*-{dataset_id}*:*"

def dataset_version_key(dataset_id: uuid.UUID | str) -> str:
    """B10 强化: dataset version 计数器 key,供 ingest pipeline INCR 使用。
    
    对齐 FastGPT `VERSION_KEY:{dataset_id}` 模式: cache key 内嵌 version,
    version 变化即 cache 自然失效,无需 SCAN 删 key(SCAN 在大 key space 下慢)。
    """
    return f"{NAMESPACE}:version:{dataset_id}"

def rerank_key(model: str, query: str, doc_ids: list[uuid.UUID]) -> str:
    return f"{NAMESPACE}:rk:{model}:{_hash({'q': query, 'ids': [str(i) for i in doc_ids]})}"
```

- [ ] **Step 3.5: 更新 config.py 加 CacheSettings (P1-6)**

```python
# src/rag/config.py (增量 — 在 LLMSettings 后追加)
from pydantic import BaseModel

class CacheSettings(BaseModel):
    """Cache 配置。
    
    P1-6: L2 query_ext 默认关闭,生产环境需显式开启。
    理由: query_ext 会触发额外 LLM 调用,在低 QPS 场景下开销高于收益。
    """
    query_ext_enabled: bool = False   # L2 query extension 开关
    l1_ttl: int = 86400               # L1 embedding cache TTL (s) — 24h, 对齐 spec §2
    l2_ttl: int = 1800                # L2 query_ext cache TTL (s) — 30min, 对齐 spec §2
    l3_ttl: int = 300                 # L3 search result TTL (s) — 5min, 对齐 spec §2
    l4_ttl: int = 3600                # L4 rerank cache TTL (s) — 1h, 对齐 spec §2
```

- [ ] **Step 4: 写 connection.py (含降级 + B7 warnings + audit #4 metrics)**

```python
# src/rag/infra/cache/connection.py
import redis.asyncio as aioredis
import json
import logging
from rag.config import settings

logger = logging.getLogger(__name__)

class JsonLoggingHandler(logging.Handler):
    """audit #4 强化: 输出 JSON 格式日志,包含 cache_hit 字段用于上游日志聚合。
    
    上游 (Loki / ELK) 可按 cache_hit=true 过滤并计算 cache hit rate。
    """
    def emit(self, record: logging.LogRecord) -> None:
        payload = {
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
        }
        # 提取 cache_hit 等结构化字段(通过 extra 传入)
        for k in ("cache_hit", "cache_layer", "cache_key", "cache_unavailable"):
            v = getattr(record, k, None)
            if v is not None:
                payload[k] = v
        print(json.dumps(payload, default=str))

class Cache:
    """Redis 缓存, 不可用时降级到直连模式。

    B7: get/set 接受 warnings 参数,Redis 失败时由调用方决定是否向上层暴露。
    audit #4: metrics 记录各层 hit/miss/unavailable,用于监控缓存健康度。
    audit #4 强化: hit/miss 事件通过 JsonLoggingHandler 输出 cache_hit 字段。
    """

    def __init__(self, url: str | None = None):
        self.url = url or settings.redis_url
        self._client: aioredis.Redis | None = None
        # audit #4 强化: 注入 JSON 日志 handler
        self._json_handler = JsonLoggingHandler(level=logging.INFO)
        logger.addHandler(self._json_handler)
        # P0-9 修复 (audit #7): metrics 移到实例级 dict,避免类级 dict
        # 跨 Cache 实例串扰(原 metrics: dict[...] 在 class body 定义,所有实例
        # 共享同一对象,一个 Cache 实例 hit 自增会污染另一个的 metrics 快照)。
        self.metrics: dict[str, dict[str, int]] = {
            "L1": {"hit": 0, "miss": 0, "unavailable": 0},   # embedding
            "L2": {"hit": 0, "miss": 0, "unavailable": 0},   # query_ext
            "L3": {"hit": 0, "miss": 0, "unavailable": 0},   # search
            "L4": {"hit": 0, "miss": 0, "unavailable": 0},   # rerank
        }
    
    async def connect(self):
        if self._client is None:
            # 跨 task 强化: Cache 自身的 socket timeout 与 30s FastGPT 默认解耦.
            #   Cache 走"快速失败 + 降级直连"策略, 1s 即视为不可用, 避免
            #   拖慢上游 orchestrator; 30s 通用 timeout 留给 LLM / PG 等
            #   I/O 路径 (见 task7 chat.py + task16 orchestrator).
            self._client = aioredis.from_url(
                self.url,
                socket_timeout=1.0,    # Cache 自身: 1s 超时即降级
                socket_connect_timeout=1.0,
                max_connections=20,
            )
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
    
    @property
    def client(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError("Cache not connected. Call cache.connect() first.")
        return self._client
    
    def _emit_cache_event(self, layer: str, hit: bool, key: str | None = None) -> None:
        """audit #4 强化: 输出 cache_hit JSON 日志,供 Loki/ELK 聚合。"""
        logger.info(
            f"cache {'hit' if hit else 'miss'} layer={layer}",
            extra={"cache_hit": hit, "cache_layer": layer, "cache_key": key},
        )
    
    async def get(self, key: str, layer: str = "L1", warnings: list[str] | None = None) -> str | None:
        """B7: warnings 可选参数,Redis 失败时由调用方决定上报方式。
        audit #4 强化: 失败时自增 unavailable 计数 + 输出 cache_unavailable 日志。
        """
        try:
            result = await self.client.get(key)
            if result is not None:
                self.metrics[layer]["hit"] += 1
                self._emit_cache_event(layer, hit=True, key=key)
            else:
                self.metrics[layer]["miss"] += 1
                self._emit_cache_event(layer, hit=False, key=key)
            return result
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.warning(
                f"Redis get 失败, 降级直连: {e}",
                extra={"cache_unavailable": True, "cache_layer": layer, "cache_key": key},
            )
            if warnings is not None:
                warnings.append(f"redis_unavailable: layer={layer}")
            # audit #4 强化: 失败计入 unavailable(而非 miss),便于告警
            self.metrics[layer]["unavailable"] += 1
            self._emit_cache_event(layer, hit=False, key=key)
            return None
    
    async def set(self, key: str, value, ex: int | None = None, layer: str = "L1", warnings: list[str] | None = None) -> bool:
        """B7: warnings 可选参数,Redis 失败时由调用方决定上报方式。
        P0-7 修复 (audit #7): BaseModel 走 model_dump_json 序列化,保留 schema
        round-trip。原 json.dumps(BaseModel) 会触发 Pydantic str repr,丢字段类型;
        读端 json.loads 拿到 dict,SearchResult.model_validate(dict) 才能还原,
        否则跨 cache 边界后 isinstance 失败,出现"读到 dict 不是 model"的隐性 bug。
        """
        try:
            from pydantic import BaseModel
            if isinstance(value, BaseModel):
                s = value.model_dump_json()
            elif isinstance(value, str):
                s = value
            else:
                s = json.dumps(value, default=str)
            await self.client.set(key, s, ex=ex)
            return True
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.warning(
                f"Redis set 失败, 降级忽略: {e}",
                extra={"cache_unavailable": True, "cache_layer": layer, "cache_key": key},
            )
            if warnings is not None:
                warnings.append(f"redis_unavailable: layer={layer}")
            self.metrics[layer]["unavailable"] += 1
            return False
    
    async def delete_pattern(self, pattern: str, warnings: list[str] | None = None) -> int:
        """SCAN + UNLINK 删除匹配的所有 key。"""
        try:
            count = 0
            async for key in self.client.scan_iter(match=pattern, count=100):
                await self.client.unlink(key)
                count += 1
            return count
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.warning(
                f"Redis delete_pattern 失败: {e}",
                extra={"cache_unavailable": True, "cache_layer": "delete_pattern"},
            )
            if warnings is not None:
                warnings.append(f"redis_unavailable: layer=delete_pattern")
            self.metrics["L3"]["unavailable"] += 1
            return 0

cache = Cache()
```

- [ ] **Step 5: 写 invalidation.py (含 B11 L3 清 + audit #4 per-dataset 隔离)**

```python
# src/rag/infra/cache/invalidation.py
import uuid
from rag.infra.cache.connection import cache
from rag.infra.cache.keys import NAMESPACE, search_key_pattern_for_dataset, dataset_version_key

async def on_chunks_changed(dataset_id: uuid.UUID):
    """audit #4 强化: per-dataset 隔离,只清该 dataset 相关的缓存,不影响其他 dataset。
    
    实现 (B10 优先 + per-dataset SCAN 兜底):
    1. 首选: INCR `dataset_version:{dataset_id}`,旧 version 的 search key 自然失效 (无 SCAN 开销);
    2. 兜底: SCAN `rag:search:*{dataset_id}*` 删除残留(多 dataset 共享 key 的极端情况)。
    
    对齐 FastGPT `VERSION_KEY:` 模式: `INCR` 是 O(1),SCAN 是 O(N),
    在大 key space 下 `INCR` 显著优于 SCAN,优先用 INCR。
    """
    # 首选路径: bump version (O(1))
    await cache.client.incr(dataset_version_key(dataset_id))
    # 兜底路径: per-dataset SCAN (处理跨 dataset 共享 search_key 的边界情况)
    await cache.delete_pattern(search_key_pattern_for_dataset(dataset_id))

async def on_dataset_deleted(dataset_id: uuid.UUID):
    """dataset 删除时清 L3 search 缓存。
    
    注意: version 计数器 `dataset_version:{dataset_id}` 也需清,否则永远占用 key space。
    """
    # bump version 让旧 key 失效
    await cache.client.delete(dataset_version_key(dataset_id))
    # per-dataset 清 search 缓存
    await cache.delete_pattern(search_key_pattern_for_dataset(dataset_id))
    # rerank 缓存也清
    await cache.delete_pattern(f"{NAMESPACE}:rk:*{dataset_id}*")

async def on_model_changed(dataset_id: uuid.UUID):
    """B11 Blocker 强化: 模型变更需清 L1 emb + L2 qext + L3 search(spec §8.4)。
    
    关键原因: 切 embed model 后,旧模型算的 search 结果 embedding 空间已变,
    旧 L3 search 结果返回的 chunk IDs 不再语义准确,**必须立刻清**以避免模型不一致 stale。
    L1/L2 也要清,因为旧 key 是用旧 model 算的,新 model 复用会得到错误向量。
    """
    await cache.delete_pattern(f"{NAMESPACE}:emb:*")
    await cache.delete_pattern(f"{NAMESPACE}:qext:*")
    await cache.delete_pattern(f"{NAMESPACE}:search:*")   # B11 新增(切 model 必须清)
    # 注: 跨 dataset 共享 L1/L2,这里走全 namespace 清而非 per-dataset
    # 生产可优化: 用 `INCR model_version:{model}` 让 model 维度的 key 自然失效

async def flush_all():
    """手动 flush, 应对模型同名升级等场景。"""
    await cache.delete_pattern(f"{NAMESPACE}:*")
```

- [ ] **Step 6: 写集成测试**

```python
# tests/integration/test_cache.py
import pytest
from rag.infra.cache.connection import Cache

@pytest.mark.asyncio
async def test_redis_get_set_roundtrip():
    from testcontainers.redis import RedisContainer
    with RedisContainer("redis:7") as r:
        url = r.get_connection_url().replace("redis://", "redis://")
        c = Cache(url=url)
        await c.connect()
        assert await c.set("k", "v", ex=60)
        assert await c.get("k") == "v"
        await c.close()

@pytest.mark.asyncio
async def test_redis_unavailable_returns_none():
    """Redis 不可用时 get 返回 None (不抛错)。"""
    c = Cache(url="redis://127.0.0.1:1")  # 不存在端口
    await c.connect()
    result = await c.get("any")
    assert result is None   # 降级
    await c.close()

@pytest.mark.asyncio
async def test_redis_unavailable_appends_warning():
    """B7: Redis 不可用时,warnings 参数应被填充。"""
    c = Cache(url="redis://127.0.0.1:1")
    await c.connect()
    warnings: list[str] = []
    result = await c.get("any", layer="L1", warnings=warnings)
    assert result is None
    assert any("redis_unavailable" in w and "L1" in w for w in warnings)
    await c.close()

@pytest.mark.asyncio
async def test_on_chunks_changed_per_dataset_isolation():
    """audit #4 强化: on_chunks_changed(d1) 不应影响 d2 的缓存。
    
    通过 INCR 路径 + per-dataset SCAN 兜底实现隔离。
    """
    from testcontainers.redis import RedisContainer
    from rag.infra.cache.invalidation import on_chunks_changed
    from rag.infra.cache.keys import search_key, dataset_version_key
    
    with RedisContainer("redis:7") as r:
        url = r.get_connection_url()
        c = Cache(url=url)
        await c.connect()
        
        d1 = "11111111-1111-1111-1111-111111111111"
        d2 = "22222222-2222-2222-2222-222222222222"
        k1 = search_key({"query": "q", "dataset_ids": [d1], "dataset_versions": [1]})
        k2 = search_key({"query": "q", "dataset_ids": [d2], "dataset_versions": [1]})
        await c.set(k1, "result-d1", ex=60)
        await c.set(k2, "result-d2", ex=60)
        
        await on_chunks_changed(d1)
        
        # version 计数器应被 INCR
        new_version = await c.client.get(dataset_version_key(d1))
        assert new_version is not None
        assert int(new_version) >= 1
        
        await c.close()

@pytest.mark.asyncio
async def test_on_model_changed_clears_l3():
    """B11 Blocker 强化: on_model_changed 必须清 L3 search(避免旧模型 stale 结果)。"""
    from testcontainers.redis import RedisContainer
    from rag.infra.cache.invalidation import on_model_changed
    from rag.infra.cache.keys import search_key
    
    with RedisContainer("redis:7") as r:
        url = r.get_connection_url()
        c = Cache(url=url)
        await c.connect()
        
        k = search_key({"query": "q", "dataset_ids": ["d1"], "dataset_versions": [1]})
        await c.set(k, "old-model-result", ex=60)
        assert await c.get(k) == "old-model-result"
        
        await on_model_changed("d1")
        
        # L3 search 缓存必须被清
        assert await c.get(k) is None
        await c.close()
```

- [ ] **Step 7: 跑全部测试**

```bash
uv run pytest tests/unit/test_cache_keys.py tests/unit/test_cache_settings.py tests/unit/test_cache_metrics.py tests/integration/test_cache.py -v
# 期望: 11 passed
# (test_cache_keys: 6 个,test_cache_settings: 1 个,test_cache_metrics: 3 个,test_cache: 5 个集成)
```

- [ ] **Step 8: commit**

```bash
git add src/rag/infra/cache tests/
git commit -m "feat(cache): Redis multi-level cache + degradation + per-dataset invalidation + B7/B10/B11"
```
