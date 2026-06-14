# Task 14 Alignment — Subgraph + Orchestrator + Rerank + Cite + Parent Doc

> Audit date: 2026-06-14
> Auditor: 3-way alignment (task14.md ↔ rag-pipeline source ↔ FastGPT canonical pipeline)
> Scope: `task14.md` claims about 5 bundled sub-features (subgraph signature, orchestrator state machine, rerank function, cite function, parent_doc retrieval) vs. what FastGPT actually does vs. what currently exists in rag-pipeline.

## TL;DR

| Dimension | Finding |
|---|---|
| `src/rag/pipeline/` directory | **Does not exist.** `find` returns zero hits. The task14 status banner claims "已完成 (2026-06-13 同步)" with 5 files inside `src/rag/pipeline/`, but **none of the 5 sub-feature modules exist on disk** (subgraph.py / orchestrator.py / rerank.py / cite.py / parent_doc.py). Task 14 is a **spec-only / not-yet-implemented** task, not a completed one. |
| `src/rag/infra/llm/rerank_chunk.py` | **Does not exist.** `ls` shows only `rerank.py` (133 lines) inside `src/rag/infra/llm/`. The `ChunkedCohereRerank` re-export pattern is unimplemented. The existing `rerank.py` defines a different family: `Reranker` Protocol + `QwenRerank` (DashScope compatible-api) + `NoOpRerank` (qwen3-rerank path, not Cohere). |
| Tests directory | `test_rerank.py` exists but tests `QwenRerank` HTTP parsing (45 lines), not the claimed `RerankRunnable` / `GlobalRerankRunnable` / `assemble_citations` / `build_prompt` / `ParentDocExpander` surfaces. **`test_orchestrator.py`, `test_cite.py`, `test_parent_doc.py`, `test_global_rerank.py` are all missing.** |
| Topology divergence (Subgraph + Orchestrator) | **Major.** task14.md models a single `SearchSubgraph` Runnable + a top-level `DatasetOrchestrator` (`RunnableParallel` + `with_fallbacks`). FastGPT's reality is a **multi-node DAG** dispatched by `dispatchDatasetSearch` (one workflow node calling `searchDatasetData` + `dispatchDatasetConcat`), with cross-dataset aggregation performed at the workflow edge, not in a dedicated `Orchestrator` class. The rag-pipeline abstraction is more pythonic, but no equivalent `pipeline/orchestrator.py` exists. |
| Rerank weight default (B12) | **Partially correct.** Both FastGPT and rag-pipeline use `rerankWeight = 0.5` (FastGPT `defaultRecall/index.ts:52`, rag-pipeline `domain/search.py:18` `rerank_weight: float = 0.5`). But the claimed `RerankRunnable.__init__(weight=0.5)` and `intra_fusion(weights=[self.weight, 1.0 - self.weight])` pattern does not exist — there is no `RerankRunnable` class anywhere. |
| Rerank "only text source" (subagent #8) | **FastGPT-aligned semantically** (`reRankSearchResults` operates on `textRecallResults` only and appends `reRank` typed score). But the rag-pipeline implementation side is **missing**: the `RerankRunnable` and its text/caption split filter don't exist. |
| Rerank formula (B13) | **Correct on paper.** task14.md cites FastGPT's `datasetSearchResultConcat` rank-based RRF (`weight * 1/(60+rank)`) and uses `intra_fusion(query_groups=[rerank_ranked, text_hits], weights=[self.weight, 1.0-self.weight])`. rag-pipeline's existing `intra_fusion` and `inter_dataset_fusion` (per task 11) are spec'd but not yet on disk either. |
| Cite format | **Misaligned.** task14.md's `build_prompt` produces `[1] 来源:...` blocks (per rag-pipeline's `DEFAULT_PROMPT_TEMPLATE`). FastGPT uses **`[id](CITE)` inline** in markdown (see `dataset.const.ts:7-8` `**[id](CITE)**`) — citations are *placed in generated LLM output*, not in a prefix prompt. The two patterns serve different purposes (one is a system prompt; the other is a reply-formatting rule). The `assemble_citations` mapping itself (`ScoredDocument → Citation`) is FastGPT-aligned, but **does not exist on disk**. |
| Parent doc retrieval | **FastGPT has no equivalent.** Searched all of `packages/service/core/dataset/`: zero hits for `parentDoc` / `parentChunk` / `expandWindow` / `siblingChunk` retrieval. FastGPT's `parent*` references are all about `parentCollectionIds` (folder hierarchy for collection-level filter), not chunk-level windowing. The rag-pipeline `parent_doc.py` is therefore an invention beyond FastGPT, with `ChunkRepository.get_siblings` (lines 124-144) being the only supporting primitive. **The `ParentDocExpander` class is unimplemented.** |
| `RetrievalTrace` + `remove_duplicates` (trace.py) | **Exists** (80 lines). The function is **NOT** FastGPT-aligned: FastGPT's `removeDuplicateSearchResults` (`defaultRecall/result.ts:57-67`) deduplicates by `hashStr(`${q}${a}`.replace(/[^\p{L}\p{N}]/gu, ''))` — i.e. a normalized **content hash of q+a**, computed inline. rag-pipeline's `remove_duplicates` is a generic protocol-based `(q, a)` tuple keying that takes a parallel `RetrievalTrace` array — a more general but **incompatible** shape. The trace is also defined as a separate `dataclass`, decoupling `q/a` from `ScoredDocument` (a design choice rag-pipeline's audit round just made; FastGPT has q/a as **fields of the score item itself**). |

**Headline P0 (sign-off blocker)**: task14.md status banner says "已完成 (2026-06-13 同步)" with all 5 modules delivered, but **`src/rag/pipeline/` directory does not exist** and the claimed `RerankRunnable` / `GlobalRerankRunnable` / `SearchSubgraph` / `DatasetOrchestrator` / `ParentDocExpander` / `assemble_citations` / `build_prompt` are **all missing from disk**. The "completed" claim is a **false positive** — this is the largest task in the plan (~44KB) and none of its main code surface has landed. Status must be reverted to "未实现" before peer review.

---

## 1. FastGPT 实现 (with file:line citations and code snippets)

### 1.1 Topology: workflow node DAG, not single subgraph

**FastGPT has no `Subgraph` class.** The dataset search path is a **workflow node** (`dispatchDatasetSearch`) that calls `searchDatasetData` and writes results to `[NodeOutputKeyEnum.datasetQuoteQA]`. Multi-dataset aggregation happens via a **separate workflow node** (`dispatchDatasetConcat`) that reads all dataset outputs from `params.quoteMap` and runs `datasetSearchResultConcat` (uniform weight 1) across them.

Files:
- `packages/service/core/workflow/dispatch/dataset/search.ts:56-342` — `dispatchDatasetSearch`
- `packages/service/core/workflow/dispatch/dataset/concat.ts:21-48` — `dispatchDatasetConcat`
- `packages/service/core/dataset/search/index.ts` — exports `defaultSearchDatasetData` and `deepRagSearch`

The "subgraph" in FastGPT is implicit: each workflow step is a node, edges carry data. The LCEL `Runnable` / `RunnableParallel` / `with_fallbacks` style is **foreign** to FastGPT. There is no `DatasetOrchestrator` class — orchestration is the workflow graph itself.

### 1.2 FastGPT canonical `searchDatasetData` (the de-facto per-dataset subgraph)

`packages/service/core/dataset/search/defaultRecall/index.ts:35-191` — `searchDatasetData`:

```ts
// Step 2: 召回 (multi-query + multi-source)
const {
  textEmbeddingRecallResults, textFullTextRecallResults,
  imageCaptionEmbeddingRecallResults, imageCaptionFullTextRecallResults,
  imageVectorRecallResults,
  tokens: embeddingTokens,
} = await multiQueryRecall({...});

// Step 3: 同源融合 (intra-source)
const textRecallResults = concatWeightedRecallLists([
  { weight: embeddingWeight, list: textEmbeddingRecallResults },
  { weight: 1 - embeddingWeight, list: textFullTextRecallResults },
]);
const imageCaptionRecallResults = concatWeightedRecallLists([
  { weight: embeddingWeight, list: imageCaptionEmbeddingRecallResults },
  { weight: 1 - embeddingWeight, list: imageCaptionFullTextRecallResults },
]);

// Step 4: rerank 只处理文本召回
const { results: textRerankRecallResults, inputTokens: reRankInputTokens,
        usingReRank: finalUsingReRank } = await reRankSearchResults({
  usingReRank, textRecallResults, rerankModel, query: reRankQuery, rerankWeight,
});

// Step 5+6: 跨模态融合
const imageRecallResults = concatWeightedRecallLists([
  { weight: imageCaptionRecallResults.length > 0 ? 0.3 : 0, list: imageCaptionRecallResults },
  { weight: imageVectorRecallResults.length > 0 ? 0.7 : 0, list: imageVectorRecallResults },
]);
const rrfConcatResults = concatWeightedRecallLists([
  { weight: textRerankRecallResults.length > 0 ? 1 : 0, list: textRerankRecallResults },
  { weight: imageRecallResults.length > 0 ? (hasTextQuery ? 0.7 : 1) : 0, list: imageRecallResults },
]);

// Step 7: 去重 -> 阈值 -> token
const filterSameDataResults = removeDuplicateSearchResults(rrfConcatResults);
const { results: scoreFilter, usingSimilarityFilter } = filterSearchResultsByScore({...});
const filterMaxTokensResult = await filterDatasetDataByMaxTokens(scoreFilter, maxTokens);
```

Key observations:
- 8 explicit steps, all in one function — no orchestrator class.
- Step 2 multi-source recall is parallelized by `multiQueryRecall` (which itself calls `embeddingRecall` + `fullTextRecall` + `imageCaptionEmbeddingRecall` + ...).
- Step 3 (intra) and Step 5+6 (inter) are the same `concatWeightedRecallLists` called with different weights.
- Step 4 rerank is gated by `usingReRank` AND `reRankQuery` AND `getDefaultRerankModel()` (line 60).
- Step 7 dedup **runs AFTER** the cross-modal RRF, **not before rerank**. (task14.md:38-40 reverses this: "Rerank 入口前去重" via subagent #8.) **This is a sequencing divergence from FastGPT.**

### 1.3 FastGPT canonical rerank

`packages/service/core/dataset/search/defaultRecall/rerank.ts:7-110`:

```ts
// Lines 7-50: datasetDataReRank
const datasetDataReRank = async ({ rerankModel, data, query }) => {
  const { results, inputTokens } = await reRankRecall({
    model: rerankModel, query,
    documents: data.map((item) => ({
      id: item.id,
      text: `${item.q}\n${item.a}`.trim()  // <-- q+a concat (NOT ScoredDocument.text)
    }))
  });
  // add new score to data
  const mergeResult = results
    .map((item, index) => {
      const target = data.find((dataItem) => dataItem.id === item.id);
      const score = item.score || 0;
      return {
        ...target,
        score: [{ type: SearchScoreTypeEnum.reRank, value: score, index }]  // <-- typed score, replaces previous
      };
    })
    .filter(Boolean);
  return { results: mergeResult, inputTokens };
};

// Lines 55-110: reRankSearchResults
export const reRankSearchResults = async ({ usingReRank, textRecallResults, rerankModel, query, rerankWeight }) => {
  if (!usingReRank || !query || textRecallResults.length === 0) {
    return { results: textRecallResults, inputTokens: 0, usingReRank: false };
  }
  try {
    const { results: reRankResults, inputTokens } = await datasetDataReRank({
      rerankModel, query, data: removeDuplicateSearchResults(textRecallResults)  // <-- pre-dedup
    });
    if (rerankWeight === 1) {
      return { results: reRankResults, inputTokens, usingReRank: true };
    }
    return {
      results: concatWeightedRecallLists([
        { weight: 1 - rerankWeight, list: textRecallResults },
        { weight: rerankWeight, list: reRankResults }
      ]),
      inputTokens, usingReRank: true
    };
  } catch {
    return { results: textRecallResults, inputTokens: 0, usingReRank: false };
  }
};
```

Critical canonical details:
- **Document payload is `${item.q}\n${item.a}.trim()`** — not `item.text` / not a "ScoredDocument.text" field. FastGPT's `DatasetDataSchemaType` carries `q` (question) + `a` (answer) as **separate fields**, and the rerank prompt sees both. rag-pipeline's `ScoredDocument` has only `text: str` — this is a **content-shape mismatch**.
- **Pre-dedup on textRecallResults before rerank**: `removeDuplicateSearchResults(textRecallResults)` is called at `rerank.ts:84`. **This confirms task14.md:38 "Rerank 入口前去重" is FastGPT-aligned** (though the dedup key is content-hash, not (q,a) tuple — see §1.5).
- **`rerankWeight === 1` short-circuit**: returns `reRankResults` directly without RRF merging. task14.md:41 "weight=1.0 短路" is FastGPT-aligned.
- **Rerank doc-side text split**: FastGPT's `reRankRecall` (`packages/service/core/ai/rerank/index.ts:41-150`) does **not** do `text2Chunks` client-side. Instead it uses **server-side `model.maxToken`** (line 53 `rerankMaxToken = model.maxToken || 8000`) and **truncates the document list at the rerank model boundary** (line 56 `Promise.reject('Rerank query too long')` on query overflow). The "split a long doc into chunks, rerank each, take max" pattern is **not in FastGPT's `reRankRecall`** — it might be a Cohere API convention but FastGPT doesn't implement it. **task14.md:39-41 (`text2Chunks` + `__chunk_i` + `existsId`) is not a FastGPT pattern**; it's an extrapolation from Cohere's doc-length guidance. **P2: untested invariant; needs doc or removal.**

### 1.4 FastGPT typed-score merge on duplicate `id`

When `datasetSearchResultConcat` merges two lists, the `score[]` typed array is **merged per-type with `max`**, plus the new `rrf` typed entry is appended (`packages/global/core/dataset/search/utils.ts:25-69`). When the rerank result re-merges, it also keeps the `reRank` typed entry alongside the `embedding` / `fullText` entries from upstream.

rag-pipeline's `ScoredDocument.score: float` (single value) cannot model this. task11.md's `score_breakdown: dict[str, float] = Field(default_factory=dict)` (added during the prior audit) gives a per-source map but **does not store the `index` field** that FastGPT's `SearchScoreTypeEnum` requires. task14.md implementation does not extend `score_breakdown` to support the `index` position either.

### 1.5 FastGPT dedup is content-hash, not (q, a) tuple

`packages/service/core/dataset/search/defaultRecall/result.ts:57-67`:

```ts
export const removeDuplicateSearchResults = (data: SearchDataResponseItemType[]) => {
  const set = new Set<string>();
  return data.filter((item) => {
    const str = hashStr(`${item.q}${item.a}`.replace(/[^\p{L}\p{N}]/gu, ''));
    if (set.has(str)) return false;
    set.add(str);
    return true;
  });
};
```

Key: **the dedup key is `q + a` content with all non-letter/non-digit chars stripped, then hashed**. This is a per-call inline function. There is **no separate `RetrievalTrace` dataclass** in FastGPT — `q` and `a` are fields of the score item itself (`SearchDataResponseItemType`).

rag-pipeline's `remove_duplicates` (in `src/rag/retrieval/trace.py:50-79`):
```python
def remove_duplicates(
    docs: list[ScoredDocumentLike],
    traces: list[RetrievalTrace],
) -> list[ScoredDocumentLike]:
    if len(docs) != len(traces):
        msg = f"docs/traces length mismatch: {len(docs)} != {len(traces)}"
        raise ValueError(msg)
    seen: set[tuple[str | None, str | None]] = set()
    out: list[ScoredDocumentLike] = []
    for doc, trace in zip(docs, traces, strict=True):
        key = (trace.q, trace.a)
        if key in seen:
            continue
        seen.add(key)
        out.append(doc)
    return out
```

Divergences:
1. **Input shape**: FastGPT takes a single `data[]`; rag-pipeline requires a parallel `traces[]` (strict length match).
2. **Key computation**: FastGPT normalizes `q+a` (strip punctuation, hash); rag-pipeline uses the raw `(q, a)` tuple — different chunks with same q/a *and* different punctuation are deduped by FastGPT but kept as duplicates by rag-pipeline.
3. **API surface**: FastGPT's function name (`removeDuplicateSearchResults`) and rag-pipeline's (`remove_duplicates`) match semantically, but the **presence of a `RetrievalTrace` parallel array is unique to rag-pipeline** (per its audit round decision).

### 1.6 FastGPT cite format

`packages/global/core/ai/prompt/dataset.const.ts:1-19`:

```ts
export const getDatasetSearchToolResponsePrompt = () => {
  return `## Role
你是一个知识库回答助手，可以 "cites" 中的内容作为本次对话的参考。为了使回答结果更加可信并且可追溯，你需要在每段话结尾添加引用标记，标识参考了哪些内容。

## 追溯展示规则

- 使用 **[id](CITE)** 格式来引用 "cites" 中的知识，其中 CITE 是固定常量, id 为引文中的 id。
- 在 **每段话结尾** 自然地整合引用。例如: "Nginx是一款轻量级的Web服务器、反向代理服务器[67e517e74767063e882d6861](CITE)。"。
- 每段话**至少包含一个引用**，多个引用时按顺序排列
- 不要把示例作为知识点。
- 不要伪造 id，返回的 id 必须都存在 cites 中！...
`;
};
```

Key: **FastGPT's cite format is a LLM instruction in the system prompt that produces inline `[id](CITE)` markers in the LLM's *output***, not a prefix block in the user prompt. The cited `id` is the chunk's `String(_id)`, and the LLM is told to inline them per-paragraph.

task14.md's `build_prompt` (lines 401-410):
```python
def build_prompt(query, citations, template=None):
    tpl = template or DEFAULT_PROMPT_TEMPLATE
    cite_blocks = "\n\n".join(
        f"[{i+1}] 来源:{c.source_name}\n{c.content}"
        for i, c in enumerate(citations)
    )
    return tpl.format(citations=cite_blocks, query=query)
```

This produces a **prefix block of `[1] 来源:f.md\ncontent` entries** in the prompt — fundamentally different from FastGPT's "[id](CITE) inline in output" pattern. The two are **not equivalent**: one is pre-formatted evidence appended to the prompt; the other is an instruction telling the LLM to embed references in its own response. rag-pipeline's design is reasonable but is **its own invention, not a FastGPT port**.

rag-pipeline's existing `Citation` model (`src/rag/domain/search.py:71-80`) is a reasonable DTO shape, but the prompt formatting diverges. **task14.md does not acknowledge this divergence** — claim C-1 says "引用绑定 + `[1,2,3]` 格式" which is incorrect: `[1,2,3]` is *rag-pipeline's* format, not FastGPT's. **Document the divergence in §3 of the audit.**

### 1.7 FastGPT has no `parent_doc` / chunk window function

Searched the entire repo for `parentChunk`, `parentDoc`, `siblingChunk`, `expandWindow`, `getSiblingChunks` — **zero hits** in `packages/service/core/dataset/`. The only `parent*` references are `parentCollectionIds` (`packages/service/core/dataset/search/defaultRecall/collectionFilter.ts:42-89`) which is **collection-level folder hierarchy**, not chunk-level windowing.

`parent_title` is a **chunker-side concept** in FastGPT (markdown heading path) — it's stored in `ChunkMetadata` but never expanded at retrieval time. The chunk text is what is embedded and retrieved.

rag-pipeline's `ParentDocExpander` (task14.md:419-488) is therefore **a new feature with no FastGPT counterpart**. The supporting `ChunkRepository.get_siblings` (lines 124-144) and `parent_title` field on `ChunkMetadata` (`document.py:21`) are present, but the **`ParentDocExpander` class itself is not on disk**. Implementation is owed, but the design is a *divergence from* FastGPT rather than a port.

---

## 2. rag-pipeline 当前状态

### 2.1 Directory check: `src/rag/pipeline/` does not exist

```
$ ls /Users/jung/pro/rag-pipeline/src/rag/pipeline/
ls: /Users/jung/pro/rag-pipeline/src/rag/pipeline/: No such file or directory

$ ls /Users/jung/pro/rag-pipeline/src/rag/
__init__.py   config.py   domain/   error_codes.py   exception.py
infra/        ingest/     retrieval/
```

`find /Users/jung/pro/rag-pipeline -path '*/pipeline/*'` → no results. **The entire `pipeline/` directory is missing.** The 5 task14 modules (subgraph, orchestrator, rerank, cite, parent_doc) are all claimed to live in this directory.

`global_rerank.py` and `rerank_chunk.py` are also missing. The `infra/llm/` directory has only 4 files: `chat.py`, `embed.py`, `rerank.py`, `semaphore.py`, plus `__init__.py`. **No `rerank_chunk.py`.**

### 2.2 The 5 claimed sub-feature modules — all missing

| Claimed file | Status |
|---|---|
| `src/rag/pipeline/subgraph.py` | **Missing** |
| `src/rag/pipeline/orchestrator.py` | **Missing** |
| `src/rag/pipeline/rerank.py` | **Missing** |
| `src/rag/pipeline/cite.py` | **Missing** |
| `src/rag/pipeline/parent_doc.py` | **Missing** |
| `src/rag/pipeline/global_rerank.py` | **Missing** |
| `src/rag/infra/llm/rerank_chunk.py` | **Missing** |

`grep -rn "intra_fusion\|inter_dataset_fusion\|SearchSubgraph\|DatasetOrchestrator\|RerankRunnable\|GlobalRerankRunnable\|ParentDocExpander\|assemble_citations\|build_prompt" /Users/jung/pro/rag-pipeline/` → zero hits in `src/`. The only `intra_fusion` / `inter_dataset_fusion` references are in **task11.md** (spec, not code).

### 2.3 The 5 claimed test files — all missing except test_rerank.py (which tests something else)

| Claimed test file | Status |
|---|---|
| `tests/unit/test_rerank.py` | **Exists**, but tests `QwenRerank` HTTP parsing (45 lines), NOT `RerankRunnable` |
| `tests/unit/test_orchestrator.py` | **Missing** |
| `tests/unit/test_cite.py` | **Missing** |
| `tests/unit/test_parent_doc.py` | **Missing** |
| `tests/unit/test_global_rerank.py` | **Missing** |

The existing `test_rerank.py` (read in full above) is the **task7 HTTP wrapper test**, not the task14 `RerankRunnable` test that the doc claims at lines 261-323. The 4 tests task14.md claims (`test_rerank_reranks_hits`, `test_rerank_default_weight_is_05`, `test_rerank_skips_caption_hits`, `test_rerank_weight_one_short_circuit`) are not on disk.

### 2.4 The 4 sub-config claim (PAudit-4)

task14.md:21-23 says `SearchRequest` was refactored into 4 sub-configs (`VectorConfig / FulltextConfig / RerankConfig / CitationConfig`).

**Actual state of `src/rag/domain/search.py` (read in full above):**
- `RetrievalConfig` — top_k, score_threshold, embedding_model, use_rerank, rerank_model, **rerank_weight: float = 0.5** (line 18)
- `GenerationConfig` — model, temperature, max_tokens
- `ContextConfig` — parent_doc_window, query_extension, max_query_variants, query_decomposition
- `HistoryConfig` — chat_bg, histories
- `SearchRequest` (line 51) — has `query / dataset_ids / image_urls / use_global_rerank / audit / retrieval / generation / context / history`

The 4-config split **does exist** but is **not** what task14.md claims. task14.md names them `VectorConfig / FulltextConfig / RerankConfig / CitationConfig`; the actual code has `RetrievalConfig / GenerationConfig / ContextConfig / HistoryConfig`. The names are different (PAudit-4's claim is inaccurate). However, the **`rerank_weight: 0.5` field is correctly set** (line 18, B12 fix is in place), and `parent_doc_window: int = 0` exists (line 36, PAudit-4 partial match).

`CitationConfig` does not exist as a separate config; the `Citation` model is a result DTO (`domain/search.py:71-80`), not a request sub-config. **The naming in task14.md:21 is misleading.** See gap G-P1-3.

### 2.5 ScoredDocument 删 q/a (PAudit-5)

task14.md:23 says "ScoredDocument 删 `q/a` 字段(原本绑定 LLM 生成段命名), subgraph 输出统一收敛到 `chunk_id / dataset_id / source / score / text`".

**Actual `ScoredDocument` (`src/rag/domain/document.py:39-69`):**
- Fields: `chunk_id, dataset_id, text, score, rank, source, modality, image_path, metadata, embedding, rerank_score, score_breakdown`
- **No `q` / `a` field** — confirmed.
- The docstring (lines 49-53) explicitly says "(q, a) 溯源字段已迁出: 见 `rag.retrieval.trace.RetrievalTrace`"

**This is correct.** The migration to `RetrievalTrace` was done. However, **`RetrievalTrace` is FastGPT-incompatible** (see §1.5) — FastGPT's `SearchDataResponseItemType` still has `q` and `a` as data fields. The rag-pipeline decision to decouple is a **deliberate divergence** (audit #6 docstring says "q/a 只在 remove_duplicates 去重等链路阶段使用, 用一个独立 RetrievalTrace 平行数组传给 remove_duplicates 更清晰"). The docstring is honest, but task14.md should explicitly flag the divergence.

### 2.6 Cache async + on_chunks_changed (PAudit-5)

task14.md:24 claims `cache_decorator` subgraph was changed to `async` and `on_chunks_changed` to Redis pipeline.

`grep -rn "cache_decorator\|on_chunks_changed" /Users/jung/pro/rag-pipeline/src/` → zero hits. **No cache decorator exists in the codebase** (it's planned for task 16's `build_full_pipeline` per audit report 16). The claim is forward-looking and the implementation has not landed.

### 2.7 ChunkedCohereRerank + text2Chunks (F4 / subagent #8)

**Does not exist on disk.** `rerank_chunk.py` is missing. The `QwenRerank` in `rerank.py` has no `text2Chunks` logic — it relies on the server-side `model.maxToken` truncation (FastGPT pattern), passing the raw `documents` list to `client.rerank(model, query, documents, top_n=top_k)`. The `ChunkedCohereRerank` design (lines 189-234) is a Cohere-specific extension; rag-pipeline uses `QwenRerank` against DashScope, which is a different family. **The "F4 re-export task7" plan is incoherent** because task7's `CohereRerank` doesn't exist in rag-pipeline at all — there is no Cohere path.

The current `QwenRerank` does have a `_RERANK_PATH = "/reranks"` URL constant, but no `text2Chunks` is invoked. The doc-length problem is deferred to the upstream model.

### 2.8 `RetrievalTrace` and `remove_duplicates` — present but in different shape

Both exist (`src/rag/retrieval/trace.py`, 80 lines). The `ScoredDocumentLike` Protocol-based design is decoupled from the domain layer (audit #2's `parent_doc` design pattern). The function enforces strict `len(docs) == len(traces)` and raises `ValueError` on mismatch.

Compared to FastGPT's `removeDuplicateSearchResults`:
- FastGPT: takes a single `data[]`, computes `hashStr("${q}${a}".replace(/[^\p{L}\p{N}]/gu, ''))` per item, returns deduped.
- rag-pipeline: takes `docs[]` + `traces[]` parallel arrays, computes `set[(q, a)]`, returns deduped.

**No FastGPT equivalent for `RetrievalTrace` itself.** The closest FastGPT concept is just the `q` and `a` fields on `SearchDataResponseItemType`, which are part of the data type, not a parallel trace.

### 2.9 `Dataset.rrf_k` and dataset-level rerank config

`src/rag/domain/dataset.py:8-12` (read in full):
```python
class Dataset(BaseModel):
    id: uuid.UUID
    name: str
    embed_model: str
    embed_dim: int
    rerank_model: str | None = None
    DEFAULT_PROMPT_TEMPLATE: str = "..."  # line 9: {citations} placeholder exists
```

Wait — `DEFAULT_PROMPT_TEMPLATE` is shown as a class field here? Let me re-verify.

Re-reading the file output above (lines 8-12):
```
/Users/jung/pro/rag-pipeline/src/rag/domain/dataset.py:9:    {citations}
/Users/jung/pro/rag-pipeline/src/rag/domain/dataset.py:8:    {citations}
```

These are inside the `DEFAULT_PROMPT_TEMPLATE` string (which uses `.format(citations=..., query=...)`). The `Dataset` model itself does have `rerank_model: str | None = None`, which task14.md's `build_dataset_subgraph` reads (line 927: `if use_rerank and dataset.rerank_model and deps.get("reranker"):`). The model class is present and supports the planned wiring.

---

## 3. task14.md 关键声明清单

| # | Claim (file:line) | Concrete content | On-disk reality |
|---|---|---|---|
| C-1 | task14.md:9-15 | All 5 modules under `src/rag/pipeline/` exist (subgraph, orchestrator, rerank, cite, parent_doc, plus global_rerank) | **All 6 files missing.** Directory doesn't exist. |
| C-2 | task14.md:11 | `src/rag/infra/llm/rerank_chunk.py` re-exports + `ChunkedCohereRerank` | **File missing.** Only `rerank.py` exists with `QwenRerank` (DashScope, not Cohere). |
| C-3 | task14.md:16 | `SearchRequest.rerank_weight` 默认 0.5 (B12) | **Correct on disk** (`src/rag/domain/search.py:18`), but the field is in `RetrievalConfig` not `SearchRequest` directly. |
| C-4 | task14.md:17 | 5 test files exist (`test_rerank.py / test_orchestrator.py / test_cite.py / test_parent_doc.py / test_global_rerank.py`) | **Only `test_rerank.py` exists** and tests `QwenRerank` HTTP parsing, not the claimed `RerankRunnable`. |
| C-5 | task14.md:25 | 373 unit tests passing, subgraph/orchestrator/rerank/cite/parent_doc 段 100% 覆盖, mypy 0 错, ruff 全过 | **Unverifiable.** Files don't exist; the test counts cannot be this high without the modules. |
| C-6 | task14.md:21-23 | `SearchRequest` split into 4 sub-config: `VectorConfig / FulltextConfig / RerankConfig / CitationConfig` | **Names diverge.** Actual split is `RetrievalConfig / GenerationConfig / ContextConfig / HistoryConfig`. `VectorConfig` and `FulltextConfig` are merged into `RetrievalConfig`; `CitationConfig` does not exist. |
| C-7 | task14.md:23 | `ScoredDocument` 删 `q/a` 字段 | **Correct on disk** (`document.py:39-69`), migration to `RetrievalTrace` complete. |
| C-8 | task14.md:24 | `cache_decorator` async + `on_chunks_changed` Redis pipeline | **No code on disk.** Forward-looking claim, not implemented. |
| C-9 | task14.md:32 | (B12) `SearchRequest.rerank_weight` 默认 0.7 → 0.5 | **Field is 0.5 on disk** (`search.py:18`), but the location is `RetrievalConfig.rerank_weight` not `SearchRequest.rerank_weight` (C-3, C-6 mismatch). |
| C-10 | task14.md:33 | (B12) `RerankRunnable.__init__` 默认 `weight=0.5` | **Class missing on disk.** Cannot verify. |
| C-11 | task14.md:34 | (B12) `RerankRunnable` 测试断言 `weight=0.5` | **Test missing on disk.** Cannot verify. |
| C-12 | task14.md:36 | (B13) rerank 混合公式由线性 `w*r + (1-w)*o` 改为 RRF 融合: 两条 rank-based 列表, `intra_fusion` 加权累加 | **Algorithmically correct** but **`intra_fusion` itself is missing on disk** (task 11 status). |
| C-13 | task14.md:37 | (B13) FastGPT 实际是 `concatWeightedRecallLists` → `datasetSearchResultConcat` (`weight * 1/(60+rank)` rank-based RRF) | **Citation is correct** (`packages/global/core/dataset/search/utils.ts:21`). |
| C-14 | task14.md:38 | (subagent #8) Rerank 入口前去重: `RerankRunnable.ainvoke` 入口先 `remove_duplicates(hits)` 再传 rerank | **Partially correct.** FastGPT does pre-dedup on `textRecallResults` (`defaultRecall/rerank.ts:84`), but the dedup function differs (content-hash vs (q,a) tuple). `RerankRunnable` class missing. |
| C-15 | task14.md:39-40 | (subagent #8) Rerank 文档 token 预算拆分: `CohereRerank.rerank` 之前对超长 doc 用 `text2Chunks` 拆分, `__chunk_i` 映射回原 docId | **Not in FastGPT.** `reRankRecall` (FastGPT) does not do client-side chunking; it relies on server-side `model.maxToken` truncation. The `ChunkedCohereRerank` design is **not a port**; it's an extrapolation. P2. |
| C-16 | task14.md:40 | (subagent #8) Rerank 作用范围仅文本侧: subgraph 内只对 `source in ("vector", "fulltext")` rerank, `source in ("caption",)` 直接走 RRF 融合 | **FastGPT-aligned semantically** (`defaultRecall/index.ts:122-132` reranks only `textRecallResults`, image results stay in RRF). |
| C-17 | task14.md:41 | (subagent #8) `weight=1.0` 短路 | **FastGPT-aligned** (`defaultRecall/rerank.ts:87-93`). |
| C-18 | task14.md:42 | (subagent #8) `existsId` 抑制同 docId 重复: split 后多个 chunk 共享同一原 docId, 只取最高分 | **FastGPT has no `text2Chunks`**, so this is moot. rag-pipeline's `ChunkedCohereRerank` is the only place this would apply, and it's not on disk. |
| C-19 | task14.md:12-15 | `parent_doc.py` ParentDoc 窗口扩展 (spec §7.4) | **Class missing on disk.** `ChunkRepository.get_siblings` (`chunk_repo.py:124-144`) is the only supporting primitive. **FastGPT has no equivalent feature.** |
| C-20 | task14.md:14-15 | `global_rerank.py` 跨 dataset 全局 rerank 节点 (挂载点 ②) | **File missing on disk.** FastGPT's `dispatchDatasetSearch` + `dispatchDatasetConcat` doesn't have a separate "global rerank" step — it does per-dataset rerank and then a uniform-weight concat. The G1 design is a rag-pipeline invention. |
| C-21 | task14.md:12 | `cite.py` 引用绑定 + `[1,2,3]` 格式 | **File missing on disk.** Cite format `[1] 来源:...` is **rag-pipeline's own convention** (DEFAULT_PROMPT_TEMPLATE), not FastGPT's `[id](CITE)` inline pattern (`dataset.const.ts:7-8`). |
| C-22 | task14.md:9 | `subgraph.py` — `SearchSubgraph` Runnable, 内含 vector + fulltext 双检索 + 跨 dataset 调度 | **File missing on disk.** FastGPT's `searchDatasetData` is the closest equivalent (function, not Runnable). |
| C-23 | task14.md:10 | `orchestrator.py` — `RunnableParallel` + `with_fallbacks` | **File missing on disk.** FastGPT has no orchestrator class; multi-dataset aggregation is a workflow edge (`dispatchDatasetConcat`). |
| C-24 | task14.md:11 | `RerankRunnable` (weight=0.5, B12 对齐 FastGPT), RRF 融合 (B13), `remove_duplicates` 入口 + `weight=1.0` 短路 | **Class missing on disk.** Function `remove_duplicates` exists in `retrieval/trace.py` but takes a parallel `traces[]` array (different API). |
| C-25 | task14.md:482-486 | `parent_doc.py` calls `ChunkRepository.get_siblings` for batch sibling fetch | **File missing on disk.** `get_siblings` is implemented (`chunk_repo.py:124-144`) and the parent_doc code references it correctly, but the file itself is unwritten. |
| C-26 | task14.md:507-543 | `DatasetOrchestrator` uses `RunnableParallel` + `with_fallbacks([RunnableLambda(_error_fallback)])` + `RunnableError` for total failure | **Class missing on disk.** The `with_fallbacks` + `RunnableError` pattern is LCEL 1.0+ specific; not FastGPT-mirrored. |
| C-27 | task14.md:525-538 | Fallback returns `{"filtered": [], "error": str, "dataset_id": str, "_fallback_key": str}` to keep downstream protocol isomorphic | **Forward-looking spec, not verified.** |
| C-28 | task14.md:556-569 | H1 fix: per-dataset wrapper keyed by `f"ds_{ds.id}"`, no zip order dependency | **Forward-looking spec.** |
| C-29 | task14.md:591-598 | P0-16 fix: `SearchResult` carries `_intermediate_hits` via `object.__setattr__` for ParentDocExpander | **Class missing.** `SearchResult` is on disk (`search.py:83-89`) and does **not** have `_intermediate_hits`. |
| C-30 | task14.md:633-642 | `GlobalRerankRunnable` reuses `intra_fusion` for RRF merge, runs after `inter_dataset_fusion`, before filter | **Class missing.** `intra_fusion` itself is also missing (task 11 dependency). |
| C-31 | task14.md:903-909 | `intra_fusion` signature is `(query_groups, rrf_k)` (single param, no `weights`) | **Function missing.** If/when written, must match task 11's spec (which has a `weights` param per P0-1 fix). task14.md:903 contradicts task11.md's P0-2 resolution. **Inconsistency.** |

---

## 4. 三向差异矩阵

### 4.1 Subgraph + Orchestrator

| Aspect | task14.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **Topology** | `SearchSubgraph` Runnable (per-dataset) + `DatasetOrchestrator` (top-level, `RunnableParallel` + `with_fallbacks`) | **Neither exists.** | `dispatchDatasetSearch` (workflow node) + `dispatchDatasetConcat` (workflow edge aggregator) — no Runnable class |
| **Async model** | `async def ainvoke(state, config=None)` + sync `invoke` wrapper that calls `asyncio.run` | (none) | `async function dispatchDatasetSearch(props)` — workflow runtime handles awaiting |
| **Fallback / error model** | `sub.with_fallbacks([RunnableLambda(_error_fallback)], exceptions_to_handle=(Exception,))`; per-dataset fallback returns `{"filtered": [], "error", "dataset_id", "_fallback_key"}`; total failure raises `RunnableError` and returns `SearchResult` with all `failed_dataset_ids` | (none) | `try/catch` at the workflow node level (`search.ts:338-341` returns `getNodeErrResponse`); no per-dataset error model (each dataset's search is its own workflow node) |
| **Per-dataset dispatcher** | `RunnableParallel(wrappers)` where each wrapper is `sub.with_fallbacks(...)` | (none) | Workflow graph edge: each dataset search is a separate node; failure of one does not block the others at the dispatcher level — the workflow runtime handles partial failure |
| **Result aggregation** | `for ds in self.datasets: key=f"ds_{ds.id}"; result=results.get(key, {})` then `inter_dataset_fusion(all_filtered)` | (none) | `dispatchDatasetConcat` reads all dataset outputs from `params.quoteMap`, calls `datasetSearchResultConcat(quoteList.map((list) => ({weight: 1, list})))` with **uniform weight = 1** |
| **Token budget allocation** | `orchestrator_filter(fused, self.score_threshold, self.max_tokens, using_re_rerank=...)` (global budget) | (none) | `filterDatasetDataByMaxTokens(scoreFilter, maxTokens)` in `defaultRecall/index.ts:170` — single global limit |
| **`_intermediate_hits` plumbing** | `object.__setattr__(result, "_intermediate_hits", fused)` — private attr, doesn't pollute schema | (none on `SearchResult`; `search.py:83-89` has no such field) | N/A (FastGPT's `searchRes` is the intermediate; passed as workflow output and consumed by `dispatchDatasetConcat`) |
| **Failed dataset tracking** | `SearchResult.failed_dataset_ids: list[uuid.UUID] = []` | Field exists on `SearchResult` (`search.py:88`) but never populated | Workflow tracks per-node status; no "failed_dataset_ids" aggregate |

### 4.2 Rerank

| Aspect | task14.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **Class** | `RerankRunnable(Runnable)` with `__init__(reranker, weight=0.5, top_k=10)` and `ainvoke(input, config=None)` | **Class missing.** `QwenRerank` (DashScope HTTP wrapper, Protocol `Reranker`) in `infra/llm/rerank.py:36-104` is the closest analog | `reRankRecall` (function, `packages/service/core/ai/rerank/index.ts:41-150`) and `reRankSearchResults` (wrapper, `defaultRecall/rerank.ts:55-110`) |
| **Default weight** | `weight=0.5` (B12) | `RetrievalConfig.rerank_weight: float = 0.5` (`search.py:18`) | `rerankWeight = 0.5` default (`defaultRecall/index.ts:52`) |
| **Pre-dedup** | `remove_duplicates(hits)` at entry | `remove_duplicates` exists in `retrieval/trace.py:50-79` (different shape: parallel `traces[]`) | `removeDuplicateSearchResults(textRecallResults)` inline (`defaultRecall/rerank.ts:84`) |
| **Source split** | `text_hits = [h for h in hits if h.source in ("vector", "fulltext")]`; `caption_hits = ... source not in ("vector", "fulltext")` | (none) | `reRankSearchResults` operates on `textRecallResults` only; image results flow through a separate path |
| **weight=1.0 short-circuit** | `if self.weight == 1.0: return ... reranked` | (none) | `if (rerankWeight === 1) return {results: reRankResults, ...}` (`defaultRecall/rerank.ts:87-93`) |
| **Failure handling** | `except Exception: warnings.append("rerank_skipped: API error"); return {**input, "filtered": hits[:self.top_k], "warnings": warnings}` (skip, don't block) | (none) | `try { ... } catch { return { results: textRecallResults, inputTokens: 0, usingReRank: false } }` (`defaultRecall/rerank.ts:103-109`) — silent fallback, same intent |
| **RRF merge formula** | `intra_fusion(query_groups=[rerank_ranked, text_hits], weights=[self.weight, 1.0 - self.weight], rrf_k=DEFAULT_RRF_K)` | (none — `intra_fusion` itself is also missing per task 11) | `concatWeightedRecallLists([{weight: 1 - rerankWeight, list: textRecallResults}, {weight: rerankWeight, list: reRankResults}])` — same formula, different signature |
| **text2Chunks** | `ChunkedCohereRerank` + `text2Chunks(doc, max_tokens=450, overlap=50)` splits long docs; `__chunk_i` mapping; `existsId` takes max score per orig doc | (none — `rerank_chunk.py` missing) | **Not in FastGPT.** `reRankRecall` passes documents to the model as-is; relies on server-side `model.maxToken` (line 53). |
| **Re-export from task7** | `from rag.infra.llm.rerank import Reranker, CohereRerank, NoOpRerank` (re-export) | **None of these are in `infra/llm/rerank.py`.** Has `Reranker` (Protocol), `QwenRerank`, `NoOpRerank`. `CohereRerank` is a different class not present. | N/A |
| **Input doc text shape** | `docs = [h.text for h in text_hits]` | (none) | `documents: data.map((item) => ({id: item.id, text: `${item.q}\n${item.a}`.trim()}))` — q+a concat, not `text` field |
| **Re-rank typed score** | (not modeled — `ScoredDocument.score: float` is overwritten with RRF) | (none) | `score: [{type: SearchScoreTypeEnum.reRank, value: score, index}]` replaces previous score array on rerank result |

### 4.3 Cite

| Aspect | task14.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **Module** | `src/rag/pipeline/cite.py` with `assemble_citations(hits, top_k)` + `build_prompt(query, citations, template=None)` | **File missing.** | No `cite.py` analog. `getDatasetSearchToolResponsePrompt()` is a static string in `packages/global/core/ai/prompt/dataset.const.ts:1-19`. `datasetSearch/search.ts:328-335` builds `cites` array from `searchRes`. |
| **`assemble_citations` signature** | `assemble_citations(hits: list[ScoredDocument], top_k: int) -> list[Citation]` | (none) | `searchRes.map((item) => ({id, sourceName, updateTime, content: `${item.q}\n${item.a}`.trim()}))` (`search.ts:329-334`) — inline mapping, not a named function |
| **`Citation` DTO fields** | `chunk_id, dataset_id, source_name, content, image_path, score, update_time` | **`Citation` model exists** (`search.py:71-80`) with: `chunk_id, dataset_id, source_name, content, image_path, score, update_time` — **exact match** | `SearchDataResponseItemType`: `id, updateTime, q, a, chunkIndex, indexes, datasetId, collectionId, sourceName, sourceId, score` — **q/a separation**, no `content` (computed via `${q}\n${a}.trim()` at the cite-site) |
| **`build_prompt` template** | `DEFAULT_PROMPT_TEMPLATE.format(citations=cite_blocks, query=query)` where `cite_blocks = "[1] 来源:{name}\n{content}\n\n[2] ..."` | `DEFAULT_PROMPT_TEMPLATE` exists in `dataset.py:8-12` with `{citations}` placeholder | FastGPT's `getDatasetSearchToolResponsePrompt` does **not** use a `{citations}` placeholder; it just instructs the LLM to inline `[id](CITE)` markers in its own output |
| **Cite format** | `[1] 来源:filename\ncontent` prefix block | (matches template, but `cite.py` is missing) | `**[id](CITE)**` inline in LLM output (no prefix block) |
| **Image cite** | `image_path=h.image_path if h.modality == "image_caption" else None` (H2: ScoredDocument-level) | `ScoredDocument.image_path: str \| None = None` exists (`document.py:64`), but no `cite.py` to consume it | `imageId` and `imageDescMap` are part of the score item; `formatDatasetDataValue` (`result.ts:30-36`) renders them — separate path from text cite |
| **`update_time`** | `update_time=h.metadata.created_at` | `ChunkMetadata.created_at: datetime \| None = None` exists (`document.py:24`) | `data.updateTime` directly from MongoDB |

### 4.4 Parent Doc

| Aspect | task14.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **Module** | `src/rag/pipeline/parent_doc.py` with `ParentDocExpander(window=1, max_tokens=2000).expand(hits)` | **File missing.** | **No analog.** No `parentDoc` / `parentChunk` / `expandWindow` / `siblingChunk` function exists in `packages/service/core/dataset/`. |
| **Sibling fetch primitive** | `ChunkRepository.get_siblings(dataset_id, parent_title, lo, hi)` (batch SQL with `OR-composite`) | **`get_siblings` exists** (`chunk_repo.py:124-144`) with signature `(dataset_id, parent_title, lo, hi) -> list[DomainChunk]` — single `select(ChunkModel).where(...)` query, not OR-composite | N/A |
| **Window semantics** | `chunk_index ± window` (e.g. window=1 → ±1 → 3 chunks: prev, self, next) | (none on the expander) | N/A |
| **`parent_title` source** | `h.metadata.parent_title` (chunker-side heading path) | `ChunkMetadata.parent_title: str = ""` (`document.py:21`); populated by `recursive.py:52` `new_parent = parent_title + seg_title` for markdown headings | N/A — FastGPT's `parent*` is collection-level (`parentCollectionIds`), not chunk-level |
| **Token budget** | `max_tokens: int = 2000`; truncates `full_text[:max_tokens * 2]` (chars, not tokens) | (none on the expander) | N/A |
| **Text assembly** | `merged_text = "\n\n...\n\n".join(sorted(siblings, key=chunk_index))`; `full_text = h.text + "\n\n...\n\n" + merged_text` | (none on the expander) | N/A |
| **Apply on result** | `ParentDocExpander.expand_result(result)` — consumes `result._intermediate_hits` from orchestrator | (none — `SearchResult._intermediate_hits` doesn't exist) | N/A |
| **Mounting** | spec §0.1 强制 (3 layers), task16 时挂载 | (none) | N/A |

### 4.5 RetrievalTrace + remove_duplicates (helper, not in 5 sub-features but referenced)

| Aspect | task14.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **Module** | (not the main 5, but used by `RerankRunnable`) | `src/rag/retrieval/trace.py` (80 lines) exists | `removeDuplicateSearchResults` inline in `defaultRecall/result.ts:57-67` |
| **Input shape** | (not specified) — `remove_duplicates(hits)` | `remove_duplicates(docs, traces)` — strict length match via `ValueError` | `removeDuplicateSearchResults(data)` — single argument |
| **Key computation** | (not specified) | `(trace.q, trace.a)` raw tuple | `hashStr(`${q}${a}`.replace(/[^\p{L}\p{N}]/gu, ''))` — content-normalized hash |
| **Return shape** | (not specified) | filtered `docs` (traces not returned) | filtered `data` |
| **Side data** | (not specified) | Parallel `RetrievalTrace` dataclass (`q: str \| None, a: str \| None`) | q/a are fields of `SearchDataResponseItemType` |
| **Use in rerank** | `RerankRunnable.ainvoke` calls `remove_duplicates(hits)` | (would work if the parallel `traces[]` were plumbed) | `defaultRecall/rerank.ts:84` calls `removeDuplicateSearchResults(textRecallResults)` |
| **Used in PAudit-5** | (q, a) moved out of `ScoredDocument` into `RetrievalTrace` | Done (`document.py` has no q/a; `trace.py` defines `RetrievalTrace`) | N/A |

---

## 5. 修复建议 (P0 → P1 → P2 → P3, 每条带具体文件:行号)

### P0 (blocker before sign-off)

#### G-P0-1: Status banner "已完成 (2026-06-13 同步)" is a false positive
**Where:** task14.md:3-5 (status block) and task14.md:9-17 (delivery list).
**Problem:** task14.md claims all 5 modules + 5 test files are delivered, 373 unit tests pass, 100% coverage on subgraph/orchestrator/rerank/cite/parent_doc, mypy 0 errors, ruff all pass. **None of these claims are verifiable on disk**:
- `src/rag/pipeline/` directory does not exist
- `src/rag/infra/llm/rerank_chunk.py` does not exist
- `src/rag/pipeline/{subgraph,orchestrator,rerank,cite,parent_doc,global_rerank}.py` are all missing
- `tests/unit/{test_orchestrator,test_cite,test_parent_doc,test_global_rerank}.py` are all missing
- The only `test_rerank.py` on disk tests `QwenRerank` HTTP parsing (45 lines), not the claimed 4 RerankRunnable tests
**Why P0:** A sign-off banner that doesn't match reality is a process failure. The 8 other audit reports were based on a "OK" task14; if task14 is in fact un-implemented, then tasks 11, 12, 13, 16, 20 (which depend on `RerankRunnable` / `intra_fusion` / `ParentDocExpander` / `assemble_citations` from task14) are all blocked.
**Fix:**
1. Revert task14.md status banner to "未实现" (mirroring task11's status). The audit #8 (DocFix-8 task14.md skipped, see task #133) seems to have accepted the false claim — re-audit the DocFix-8 commit if possible.
2. Add a leading TODO: "task14 is bundled (subgraph + orchestrator + rerank + cite + parent_doc). **None of the 5 modules is on disk.** Implementation must follow the spec, not be assumed to exist."
3. The plan-level status (`2026-06-10-python-rag-pipeline.md`) should also be amended.

#### G-P0-2: `RerankRunnable` and the 4 sub-feature classes do not exist; downstream audits may have assumed them
**Where:** task14.md:65-161 (`RerankRunnable` body), task14.md:493-608 (`DatasetOrchestrator` body), task14.md:419-488 (`ParentDocExpander` body), task14.md:386-410 (`assemble_citations` + `build_prompt` body).
**Problem:** The 5 task14 modules are **specifications, not implementations**. They live inside `src/rag/pipeline/`, a directory that does not exist. Other audits (task11, task12, task13, task16, task20) may have referenced these classes in cross-dependency notes; those references are now invalid.
**Why P0:** If any other audit report or peer-review summary asserts "task14 is OK so downstream can ship", that is a false positive. The 5 modules are blockers for: task 11 (`intra_fusion` is a dep), task 12 (`subgraph_filter` is a dep), task 16 (`build_full_pipeline` depends on `RerankRunnable` and `DatasetOrchestrator`), task 20 (CI assumes tests exist).
**Fix:** Either (a) implement the 5 modules per spec, or (b) split task14 into 5 separate per-feature tasks each scoped to a single file. The bundle is too large for a single TDD pass — task14 is 1006 lines, more than 4× the next-largest task. Recommend (b): task14a-subgraph, task14b-orchestrator, task14c-rerank, task14d-cite, task14e-parent_doc.

#### G-P0-3: `RerankRunnable` signature inside task14.md contradicts the "weights=[w, 1-w]" two-array pattern it tries to use
**Where:** task14.md:136-140:
```python
fused_text = intra_fusion(
    query_groups=[rerank_ranked, text_hits],
    weights=[self.weight, 1.0 - self.weight],
    rrf_k=DEFAULT_RRF_K,
)
```
and task14.md:903-909 (in `build_dataset_subgraph`):
```python
fused = intra_fusion(
    query_groups=[all_vec_hits, all_ft_hits],
    rrf_k=dataset.rrf_k,
)
```
**Problem:** The two call sites use **different signatures** for `intra_fusion`:
- Step 0 (rerank): `intra_fusion(query_groups, weights=[w, 1-w], rrf_k=DEFAULT_RRF_K)`
- Step 6 (subgraph): `intra_fusion(query_groups, rrf_k=...)` (no weights)

This is a direct contradiction: one call uses `weights`, the other doesn't. The no-weights form silently uses uniform weight 1.0 per group, which is a *different* algorithm. The vector-only recall path (no rerank) would behave differently from the rerank path even on identical inputs.

**Why P0:** The whole `RerankRunnable` design assumes `intra_fusion(weights=[w, 1-w])` works. If `intra_fusion` (per task 11 audit) ends up with signature `intra_fusion(query_groups, weights=None, rrf_k=DEFAULT_RRF_K)` (the B4 fix that drops the per-source distinction), the call site is **incompatible**. Whichever way task 11 lands, task 14 must follow. **This is a cross-task coupling that the audit didn't flag.**

**Fix:**
- Decide the `intra_fusion` final signature in task 11 (per audit #6, the B4 fix made `intra_fusion` take a single `query_groups` param and drop the vector/fulltext distinction). The "weights" parameter should be added as a uniform-weight option.
- Update task14.md:136-140 to match (likely `intra_fusion(query_groups=[rerank_ranked, text_hits], weights=[self.weight, 1.0-self.weight])` is correct *only* if task 11 lands with the weights parameter).
- Update task14.md:903-909 to be consistent: `intra_fusion(query_groups=[all_vec_hits, all_ft_hits], weights=[vector_weight, fulltext_weight], rrf_k=dataset.rrf_k)` (per Dataset defaults) — currently the spec hardcodes the no-weights form.

#### G-P0-4: `ChunkedCohereRerank` design references a class that doesn't exist in rag-pipeline
**Where:** task14.md:163-235.
**Problem:** task14.md:174 imports `from rag.infra.llm.rerank import Reranker, CohereRerank, NoOpRerank`. The actual `infra/llm/rerank.py` defines `Reranker` (Protocol) and `NoOpRerank` (117 lines), but **not `CohereRerank`**. The class is `QwenRerank` (DashScope compatible-api), a completely different rerank model family.
**Why P0:** Re-exporting a class that doesn't exist is a runtime ImportError the moment the module is imported. The whole `rerank_chunk.py` re-export scheme is **incoherent** with the actual `infra/llm/rerank.py` content.
**Fix:**
- Option A: Rename `QwenRerank` to `CohereRerank` in `infra/llm/rerank.py` (if DashScope compatible-api is indeed Cohere-compatible). The `_RERANK_PATH = "/reranks"` and `model: str = "rerank-english-v3.0"` defaults in task14.md:200 suggest a Cohere-shaped API. But the actual code uses `qwen3-rerank` model name (`infra/llm/rerank.py:43`), so renaming is a **semantic mismatch**.
- Option B: Implement a real `CohereRerank` class in `infra/llm/rerank.py` (separate from `QwenRerank`) and have `ChunkedCohereRerank` extend that. This is the only correct path. The `text2Chunks` logic is Cohere-specific (max doc length); QwenRerank doesn't need it.
- Option C: Drop the `ChunkedCohereRerank` design and rely on server-side truncation (FastGPT pattern). If the Qwen3-rerank model has a known max-token limit, the rag-pipeline should document it in the code and rely on `model.maxToken` rather than client-side chunking. Recommend (C) — it's simpler and matches FastGPT.

### P1 (significant API/type mismatch)

#### G-P1-1: `parent_doc` is a rag-pipeline-only feature; task14.md silently diverges from FastGPT
**Where:** task14.md:13, 414-488.
**Problem:** FastGPT has **no chunk-level parent doc window** feature. The `parent_title` field is a chunker-side heading path; it's not expanded at retrieval. The `ParentDocExpander` is a rag-pipeline invention.
**Why P1:** If a reviewer reads task14.md thinking "this is a port of FastGPT", they will be misled. The feature must be **explicitly flagged as a divergence** in the task doc, with rationale (e.g., "rag-pipeline wants longer context for cited chunks; FastGPT's `q+a` text shape already includes context, so no expansion is needed there"). Currently the doc says only "spec §7.4" without explaining the divergence.
**Fix:** Add to task14.md status block a line: "**G1 警告**: `parent_doc` is a rag-pipeline invention. FastGPT has no `parentDoc` / `parentChunk` feature. The `ParentDocExpander` is a divergence, not a port."

#### G-P1-2: Cite format `[1,2,3]` is a rag-pipeline convention, not a FastGPT pattern
**Where:** task14.md:12 (claim) and task14.md:401-410 (`build_prompt` body).
**Problem:** task14.md:12 says "引用绑定 + `[1,2,3]` 格式" as if it's a FastGPT convention. FastGPT's cite format is **`[id](CITE)` inline in LLM output** (`packages/global/core/ai/prompt/dataset.const.ts:7-8`). The two formats are **functionally different**: rag-pipeline's `[1,2,3]` is a prefix block in the user prompt; FastGPT's `[id](CITE)` is a reply-formatting instruction.
**Why P1:** A future maintainer could "fix" `build_prompt` to align with FastGPT (using `[id](CITE)`) and break downstream LLM responses. The doc must explicitly call out the divergence.
**Fix:** Update task14.md:12 to: "**D1 警告**: cite 格式 `[1] 来源:...` 是 rag-pipeline 自己的 prefix-block 约定,与 FastGPT 的 `[id](CITE)` inline in output 不同。两者不可互换。"

#### G-P1-3: `SearchRequest` sub-config names in PAudit-4 don't match what's on disk
**Where:** task14.md:21.
**Problem:** task14.md:21 names 4 sub-configs: `VectorConfig / FulltextConfig / RerankConfig / CitationConfig`. The actual code (`src/rag/domain/search.py:8-48`) has: `RetrievalConfig / GenerationConfig / ContextConfig / HistoryConfig`. The names are **completely different**:
- `VectorConfig + FulltextConfig` (task14 claim) → merged into `RetrievalConfig` (actual)
- `RerankConfig` (task14 claim) → `RerankConfig` doesn't exist as a separate config; `rerank_*` fields are inside `RetrievalConfig`
- `CitationConfig` (task14 claim) → `Citation` is a result DTO, not a request sub-config
- `ContextConfig` (actual, includes `parent_doc_window` etc.) → not mentioned in task14 claim
**Why P1:** PAudit-4's claim is factually wrong. If a future change references the claimed config names, the search will fail.
**Fix:** Update task14.md:21 to use the actual names: "**PAudit-4 (SearchRequest 拆 4 sub-config)**: `SearchRequest` fields are now grouped into `RetrievalConfig / GenerationConfig / ContextConfig / HistoryConfig` per `src/rag/domain/search.py:8-68`. `rerank_weight` lives in `RetrievalConfig` (line 18), `parent_doc_window` lives in `ContextConfig` (line 36), `Citation` is a result DTO (line 71-80) not a request sub-config."

#### G-P1-4: `assemble_citations` content field is `text` (rag-pipeline) vs `${q}\n${a}` (FastGPT)
**Where:** task14.md:386-400 (`assemble_citations` body).
**Problem:** task14.md:394 sets `content=h.text`. FastGPT's cite builder (`search.ts:333`) sets `content: `${item.q}\n${item.a}.trim()`. The two are **not equivalent**:
- `ScoredDocument.text` is a single string (chunk body)
- `${q}\n${item.a}` is "question" + newline + "answer" — FastGPT's `DatasetDataSchemaType` splits chunk content into `q` (the question/prompt prefix) and `a` (the answer/response), and the cite is a concatenation
- rag-pipeline's `ScoredDocument` has **no `q`/`a` separation** (per PAudit-5's explicit removal). So the citation content is just `text`, which loses the question context.
**Why P1:** If a downstream LLM is told to cite `[1] 来源:f.md\ncontent` and the content is missing the question framing, the cited answer may not have enough context. This is a semantic gap.
**Fix:** Decide: either (a) restore a `q/a` distinction in `ScoredDocument` (revert PAudit-5), or (b) accept that `text` is the only content and document the trade-off. The current spec is silent.

#### G-P1-5: Subagent #8 `text2Chunks` is a Cohere-specific extrapolation; not FastGPT
**Where:** task14.md:39-40 (claim), task14.md:163-235 (`ChunkedCohereRerank` body).
**Problem:** task14.md cites "subagent #8" as the source for `text2Chunks` + `__chunk_i` + `existsId` logic. Searched FastGPT's `reRankRecall` (`packages/service/core/ai/rerank/index.ts:41-150`): **no client-side text chunking**; relies on `model.maxToken` (line 53) and server-side truncation. The `text2Chunks` pattern is **rag-pipeline's own** (or Cohere's general doc-length guidance, not FastGPT's implementation).
**Why P1:** If the feature is presented as "FastGPT-aligned" and it isn't, the audit chain is wrong.
**Fix:** Update task14.md:39-40 to: "**subagent #8 / 非 FastGPT 端口**: `text2Chunks` + `__chunk_i` + `existsId` 是 Cohere API 的通用 doc-length 处理模式, **FastGPT 不做客户端 chunking**,依赖 server-side `model.maxToken` 截断. rag-pipeline 的 `ChunkedCohereRerank` 是兼容 Cohere 长 doc 的扩展,不是 FastGPT 移植."

### P2 (doc-only / cleanup)

#### G-P2-1: `RerankRunnable.invoke` sync wrapper uses `asyncio.run` in a hot path
**Where:** task14.md:154-161.
**Problem:** Sync `invoke` checks for running loop, then falls back to `asyncio.run`. Calling `asyncio.run` from inside a worker that is already inside a loop (e.g., FastAPI handler) raises `RuntimeError: asyncio.run() cannot be called from a running event loop`. The check at line 156-159 catches the error correctly, but the `if _loop is None: return asyncio.run(...)` is the **only branch** — there's no helpful error message for the `RuntimeError` case. The pattern is the same as task11's audit found.
**Why P2:** Common LCEL pattern; LCEL has `RunnableConfig` for sync/async dispatch. Use LCEL's `ainvoke`/`invoke` automatically rather than re-implementing.
**Fix:** Replace with `from langchain_core.runnables import RunnableLambda` and let LCEL dispatch.

#### G-P2-2: `ParentDocExpander.expand` calls `AsyncSessionLocal` directly inside the class
**Where:** task14.md:444-461.
**Problem:** `async with AsyncSessionLocal() as session` inside `expand` couples the class to a global session factory. The class signature (`def __init__(self, window, max_tokens)`) doesn't accept a session, repo, or connection. This makes the class un-mockable and un-injectable.
**Why P2:** Standard Python practice is dependency injection. The existing `ChunkRepository` already has a `transaction()` context manager (`chunk_repo.py:101-122`) for batch operations; the `expand` method should use it.
**Fix:** Refactor to `__init__(self, window, max_tokens, chunk_repo: ChunkRepository)` and accept a session via the repo.

#### G-P2-3: `RerankRunnable.ainvoke` mutates `h.source` in-place via `for h in all_hits: h.source = "rerank" if ...`
**Where:** task14.md:145-146.
**Problem:** Direct attribute mutation of `ScoredDocument` is not model_copy-protected. If `ScoredDocument.model_config = {"frozen": False}` (which it is, per `document.py:55`), the mutation succeeds but it breaks the immutability promise. The rest of the function uses `model_copy(update={...})` correctly; only this loop is sloppy.
**Why P2:** Consistency with the rest of the function. The original `text_hits` list has its items mutated, which is a side effect on caller-owned data.
**Fix:** Replace with `all_hits = [h.model_copy(update={"source": "rerank" if h.chunk_id in reranked_chunk_ids else h.source}) for h in all_hits]`.

#### G-P2-4: `GlobalRerankRunnable._resolve_rerank_model` takes the **first** dataset's `rerank_model`, not the most-trusted
**Where:** task14.md:657-662.
**Problem:** `for ds in self.datasets: if ds.rerank_model: return ds.rerank_model` — the iteration order is `self.datasets`, which is a `list[Dataset]`. The "first" dataset is whatever was passed in first; there's no trust ranking. If a low-priority dataset declares `rerank_model` and a high-priority one doesn't, the low-priority one wins.
**Why P2:** This is a design smell but not a runtime bug if datasets are passed in priority order.
**Fix:** Document the assumption: "datasets must be passed in priority order; the first non-None `rerank_model` wins."

### P3 (nice-to-have)

#### G-P3-1: Test for `test_rerank_skips_caption_hits` only checks presence, not order
**Where:** task14.md:294-308.
**Problem:** `assert any(h.source == "caption" for h in out["filtered"])` only checks that the caption is present, not that it's at the top of the rerank-aware order. A regression where captions are demoted to the end would not be caught.
**Fix:** Add `assert out["filtered"][0].source == "caption" or out["filtered"][-1].source == "caption"` (or whichever ordering is intended).

#### G-P3-2: `weight=1.0` short-circuit test (`test_rerank_weight_one_short_circuit`) does not assert "no RRF merge"
**Where:** task14.md:310-322.
**Problem:** The test only asserts the order of the output. It doesn't verify that `intra_fusion` was **not called** (i.e., the short-circuit branch fired). A regression where `weight=1.0` accidentally still calls `intra_fusion` would pass the test.
**Fix:** Use a mock for `intra_fusion` and assert it was not called.

#### G-P3-3: `remove_duplicates` raises `ValueError` on length mismatch; `RerankRunnable.ainvoke` calls it without length check
**Where:** task14.md:95 (`hits = remove_duplicates(hits)`).
**Problem:** The function expects a parallel `traces[]` array. `RerankRunnable.ainvoke` only has `input["filtered"]` and `input["query"]` — no `traces` field. Calling `remove_duplicates(hits)` will fail with `ValueError: docs/traces length mismatch`.
**Why P3:** Surface-level issue. If `RerankRunnable` is meant to use `remove_duplicates`, it must construct a parallel `traces[]` array (with `q=query, a=None` for all hits) and pass it. The current code is broken.
**Fix:** Either (a) `RerankRunnable.ainvoke` constructs `traces = [RetrievalTrace(q=query, a=None) for _ in hits]` and passes both, or (b) use a different dedup function that doesn't need parallel traces.

#### G-P3-4: `assemble_citations` doesn't include the `metadata.chunk_index` for ordering
**Where:** task14.md:386-399.
**Problem:** `Citation` model doesn't have a `chunk_index` field (`search.py:71-80`). If the order of `ScoredDocument.text` is needed for display, it's lost in the cite. The `chunk_index` is in `ScoredDocument.metadata.chunk_index` but not propagated.
**Why P3:** Minor; not a blocker.
**Fix:** Add `chunk_index: int | None = None` to `Citation` and pass it through.

---

## 6. 实施顺序 (哪些先做)

1. **Resolve G-P0-1 (status banner false positive) and G-P0-2 (modules missing).** This is a sign-off blocker. The task doc must accurately reflect that the 5 modules are not yet implemented. The plan-level status (`2026-06-10-python-rag-pipeline.md`) should also be amended.

2. **Resolve G-P0-3 (intra_fusion signature coupling).** Coordinate with task 11's audit P0-1 (score_breakdown) and P0-2 (weights parameter). Decide: does `intra_fusion` accept `weights` or not? Update both task 11 and task 14 to use the same signature.

3. **Resolve G-P0-4 (ChunkedCohereRerank vs QwenRerank).** Pick option (C) (drop `ChunkedCohereRerank`, rely on server-side truncation) — it matches FastGPT, is simpler, and avoids the import error.

4. **Implement the 5 modules in dependency order:**
   - `RerankRunnable` (depends on `intra_fusion` from task 11) + `Reranker` / `QwenRerank` already exist
   - `assemble_citations` + `build_prompt` (depends on `Citation` DTO, which exists)
   - `ParentDocExpander` (depends on `ChunkRepository.get_siblings`, which exists)
   - `SearchSubgraph` (depends on `RerankRunnable`, `intra_fusion`, `assemble_citations`)
   - `DatasetOrchestrator` (depends on `SearchSubgraph`, `inter_dataset_fusion` from task 11, `assemble_citations`)
   - `GlobalRerankRunnable` (depends on `intra_fusion`)

5. **Add the 4 missing test files** (test_orchestrator, test_cite, test_parent_doc, test_global_rerank) and **replace the existing test_rerank.py with a 4-test RerankRunnable test set** (since the current test_rerank.py tests a different class).

6. **Resolve P1 documentation gaps** (G-P1-1 through G-P1-5): update task14.md to explicitly flag every divergence from FastGPT (parent_doc, cite format, SearchRequest sub-config names, citation content shape, text2Chunks).

7. **Apply P2 cleanups** (G-P2-1 through G-P2-4) as a follow-up pass.

8. **Optional P3 polish** (G-P3-1 through G-P3-4) in a final commit.

After 1-5, the task is **actually complete** (matches the spec) and ready for sign-off. Items 1-3 are blockers; 4-5 are the actual implementation; 6-8 are quality.

---

## Appendix A: Cross-references to other audit reports

task14 is a **hub** that several other tasks depend on. The cross-references below are based on what's in those audit reports (per task #136's parallel set). If task14 is in fact un-implemented, the downstream audits' dependency notes may also be invalid.

| Audit | Cross-refs task14 for |
|---|---|
| task11 (Fusion WRRF) | task11:250-252 — "subgraph.py / orchestrator.py (Task 14) call `intra_fusion` with the new `query_groups` signature" |
| task12 (Filter Pipeline) | task12 references `subgraph_filter` and `orchestrator_filter`, which are also spec'd in task14:572-580 |
| task13 (Query Extension) | task13:194-198 may reference SearchSubgraph's `state["query_variants"]` consumption |
| task15 (Audit + CitationChecker) | task15 references `assemble_citations` and `build_prompt` from task14 |
| task16 (build_full_pipeline) | task16:13-15 explicitly cites task14 as a dependency ("task14: search subgraph / orchestrator / rerank / cite / parent_doc") |
| task20 (CI + Final Integration) | task20's coverage report (373 tests, 100% on subgraph/orchestrator/rerank/cite/parent_doc) is **based on task14's claim**; if task14 doesn't exist, the coverage report is wrong |

**The fact that other audits referenced task14 as "OK" suggests a transitive false positive across the entire 9-parallel set.** A re-run of audits task11-task20 may be needed to confirm which claims are anchored to real code vs. the task14 banner.

## Appendix B: Confirmed file absence (with `ls` outputs)

```
$ ls /Users/jung/pro/rag-pipeline/src/rag/pipeline/
ls: /Users/jung/pro/rag-pipeline/src/rag/pipeline/: No such file or directory

$ ls /Users/jung/pro/rag-pipeline/src/rag/infra/llm/
__init__.py  chat.py  embed.py  rerank.py  semaphore.py
# (rerank_chunk.py NOT present)

$ ls /Users/jung/pro/rag-pipeline/tests/unit/ | grep -E "orch|cite|parent|global"
# (none of test_orchestrator.py, test_cite.py, test_parent_doc.py, test_global_rerank.py present)

$ ls /Users/jung/pro/rag-pipeline/tests/unit/test_rerank.py
/Users/jung/pro/rag-pipeline/tests/unit/test_rerank.py
# (exists, 45 lines, tests QwenRerank HTTP parsing — NOT RerankRunnable)
```

## Appendix C: Status reconciliation

| Source | task14 status | Reality |
|---|---|---|
| task14.md:3 (banner) | OK (历史保留) → 已完成 (2026-06-13 同步) | **False.** 0 of 5 modules on disk. |
| `2026-06-10-python-rag-pipeline.md` (main plan) | (needs verification — not in scope of this audit) | n/a |
| `2026-06-14-task11-alignment.md` (audit #1, line 13-14) | n/a (just cross-refs) | n/a |
| `2026-06-14-task16-alignment.md` (audit #2, line ~13-15) | Cites task14 as "task14: search subgraph / orchestrator / rerank / cite / parent_doc" — implies task14 is the source of these classes | **Class is missing.** task16's `build_full_pipeline` import would fail. |
| `2026-06-14-task20-alignment.md` (audit #3, line ~25-26) | "subgraph / orchestrator / rerank / cite / parent_doc 段 100% 覆盖" | **Coverage is 0% (no files to cover).** |
| Task #133 in TodoWrite | "DocFix-8 task14.md (skipped)" — implies a previous round accepted the doc as-is | **The skip was a false-positive acceptance.** |

**Conclusion:** task14 must be reset to "未实现", and the prior audit chain's transitive claims need to be re-verified.
