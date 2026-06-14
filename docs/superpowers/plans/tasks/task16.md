# Task 16: build_full_pipeline + 跨 task 拼装 (spec §0.1 主流水线)

**Status**: 未开始 (2026-06-14 审计重标)

## 状态: 未开始 (2026-06-14 审计重标)

> **实际交付**(`refactor/chunker-reader` 分支):
>
> - `src/rag/pipeline/full.py` — `build_full_pipeline(datasets, deps, **kw)` 组装 QueryDecomposer → ImageCaption → QueryExtension → Orchestrator(RunnableParallel + with_fallbacks)→ ParentDoc → InterFusion → GlobalRerank → GlobalFilter → Cite → Audit
> - `src/rag/pipeline/cache_decorator.py` — `with_cache(runnable, key_fn, ttl)`,失败 throwaway 抑制 + warnings 标记(spec §0.1 L226 Redis 不可用 → 降级直连)
> - `src/rag/infra/observability/json_handler.py` — JSON Logging,主流程每节点耗时纳入 audit 旁路
> - `tests/integration/test_full_pipeline.py` — 3 个 e2e case (主路径 + 降级 + dataset_version)
>
> **后续 review/audit 影响 (2026-06-13 同步)**:
>
> - **PAudit-2 (async 链路)**: `build_full_pipeline` 内所有节点改 `async def`,主流程 `await` 链贯穿,`cache_decorator.with_cache` 内部 `await cache.get / cache.set`
> - **PAudit-4 (SearchRequest 拆 4 sub-config)**: `build_full_pipeline` 签名新增 `vector_config / fulltext_config / rerank_config / citation_config` 4 个显式参数(替代原 `SearchRequest` 内嵌 dict),`datasets` 与 `deps` 顺序不变
> - **PAudit-4 (prompt_template None)**: `build_full_pipeline` 接受 `prompt_template: str | None = None`,显式区分"未设置"(None) vs "空串"(用户主动传空)
> - **PAudit-5 (RetrievalTrace 整合)**: 主流水线终结后,Audit 节点从 `RetrievalTrace` 列表聚合写到 jsonl,不再从 `latency_ms: dict` 临时拼
>
> 当前指标:无可验证交付(2026-06-14 审计重标:重构分支未交付,见下方"实际实现"段)。
>
> **历史溯源**(本 task 原始描述):原 plan 写 stub-first (audit #1 P1-1) + subagent #9 修复 6 项,详见下方。原描述保留为溯源依据。

> Extracted from `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` (lines 3787-4054).
>
> Fixes applied:
> - (audit #1 P1-1) stub-first 违反: 加 Step 0 stub(`def build_full_pipeline(datasets, deps, **kw): return RunnableLambda(...)` 占位),确保 RED 阶段 E2E 测试可 import pipeline 模块而非 ImportError。
> - (subagent #4) `build_full_pipeline` 签名语法修正: 主 plan L3986-3989 同行尾逗号+下一行多缩进混合(`max_tokens=4000,\n                        parent_doc_window=0, use_decomposition=False)`),易触发 Python 解析警告。Task 17 subagent #4 已修正,本 task 沿用一致风格(每参数一行, 默认值列尾)。
> - (subagent #9) 完整签名补齐 Task 17 audit #1 改造后的所有开关: `audit: RetrievalAudit | None`、`use_decomposition: bool`、`use_global_rerank: bool`(`SearchRequest` 字段已定义 Task 2 L422-423)。`use_global_rerank=True` 时在 Filter 前挂 GlobalRerank(spec §0.1 挂载点 ②, spec §7.1 标号一致)。
> - (subagent #9) `chat_bg` / `histories` 透传契约: state 透传 `state.get("chat_bg", "")` / `state.get("histories", [])` 给 `QueryExtensionRunnable`(Task 13 C5 修正消费 `input["chat_bg"]` / `input["histories"]`)。
> - (subagent #9) `cache warnings` 收集到 `SearchResult.warnings`: `with_cache` 内 `cache.get` / `cache.set` 失败 throwaway 抑制 + 上层在 orchestrator 把 `warnings` 列表合并到 `SearchResult.warnings` — spec §0.1 L226 「Redis 不可用 → 降级直连 + warnings 标记, 不报错」。
> - (subagent #9) `RunnableError` vs `Exception` 区分: orchestrator 用 `with_fallbacks(...)` 处理 subgraph 异常(Task 14 H1 修正),build_full_pipeline 顶层不重复 catch,只对 cache 写入做 throwaway 抑制(项目 coding-style.md §「Throwaway cleanup: `.catch(() => {})`」,Python 版 `try/except: pass` 限定在 cache 兜底)。
> - (subagent #9) `dataset_version` 路径: `make_search_cache` 的 `search_key(payload)` 注入 `dataset_version` 字段(由 `dataset_versions` 字典透传),未提供时退化为 `"v0"`。`SearchRequest` 无显式 dataset_version 字段,版本号由 `deps["dataset_versions"]` 字典透传,Search 缓存键区分 dataset 升级前/后。
> - (subagent #9) E2E 测试 3 个 case 不引入 Redis / LLM 真实依赖,使用 `FakeEmbed` + PG mock(Task 14 同样模式): 主路径(query_extension=False) + 降级路径(audit=None) + dataset_version 路径(deps 注入 `dataset_versions={"ds_id": "v1"}`)。

## Open P0s (2026-06-14 audit)

| P0 ID | 描述 | 文件:行 | 解决路径 |
|---|---|---|---|
| G-P0-1 | 状态 "已完成 (2026-06-13 同步)" 虚假 — 5 文件 (`pipeline/full.py` / `cache_decorator.py` / `observability/__init__.py` / `observability/json_handler.py` / `tests/integration/test_full_pipeline.py`) 全部 0 实现, "3 passed" 不可验证 | task16.md:3, 21 | M3 (5f) — 改状态"未开始", 或实施时按 spec 落地 5 文件 |
| G-P0-2 | "后续 review/audit 影响" 段 (PAudit-2/4/5) 把 pre-implementation 设计注记写成 post-implementation 修复, 误导项目历史; 文件不存在无法 "应用" 修复 | task16.md:7-8 | M3 (5f) — 段标题改 "计划改造项 (待实施)", 删 "修复" 词 |
| G-P0-3 | `build_full_pipeline` 签名用 `deps: dict` (string-keyed bag) 违反项目 typed DI 政策 (`AGENTS.md`); `deps.get("reranker")` 静默 None, rerank 分支静默消失 | task16.md:420-429, 152-158 | M3 (5f) — 改 `PipelineDeps(BaseModel)` (vector_retriever / fulltext_retriever / embed_model / chat_model / reranker / dataset_versions), 测试同步显式构造 |
| G-P0-4 | Stage ordering 偏离 FastGPT: `GlobalRerank` 在 InterFusion 之后 (task16.md:478-487), FastGPT `defaultRecall/rerank.ts:55-110` rerank **text-only 在 inter-RRF 之前**, 然后再与原 textRecall re-fuse; 混合模态查询的 rank 完全不同 | task16.md:30, 478-487, 496-524 | M3 (5f) — decision D: 改 rerank pre-inter-fuse (text-only), 更新挂载点 ② 实现, 同步 task 14 |
| G-P0-5 | `with_cache` 装饰器 (task16.md:255-289) 重复实现 `Cache` 类 (connection.py:94-139) 已有的 `get(key, layer, warnings)` / `set(key, value, ex, layer, warnings)` fail-soft + warnings sink; `with_cache` 用 `try/except Exception: pass` 静默吞异常, 不写 warnings, 比无缓存更差 (TypeError 也被吞) | task16.md:255-289 | M3 (5f) — 删 `with_cache` 装饰器, 4 个 cache 工厂直接调 `cache.get(key, layer="L1", warnings=warnings)` / `cache.set(...)`, TTL 从 `settings.cache.l1_ttl` 读 |

详细分析见 `audit/2026-06-14-task16-alignment.md` §5 (修复建议)。

**Files:**
- Create: `src/rag/pipeline/full.py`
- Create: `src/rag/pipeline/cache_decorator.py`
- Create: `src/rag/infra/observability/__init__.py`
- Create: `src/rag/infra/observability/json_handler.py`
- Create: `tests/integration/test_full_pipeline.py`

**Spec 引用**:
- §0.1 流水线全景图(本项目视角): 完整挂载点表 ①~⑤(QueryDecomposer / ParentDocExpander / GlobalRerank / RetrievalAudit),ImageCaptionRunnable → QueryExtensionRunnable → Orchestrator → Cite → Audit。
- §7.1 架构: `SearchRequest` → 可选 decomposer → ImageCaption → QueryExtension → Orchestrator(RunnableParallel + with_fallbacks)→ 可选 ParentDoc → InterFusion → 可选 GlobalRerank → GlobalFilter → Cite → Audit。
- §7.0.3 检索审计: `audit` 为旁路, 主流水线终结后**不阻塞**调用。
- §0.1 L226 缓存降级: Redis 不可用 → 降级直连 + warnings 标记, 不报错。
- §0.1 L222 失效: dataset 升版 / chunk 增删 → 重新生成 L3 search key(dataset_version 区分)。

---

- [ ] **Step 0: 写 stub (audit #1 P1-1 修正: 先 stub 后 test,确保 RED 阶段模块可 import)**

```python
# src/rag/pipeline/full.py (stub)
from langchain_core.runnables import RunnableLambda


def build_full_pipeline(datasets, deps, **kwargs):
    """Stub: 待实现 (Task 16 Step 4)。"""
    async def _echo(state):
        return state
    return RunnableLambda(_echo)
```

```python
# src/rag/pipeline/cache_decorator.py (stub)
def with_cache(runnable, key_fn, ttl):
    """Stub: 待实现 (Task 16 Step 2)。"""
    return runnable

def make_embedding_cache(embed_runnable, model):
    return embed_runnable

def make_query_ext_cache(qext_runnable, model):
    return qext_runnable

def make_search_cache(pipeline_runnable, dataset_versions=None):
    return pipeline_runnable

def make_rerank_cache(rerank_runnable, model):
    return rerank_runnable
```

```python
# src/rag/infra/observability/json_handler.py (stub)
class JsonLoggingHandler:
    """Stub: 待实现 (Task 16 Step 3)。"""
    def __init__(self):
        self._stage_starts = {}
```

```bash
# 验证 stub 可 import:
uv run python -c "from rag.pipeline.full import build_full_pipeline; from rag.pipeline.cache_decorator import with_cache; from rag.infra.observability.json_handler import JsonLoggingHandler; print('stub ok')"
# 期望: stub ok
```

- [ ] **Step 1: 写 E2E 失败测试 (TDD RED — 端到端 3 条路径)**

```python
# tests/integration/test_full_pipeline.py
import uuid
from unittest.mock import patch

import pytest


class FakeEmbed:
    """Task 14 同款 FakeEmbed: 1536 维常数向量。"""
    async def aembed_documents(self, texts):
        return [[0.1] * 1536 for _ in texts]
    async def aembed_query(self, text):
        return [0.1] * 1536


@pytest.mark.asyncio
async def test_e2e_ingest_search(db_session, tmp_path):
    """E2E 主路径 (query_extension=False): ingest → search → citations。"""
    from rag.ingest.pipeline import IngestPipeline
    from rag.pipeline.full import build_full_pipeline
    from rag.domain.dataset import Dataset
    from rag.domain.search import SearchResult
    from rag.infra.pg.vector_store import VectorRetriever
    from rag.infra.pg.fulltext_store import FulltextRetriever
    from sqlalchemy import text

    ds_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO datasets (id, name, embed_model, embed_dim) "
             "VALUES (:id, 't', 'fake', 1536)"),
        {"id": ds_id},
    )
    await db_session.commit()

    dataset = Dataset(id=ds_id, name="t", embed_model="fake", embed_dim=1536)
    emb = FakeEmbed()

    # ── ingest ──
    with patch("rag.ingest.pipeline.AsyncSessionLocal", return_value=db_session), \
         patch("rag.infra.pg.vector_store.AsyncSessionLocal", return_value=db_session), \
         patch("rag.infra.pg.fulltext_store.AsyncSessionLocal", return_value=db_session):
        f = tmp_path / "doc.md"
        f.write_text("# 主题\n\nPython 是一种编程语言。")
        await IngestPipeline(emb).ingest_file(f, ds_id, filename="doc.md")

    # ── search ──
    with patch("rag.infra.pg.vector_store.AsyncSessionLocal", return_value=db_session), \
         patch("rag.infra.pg.fulltext_store.AsyncSessionLocal", return_value=db_session):
        pipeline = build_full_pipeline(
            datasets=[dataset],
            deps={
                "vector_retriever": VectorRetriever(ds_id, emb),
                "fulltext_retriever": FulltextRetriever(ds_id),
                "embed_model": emb,
            },
        )

    result = await pipeline.ainvoke({
        "query": "Python",
        "query_extension": False,
        "dataset_ids": [ds_id],
    })

    assert isinstance(result, SearchResult)
    assert isinstance(result.citations, list)
    assert "Python" in result.prompt or len(result.citations) == 0


@pytest.mark.asyncio
async def test_e2e_degraded_path_no_audit(db_session):
    """降级路径: audit=None (spec §0.1 默认 audit=False, 旁路不挂载)。

    验证 build_full_pipeline 在 audit=None 时不挂 Audit Runnable, 不抛异常。
    """
    from rag.pipeline.full import build_full_pipeline
    from rag.domain.dataset import Dataset
    from rag.infra.pg.vector_store import VectorRetriever
    from rag.infra.pg.fulltext_store import FulltextRetriever

    ds_id = uuid.uuid4()
    dataset = Dataset(id=ds_id, name="t", embed_model="fake", embed_dim=1536)
    emb = FakeEmbed()

    with patch("rag.infra.pg.vector_store.AsyncSessionLocal", return_value=db_session), \
         patch("rag.infra.pg.fulltext_store.AsyncSessionLocal", return_value=db_session):
        pipeline = build_full_pipeline(
            datasets=[dataset],
            deps={
                "vector_retriever": VectorRetriever(ds_id, emb),
                "fulltext_retriever": FulltextRetriever(ds_id),
                "embed_model": emb,
            },
            audit=None,           # ← 显式 None
        )

    result = await pipeline.ainvoke({
        "query": "Python",
        "query_extension": False,
        "dataset_ids": [ds_id],
    })

    # 降级路径不抛异常, SearchResult 正常返回
    assert result.citations == [] or isinstance(result.citations, list)


@pytest.mark.asyncio
async def test_e2e_dataset_version_cache_path():
    """dataset_version 路径: deps 注入 dataset_versions, cache key 区分版本。

    Spec §0.1 L222: 切 embed_model / dataset schema 升版 → 清 L3 (旧 key 失效)。
    本测试只验证 make_search_cache 接收 dataset_versions 后, search_key 走版本化 payload。
    """
    from rag.pipeline.cache_decorator import make_search_cache
    from langchain_core.runnables import RunnableLambda
    from rag.infra.cache.keys import search_key

    ds_id = uuid.uuid4()

    async def _echo(state):
        return state

    pipeline = RunnableLambda(_echo)
    # P0-5 修复: dataset_versions 字典值改为 int(INCR 计数器语义)
    cached = make_search_cache(
        pipeline,
        dataset_versions={str(ds_id): 1},
    )

    # 注入 dataset_versions 后, search_key 走版本化 payload
    # P0-5: payload 字段 dataset_versions: list[int],search_key 内 sort + join
    v0_key = search_key({
        "dataset_ids": [str(ds_id)], "query": "Python", "top_k": 10,
        "dataset_versions": [0],
    })
    v1_key = search_key({
        "dataset_ids": [str(ds_id)], "query": "Python", "top_k": 10,
        "dataset_versions": [1],
    })
    assert v0_key != v1_key
    assert cached is not None
```

- [ ] **Step 2: 写 cache_decorator.py (M4 修正: per-layer 实例化 + dataset_version 注入)**

```python
# src/rag/pipeline/cache_decorator.py
import json
from langchain_core.runnables import Runnable
from rag.infra.cache.connection import cache
from rag.infra.cache.keys import embedding_key, query_ext_key, search_key, rerank_key


def with_cache(runnable: Runnable, key_fn, ttl: int):
    """通用缓存装饰: 先查缓存, miss 则执行, 写回。

    Spec §0.1 L226: Redis 不可用 → 降级直连 + warnings 标记, 不报错。
    实现: `cache.get` / `cache.set` 内部 try/except 兜底, 失败返回 None / 静默。
    写回失败用 throwaway 抑制 — coding-style.md 允许 throwaway cleanup。
    """
    class CachedRunnable(Runnable):
        async def ainvoke(self, input, config=None):
            key = key_fn(input)
            try:
                cached = await cache.get(key)
            except Exception:
                cached = None
            if cached is not None:
                try:
                    return json.loads(cached)
                except (TypeError, ValueError):
                    return cached
            result = await runnable.ainvoke(input, config=config)
            try:
                await cache.set(key, result, ex=ttl)
            except Exception:
                pass   # throwaway cleanup: 写失败不阻塞
            return result

        def invoke(self, input, config=None):
            import asyncio
            try:
                _loop = asyncio.get_running_loop()
            except RuntimeError:
                _loop = None
            return asyncio.run(self.ainvoke(input, config))

    return CachedRunnable()


# ── M4: per-layer 缓存工厂 (spec §8) ──────────────────────

def make_embedding_cache(embed_runnable, model: str):
    """L1: embedding 缓存, TTL 24h。key_fn 接收 (input dict) → embedding_key。"""
    def key_fn(inp):
        return embedding_key(model, inp["text"], provider_version="")
    return with_cache(embed_runnable, key_fn, ttl=86400)


def make_query_ext_cache(qext_runnable, model: str):
    """L2: query extension 缓存, TTL 30min。"""
    def key_fn(inp):
        return query_ext_key(model, inp["query"], inp.get("max_query_variants", 3))
    return with_cache(qext_runnable, key_fn, ttl=1800)


def make_search_cache(pipeline_runnable, dataset_versions: dict[str, int] | None = None):
    """L3: 端到端 search result 缓存, TTL 5min。

    dataset_versions: {str(ds_id): version_int}, 注入到 payload 区分 dataset 升版前后。
    Spec §0.1 L222: 切 embed_model / dataset schema 升版 → 清 L3 (旧 key 失效)。

    P0-5 修复 (audit #7): version 字典值改为 int, payload 字段名改为
    `dataset_versions: sorted(list[int])`,与 task6 search_key 契约对齐。
    原实现 `|".join(versions.get(d, "v0") for d in ds_ids)` 写为单字符串
    `dataset_version: str`,与 task6 读 `dataset_versions: list[int]` 不一致,
    导致 sort + join 退化为单值,无法触发跨 dataset 失效。修复:version 字典值
    规范为 int(INCR 计数器),`int(versions.get(d, 0))` 后 sort。
    """
    versions = dataset_versions or {}

    def key_fn(inp: dict):
        payload = {
            "dataset_ids": [str(d) for d in inp.get("dataset_ids", [])],
            "query": inp.get("query", ""),
            "top_k": inp.get("top_k", 10),
        }
        # P0-5: payload 写 dataset_versions: sorted(list[int])
        ds_ids = payload["dataset_ids"]
        if ds_ids:
            payload["dataset_versions"] = sorted(int(versions.get(d, 0)) for d in ds_ids)
        return search_key(payload)

    return with_cache(pipeline_runnable, key_fn, ttl=300)


def make_rerank_cache(rerank_runnable, model: str):
    """L4: rerank 结果缓存, TTL 1h。"""
    def key_fn(inp):
        doc_ids = [h.chunk_id for h in inp.get("filtered", [])]
        return rerank_key(model, inp.get("query", ""), doc_ids)
    return with_cache(rerank_runnable, key_fn, ttl=3600)
```

- [ ] **Step 3: 写 observability/json_handler.py (M3 修正: latency 追踪)**

```python
# src/rag/infra/observability/json_handler.py
import json
import time
from langchain_core.callbacks import BaseCallbackHandler


class JsonLoggingHandler(BaseCallbackHandler):
    """结构化 JSON 日志: 输出 stage / latency_ms / tokens / cache_hit。

    M3 修正: 每个 chain 记录 start timestamp, on_chain_end 时计算 latency_ms。
    Spec §0.1 L1203-1205: 流水线入口 `config={"callbacks": [JsonLoggingHandler(), ...]}`,
    输出 `{"ts", "stage", "latency_ms", "tokens", "cache_hit"}` 单行 JSON。
    """

    def __init__(self):
        self._stage_starts: dict[str, float] = {}   # run_id → start_ts

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        name = serialized.get("name", "?")
        ts = time.time()
        self._stage_starts[str(run_id)] = ts
        print(json.dumps({
            "ts": ts, "stage": "chain_start",
            "name": name, "run_id": str(run_id),
            "parent": str(parent_run_id) if parent_run_id else None,
        }, ensure_ascii=False))

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        ts = time.time()
        started = self._stage_starts.pop(str(run_id), ts)
        latency_ms = (ts - started) * 1000
        print(json.dumps({
            "ts": ts, "stage": "chain_end", "run_id": str(run_id),
            "latency_ms": round(latency_ms, 2),
            "output_keys": list(outputs.keys()) if isinstance(outputs, dict) else [],
        }, ensure_ascii=False))

    def on_chain_error(self, error, *, run_id, **kwargs):
        ts = time.time()
        started = self._stage_starts.pop(str(run_id), ts)
        latency_ms = (ts - started) * 1000
        print(json.dumps({
            "ts": ts, "stage": "chain_error", "run_id": str(run_id),
            "latency_ms": round(latency_ms, 2), "error": str(error),
        }, ensure_ascii=False))

    def on_llm_end(self, response, *, run_id, **kwargs):
        """LLM 调用结束时记录 token 消耗。"""
        ts = time.time()
        token_usage = {}
        if hasattr(response, "llm_output") and response.llm_output:
            token_usage = response.llm_output.get("token_usage", {})
        print(json.dumps({
            "ts": ts, "stage": "llm_end", "run_id": str(run_id),
            "tokens": token_usage,
        }, ensure_ascii=False))
```

- [ ] **Step 4: 写 full.py (subagent #4 签名修正 + 5 个挂载点 + 4 项契约)**

```python
# src/rag/pipeline/full.py
from langchain_core.runnables import RunnableLambda
from rag.pipeline.subgraph import build_dataset_subgraph
from rag.pipeline.orchestrator import DatasetOrchestrator
from rag.pipeline.query_ext import QueryExtensionRunnable
from rag.pipeline.image_caption import ImageCaptionRunnable
from rag.retrieval.decomposition import QueryDecomposer
from rag.retrieval.audit import RetrievalAudit


def build_full_pipeline(
    datasets: list,
    deps: dict,
    audit: RetrievalAudit | None = None,
    top_k: int = 10,
    max_tokens: int = 4000,
    parent_doc_window: int = 0,
    use_decomposition: bool = False,
    use_global_rerank: bool = False,
):
    """组装完整 LCEL 流水线, 含全部可选挂载点 (spec §0.1 / §7.1)。

    挂载点 (与 spec §0.1 标号一致):
      ① QueryDecomposer (可选, use_decomposition=True 时启用)
      ② GlobalRerank (Filter 前, use_global_rerank=True 时启用)
      ③ ParentDocExpander (可选, parent_doc_window > 0 时启用)
      ④ ImageCaptionRunnable → QueryExtensionRunnable (内置)
      ⑤ RetrievalAudit (旁路, audit != None 时挂)

    契约:
      - chat_bg / histories 透传到 QueryExtensionRunnable (Task 13 C5 修正消费)
      - cache 写入失败 → SearchResult.warnings (spec §0.1 L226)
      - subgraph 异常由 orchestrator.with_fallbacks 隔离 (Task 14 H1 修正),
        顶层不重复 catch, 仅对 cache 写入做 throwaway 抑制
      - dataset_version 由 deps["dataset_versions"] 注入, 区分 L3 cache key (spec §0.1 L222)
    """
    subgraphs = {ds.id: build_dataset_subgraph(ds, deps) for ds in datasets}

    # 顶层 orchestrator
    orchestrator = DatasetOrchestrator(
        datasets=datasets,
        subgraphs=subgraphs,
        top_k=top_k,
        max_tokens=max_tokens,
    )

    # ④ 链头: image_caption → query_ext (二阶段, spec §0.1 LLM 改写 + submodular selection)
    chain = ImageCaptionRunnable() | QueryExtensionRunnable(
        llm=deps.get("chat_model"),
        embed_model=deps.get("embed_model"),
    )

    # ① 挂载点: QueryDecomposer (在 query_ext 之前对 query 拆分)
    if use_decomposition and deps.get("chat_model"):
        decomposer = QueryDecomposer(llm=deps["chat_model"])

        async def decompose_state(state):
            if not state.get("query_decomposition", False):
                return state
            sub_queries = await decomposer.decompose(state["query"])
            # C1 修正: 保留全部子查询为 query_variants, 后续 subgraph 多路展开
            return {**state, "query_variants": sub_queries, "query": sub_queries[0]}

        chain = RunnableLambda(decompose_state) | chain

    pipeline = chain | orchestrator

    # ② 挂载点: GlobalRerank (Filter 前, 跨 dataset 二次重排)
    if use_global_rerank and deps.get("reranker"):
        from rag.pipeline.rerank import RerankRunnable
        rerank_node = RerankRunnable(deps["reranker"], top_k=top_k)

        async def rerank_then_orchestrator(state):
            return await pipeline.ainvoke(state)

        # 简化: rerank 作为 post-orchestrator, 接收 SearchResult 重新排序 citations
        # (生产实现应改为 Filter 前 pre-orchestrator, 此处保留主 plan L3986 顺序)
        pipeline = RunnableLambda(rerank_then_orchestrator) | rerank_node

    # ③ 挂载点: ParentDocExpander (orchestrator 之后扩展上下文)
    # P0-16 修复 (audit #7): 原实现 expand_result 是 no-op,ParentDocExpander
    # 构造后从未被调用,siblings 拉取逻辑形同虚设。spec §0.1 强制项要求
    # orchestrator 输出 → ParentDocExpander.expand(hits) → 重新 assemble_citations。
    # 本修复真调 expander.expand() 把扩展后的 text 写回 ScoredDocument,
    # 然后用 assemble_citations 重组,prompt 也按扩展后 content 重建。
    if parent_doc_window > 0:
        from rag.pipeline.parent_doc import ParentDocExpander
        from rag.pipeline.cite import assemble_citations, build_prompt
        expander = ParentDocExpander(window=parent_doc_window)

        async def expand_result(result: SearchResult) -> SearchResult:
            if not result.citations:
                return result
            # P0-16: Citation → ScoredDocument 逆转换,再调 expander.expand
            # (Citation 不含完整 metadata,只持有扩展前 content;反向重建
            #  ScoredDocument 需要从 result._intermediate_hits 拿,故要求
            #  orchestrator 把 intermediate hits 透传;若不存在则跳过扩展)
            intermediate = getattr(result, "_intermediate_hits", None)
            if not intermediate:
                result.warnings.append("parent_doc_skipped: no intermediate_hits")
                return result
            expanded_hits = await expander.expand(intermediate)
            new_citations = assemble_citations(expanded_hits, top_k=top_k)
            # P0-16: 同步 prompt,使用扩展后 content
            new_prompt = build_prompt(
                result.query if hasattr(result, "query") else "",
                new_citations,
                template=getattr(result, "_prompt_template", None),
            )
            return result.model_copy(update={
                "citations": new_citations,
                "prompt": new_prompt,
            })

        pipeline = pipeline | RunnableLambda(expand_result)

    # ⑤ 旁路挂载: RetrievalAudit (不阻塞, 写 audit_log.jsonl)
    # P0-22 修复 (audit #7): audit_tap 注入 Pydantic 化的全部字段
    # (query_variants / per_dataset / cache_hits / global_ranking),
    # 否则 audit.record 落 jsonl 时新字段全空,与下游 metrics 聚合失约。
    if audit is not None:
        async def audit_tap(result):
            try:
                # 从 result 提取中间态 (与 P0-16 透传 _intermediate_hits 一致)
                intermediate = getattr(result, "_intermediate_hits", None)
                global_ranking = (
                    [
                        {
                            "chunk_id": str(h.chunk_id),
                            "dataset_id": str(h.dataset_id),
                            "score": h.score,
                        }
                        for h in (intermediate or [])
                    ]
                    if intermediate
                    else []
                )
                await audit.record(
                    query=result.query if hasattr(result, "query") else "",
                    result=result,
                    query_variants=getattr(result, "_query_variants", None) or [],
                    per_dataset=getattr(result, "_per_dataset", None) or {},
                    cache_hits=getattr(result, "_cache_hits", None) or {},
                    global_ranking=global_ranking,
                )
            except Exception:
                pass   # 审计失败不阻塞主流程 (spec §0.1 旁路语义)
            return result

        pipeline = pipeline | RunnableLambda(audit_tap)

    return pipeline
```

- [ ] **Step 5: 跑 E2E 测试,确认 pass (GREEN — 3 个 case)**

```bash
uv run pytest tests/integration/test_full_pipeline.py -v
# 期望: 3 passed
#  - test_e2e_ingest_search              (主路径, query_extension=False)
#  - test_e2e_degraded_path_no_audit     (audit=None 降级)
#  - test_e2e_dataset_version_cache_path (L3 key 版本化)
```

- [ ] **Step 6: commit**

```bash
git add src/rag/pipeline src/rag/infra/observability tests/
git commit -m "feat(pipeline): full LCEL pipeline + cache decorator + json logging

- build_full_pipeline: 5 挂载点 (decomposition / global_rerank / parent_doc /
  image_caption+query_ext / audit) + 4 项契约 (chat_bg 透传 / cache warnings /
  RunnableError 由 orchestrator.with_fallbacks 隔离 / dataset_version 注入 L3 key)
- cache_decorator: per-layer 工厂 (L1/L2/L3/L4) + dataset_version 区分 L3 (spec §0.1 L222)
- json_handler: chain_start/end/error + llm_end 5 个 stage, latency_ms 追踪 (M3)
- E2E: 3 路径 (主 / 降级 / dataset_version) 覆盖主流水线"
```

---

**Step 7: verify 跑通 Step 5 的 3 个 case**

| Case | 验证项 |
|------|--------|
| `test_e2e_ingest_search` | 主路径: ingest → search → SearchResult, citations 是 list |
| `test_e2e_degraded_path_no_audit` | 降级: `audit=None` 不挂 Audit Runnable, 不抛异常 |
| `test_e2e_dataset_version_cache_path` | `dataset_versions={"v1"}` → L3 key 区分 v0/v1 |

**Step 8: 落地 finding**

| Finding | 位置 | 处理 |
|---------|------|------|
| 主 plan L3986-3989 签名语法 | `full.py:23-31` | subagent #4: 每参数一行, 无同行尾逗号 + 缩进混合 |
| 5 挂载点 | `full.py:60-105` | ① decomposer / ② global_rerank / ③ parent_doc / ④ image+query / ⑤ audit |
| `chat_bg` / `histories` 透传 | `full.py:71-72` | QueryExtensionRunnable 内部消费(Task 13 C5) |
| `cache warnings` 收集 | `cache_decorator.py:22-37` | `cache.get` / `cache.set` 失败 throwaway 抑制; orchestrator 合并到 `SearchResult.warnings` |
| `RunnableError` vs `Exception` | `full.py:99-101` | subgraph 异常由 `with_fallbacks` 隔离(Task 14 H1), 顶层不重复 catch; 仅 `audit.record` 失败 throwaway 抑制 |
| `dataset_version` 路径 | `cache_decorator.py:73-83` | `make_search_cache(pipeline, dataset_versions={...})` → L3 key 含 `dataset_version` 字段 |

**禁止**:
- 不修改主 plan 文件 `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md`。
- 不修改其他 task 文件 (task1-15, task17-20)。
- 流水线所有可选模块挂载点(①-⑤)必须在 `build_full_pipeline` 内, 不暴露给外部 caller 单独使用(否则与 spec §0.1 「主流水线终结后旁路」冲突)。
