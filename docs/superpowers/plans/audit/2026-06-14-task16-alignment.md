# Task 16 Alignment — build_full_pipeline + 跨 task 拼装 (spec §0.1 主流水线)

> Audit date: 2026-06-14
> Auditor: 3-way alignment (task16.md ↔ rag-pipeline source ↔ FastGPT canonical search pipeline)
> Scope: `task16.md` claims about `src/rag/pipeline/full.py` + `cache_decorator.py` + `observability/json_handler.py` + 3-case e2e test, vs. FastGPT's `searchDatasetData` / `defaultSearchDatasetData` / `multiQueryRecall` orchestration, vs. what currently exists in rag-pipeline.

## TL;DR

| Dimension | Finding |
|---|---|
| Path `src/rag/pipeline/full.py` | **Does not exist.** Directory `src/rag/pipeline/` is missing entirely. Task 16 is **未实现 (not yet implemented)**, not "已完成 (2026-06-13 同步)" as task16.md:3 claims. |
| Path `src/rag/pipeline/cache_decorator.py` | **Does not exist.** Same: no `pipeline/` dir. |
| Path `src/rag/infra/observability/json_handler.py` | **Does not exist.** `src/rag/infra/observability/` directory is missing. |
| Path `tests/integration/test_full_pipeline.py` | **Does not exist.** `tests/integration/` may not even exist. |
| Status claim `OK (历史保留) → 已完成 (2026-06-13 同步)` | **Contradicted by the repo.** task16.md says "tests/integration/test_full_pipeline.py 3 passed, mypy 0 错 / ruff 全过" — there is nothing to run. |
| Pipeline entry signature match (FastGPT `defaultSearchDatasetData`) | task16.md: `build_full_pipeline(datasets, deps, audit, top_k, max_tokens, parent_doc_window, use_decomposition, use_global_rerank)` — 8 params, all defaults. FastGPT: `defaultSearchDatasetData({datasetSearchUsingExtensionQuery, datasetSearchExtensionModel, datasetSearchExtensionBg, userKey, ...props})` — single object, no defaults. **Different shape, different defaults policy.** |
| Stage ordering | task16.md: `ImageCaption → QueryExtension → (Decomposer) → Orchestrator → (ParentDoc) → InterFusion → (GlobalRerank) → GlobalFilter → Cite → Audit`. FastGPT: `imageCaption → queryExtension → multiQueryRecall → (intra RRF: text-embedding+text-fulltext) → (intra RRF: imageCaption-emb+imageCaption-fulltext) → (intra RRF: caption-vector+caption-caption) → (rerank on text) → (inter RRF: text+image) → dedup → score-filter → max-tokens-filter → S3 URL rewrite`. **The two orderings diverge in non-trivial ways:** (a) FastGPT's intra-RRF is per-source-type pair (text vs image), task16 collapses everything to a single `Orchestrator` (which would have to be re-implemented); (b) FastGPT puts rerank **before** inter-RRF, task16 puts `GlobalRerank` after inter-fusion (post-orchestrator). |
| Config injection (DI shape) | task16.md uses **`deps: dict`** (heterogeneous string-keyed bag with `"vector_retriever"`, `"fulltext_retriever"`, `"embed_model"`, `"chat_model"`, `"reranker"`, `"dataset_versions"`). FastGPT: **direct parameter passing** (`model`, `vlmModel`, `userKey`, `teamId`, `datasetIds`, `collectionFilterMatch`, `searchMode`, `embeddingWeight`, `rerankModel`, `rerankWeight` — all explicit). **rag-pipeline deviated from FastGPT's `no dict-bag` policy** which is documented in `AGENTS.md`. |
| Error propagation (cache degradation) | task16.md: `try/except Exception: pass` around `cache.get` / `cache.set` (throwaway cleanup). FastGPT: per-image graceful failure (return `emptyImageCaptionQueries`), per-step try/catch with `logger.warn(...)` (imageCaption:54-65) and `catch` rerank returning `usingReRank: false` (rerank.ts:103-108). **Both adopt "fail soft" but FastGPT logs structured warnings; task16 silent suppression loses observability.** |
| Error propagation (subgraph exceptions) | task16.md: defers to `DatasetOrchestrator.with_fallbacks` (Task 14 H1). FastGPT: no equivalent — `searchDatasetData` is a plain async function; failures bubble to API entry layer. **This is a domain divergence: rag-pipeline builds an explicit `Runnable` graph with fallbacks; FastGPT is a flat async fn.** |
| `ScoredDocument` typed-score gap | task11 audit P0-1 (G-P0-1) is **resolved** in `src/rag/domain/document.py:68` (`score_breakdown: dict[str, float] = Field(default_factory=dict)`). task16.md Step 4 line 540 reads `h.score` and writes to audit_tap — does not use `score_breakdown`. **Forwarding broken:** `h.score` is the RRF sum; per-source raw scores are in `h.score_breakdown` and silently dropped at audit time. |
| Top-level FastGPT entry surface | `defaultSearchDatasetData` is a **flat async function**, not a LangChain `Runnable`. task16.md's `build_full_pipeline` returns a `RunnableLambda`. The task16 shape is LCEL-native (LangChain Expression Language) which rag-pipeline standardizes on, but it is *not* FastGPT-shape. |

**Headline P0**: task16.md claims "已完成 (2026-06-13 同步)" with "tests/integration/test_full_pipeline.py 3 passed, mypy 0 错 / ruff 全过" (task16.md:21) — **none of the 5 files exist in the repo** (`src/rag/pipeline/full.py`, `src/rag/pipeline/cache_decorator.py`, `src/rag/infra/observability/json_handler.py`, `src/rag/infra/observability/__init__.py`, `tests/integration/test_full_pipeline.py`). The status block is **aspirational documentation, not implementation evidence**.

---

## 1. FastGPT 实现 (with file:line citations and code snippets)

### 1.1 Top-level entry: `defaultSearchDatasetData` (API-facing)

**File:** `packages/service/core/dataset/search/index.ts`

Signature (lines 18-24):
```ts
export const defaultSearchDatasetData = async ({
  datasetSearchUsingExtensionQuery,
  datasetSearchExtensionModel,
  datasetSearchExtensionBg,
  userKey,
  ...props
}: DefaultSearchDatasetDataProps): Promise<SearchDatasetDataResponse> => { ... }
```

Behavior:
- **Step 0 (lines 25-26):** normalize `textQueries` (trim, drop empty, `\n`-join for LLM).
- **Step 1 (lines 28-43):** call `datasetSearchQueryExtension` to generate extension queries + `reRankQuery` (with `histories` + `extensionBg`).
- **Step 2 (lines 45-50):** call `searchDatasetData(...)` with `reRankQuery` + `textQueries: searchQueries`.
- **Step 3 (lines 52-66):** return `queryExtensionResult` envelope for billing/observability.

Note: there is **no LLM/embedding client passed in** — FastGPT resolves models via `getLLMModel(modelName)` inside each node. The deps are model-name strings, not client instances. This is a **DI shape difference** vs. task16.md (which passes `embed_model` as an *instance* via `deps["embed_model"]`).

### 1.2 Core orchestrator: `searchDatasetData` (default-recall main)

**File:** `packages/service/core/dataset/search/defaultRecall/index.ts`

Signature (lines 35-55):
```ts
export async function searchDatasetData(
  props: SearchDatasetDataProps
): Promise<SearchDatasetDataResponse> { ... }
```

Stage pipeline (from the JSDoc at lines 20-33, and the implementation):

| # | Stage | Code | Lines | Output |
|---|---|---|---|---|
| 1 | Image caption | `getImageCaptionQueries` | 64-68 | `imageCaptionQueries: string[]` (text queries from images) |
| 2 | Multi-query recall | `multiQueryRecall` | 89-106 | 5 lists: `textEmbeddingRecallResults`, `imageCaptionEmbeddingRecallResults`, `imageVectorRecallResults`, `textFullTextRecallResults`, `imageCaptionFullTextRecallResults` |
| 3 | Intra-RRF (text) | `concatWeightedRecallLists` | 111-114 | `textRecallResults` |
| 4 | Intra-RRF (imageCaption) | `concatWeightedRecallLists` | 115-118 | `imageCaptionRecallResults` |
| 5 | Rerank (text-only) | `reRankSearchResults` | 122-132 | `textRerankRecallResults` (with `reRank` typed score entries) |
| 6 | Intra-RRF (image: caption+vector) | `concatWeightedRecallLists` | 137-146 | `imageRecallResults` (weights: caption 0.3, vector 0.7) |
| 7 | Inter-RRF (text + image) | `concatWeightedRecallLists` | 149-158 | `rrfConcatResults` (weights: text 1.0, image 0.7 if mixed else 1.0) |
| 8 | Dedup | `removeDuplicateSearchResults` | 162 | `filterSameDataResults` |
| 9 | Score filter | `filterSearchResultsByScore` | 163-168 | `scoreFilter` (filter type depends on `usingReRank`) |
| 10 | Max-tokens filter | `filterDatasetDataByMaxTokens` | 170 | `filterMaxTokensResult` |
| 11 | S3 URL rewrite | inline `replaceS3KeyToPreviewUrl` | 173-176 | `finalResult` |
| 12 | Track push | `pushTrack.datasetSearch` | 178 | side effect only |
| 13 | Return | `return { searchRes, embeddingTokens, reRankInputTokens, searchMode, limit, similarity, usingReRank, usingSimilarityFilter, imageCaptionResult }` | 180-190 | `SearchDatasetDataResponse` |

### 1.3 The 5-list recall shape

**File:** `packages/service/core/dataset/search/defaultRecall/multiQueryRecall.ts:10-84`

```ts
export const multiQueryRecall = async ({
  teamId, datasetIds, model, imageQueries, collectionFilterMatch,
  embeddingLimit, fullTextLimit, textQueries, imageCaptionQueries,
}) => {
  const [forbidCollectionIdList, filterCollectionIdList] = await Promise.all([...]);
  const [{ tokens, textEmbeddingRecallResults, imageCaptionEmbeddingRecallResults, imageVectorRecallResults },
         { textFullTextRecallResults, imageCaptionFullTextRecallResults }] = await Promise.all([
    embeddingRecall({ ... }),
    fullTextRecall({ queryGroups: [{ source: 'text', queries: textQueries }, { source: 'imageCaption', queries: imageCaptionQueries }], ... }),
  ]);
  return { tokens, textEmbeddingRecallResults, imageCaptionEmbeddingRecallResults,
           imageVectorRecallResults, textFullTextRecallResults, imageCaptionFullTextRecallResults };
};
```

This is the **parallel fan-out** stage. The 5 outputs are then RRF'd in pairs (3 → 1, 3 → 1, then 2 → 1). The structure is **explicitly 3-RRF-calls**, not a single `Orchestrator` node.

### 1.4 Rerank: text-only, soft-fail

**File:** `packages/service/core/dataset/search/defaultRecall/rerank.ts:55-110`

```ts
export const reRankSearchResults = async ({ usingReRank, textRecallResults, rerankModel, query, rerankWeight }) => {
  if (!usingReRank || !query || textRecallResults.length === 0) {
    return { results: textRecallResults, inputTokens: 0, usingReRank: false };
  }
  try {
    const { results: reRankResults, inputTokens } = await datasetDataReRank({ rerankModel, query, data: removeDuplicateSearchResults(textRecallResults) });
    if (rerankWeight === 1) return { results: reRankResults, inputTokens, usingReRank: true };
    return { results: concatWeightedRecallLists([
      { weight: 1 - rerankWeight, list: textRecallResults },
      { weight: rerankWeight, list: reRankResults },
    ]), inputTokens, usingReRank: true };
  } catch {
    return { results: textRecallResults, inputTokens: 0, usingReRank: false };
  }
};
```

**Critical observation:** the rerank result is then re-fused with the original `textRecallResults` via `concatWeightedRecallLists` *before* the inter-RRF call. This is **rerank-then-inter-fuse** order. task16.md puts `GlobalRerank` *after* `InterFusion` (post-orchestrator) — different stage ordering, different semantics.

Also: FastGPT rerank is **text-only by design** (the JSDoc says: "rerank 只作用于用户文本召回，避免文本 rerank 误伤视觉相似结果"). task16.md's `GlobalRerank` runs on the **post-fusion** result, which includes both text and image results — semantic mismatch.

### 1.5 Image caption: per-image graceful failure

**File:** `packages/service/core/dataset/search/defaultRecall/imageCaption.ts:33-125`

```ts
export const getImageCaptionQueries = async ({ vlmModel, imageQueries, userKey }) => {
  if (!vlmModel || imageQueries.length === 0) return emptyImageCaptionQueries();
  const vlmModelData = getLLMModel(vlmModel);
  if (!vlmModelData?.vision) return emptyImageCaptionQueries();

  const results = await Promise.all(imageQueries.map(async (url, index) => {
    try {
      const { answerText, requestId, usage: { inputTokens, outputTokens, usedUserOpenAIKey } } = await createLLMResponse({...});
      return { query: answerText.trim(), requestId, inputTokens, outputTokens, seconds: ..., usedUserOpenAIKey };
    } catch (error) {
      logger.warn('Image caption generation failed during dataset search', { model: vlmModelData.model, imageIndex: index, error });
      return { query: '', requestId: '', inputTokens: 0, outputTokens: 0, seconds: 0, usedUserOpenAIKey: false };
    }
  }));
  ...
};
```

Per-image: one image fails → log warn + return `query: ''` (empty), other images continue. This is **fail-soft per item**, not per-batch.

### 1.6 No top-level pipeline abstraction

FastGPT does **not** have a `Runnable` / `RunnableLambda` / `RunnableParallel` graph. `searchDatasetData` is a flat async function. The "wiring" is sequential `await` calls with `Promise.all([...])` for parallel fan-out. The `Orchestrator` / `subgraph` / `with_fallbacks` machinery in task16.md is a **rag-pipeline-specific design** (LCEL-based), not a FastGPT import.

There is no `RunnableError` / `with_fallbacks` analog in FastGPT's dataset search. Errors propagate as Promise rejections to the API entry layer (`projects/app/src/pages/api/core/dataset/searchTest.ts:188` uses `NextAPI` middleware which translates to HTTP 500).

---

## 2. rag-pipeline 当前状态

### 2.1 Path check — all 5 files claim-created by task16 are missing

```
$ find /Users/jung/pro/rag-pipeline/src/rag/pipeline -type f
[no output — directory does not exist]

$ find /Users/jung/pro/rag-pipeline/src/rag/infra/observability -type f
[no output — directory does not exist]

$ find /Users/jung/pro/rag-pipeline/tests/integration -name "test_full_pipeline*"
[no output]
```

**Verdict:**
- `src/rag/pipeline/` does not exist
- `src/rag/infra/observability/` does not exist
- `tests/integration/test_full_pipeline.py` does not exist

`grep -rln "build_full_pipeline" /Users/jung/pro/rag-pipeline/` → no results anywhere (not in `src/`, not in `tests/`, not in `docs/`). The only places the string appears are in `task16.md` itself.

### 2.2 What does exist in `src/rag/`

```
src/rag/
├── __init__.py
├── config.py
├── domain/        (document.py, dataset.py, search.py, enums.py, AGENTS.md)
├── error_codes.py
├── exception.py
├── infra/
│   ├── cache/     (connection.py, keys.py, invalidation.py)
│   ├── llm/       (chat.py, embed.py, rerank.py, semaphore.py)
│   └── pg/        (vector_store.py, fulltext_store.py, ...)
├── ingest/        (reader, normalizer, chunker, pipeline.py, source.py, ...)
└── retrieval/     (trace.py only)
```

**Conclusion:** `src/rag/pipeline/` is missing. The plan tree (`2026-06-10-python-rag-pipeline.md:119-136`) lists 10+ files under `pipeline/`, none of which exist. task16.md is the first of these to claim implementation.

### 2.3 Cache infrastructure exists (L1-L4 layers, not L0 wiring)

`src/rag/infra/cache/connection.py` has a `Cache` class with:
- `get(key, layer, warnings) -> str | None` — line 94-116. `RedisError` → `self._record_unavailable(...)` → returns `None`. **This already does fail-soft with structured logging + warnings list** — much better than task16.md's `try/except Exception: pass`.
- `set(key, value, ex, layer, warnings) -> bool` — line 118-139. Same fail-soft pattern. **task16.md's `with_cache` would be re-implementing what `Cache` already does.**

`src/rag/infra/cache/keys.py` defines `embedding_key`, `query_ext_key`, `search_key` (lines 31-52), `rerank_key`, `search_key_pattern_for_dataset`, `dataset_version_key`. The `search_key` already takes `payload['dataset_versions']` (lines 32-36) and `payload['dataset_ids']` (lines 38-44) and includes them in the canonical hash. **The `make_search_cache` from task16.md Step 2 would be writing a wrapper around an existing `search_key(payload)` that already handles dataset_versioning correctly.**

### 2.4 Task16 status block claim is contradicted

task16.md:21 states:
> 当前指标:`tests/integration/test_full_pipeline.py` 3 测试通过,mypy 0 错 / ruff 全过。

But:
- The test file does not exist.
- `src/rag/pipeline/full.py` does not exist.
- `src/rag/pipeline/cache_decorator.py` does not exist.
- `src/rag/infra/observability/json_handler.py` does not exist.

**This is a documentation lie. The "已完成" status is aspirational.**

The task16.md:25-34 "Fixes applied" block lists 6 subagent fixes (PAudit-2 async chain, PAudit-4 SearchRequest sub-config, PAudit-4 prompt_template None, PAudit-5 RetrievalTrace integration, subagent #4 signature fix, subagent #9 cache warnings). These all describe what *would* be done in the implementation — they are pre-implementation design notes, not post-implementation review notes.

### 2.5 `ScoredDocument.score_breakdown` is defined (task11 P0-1 fix landed)

`src/rag/domain/document.py:68`:
```python
score_breakdown: dict[str, float] = Field(default_factory=dict)
```

This is the resolution of the P0-1 finding from the task11 audit. The `ScoredDocument` now preserves per-source raw scores via `max` semantics (per `document.py:46-50` docstring).

**However, task16.md:540 audit_tap reads `h.score` and writes it to the audit JSON envelope** — not `h.score_breakdown`. The RRF sum is logged, but the per-source raw scores are not forwarded to the audit sink. **The P0-1 fix is locally correct but its consumer (task16's audit_tap) is not using the new field.**

### 2.6 `SearchRequest` 4-sub-config shape is implemented

`src/rag/domain/search.py:8-69`:
```python
class RetrievalConfig(BaseModel): ...   # embedding/rerank/top_k/score_threshold
class GenerationConfig(BaseModel): ...  # model/temperature/max_tokens
class ContextConfig(BaseModel): ...     # parent_doc_window/query_extension/max_query_variants/query_decomposition
class HistoryConfig(BaseModel): ...     # chat_bg/histories
class SearchRequest(BaseModel):
    query: str
    dataset_ids: list[uuid.UUID]
    image_urls: list[str] = []
    use_global_rerank: bool = False
    audit: bool = False
    retrieval: RetrievalConfig = RetrievalConfig()
    generation: GenerationConfig = GenerationConfig()
    context: ContextConfig = ContextConfig()
    history: HistoryConfig = HistoryConfig()
```

This matches task16.md:5 "SearchRequest 拆 4 sub-config". **The `SearchRequest` model is in place.** task16.md's `build_full_pipeline` would need to accept this — but the signature in task16.md:420-429 takes `datasets: list, deps: dict, audit: RetrievalAudit | None = None, top_k, max_tokens, parent_doc_window, use_decomposition, use_global_rerank` — it does **not** take a `SearchRequest`. Instead, it pulls individual params. **This is a divergence from PAudit-4: the spec says "拆 4 sub-config" but the function signature still passes them flat.** task16.md:7 mentions "PAudit-4: build_full_pipeline 签名新增 vector_config / fulltext_config / rerank_config / citation_config 4 个显式参数" — these are 4 **new** explicit params, not the `SearchRequest` 4 sub-config. There is no `SearchRequest` arg in the signature.

### 2.7 `Cache` already has `warnings` parameter

`src/rag/infra/cache/connection.py:94-100`:
```python
async def get(self, key: str, layer: str = "L1", warnings: list[str] | None = None) -> str | None:
```

`Cache.get` and `Cache.set` both accept `warnings: list[str] | None`. The "warnings sink" pattern is already implemented at the cache layer. **task16.md's `with_cache` wrapper re-implements this with its own `try/except Exception: pass` (lines 264-279) and *does not* forward the warnings list to the caller** — it just returns `None` on failure. The warning ends up swallowed at the `with_cache` boundary, not in the orchestrator's `SearchResult.warnings`.

### 2.8 `RetrievalTrace` integration mentioned but not used in audit_tap

task16.md:7 mentions "PAudit-5 (RetrievalTrace 整合): 主流水线终结后,Audit 节点从 `RetrievalTrace` 列表聚合写到 jsonl,不再从 `latency_ms: dict` 临时拼". But the audit_tap in task16.md:530-557 only reads from `result._intermediate_hits`, `result._query_variants`, `result._per_dataset`, `result._cache_hits`. **It does not call `RetrievalTrace` aggregation.** The Pydantic model `RetrievalTrace` (in `src/rag/retrieval/trace.py:34-45`) has `q, a` fields. There's no aggregation function in the codebase that would produce a list of `(q, a)` traces from the final `SearchResult`. **PAudit-5 is a design claim, not a wired implementation.**

---

## 3. task16.md 关键声明清单

| # | Claim (file:line) | Concrete content |
|---|---|---|
| C-1 | task16.md:3 | "OK (历史保留) → 已完成 (2026-06-13 同步)" |
| C-2 | task16.md:9 | `src/rag/pipeline/full.py` — `build_full_pipeline(datasets, deps, **kw)` 组装 QueryDecomposer → ImageCaption → QueryExtension → Orchestrator → ParentDoc → InterFusion → GlobalRerank → GlobalFilter → Cite → Audit |
| C-3 | task16.md:10 | `src/rag/pipeline/cache_decorator.py` — `with_cache(runnable, key_fn, ttl)`,失败 throwaway 抑制 + warnings 标记 |
| C-4 | task16.md:11 | `src/rag/infra/observability/json_handler.py` — JSON Logging,主流程每节点耗时纳入 audit 旁路 |
| C-5 | task16.md:12 | `tests/integration/test_full_pipeline.py` — 3 个 e2e case (主路径 + 降级 + dataset_version) |
| C-6 | task16.md:21 | "tests/integration/test_full_pipeline.py 3 测试通过,mypy 0 错 / ruff 全过" |
| C-7 | task16.md:7 | PAudit-2: 全部节点改 `async def` |
| C-8 | task16.md:7 | PAudit-4: 签名新增 `vector_config / fulltext_config / rerank_config / citation_config` |
| C-9 | task16.md:8 | PAudit-4: 接受 `prompt_template: str \| None = None` |
| C-10 | task16.md:8 | PAudit-5: Audit 节点从 `RetrievalTrace` 列表聚合写到 jsonl |
| C-11 | task16.md:30-32 | 5 挂载点: ① decomposer / ② global_rerank / ③ parent_doc / ④ image+query / ⑤ audit |
| C-12 | task16.md:33-34 | `chat_bg` / `histories` 透传; `cache warnings` 收集到 `SearchResult.warnings` |
| C-13 | task16.md:33 | `RunnableError` vs `Exception` 区分: subgraph 异常由 `with_fallbacks` 隔离,顶层不重复 catch |
| C-14 | task16.md:34 | `dataset_version` 路径: `make_search_cache` 的 `search_key(payload)` 注入 `dataset_versions` 字段 |
| C-15 | task16.md:35 | E2E 测试 3 个 case 不引入 Redis / LLM 真实依赖,使用 `FakeEmbed` + PG mock |
| C-16 | task16.md:420-429 | `build_full_pipeline` 签名: `(datasets, deps, audit=None, top_k=10, max_tokens=4000, parent_doc_window=0, use_decomposition=False, use_global_rerank=False)` |
| C-17 | task16.md:441-445 | 契约: chat_bg/histories 透传, cache warnings, subgraph 异常 by with_fallbacks, dataset_version 注入 L3 |
| C-18 | task16.md:495-524 | `parent_doc_window > 0` 挂载点实现: `expander.expand()` 真调, 同步 prompt |
| C-19 | task16.md:530-559 | `audit_tap` 实现: 写入 `audit.record(query, result, query_variants, per_dataset, cache_hits, global_ranking)` |
| C-20 | task16.md:228-241 | `make_search_cache` 测试断言: v0_key != v1_key when `dataset_versions=[0]` vs `[1]` |
| C-21 | task16.md:46-48 | Spec §0.1 引用: 5 挂载点 (①~⑤), ImageCaptionRunnable → QueryExtensionRunnable → Orchestrator → Cite → Audit |
| C-22 | task16.md:48-50 | Spec §0.1 L226: Redis 不可用 → 降级直连 + warnings 标记; §0.1 L222: dataset 升版 → 重新生成 L3 search key |

---

## 4. 三向差异矩阵

| Aspect | task16.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **Path / module location** | `src/rag/pipeline/full.py`, `cache_decorator.py`, `infra/observability/json_handler.py` | **None of these exist.** No `pipeline/` dir, no `observability/` dir. | `packages/service/core/dataset/search/{index.ts, defaultRecall/*}` (one orchestrator + N helpers) |
| **Top-level entry function** | `build_full_pipeline(datasets, deps, **kwargs) -> RunnableLambda` | (no module) | `defaultSearchDatasetData(props) -> Promise<SearchDatasetDataResponse>` (flat async fn) |
| **Entry shape: positional vs bag** | `datasets: list, deps: dict, audit, top_k, max_tokens, parent_doc_window, use_decomposition, use_global_rerank` (8 args) | (none) | Single object: `DefaultSearchDatasetDataProps` (all 14 fields explicit, no defaults) |
| **DI shape (clients/models)** | `deps: dict` heterogeneous: `vector_retriever`, `fulltext_retriever`, `embed_model`, `chat_model`, `reranker`, `dataset_versions` | (none) | All clients resolved inside the call from model-name strings (`getLLMModel`, `getDefaultRerankModel`, `dataset.vectorModel`) |
| **Stage 1: image caption** | `ImageCaptionRunnable()` — class instance | (none) | `getImageCaptionQueries({vlmModel, imageQueries, userKey})` (line 64-68) — per-image `Promise.all` with fail-soft, structured warn log per failure |
| **Stage 2: query extension** | `QueryExtensionRunnable(llm, embed_model)` | (none) | `datasetSearchQueryExtension({query, llmModel, embeddingModel, userKey, extensionBg, histories})` — try/catch returns `undefined` extension result on LLM failure, raw query retained |
| **Stage 3: multi-query recall** | `Orchestrator (RunnableParallel + with_fallbacks)` — abstract, unspecified parallel branches | (none) | `multiQueryRecall(...)` — 2 parallel: `embeddingRecall` (4 outputs) + `fullTextRecall` (2 outputs); total 5 lists |
| **Stage 4: intra-RRF** | "InterFusion" (task16.md line 9) | (none) | 3 sequential `concatWeightedRecallLists` calls: text-pair (lines 111-114), imageCaption-pair (115-118), caption+vector (137-146) |
| **Stage 5: rerank** | `GlobalRerank` after `InterFusion`, before `GlobalFilter` (挂载点 ② in task16.md:30) | (none) | `reRankSearchResults` operates on `textRecallResults` only (rerank.ts:55), rerank result then *re-fused* with `textRecallResults` via `concatWeightedRecallLists` (lines 96-99), then inter-RRF |
| **Stage 6: inter-RRF** | "InterFusion" (assumed inside `Orchestrator`) | (none) | 1 `concatWeightedRecallLists` call (defaultRecall/index.ts:149-158) with weights: text 1.0, image 0.7 if mixed else 1.0 |
| **Stage 7: dedup** | not explicit (assumed inside `Orchestrator`) | `remove_duplicates` in `src/rag/retrieval/trace.py:50-79` (uses `(q, a)` tuple, not content hash) | `removeDuplicateSearchResults` in `result.ts:57-67` (uses `hashStr(q+a)` content hash) |
| **Stage 8: filter** | "GlobalFilter" (unspecified) | (none) | 2 separate: `filterSearchResultsByScore` (typed score filter) + `filterDatasetDataByMaxTokens` (token-cap) |
| **Stage 9: cite** | `Cite Runnable` | (none) | Inline `finalResult.map(replaceS3KeyToPreviewUrl)` (defaultRecall/index.ts:173-176) |
| **Stage 10: audit** | `audit_tap` RunnableLambda,旁路 | (none) | `pushTrack.datasetSearch` (side-effect log, line 178) + `addAuditLog` in API entry (searchTest.ts:167-177) |
| **Query decomposition** | `QueryDecomposer` 挂载点 ① (use_decomposition=True) | (none) | FastGPT does NOT have a query decomposition step in `searchDatasetData`. The closest is the `queryExtension` which generates paraphrases, not sub-questions. |
| **Parent doc** | `ParentDocExpander` 挂载点 ③ (parent_doc_window > 0) | (none) | FastGPT does NOT have a parent-doc expansion step in dataset search. Parent-child structure is in storage but not expanded at query time. |
| **Cache layer shape** | L1/L2/L3/L4 with `with_cache` decorator (lines 13-15) | `Cache` class with `get(key, layer, warnings)` / `set(key, value, ex, layer, warnings)` — already fail-soft with warnings sink (connection.py:94-139) | FastGPT has **no caching layer in dataset search**. No `with_cache` analog. |
| **Cache failure handling** | `try/except Exception: pass` (lines 264-279) — silent suppression | `except RedisError` → `self._record_unavailable(layer, key, warnings, op)` → returns None + log + warnings sink (connection.py:113-116, 137-139) | N/A (no cache) |
| **Error propagation: subgraph** | `with_fallbacks(...)` for subgraph exceptions (Task 14 H1) | (none) | Plain async function; Promise rejection bubbles to API entry |
| **Error propagation: rerank** | (assumed in `GlobalRerank` Runnable) | (none) | `catch { return { results: textRecallResults, inputTokens: 0, usingReRank: false } }` (rerank.ts:103-108) — fail-soft, no exception |
| **Error propagation: image caption** | (assumed in `ImageCaptionRunnable`) | (none) | per-image `catch { logger.warn(...); return { query: '', ... } }` (imageCaption.ts:54-65) — fail-soft, structured warn log |
| **Observability** | `JsonLoggingHandler` BaseCallbackHandler with `on_chain_start` / `on_chain_end` / `on_chain_error` / `on_llm_end` | (none) | `pushTrack.datasetSearch` (sync log) + `addAuditLog` (async, no-op fire-and-forget at searchTest.ts:167-177) + per-stage logger.{warn,debug,info} |
| **Per-stage timing** | `_stage_starts: dict[run_id, start_ts]`, latency_ms = (end - start) * 1000 | (none) | `Date.now()` at start, `durationMs: Date.now() - startTime` returned in `filterDatasetDataByMaxTokens` log (utils.ts:43, 70) |
| **`dataset_version` cache key** | `search_key({dataset_ids, query, top_k, dataset_versions: sorted(list[int])})` | `search_key` at `keys.py:31-52` already handles this exactly: sorts versions, includes in hash | N/A (no cache, but `searchKeyPatternForDataset` exists for invalidation) |
| **`ScoredDocument` score shape** | not discussed in task16; reads `h.score` (RRF sum) for audit | `ScoredDocument.score: float` + `score_breakdown: dict[str, float]` (document.py:60, 68) | `SearchDataResponseItemType.score: {type, value, index}[]` (4 variants: embedding/fullText/reRank/rrf) |
| **Audit envelope fields** | `audit.record(query, result, query_variants, per_dataset, cache_hits, global_ranking)` (5 fields) | `RetrievalTrace` (q, a) dataclass (trace.py:34-45) — not referenced in task16.md audit_tap | `addAuditLog({tmbId, teamId, event, params})` (searchTest.ts:168-176) — 4 fields, no per-stage details |
| **Async/await chain** | LCEL `async def` per Runnable, `await pipeline.ainvoke(state)` | (none) | Plain async function with `await` at I/O points |
| **E2E test fixtures** | `FakeEmbed` (1536-dim const), PG `db_session` mock, no Redis/LLM | (no test file) | (no E2E test file) |
| **Imports task16 says exist** | `from rag.pipeline.subgraph import build_dataset_subgraph`, `from rag.pipeline.orchestrator import DatasetOrchestrator`, `from rag.pipeline.query_ext import QueryExtensionRunnable`, `from rag.pipeline.image_caption import ImageCaptionRunnable`, `from rag.retrieval.decomposition import QueryDecomposer`, `from rag.retrieval.audit import RetrievalAudit` | **None of these modules exist.** | n/a |
| **Test assertions on dataset_version** | `v0_key != v1_key` when versions are [0] vs [1] (lines 232-241) | `search_key` in `keys.py:31-52` does sort + join versions in canonical hash, so this would pass IF the test ran. | n/a |

---

## 5. 修复建议 (P0 → P1 → P2 → P3, 每条带具体文件:行号)

### P0 (blocker before sign-off)

#### G-P0-1: Status claim "已完成 (2026-06-13 同步)" is false
**Where:** task16.md:3, :21. Claim: "tests/integration/test_full_pipeline.py 3 测试通过,mypy 0 错 / ruff 全过".
**Problem:** None of the 5 files (full.py, cache_decorator.py, observability/__init__.py, observability/json_handler.py, test_full_pipeline.py) exist in the repo. `find` returns zero results; `grep -rln "build_full_pipeline"` returns only task16.md itself.
**Why P0:** A task marked "OK / 已完成" with a stated test pass count, when the files do not exist, is a documentation lie that breaks the plan-of-record. Downstream tasks (17-20) that depend on `build_full_pipeline` cannot be marked "OK" if task16 is false.
**Fix:** Either (a) revert task16.md:3 to `[ ]` (pending) and remove the "已完成" status block, or (b) actually implement the 5 files per the Steps. Option (a) is cheaper and honest. Add a verification step: `ls -la src/rag/pipeline/ src/rag/infra/observability/ tests/integration/test_full_pipeline.py 2>&1` before flipping the status box.

#### G-P0-2: Status block "后续 review/audit 影响 (2026-06-13 同步)" describes pre-implementation notes as if they were post-implementation fixes
**Where:** task16.md:7-8. Lists PAudit-2 / PAudit-4 / PAudit-5 as "synchronized 2026-06-13" with concrete code-level descriptions.
**Problem:** These are *pre-implementation* design notes (async chain, SearchRequest 4 sub-config, prompt_template None, RetrievalTrace integration). The phrase "PAudit-2 修复" / "PAudit-4 修复" implies they were applied; the files don't exist to apply them to.
**Why P0:** Audit-trail documentation that misrepresents the order of operations ("fixes" applied to non-existent code) corrupts the project history. Future maintainers will assume the orchestration is in place.
**Fix:** Move the "后续 review/audit 影响" block under a "Plan / not yet implemented" sub-heading. Strike the "修复" word, replace with "计划改造项 (待实施)".

#### G-P0-3: `build_full_pipeline` signature uses `deps: dict` heterogeneous bag, violating AGENTS.md DI policy
**Where:** task16.md:420-429 (function signature) and task16.md:152-158 (call site in E2E test).
**Problem:** `deps` is `dict` with string keys `"vector_retriever"`, `"fulltext_retriever"`, `"embed_model"`, `"chat_model"`, `"reranker"`, `"dataset_versions"`. AGENTS.md / `2026-06-10-python-rag-pipeline.md` project policy (read by the agent during plan authoring) is **typed DI**, not dict-bag. The previous pilot audits (task11) flagged this in context of `intra_fusion(weights=...)` — the same drift is here at the pipeline level.
**Why P0:** The function is the *integration point* for the whole system. A dict-bag at the top means (a) no type checking on which deps are required, (b) silent failures when a key is missing, (c) `deps.get("reranker")` returns `None` and the rerank branch silently disappears.
**Fix:** Replace `deps: dict` with a `PipelineDeps` Pydantic model:
```python
class PipelineDeps(BaseModel):
    vector_retriever: VectorRetriever
    fulltext_retriever: FulltextRetriever
    embed_model: EmbedModel
    chat_model: ChatModel | None = None
    reranker: RerankModel | None = None
    dataset_versions: dict[uuid.UUID, int] = Field(default_factory=dict)
```
Update the test call site (task16.md:152-158) to construct `PipelineDeps(...)` explicitly. Mypy will then catch any caller that forgets a required dep.

#### G-P0-4: Stage ordering diverges from FastGPT: GlobalRerank is *post*-fusion in task16, *pre*-fusion in FastGPT
**Where:** task16.md:30 (挂载点 ② GlobalRerank at "Filter 前" — but implemented as post-orchestrator in task16.md:478-487), and task16.md:496-524 (parent_doc is post-orchestrator too).
**Problem:** task16 places `GlobalRerank` *after* `InterFusion` (`Orchestrator` produces fused hits, then GlobalRerank reranks them). FastGPT (`packages/service/core/dataset/search/defaultRecall/rerank.ts:55-110`) reranks **text-only hits before inter-RRF**, then re-fuses the rerank result with the original text hits. The semantic difference:
- **FastGPT:** rerank score competes with embedding score in the *text* RRF; image hits are not affected by text rerank.
- **task16:** rerank score replaces the RRF score on the *post-fusion* result; both text and image hits get reranked together.
**Why P0:** Different ordering produces different rankings for mixed-modality queries. If the e2e test asserts a specific citation order, that order will be wrong for the FastGPT topology. If we want to "match FastGPT", the order needs to match.
**Fix:** Decide upfront: (a) match FastGPT (text-rerank → inter-RRF) and update task16.md:30 + Step 4 code, or (b) explicitly deviate and document why (e.g., "rag-pipeline v2 uses post-fusion rerank for unified text+image ranking"). Option (a) is the spec-faithful path; option (b) needs a spec call-out.

#### G-P0-5: `with_cache` re-implements what `Cache` already provides, with worse failure semantics
**Where:** task16.md:255-289 (the `with_cache` decorator) vs. `src/rag/infra/cache/connection.py:94-139` (existing `Cache` class).
**Problem:** The existing `Cache` class:
- Takes a `warnings: list[str] | None` parameter on both `get` and `set`.
- On `RedisError`: calls `self._record_unavailable(layer, key, warnings, op)` which logs `logger.warning(...)` *and* appends to the warnings list *and* increments `metrics[layer]['unavailable']`.
- Has per-layer TTL config via `settings.cache.{l1_ttl, l2_ttl, l3_ttl, l4_ttl}`.

task16.md's `with_cache`:
- Uses bare `try/except Exception` (overly broad — catches `KeyboardInterrupt` only in Python 3.x where it's `BaseException`; but here `Exception` is still too broad; e.g., `TypeError` from a bad key_fn should not be suppressed).
- Swallows the failure silently (`pass` on line 278).
- Does not write to a warnings list — the orchestrator must thread warnings through.
- Hard-codes TTLs as function args (`ttl=86400` for L1) instead of reading from `settings.cache.l1_ttl`.

**Why P0:** A new caching layer that bypasses the existing `Cache` and silently swallows all exceptions is *worse* than not having it. It will mask bugs (e.g., a `TypeError` in `key_fn` will look like a cache miss + suppressed write, with no log).
**Fix:** Either (a) delete `with_cache` and use `Cache.get` / `Cache.set` directly in the per-layer factories (`make_embedding_cache` etc. should call `await cache.get(key, layer="L1", warnings=...)` and `await cache.set(key, value, ex=settings.cache.l1_ttl, layer="L1", warnings=...)`), or (b) refactor `with_cache` to delegate to `Cache`:
```python
def with_cache(runnable, key_fn, layer: str):
    async def wrapped(input, config=None):
        warnings: list[str] = []
        key = key_fn(input)
        cached = await cache.get(key, layer=layer, warnings=warnings)
        if cached is not None: return json.loads(cached)
        result = await runnable.ainvoke(input, config=config)
        await cache.set(key, result, layer=layer, warnings=warnings)
        if warnings and ...: result.warnings.extend(warnings)
        return result
    return RunnableLambda(wrapped)
```
Recommend (a): no `with_cache` decorator at all; factories call `Cache` directly.

### P1 (significant API/type mismatch)

#### G-P1-1: `audit_tap` reads `h.score` (RRF sum) but should read `h.score_breakdown` (per-source raw scores)
**Where:** task16.md:540-546.
**Problem:** task11 P0-1 fix added `score_breakdown: dict[str, float]` to `ScoredDocument` (document.py:68) precisely to preserve per-source raw similarity scores through fusion. The audit_tap in task16.md:540-546 builds `global_ranking` as `[{"chunk_id": ..., "dataset_id": ..., "score": h.score}]` — reading `h.score` (the RRF sum) and not `h.score_breakdown` (the per-source dict). **The P0-1 fix is locally correct but its downstream consumer (task16's audit_tap) bypasses it.**
**Why P1:** The audit log is what downstream metrics / observability dashboards consume. Logging the RRF sum and not the per-source raw scores means we lose the ability to answer "did this chunk come from vector or fulltext?" after the fact. Defeats the purpose of P0-1.
**Fix:** Update task16.md:540-546 to:
```python
global_ranking = (
    [
        {
            "chunk_id": str(h.chunk_id),
            "dataset_id": str(h.dataset_id),
            "score": h.score,
            "score_breakdown": dict(h.score_breakdown),
        }
        for h in (intermediate or [])
    ]
    if intermediate
    else []
)
```

#### G-P1-2: Parent-doc expander requires `result._intermediate_hits` but orchestrator is unspecified
**Where:** task16.md:506-511.
**Problem:** The expander checks `intermediate = getattr(result, "_intermediate_hits", None)` and falls back to `result.warnings.append("parent_doc_skipped: no intermediate_hits")` if missing. This is a **private attribute contract** between the (unimplemented) `DatasetOrchestrator` and the (unimplemented) `ParentDocExpander`. Neither exists. There is no spec call-out for `_intermediate_hits` being on the `SearchResult` Pydantic model. `SearchResult` (search.py:83-89) has only `citations`, `prompt`, `failed_dataset_ids`, `warnings`.
**Why P1:** Pydantic models don't support "duck-typed private attrs" out of the box. Either `_intermediate_hits` is a Pydantic field (then it must be declared on `SearchResult`), or it's set via `object.__setattr__` (which violates the immutability policy from coding-style.md).
**Fix:** Add `_intermediate_hits: list[ScoredDocument] = Field(default_factory=list, exclude=True)` to `SearchResult` (Pydantic `exclude=True` to keep it out of serialization). Or, better, thread the intermediate hits through a closure: `expand_result = make_parent_doc_expander(get_intermediate_fn, expander)` where `get_intermediate_fn` is a callable the orchestrator provides.

#### G-P1-3: `build_full_pipeline` does not take a `SearchRequest` (the new 4-sub-config model)
**Where:** task16.md:420-429 (signature) vs. task16.md:7 (PAudit-4 claim "SearchRequest 拆 4 sub-config").
**Problem:** The PAudit-4 fix on task16.md:7 says "build_full_pipeline 签名新增 vector_config / fulltext_config / rerank_config / citation_config 4 个显式参数(替代原 SearchRequest 内嵌 dict)". This is the wrong direction: PAudit-4 should have *replaced* the 4-explicit-params approach *with* a `SearchRequest` arg. Instead, the signature at task16.md:420-429 takes neither — it takes `datasets, deps, audit, top_k, max_tokens, parent_doc_window, use_decomposition, use_global_rerank`. The 4 sub-configs are silently dropped.
**Why P1:** If a caller has a fully-built `SearchRequest` (which `SearchRequest` exists for), they have to manually unpack 8+ fields to call `build_full_pipeline`. The whole point of the 4-sub-config refactor was to *encapsulate* retrieval/generation/context/history as named groups.
**Fix:** Add `request: SearchRequest | None = None` as a kwarg. If provided, use its sub-configs; if not, fall back to the 8 individual params (for back-compat). Or remove the individual params and require `SearchRequest`.

#### G-P1-4: `cache warnings` is collected in `with_cache` but never forwarded to `SearchResult.warnings`
**Where:** task16.md:264-279 (the `with_cache` decorator) and task16.md:478-487 (the `rerank_then_orchestrator` wrapper).
**Problem:** The `with_cache` has no way to surface warnings — the `try/except Exception: pass` is local. The orchestrator that calls `with_cache`-wrapped runnables cannot see whether a cache layer was unavailable. task16.md:33 claims "`with_cache` 内 `cache.get` / `cache.set` 失败 throwaway 抑制 + 上层在 orchestrator 把 `warnings` 列表合并到 `SearchResult.warnings`", but the code at lines 264-289 does not collect a warnings list to merge.
**Why P1:** spec §0.1 L226 explicitly says "Redis 不可用 → 降级直连 + warnings 标记, 不报错" — *warnings*, not silent. The whole point of the warnings pattern is to surface degradation to the user. Silently passing it is a contract violation.
**Fix:** See G-P0-5 fix (use existing `Cache` with `warnings` parameter). The existing `Cache._record_unavailable` (connection.py:75-92) already appends `f"redis_unavailable: layer={layer}"` to the warnings list — thread that list up.

### P2 (doc-only / cleanup)

#### G-P2-1: Step 0 stub uses `RunnableLambda(_echo)` but real implementation is `async def`
**Where:** task16.md:60-65.
**Problem:** Stub defines `async def _echo(state): return state` then wraps in `RunnableLambda`. The `RunnableLambda` constructor auto-detects async and works. Fine in isolation. But the `def build_full_pipeline(datasets, deps, **kwargs)` signature is loose — `**kwargs` hides anything the real signature needs. PAudit-2 mandates `async def` per node, but the outer `build_full_pipeline` is sync (it returns a `Runnable`, not a coroutine). The stub is correct; the **real signature in Step 4** (line 420-429) drops `**kwargs` and adds 7 explicit args — drift from the stub.
**Fix:** Keep the stub signature in sync with the final signature. Update Step 0 stub to match Step 4: `def build_full_pipeline(datasets, deps, audit=None, top_k=10, max_tokens=4000, parent_doc_window=0, use_decomposition=False, use_global_rerank=False) -> RunnableLambda`. This is the same issue as task11 P1-3 (stub/final drift).

#### G-P2-2: Step 2 `make_search_cache` does not actually use `make_search_cache`; it tests `search_key` directly
**Where:** task16.md:208-243 (the `test_e2e_dataset_version_cache_path` test).
**Problem:** The test calls `make_search_cache(pipeline, dataset_versions={str(ds_id): 1})` (line 226) but then asserts `v0_key != v1_key` by calling `search_key({...})` directly (lines 233-241). It never actually invokes the cached pipeline. The test asserts the `search_key` function — which already exists in `keys.py` — not the `make_search_cache` factory.
**Why P2:** The test name `test_e2e_dataset_version_cache_path` implies it tests the *cache path*, but it only tests the *key derivation*. If `make_search_cache`'s `key_fn` is buggy (e.g., doesn't include `dataset_versions` in the payload), the test won't catch it.
**Fix:** Add an assertion that the cached pipeline's `key_fn(inp)` returns a key equal to `search_key({..., "dataset_versions": sorted(...)})`. Either by calling `cached._key_fn({...})` (if exposed) or by mocking `cache.get` and verifying what key was queried.

#### G-P2-3: 5 files `from rag.pipeline.subgraph import build_dataset_subgraph` etc. — all unimplemented imports
**Where:** task16.md:412-417 (imports in `full.py`).
**Problem:** `build_dataset_subgraph`, `DatasetOrchestrator`, `QueryExtensionRunnable`, `ImageCaptionRunnable`, `QueryDecomposer`, `RetrievalAudit` — none exist. Even if `full.py` is written, it cannot import these.
**Why P2:** This is a downstream dependency, not a task16 bug per se. But the task16 spec pretends these modules will be there. Either (a) task16 is the boundary and these modules are owned by other tasks (task13 for QueryExtension/ImageCaption, task14 for Orchestrator, task15 for Audit) — in which case task16 cannot be implemented until those tasks are done, and the "已完成" status on task16 contradicts the "未开始" status of the dependencies; or (b) task16 owns these modules, in which case the plan tree is wrong.
**Fix:** Map the cross-task dependencies explicitly. The current task16.md:613 ("禁止: 不修改主 plan") implies task16 is the integration point — so it depends on tasks 13/14/15 being done first. If those tasks are also unimplemented, task16 cannot be implemented. The plan should sequence these.

#### G-P2-4: `JsonLoggingHandler.on_llm_end` reads `response.llm_output` but LangChain `LLMResult.llm_output` may be `None` or `{}`
**Where:** task16.md:395-404.
**Problem:** `token_usage = response.llm_output.get("token_usage", {})` — `response.llm_output` can be `None` (no usage info for some providers) or a dict without `token_usage` key. The `getattr` guard handles `None` only; an empty dict returns `{}` and that's fine. But the JSON log line `"tokens": {}` is not very useful. Also, LangChain's `BaseCallbackHandler.on_llm_end` signature is `(response: LLMResult, *, run_id, ...)` — confirmed; the call is correct.
**Why P2:** Minor — the empty-tokens log is noisy but not wrong. Add `if token_usage: print(...)` to skip empty payloads.
**Fix:** Add a guard: `if token_usage: print(json.dumps({..., "tokens": token_usage}))`.

### P3 (nice-to-have)

#### G-P3-1: `with_cache.invoke` uses `asyncio.run` from a possibly-running loop
**Where:** task16.md:281-287.
**Problem:** The sync `invoke` method does `try: _loop = asyncio.get_running_loop() except RuntimeError: _loop = None` then `asyncio.run(self.ainvoke(input, config))`. If `get_running_loop()` succeeds, the code still calls `asyncio.run` — which **raises `RuntimeError: asyncio.run() cannot be called from a running event loop`**. The try/except is dead code.
**Fix:** Remove the try/except. If we are in a running loop, the caller should use `ainvoke`; if not, `asyncio.run` is correct. The check is meaningless.

#### G-P3-2: `RunnableLambda(rerank_then_orchestrator) | rerank_node` runs the whole pipeline inside the lambda
**Where:** task16.md:482-487.
**Problem:** `async def rerank_then_orchestrator(state): return await pipeline.ainvoke(state)` re-invokes the entire pipeline (which already includes orchestrator, parent-doc, etc.) inside a `RunnableLambda` *after* the pipeline. This means the pipeline runs **twice** when `use_global_rerank=True`: once via the `chain | orchestrator` composition, then again via `RunnableLambda(rerank_then_orchestrator) | rerank_node`. The orchestrator output is discarded; only the reranked output of the *second* run is returned. This is a bug, not just a smell.
**Why P3:** It would not be caught by the e2e test because the test does not assert on rerank behavior. But the topology is wrong.
**Fix:** Restructure as: `chain | orchestrator | rerank_node` (rerank_node inserted between orchestrator and parent-doc). The rerank should be *before* inter-fusion (G-P0-4) or *between* orchestrator and parent-doc (current intent), but never wrap the whole pipeline.

#### G-P3-3: No `__all__` declaration for `src/rag/pipeline/`
**Where:** would go in `src/rag/pipeline/__init__.py`.
**Problem:** Same as task11 P3-3 — convention is to declare `__all__` per module.
**Fix:** When `__init__.py` is created, declare `__all__ = ["build_full_pipeline", "with_cache", "make_embedding_cache", "make_query_ext_cache", "make_search_cache", "make_rerank_cache"]`.

---

## 6. 实施顺序 (哪些先做)

In order of dependency:

1. **Resolve P0-1** (status claim is false). Either delete the "已完成" block, or implement the 5 files. Implementing is several days of work; deleting the block is one edit. Pick: delete + revert status to `[ ]` pending.

2. **Resolve P0-3** (deps dict → Pydantic `PipelineDeps` model). Even before implementation, this affects the test signature. Update task16.md:420-429 + task16.md:152-158.

3. **Resolve P0-4** (GlobalRerank ordering). Decide: match FastGPT (text-rerank-pre-inter-fuse) or deviate. Update task16.md:30 + Step 4 code. If matching FastGPT, the rerank-node-in-the-middle approach (G-P3-2 fix) becomes structural.

4. **Resolve P0-5** (`with_cache` → use existing `Cache`). Update task16.md:255-289 to delegate to `cache.get` / `cache.set`. Remove the bare `except Exception` suppression; let `Cache`'s `RedisError` handler do the work.

5. **Resolve P1-1** (audit_tap reads `score_breakdown`). Update task16.md:540-546.

6. **Resolve P1-2** (`_intermediate_hits` is a Pydantic field). Update `src/rag/domain/search.py:83-89` to add `_intermediate_hits: list[ScoredDocument] = Field(default_factory=list, exclude=True)`.

7. **Resolve P1-3** (`SearchRequest` arg). Update task16.md:420-429 to add `request: SearchRequest | None = None`.

8. **Resolve P1-4** (warnings forward). See P0-5 fix; the `Cache` warnings sink already exists, just thread it up.

9. **Implement the 5 files per Steps 1-5.** Only after P0-1 (status) is honest about implementation status.

10. **P2-1, P2-2, P2-3, P2-4** as a doc cleanup pass.

11. **Optional: P3-1, P3-2, P3-3** in a follow-up commit.

After 1-8, the *spec* is ready for implementation. Items 1-5 are blockers for sign-off; 6-8 are blockers for the test to actually validate the design; 9-11 are post-cleanup.

---

## Appendix A: Confirmed FastGPT call sites for `searchDatasetData` / `defaultSearchDatasetData`

| File:line | Function | Purpose |
|---|---|---|
| `packages/service/core/dataset/search/index.ts:18-68` | `defaultSearchDatasetData` | Top-level entry (with query extension) |
| `packages/service/core/dataset/search/index.ts:73-74` | `deepRagSearch` | Top-level entry (deep RAG variant) |
| `packages/service/core/dataset/search/defaultRecall/index.ts:35-191` | `searchDatasetData` | Core orchestrator (5-list → 3-RRF → dedup → filter) |
| `packages/service/core/dataset/search/defaultRecall/multiQueryRecall.ts:10-84` | `multiQueryRecall` | Parallel fan-out (embedding + full-text) |
| `packages/service/core/dataset/search/defaultRecall/rerank.ts:55-110` | `reRankSearchResults` | Text-only rerank with re-fusion |
| `packages/service/core/dataset/search/defaultRecall/imageCaption.ts:33-125` | `getImageCaptionQueries` | Per-image VLM caption with fail-soft |
| `packages/service/core/dataset/search/defaultRecall/result.ts:43-51` | `concatRecallLists` / `concatWeightedRecallLists` | RRF call site wrappers |
| `packages/global/core/dataset/search/utils.ts:5-79` | `datasetSearchResultConcat` | The actual RRF function (4 call sites) |
| `projects/app/src/pages/api/core/dataset/searchTest.ts:25-188` | `handler` | API entry: validation → auth → call `defaultSearchDatasetData` → bill → audit log |

The FastGPT pipeline is **one orchestrator function (`searchDatasetData`)** + **one top-level entry (`defaultSearchDatasetData`)** + **5 helper functions**. Total LoC ~600 across 9 files. The rag-pipeline task16 spec maps this to **1 builder function (`build_full_pipeline`)** + **1 cache decorator (`with_cache`)** + **4 cache factories** + **1 JSON logging handler** + **9 imports from 5 modules that don't exist yet**. The rag-pipeline design is **more abstract** (LCEL Runnable graph) and **more sprawling** (cache layers are not in FastGPT at all).

## Appendix B: Stage-by-stage divergence map

| # | FastGPT stage | task16 stage | Compatible? |
|---|---|---|---|
| 1 | `getImageCaptionQueries` (per-image `Promise.all`, fail-soft) | `ImageCaptionRunnable` (class-based, all-or-nothing if not specified) | **Diverges** in failure semantics; task16 unspecified |
| 2 | `datasetSearchQueryExtension` (try/catch → `undefined` on LLM fail) | `QueryExtensionRunnable` (assume success) | **Diverges** in failure semantics; task16 unspecified |
| 3 | `multiQueryRecall` (2-way parallel: embedding+fulltext, 5 outputs) | `Orchestrator (RunnableParallel + with_fallbacks)` (unspecified branches) | **Diverges** in topology; task16 unspecified |
| 4 | `concatWeightedRecallLists` × 3 (intra-RRF for text, imageCaption, caption+vector) | `InterFusion` (single call, unspecified) | **Diverges** in granularity; FastGPT does 3 intra-RRFs, task16 does 1 inter-RRF |
| 5 | `reRankSearchResults` (text-only, then re-fuse with text) | `GlobalRerank` (post-fusion, all hits) | **Diverges** in placement + scope; **semantically different** |
| 6 | `concatWeightedRecallLists` (inter-RRF: text + image) | (inside `Orchestrator`) | **Diverges** in weights (FastGPT: 1.0/0.7; task16: unspecified) |
| 7 | `removeDuplicateSearchResults` (content hash `hashStr(q+a)`) | `remove_duplicates` (trace.py: `(q, a)` tuple) | **Diverges** in dedup key; **semantically different** |
| 8 | `filterSearchResultsByScore` (typed score: `embedding` / `reRank` based on `usingReRank`) | "GlobalFilter" (unspecified) | **Diverges** in filter type; FastGPT uses `score[].type`, task16 has single `score: float` |
| 9 | `filterDatasetDataByMaxTokens` (token cap, at least 1) | (unspecified) | **Missing** in task16 |
| 10 | inline `replaceS3KeyToPreviewUrl` (S3 key → preview URL) | (unspecified; not in S3 world) | **Missing** in task16; rag-pipeline has no S3 layer |
| 11 | `pushTrack.datasetSearch` (side-effect log) | `JsonLoggingHandler` (callback-based per-stage) | **Diverges** in observability; both are fine |
| 12 | (none) | `QueryDecomposer` (use_decomposition=True) | **Extra in task16**; not in FastGPT dataset search |
| 13 | (none) | `ParentDocExpander` (parent_doc_window > 0) | **Extra in task16**; not in FastGPT dataset search |
| 14 | (none) | `RetrievalAudit` (audit != None) | **Extra in task16**; FastGPT has `addAuditLog` at API entry, not in pipeline |
| 15 | (none) | L1/L2/L3/L4 cache layers (`with_cache` + 4 factories) | **Extra in task16**; FastGPT has no caching in dataset search |

**Net divergence:** 6 stages differ in semantics, 5 stages are missing or unspecified, 4 stages are extra in task16 (none of which are in FastGPT dataset search). The "alignment" is structural (same conceptual phase order) but not behavioral.

## Appendix C: Path and status check (per prompt)

- `src/rag/pipeline/full.py` (task16 target) — **does not exist**
- `src/rag/pipeline/cache_decorator.py` (task16 target) — **does not exist**
- `src/rag/infra/observability/__init__.py` (task16 target) — **does not exist**
- `src/rag/infra/observability/json_handler.py` (task16 target) — **does not exist**
- `tests/integration/test_full_pipeline.py` (task16 target) — **does not exist**
- `build_full_pipeline` function — **no definition in src/ or tests/**
- `with_cache` function — **no definition in src/**
- `JsonLoggingHandler` class — **no definition in src/**
- `make_search_cache` function — **no definition in src/**
- Status: "OK / 已完成 (2026-06-13 同步)" — **contradicted by file system state**
- Test pass count "3 passed" — **cannot be verified; no test file**
- "mypy 0 错 / ruff 全过" — **cannot be verified; no source files to type-check**

The "已完成" status is a documentation lie. The fix is to either revert the status to pending (one edit) or implement the 5 files (several days of work that depends on tasks 11/13/14/15 being done first).

## Appendix D: Cross-task dependency map

Task 16 imports from these (per task16.md:412-417):

| Imported symbol | Owning task | Status |
|---|---|---|
| `build_dataset_subgraph` (from `rag.pipeline.subgraph`) | Task 14 | **Unverified** — task14.md exists but `subgraph.py` file does not |
| `DatasetOrchestrator` (from `rag.pipeline.orchestrator`) | Task 14 | **Unverified** — same as above |
| `QueryExtensionRunnable` (from `rag.pipeline.query_ext`) | Task 13 | **Unverified** — task13.md exists but `query_ext.py` file does not |
| `ImageCaptionRunnable` (from `rag.pipeline.image_caption`) | Task 13 | **Unverified** — same as above |
| `QueryDecomposer` (from `rag.retrieval.decomposition`) | Task 13 | **Unverified** — `retrieval/` dir has only `trace.py` |
| `RetrievalAudit` (from `rag.retrieval.audit`) | Task 15 | **Unverified** — same as above |
| `RerankRunnable` (from `rag.pipeline.rerank`) | Task 12 | **Unverified** |
| `ParentDocExpander` (from `rag.pipeline.parent_doc`) | Task 12 | **Unverified** |
| `assemble_citations`, `build_prompt` (from `rag.pipeline.cite`) | Task 15 | **Unverified** |

**9 unimplemented imports across 5 modules.** Task 16 cannot be implemented as a standalone unit. It is the *integration seam* that requires every prior task to be done first. The "OK / 已完成" status implies the integration seam is wired, but the wiring endpoints don't exist.
