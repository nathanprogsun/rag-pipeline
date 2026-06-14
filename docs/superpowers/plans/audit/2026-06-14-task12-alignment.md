# Task 12 Alignment — Filter Pipeline (去重 / 阈值 / token 预算)

> Audit date: 2026-06-14
> Auditor: 3-way alignment (task12.md ↔ rag-pipeline source ↔ FastGPT canonical filter pipeline)
> Scope: `task12.md` claims about `src/rag/pipeline/filter.py` vs. what FastGPT actually does vs. what currently exists in rag-pipeline.

## TL;DR

| Dimension | Finding |
|---|---|
| Path `src/rag/pipeline/filter.py` | **Does not exist.** `src/rag/pipeline/` directory is missing entirely (same as task 11 finding). task 12 is **未实现**, not refactored, despite main plan listing it as "OK" (`2026-06-10-python-rag-pipeline.md:203`). |
| Dedup **already exists**, but in the **wrong place** | `remove_duplicates` is defined in `src/rag/retrieval/trace.py:50-79`, NOT in `src/rag/pipeline/filter.py`. It takes `docs, traces` (parallel arrays) — completely different signature from task12.md's `remove_duplicates(hits: list[ScoredDocument])`. |
| Dedup **semantics divergence** | FastGPT dedups by `${item.q}${item.a}` (regex-stripped, sha256 hash). task12.md originally said "text md5 hash" (spec line 159) but the current task12.md changes to `q+a` normalized md5 hash. The actual rag-pipeline impl uses `(trace.q, trace.a)` tuple identity — **no normalization at all** (whitespace/case sensitive). |
| `ScoredDocument` has migrated to `RetrievalTrace` pattern, contradicting task12.md | `domain/document.py:39-69` has NO `q` / `a` / `rerank_score` fields anymore. The docstring (lines 51-52) explicitly says `(q, a)` has been moved to a parallel-array `RetrievalTrace`. task12.md still says "ScoredDocument 新增可选 q / a 字段" (line 7, 24-25) and "rerank_score: float \| None = None 字段" (line 27). **Both claims are already obsolete** — the field migration has happened. |
| Filter ordering: dedup → score → token | **Match.** FastGPT `defaultRecall/index.ts:160-170` does dedup → similarity filter → token cap. task12.md Step 3 implementation (lines 333-359) does dedup → filter_by_score → filter_by_token_budget. Order is identical. |
| Threshold semantics: RRF score vs raw embedding score | **Mismatch.** task12.md says `using_re_rerank=True → use doc.rerank_score, else doc.score (RRF)`. FastGPT actually uses: `usingReRank → reRank typed score; else if searchMode=embedding → embedding typed score; else no filter`. FastGPT's threshold is the *raw embedding score*, not the RRF score. |
| `searchMode` parameter missing | FastGPT filter takes `searchMode: DatasetSearchModeEnum` and only applies similarity filter in `embedding` mode. task12.md has no such branching. |
| Token budget algorithm: greedy (not priority-queue / heap) | Both FastGPT and task12.md are greedy. Match. |
| Token budget "always keep first" fallback | **Match.** FastGPT `filterDatasetDataByMaxTokens` (utils.ts:63) keeps `data.slice(0, 1)` when all items are dropped. task12.md (lines 313-322) keeps first item with warning. |
| Token budget min_keep param | task12.md exposes `min_keep: int = 1`. FastGPT hardcodes 1. Minor. |
| Token counting: tiktoken (real) vs. `len(text)//2` (heuristic) | **Major mismatch.** FastGPT uses `countPromptTokensBatch` with tiktoken worker (real BPE tokenization). task12.md uses `max(len(text) // 2, 1)` heuristic. Heuristic will silently mis-budget for code/English (4 chars/token) vs Chinese (1-2 chars/token). |
| `using_re_rerank` flag | **Match in concept.** FastGPT gates `scoreType` on `usingReRank`. task12.md gates `_get_score` on `using_re_rerank`. |
| Path of `remove_duplicates` re-use | **Conflict.** Spec has `filter_pipeline` calling `remove_duplicates` (spec line 962). task12.md impl does the same in `subgraph_filter` / `orchestrator_filter` (lines 335, 353). The actual rag-pipeline `remove_duplicates` lives in `retrieval/trace.py` and takes parallel arrays. task12.md wants to put a *second* `remove_duplicates` in `pipeline/filter.py` with a *single-list* signature. **Two functions, same name, different signatures.** |
| stub-first discipline (audit #1 P1-1) | task12.md Step 0 (lines 36-93) does include a full stub module. **Match.** |
| min_keep=N>1 truncation behavior | task12.md (lines 313-322) always keeps first chunk on overflow, then drops rest. FastGPT: keeps first chunk on overflow (utils.ts:53-61) — only keeps 1, not N. |
| `_qna_hash` returns md5(text) fallback (stub) | Step 0 stub (line 45) returns `hashlib.md5(text.encode()).hexdigest()` for missing q+a. Final impl (line 262-265) returns md5 of `text` (raw, not normalized). **Heuristic difference.** FastGPT strips non-letter/number via `/[^\p{L}\p{N}]/gu` regex. |

**Headline P0**: task12.md is built on a stale snapshot of `ScoredDocument`. As of `domain/document.py:39-69`, the `q` / `a` / `rerank_score` fields it claims to "add" (lines 24-27) do **not exist on `ScoredDocument`** — they have been **migrated to `RetrievalTrace`** (parallel-array pattern, see `retrieval/trace.py:34-47`) and `rerank_score` exists but in a *different* semantic position. The implementation as written cannot import `ScoredDocument` with `q=...` constructor args.

**Headline P1**: token counting. FastGPT uses real tiktoken BPE; task12.md uses `len(text)//2` heuristic. Heuristic is silent data corruption for non-Chinese content. The test at task12.md:181-186 (`text="a" * 4000` → 2000 tokens) only passes because the heuristic happens to match in this specific case.

---

## 1. FastGPT 实现 (with file:line citations and code snippets)

### 1.1 Filter pipeline topology

**File:** `packages/service/core/dataset/search/defaultRecall/index.ts`

The filter pipeline lives at the **end** of the default recall flow, applied to the RRF-merged result. Lines 160-170:

```ts
// Step 7: 最终过滤顺序固定为:同内容去重 -> 相似度阈值 -> token 上限。
// 先去重可以避免同一 chunk 因多路召回重复占用相似度过滤和 token 预算。
const filterSameDataResults = removeDuplicateSearchResults(rrfConcatResults);
const { results: scoreFilter, usingSimilarityFilter } = filterSearchResultsByScore({
  data: filterSameDataResults,
  usingReRank: finalUsingReRank,
  searchMode,
  similarity
});

const filterMaxTokensResult = await filterDatasetDataByMaxTokens(scoreFilter, maxTokens);
```

**Order: dedup → score → token.** Confirmed. Comment at line 160-161 states this explicitly: "先去重可以避免同一 chunk 因多路召回重复占用相似度过滤和 token 预算" (dedup first to avoid the same chunk double-consuming the similarity filter and token budget).

### 1.2 Dedup: `removeDuplicateSearchResults`

**File:** `packages/service/core/dataset/search/defaultRecall/result.ts:57-67`

```ts
export const removeDuplicateSearchResults = (data: SearchDataResponseItemType[]) => {
  const set = new Set<string>();

  return data.filter((item) => {
    // 删除所有的标点符号与空格等，只对文本进行比较
    const str = hashStr(`${item.q}${item.a}`.replace(/[^\p{L}\p{N}]/gu, ''));
    if (set.has(str)) return false;
    set.add(str);
    return true;
  });
};
```

Key behaviors:

| Aspect | Behavior | Snippet |
|---|---|---|
| Dedup key | `q + a` concatenated | `` `${item.q}${item.a}` `` |
| Normalization | Strip **all** non-letter / non-number (Unicode `p{L}` + `p{N}`) | `.replace(/[^\p{L}\p{N}]/gu, '')` |
| Hash function | SHA-256 (not MD5) | `hashStr` from `packages/global/common/string/tools.ts:21-23`: `crypto.createHash('sha256').update(str).digest('hex')` |
| Algorithm | First-seen wins (preserves order, keeps rank-1) | `Set` insertion order, `filter` returns first |
| Mutability | Input array NOT mutated; `filter` returns a new array | Built-in JS `Array.prototype.filter` |

**Critical**: `removeDuplicateSearchResults` is also called from `rerank.ts:84` *before* rerank — meaning the dedup happens **twice** in the text-recall branch (once before rerank, once after RRF concat at line 162). This is intentional: rerank input is deduped to avoid wasting rerank quota, and the final RRF result is deduped again because rerank's RRF merge (with weight 1-rerankWeight) re-introduces cross-list duplicates.

**Critical**: there is **no separate `remove_duplicates` for traces in FastGPT** — FastGPT has no concept of `(q, a)` being separate from the chunk. The `q` and `a` *are* attributes of the chunk (returned as `q` field of the result item, see `formatDatasetDataValue` at `data/controller.ts`). rag-pipeline's decision to split this into a parallel `RetrievalTrace` array is a deliberate architectural divergence (explained in `trace.py:1-16`).

### 1.3 Similarity filter: `filterSearchResultsByScore`

**File:** `packages/service/core/dataset/search/defaultRecall/result.ts:69-100`

```ts
export const filterSearchResultsByScore = ({
  data, usingReRank, searchMode, similarity
}: { ... }): { results: SearchDataResponseItemType[]; usingSimilarityFilter: boolean } => {
  const scoreType = usingReRank
    ? SearchScoreTypeEnum.reRank
    : searchMode === DatasetSearchModeEnum.embedding
      ? SearchScoreTypeEnum.embedding
      : undefined;

  if (!scoreType) {
    return { results: data, usingSimilarityFilter: false };
  }

  return {
    results: data.filter((item) => {
      const targetScore = item.score.find((item) => item.type === scoreType);
      return !targetScore || targetScore.value >= similarity;
    }),
    usingSimilarityFilter: true
  };
};
```

Key behaviors:

| Aspect | Behavior |
|---|---|
| Score source | `reRank` typed entry if `usingReRank`, else `embedding` typed entry (only if `searchMode=embedding`), else **no filter** (returns input as-is) |
| Threshold comparison | `targetScore.value >= similarity` (≥, not >) |
| Missing typed score | Kept (the `!targetScore ||` short-circuits to `true`) |
| Mutability | `filter` returns new array; input items NOT mutated |
| Return tuple | `(results, usingSimilarityFilter: boolean)` — explicit `usingSimilarityFilter` flag lets the caller know whether the filter actually ran (used at `index.ts:188` to surface this in the response payload) |

**Critical**: the similarity threshold is compared against the **raw embedding similarity** (or **raw rerank score**), NOT the RRF sum. The RRF score is a *separate* typed entry (`SearchScoreTypeEnum.rrf`, value added at `utils.ts:60-64`) and is **not** used for thresholding. This is the inverse of task12.md's semantics.

**Critical**: the `searchMode` parameter is load-bearing. When `searchMode=fullTextRecall` or `searchMode=mixedRecall`, the **embedding** typed score may not exist on every item, and the function takes the safe path of returning all results unchanged (with `usingSimilarityFilter=false`).

### 1.4 Token budget: `filterDatasetDataByMaxTokens`

**File:** `packages/service/core/dataset/search/defaultRecall/utils.ts:39-79`

```ts
export const filterDatasetDataByMaxTokens = async (
  data: SearchDataResponseItemType[],
  maxTokens: number
) => {
  const startTime = Date.now();
  const tokenList = await countPromptTokensBatch(data.map((item) => item.q + item.a));
  const tokensScoreFilter = data.map((item, index) => ({
    ...item, tokens: tokenList[index] || 0
  }));

  const results: SearchDataResponseItemType[] = [];
  let totalTokens = 0;

  for await (const item of tokensScoreFilter) {
    results.push(item);
    totalTokens += item.tokens;
    if (totalTokens > maxTokens) { break; }
  }

  const filteredResults = results.length === 0 ? data.slice(0, 1) : results;
  // ... logging ...
  return filteredResults;
};
```

Key behaviors:

| Aspect | Behavior |
|---|---|
| Token counting | `countPromptTokensBatch` from `packages/service/common/string/tiktoken/index.ts:120-127` — runs in a worker thread, returns real BPE token counts per item |
| Input for counting | `item.q + item.a` (concatenation of the two fields) |
| Algorithm | **Greedy** (in input order — RRF already sorted), one-pass `for await` loop |
| Termination | `break` on first overflow |
| `min_keep` behavior | Implicit: at least the first item is always pushed before the `break` check. If **all** items overflow, the fallback at line 63 keeps the first item only (not N). |
| Overrun tolerance | **None.** If first item alone is `> maxTokens`, the item is pushed, total exceeds, but result is *non-empty* (the first item alone). No `min_keep=2` parameter. |
| Async | Yes — token counting is async (worker thread). |

A second token-budget implementation exists at `packages/service/core/workflow/utils/index.ts:10-31` (`filterSearchResultsByMaxChars`) with subtly different semantics: it adds a `maxTokens + 500` overrun buffer (allows last item to exceed by up to 500 tokens). **This is a different function used in the workflow path; not in the dataset-search filter chain.** Mentioned for completeness — task12.md should NOT model this.

### 1.5 Rerank interaction with threshold

**File:** `packages/service/core/dataset/search/defaultRecall/rerank.ts:55-110`

The rerank path has a key subtlety:

```ts
const datasetDataReRank = async ({...}) => {
  const { results, inputTokens } = await reRankRecall({...});
  // ...
  return {
    results: mergeResult,    // <-- score: [{ type: reRank, value, index }]
    inputTokens
  };
};
```

After rerank, each item's `score[]` array contains **only** `{ type: reRank, value, index }` (line 41). The original `embedding` / `fullText` / `rrf` typed entries are **replaced**, not augmented. This means the similarity filter downstream must use `scoreType=reRank` (gated by `usingReRank=true`); there is no `embedding` typed score to fall back to on reranked items.

In the no-rerank case (line 87-89), `reRankResults` is returned directly (no merge); `textRecallResults` already has `embedding` / `rrf` typed scores from the upstream RRF. This is why the threshold filter's `scoreType` selection is mode-dependent.

---

## 2. rag-pipeline 当前状态

### 2.1 Path check

```
$ ls /Users/jung/pro/rag-pipeline/src/rag/pipeline/
ls: cannot access ...: No such file or directory
```

**`src/rag/pipeline/` does not exist.** Same as task 11 finding. The plan tree (`2026-06-10-python-rag-pipeline.md:124`) lists `filter.py` under `pipeline/`, but the directory was never created.

The actual rag module layout (`src/rag/`):
```
__init__.py
config.py
domain/  (document.py, dataset.py, search.py, enums.py)
error_codes.py
exception.py
infra/   (pg, redis, llm)
ingest/  (reader, normalizer, chunker)
retrieval/  (trace.py only)
```

**No `filter.py` exists anywhere.** `find /Users/jung/pro/rag-pipeline -name "filter.py" -not -path "*.venv/*"` → no results. `find /Users/jung/pro/rag-pipeline -name "test_filter.py"` → no results.

### 2.2 `remove_duplicates` already exists in `retrieval/trace.py` — with **different signature**

**File:** `src/rag/retrieval/trace.py:50-79`

```python
def remove_duplicates(
    docs: list[ScoredDocumentLike],
    traces: list[RetrievalTrace],
) -> list[ScoredDocumentLike]:
    """按 (q, a) 元组去重, 保留同 (q, a) 下最先出现的项。"""
    if len(docs) != len(traces):
        raise ValueError(f"docs/traces length mismatch: {len(docs)} != {len(traces)}")

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

**Three significant differences from task12.md's claim**:

1. **Signature is two-arg** (`docs, traces`), not single-arg `hits: list[ScoredDocument]`. task12.md's plan to add a second `remove_duplicates(hits)` in `pipeline/filter.py` would create a **name collision** — same name, different signatures, different packages.

2. **No normalization.** The current impl uses raw `(q, a)` tuple identity — case-sensitive, whitespace-sensitive. task12.md's Step 3 (lines 247-253) has `_normalize(s)` that strips whitespace + lowercases. The two are not drop-in compatible.

3. **No hash.** Uses Python `set` of tuples (identity hash on the tuple). task12.md uses md5 hash. Not semantically different for dedup, but a different implementation choice. FastGPT uses sha256. (Hash function is invisible to behavior; the choice of md5 vs sha256 is unimportant. The important difference is *normalization*.)

### 2.3 `ScoredDocument` no longer has `q` / `a` fields

**File:** `src/rag/domain/document.py:39-69`

```python
class ScoredDocument(BaseModel):
    chunk_id: uuid.UUID
    dataset_id: uuid.UUID
    text: str
    score: float
    rank: int
    source: Literal["vector", "fulltext", "caption", "rerank"]
    modality: Literal["text", "image_caption"] = "text"
    image_path: str | None = None
    metadata: ChunkMetadata
    embedding: list[float] | None = None
    rerank_score: float | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)
```

The docstring (lines 51-52) explicitly says:
> "(q, a) 溯源字段已迁出: 见 `rag.retrieval.trace.RetrievalTrace`, 与 `ScoredDocument` 解耦, 只在去重 / 链路阶段按平行数组传入。"

**task12.md still asserts (line 24-25):**
> "subagent #8: `ScoredDocument` 新增可选 `q: str | None = None` / `a: str | None = None` 字段"

**This is a stale claim.** The `q` / `a` fields have *already* been removed from `ScoredDocument` (during task 3 implementation, per the docstring history). The Step 3 implementation in task12.md (lines 244-247) does:
```python
def _qna_hash(q: str | None, a: str | None, text: str) -> str: ...
def remove_duplicates(hits: list[ScoredDocument]) -> list[ScoredDocument]:
    for h in hits:
        key = _qna_hash(h.q, h.a, h.text)  # <-- h.q / h.a do not exist!
```

This will raise `AttributeError: 'ScoredDocument' object has no attribute 'q'` at runtime.

`rerank_score` DOES exist on `ScoredDocument` (line 67) — so the threshold-switch logic at task12.md:282-296 can work. But the constructor calls in the test (task12.md:107-113) pass `q=...` / `a=...` kwargs which will fail Pydantic validation.

### 2.4 No filter code anywhere

```
$ grep -rn "filter_by_score\|filter_by_token_budget\|subgraph_filter\|orchestrator_filter\|filter_pipeline" /Users/jung/pro/rag-pipeline/src/
src/rag/retrieval/trace.py:9:   - q / a 只在 remove_duplicates 去重 (按 query 变体下 top-1 答案)
src/rag/retrieval/trace.py:10:  等链路阶段使用, ...
src/rag/retrieval/trace.py:25:  """``ScoredDocument`` 的 duck-type 协议
src/rag/retrieval/trace.py:27:  Protocol 而非具体类型: 避免 ``remove_duplicates`` 跟 ``rag.domain.document`` 形成循环依赖
src/rag/retrieval/trace.py:41:  a: 该 query 变体下该 chunk 的 top-1 答案片段, ``remove_duplicates`` 按 (q, a) 元组做去重
src/rag/retrieval/trace.py:50:def remove_duplicates(
```

**No `filter_by_score`, no `filter_by_token_budget`, no `subgraph_filter`, no `orchestrator_filter`, no `filter_pipeline`.** task 12 is fully spec-only. The only thing that exists is `remove_duplicates` in `trace.py` (per task 3 implementation).

### 2.5 Token counting: only Chinese tokenizer exists

```
$ grep -rn "countPromptTokens\|count_tokens\|tiktoken" /Users/jung/pro/rag-pipeline/src/
(no matches in src/)
```

The only tokenization in rag-pipeline is the `chinese_tokenizer` for Postgres fulltext search (`src/rag/infra/pg/chinese_tokenizer.py:27-34`). There is **no general-purpose token counter** comparable to FastGPT's `countPromptTokensBatch`. task12.md's Step 3 plan to use `max(len(text) // 2, 1)` heuristic (line 312) is therefore the only option **without** adding a new dependency, but the heuristic is wrong for English / code.

### 2.6 `SearchRequest` does not have a `token_budget` field

**File:** `src/rag/domain/search.py:51-68`

```python
class SearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    query: str
    dataset_ids: list[uuid.UUID]
    image_urls: list[str] = []
    use_global_rerank: bool = False
    audit: bool = False
    retrieval: RetrievalConfig = RetrievalConfig()
    generation: GenerationConfig = GenerationConfig()  # has max_tokens: int = 4000
    context: ContextConfig = ContextConfig()
    history: HistoryConfig = HistoryConfig()
```

`token_budget` is **not** on `SearchRequest`. The only `max_tokens` is in `GenerationConfig.max_tokens: int = 4000` (line 28), which is the **LLM** output budget, not the **retrieval** token budget. task12.md's `subgraph_filter(..., per_dataset_token_budget=...)` and `orchestrator_filter(..., max_tokens=...)` have no obvious source of these values from `SearchRequest`. Per spec line 805: "合并后 text 长度受 max_tokens 限制" — this is also undefined on `SearchRequest`.

### 2.7 No tests for `remove_duplicates`

`find /Users/jung/pro/rag-pipeline/tests -name "*trace*"` → no results. `grep -rn "remove_duplicates" /Users/jung/pro/rag-pipeline/tests/` → no matches. **The existing `remove_duplicates` in `trace.py` has no test coverage.** This is a real gap independent of task 12 — task 3 / task 11 should have added a test, but didn't.

---

## 3. task12.md 关键声明清单

| # | Claim (file:line) | Concrete content |
|---|---|---|
| C-1 | task12.md:11-13 | Create `src/rag/pipeline/filter.py` + `tests/unit/test_filter.py` |
| C-2 | task12.md:15-34 | ScoredDocument gains `q: str \| None = None`, `a: str \| None = None`, `rerank_score: float \| None = None` |
| C-3 | task12.md:47-49 | `remove_duplicates(hits: list[ScoredDocument]) -> list[ScoredDocument]` (single-list signature) |
| C-4 | task12.md:51-57 | `filter_by_score(hits, threshold, using_re_rerank=False)` |
| C-5 | task12.md:59-65 | `filter_by_token_budget(hits, max_tokens, min_keep=1)` returns `(list, warnings)` |
| C-6 | task12.md:67-74 | `subgraph_filter(hits, score_threshold=0.0, per_dataset_token_budget=None, using_re_rerank=False)` |
| C-7 | task12.md:76-83 | `orchestrator_filter(hits, score_threshold=0.0, max_tokens=None, using_re_rerank=False)` |
| C-8 | task12.md:86-92 | `filter_pipeline` is a thin shim delegating to `orchestrator_filter` |
| C-9 | task12.md:117-126 | `test_remove_duplicates_by_qa`: same q+a → dedup, even if text differs |
| C-10 | task12.md:128-133 | `test_remove_duplicates_different_qa_kept`: same text, different q+a → keep both |
| C-11 | task12.md:135-141 | `test_remove_duplicates_fallback_to_text_when_qa_missing`: q=a=None → use text hash |
| C-12 | task12.md:143-148 | `test_remove_duplicates_qa_normalization`: case + whitespace insensitive |
| C-13 | task12.md:152-158 | `test_filter_by_score_threshold`: default uses `doc.score` |
| C-14 | task12.md:160-166 | `test_filter_by_score_uses_rerank_score_when_flag_set`: `using_re_rerank=True → rerank_score` |
| C-15 | task12.md:168-175 | `test_filter_by_score_falls_back_to_score_if_rerank_score_missing`: `rerank_score=None → use score` |
| C-16 | task12.md:179-185 | `test_filter_by_token_budget_keeps_minimum`: 3 docs, budget=100, min_keep=1 → ≥1 kept, warning emitted |
| C-17 | task12.md:189-194 | `test_filter_pipeline_runs_all_steps`: full pipeline dedup+threshold+token |
| C-18 | task12.md:198-205 | `test_subgraph_filter_uses_per_dataset_budget`: per-dataset budget semantics |
| C-19 | task12.md:207-213 | `test_orchestrator_filter_uses_global_budget`: global budget semantics |
| C-20 | task12.md:215-221 | `test_subgraph_filter_passes_rerank_flag`: rerank flag plumbing |
| C-21 | task12.md:223-229 | `test_orchestrator_filter_passes_rerank_flag`: rerank flag plumbing |
| C-22 | task12.md:268-280 | `remove_duplicates` impl: hash-based, q+a primary, text fallback |
| C-23 | task12.md:282-296 | `filter_by_score` impl: rerank_score if `using_re_rerank=True and rerank_score is not None`, else `score` |
| C-24 | task12.md:298-322 | `filter_by_token_budget` impl: `max(len(text)//2, 1)` heuristic, greedy, min_keep first-item fallback |
| C-25 | task12.md:324-341 | `subgraph_filter`: dedup → score → per-dataset token |
| C-26 | task12.md:343-359 | `orchestrator_filter`: dedup → score → global token |
| C-27 | task12.md:362-371 | `filter_pipeline` shim → `orchestrator_filter` |
| C-28 | task12.md:399-401 | Step 6: `subgraph.py` / `orchestrator.py` (Task 14) call sites need to use new functions |
| C-29 | task12.md:402 | `domain/document.py` (Task 3) should add `q` / `a` / `rerank_score` fields — **already done** (rerank_score) / **already removed** (q, a) |

**Step 0 stub discipline (audit #1 P1-1)**: Step 0 (lines 36-93) does provide a complete stub module. **Match.** All 6 function signatures are defined, all return type-safe placeholders. This is the correct stub-first shape.

---

## 4. 三向差异矩阵

| Aspect | task12.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **Path / module location** | `src/rag/pipeline/filter.py` (new file) | **Path does not exist.** No `src/rag/pipeline/` dir. `remove_duplicates` is in `retrieval/trace.py` instead. | 3 separate functions across 2 files: `defaultRecall/result.ts` (`removeDuplicateSearchResults`, `filterSearchResultsByScore`) + `defaultRecall/utils.ts` (`filterDatasetDataByMaxTokens`) |
| **Dedup key** | `q+a` normalized md5 hash, with `text` fallback when both q+a None | `(trace.q, trace.a)` raw tuple, no normalization, no text fallback | `q+a` concatenated, **regex-stripped** (`/[^\p{L}\p{N}]/gu`) to keep only letter/number, then **sha256** hash |
| **Dedup signature** | `remove_duplicates(hits: list[ScoredDocument])` | `remove_duplicates(docs, traces)` (parallel arrays) | `removeDuplicateSearchResults(data)` (single array, q/a inline) |
| **Dedup normalization** | strip whitespace + lowercase | **none** (raw identity) | strip all non-letter/number (Unicode) |
| **Filter ordering** | dedup → score → token | (none) | dedup → score → token (`defaultRecall/index.ts:160-170`) |
| **Threshold semantics (when NOT using rerank)** | compare against `doc.score` (which is RRF sum) | (none) | compare against **embedding typed score** (raw cosine) — NOT RRF sum |
| **Threshold semantics (when using rerank)** | compare against `doc.rerank_score` | `rerank_score` field exists, unused | compare against **reRank typed score** (raw rerank model output) |
| **`searchMode` parameter** | not present | (none) | required parameter: gates whether similarity filter runs at all (only `embedding` mode runs the filter) |
| **`usingSimilarityFilter` return flag** | not in any function signature | (none) | returned alongside `results`; used in `SearchDatasetDataResponse` to inform caller whether the filter actually ran |
| **Threshold operator** | `>=` (implicit, line 296 `if _get_score(h) >= threshold`) | (none) | `>=` (`result.ts:96`: `targetScore.value >= similarity`) — match |
| **Missing score behavior** | rerank_score=None falls back to score (task12.md:174) | (none) | `!targetScore ||` short-circuits to keep item (treats missing as pass) |
| **Token counting method** | `max(len(text) // 2, 1)` heuristic | (none — no general token counter) | `countPromptTokensBatch` (tiktoken BPE, real counts) |
| **Token input** | `text` only | (none) | `item.q + item.a` (concat both fields) |
| **Token algorithm** | greedy, first-item min_keep fallback | (none) | greedy, first-item min_keep fallback |
| **`min_keep` parameter** | `min_keep: int = 1` | (none) | hardcoded 1 (no parameter) |
| **Overrun tolerance** | strict `>` overflow | (none) | strict `>` overflow; secondary `filterSearchResultsByMaxChars` allows `+500` overrun (workflow path, not dataset-search) |
| **Async / sync** | sync (no `await`) | (none) | **async** (token counting runs in worker thread) |
| **Empty input short-circuit** | (not explicit, but the loop body handles empty) | (none) | `if (totalTokens > maxTokens) break;` after first push — but if input empty, returns `[]` |
| **Subgraph / orchestrator split** | 2 separate functions with different budget semantics (per-dataset vs global) | (none) | **no such split** — one filter pipeline at the end of `searchDatasetData` |
| **Reuse of existing `remove_duplicates`** | NO — task12.md creates a new `remove_duplicates` in `filter.py` with single-list signature | YES — `trace.py:50-79` already has it (with parallel arrays) | N/A (FastGPT has one impl) |
| **`using_re_rerank` flag plumbing** | `subgraph_filter(using_re_rerank)` and `orchestrator_filter(using_re_rerank)` | (none) | `usingReRank` is a top-level prop on `searchDatasetDataProps` (`defaultRecall/index.ts:50, 60`) |
| **Stub-first discipline** | Step 0 (lines 36-93) defines all 6 function signatures as return-`[]` stubs | (no module exists) | N/A |
| **Callers (where the filter runs)** | Step 6 (line 399-400): `subgraph.py` + `orchestrator.py` (Task 14) | (none) | `defaultRecall/index.ts:162-170` — single call site after RRF concat, before format-result |
| **Dedup *also* runs before rerank** | (not mentioned) | (none) | YES — `rerank.ts:84` calls `removeDuplicateSearchResults(textRecallResults)` before sending to rerank, to avoid wasting rerank quota |
| **Token budget input field** | `per_dataset_token_budget` (subgraph) + `max_tokens` (orchestrator) — no `SearchRequest` field | (none — no `token_budget` on `SearchRequest`; only `GenerationConfig.max_tokens=4000` exists) | `limit: maxTokens` (FastGPT's `SearchDatasetDataProps.limit` field) |
| **Per-source raw score preservation** | N/A (filter doesn't touch scores; relies on fusion to preserve) | `score_breakdown: dict[str, float]` exists on ScoredDocument (per task 11 fix P0-1) | preserved via `score: {type, value, index}[]` array |
| **Path inconsistency: `pipeline/filter.py` vs `retrieval/trace.py`** | task12.md creates `pipeline/filter.py` (new) | `remove_duplicates` already in `retrieval/trace.py` (different file) | N/A |
| **Tests (count)** | 13 unit tests (lines 117-229) | (none for filter; none for remove_duplicates) | 0 direct unit tests for the 3 filter functions in `defaultRecall.test.ts` (verified via grep) |
| **Lines cited in task12.md:3** | `2026-06-10-python-rag-pipeline.md` lines 2546-2673 | plan file is **505 lines**; lines 2546-2673 do not exist | spec is at `2026-06-10-python-rag-pipeline-design.md:950-969` (§7.5) |

---

## 5. 修复建议 (P0 → P1 → P2 → P3, 每条带具体文件:行号)

### P0 (blocker before sign-off)

#### G-P0-1: `ScoredDocument` does NOT have `q` / `a` fields; task12.md is built on a stale snapshot
**Where:** task12.md:24-25 (claim) + task12.md:107-113 (test) + task12.md:244, 276 (impl).
**Problem:** `src/rag/domain/document.py:39-69` (after task 3 + task 11 fix P0-1) has NO `q` / `a` fields on `ScoredDocument`. The docstring at line 51-52 explicitly says: "(q, a) 溯源字段已迁出: 见 `rag.retrieval.trace.RetrievalTrace`, 与 `ScoredDocument` 解耦". The `RetrievalTrace` parallel-array pattern is the **current** design. task12.md's plan to add `q: str | None = None` and `a: str | None = None` to `ScoredDocument` is **regressive** — it would un-do the architecture decision documented in `retrieval/trace.py:1-16`.

The Step 3 implementation at task12.md:268-280 will fail at runtime with `AttributeError: 'ScoredDocument' object has no attribute 'q'`. The tests at task12.md:107-113 will fail Pydantic validation on `q=...` / `a=...` kwargs.

**Why P0:** The implementation as written cannot import or run.

**Fix options:**

- **Option A (match current architecture, recommended):** Remove the `q` / `a` fields from `ScoredDocument` claim in task12.md. The new `pipeline/filter.py::remove_duplicates` should take a `(docs, traces)` parallel-array signature, **delegating to** the existing `retrieval/trace.py::remove_duplicates` (or re-exporting it). The test file needs `_make_trace(q, a)` helper, not `_doc(q=..., a=...)`.

- **Option B (revert the trace.py split):** Re-add `q` / `a` to `ScoredDocument` and re-think `trace.py`. This is a major architectural revert. **Recommend against** — the docstring at `trace.py:1-16` is well-reasoned and the parallel-array pattern is cleaner for trace-only use cases.

- **Option C (minimal hack):** Add `q: str | None = None` and `a: str | None = None` to `ScoredDocument` as **trace-snapshot fields** (not the canonical source), and have `remove_duplicates` write into them post-dedup. Confusing and error-prone. **Not recommended.**

**Recommended:** Option A. Update task12.md to use the existing `RetrievalTrace` parallel-array signature. Add `from rag.retrieval.trace import remove_duplicates, RetrievalTrace` at the top of `pipeline/filter.py` and re-export. The Step 0 stub at task12.md:47-49 needs to be rewritten.

#### G-P0-2: Threshold compares against RRF sum, not raw embedding score — diverges from FastGPT
**Where:** task12.md:23 (claim "filter_by_score 默认按 doc.score 过滤"), task12.md:152-158 (test), task12.md:282-296 (impl).
**Problem:** FastGPT's similarity filter (when not reranking) compares against the **embedding typed score** (raw cosine), not the RRF sum. RRF is a *ranking* signal, not a *relevance* signal — comparing 0.0/1.0 normalized RRF to a `similarity` threshold (typically 0.2-0.5 raw cosine) is meaningless.

task12.md's threshold of `doc.score >= 0.3` (test at line 156) is comparing to a number that is a sum of `w_g / (60 + rank)` terms — for a single-source hit at rank 1, `score = 1.0 / 61 ≈ 0.0164`; even with weights, RRF scores are tiny. A threshold of 0.3 would drop *every* hit.

**Why P0:** Algorithmic divergence. The threshold filter is effectively a no-op for the FastGPT-equivalent use case.

**Fix:** Change `filter_by_score` to read from `doc.score_breakdown[source]` (where `source` is `"vector"` for the default case), not `doc.score`. The `score_breakdown` field is already on `ScoredDocument` (line 68 of `domain/document.py`, added in task 11 fix P0-1).

```python
def filter_by_score(
    hits: list[ScoredDocument],
    threshold: float,
    using_re_rerank: bool = False,
) -> list[ScoredDocument]:
    def _get_score(h: ScoredDocument) -> float | None:
        if using_re_rerank and h.rerank_score is not None:
            return h.rerank_score
        # FastGPT-compatible: use the per-source raw similarity
        if h.source == "vector":
            return h.score_breakdown.get("vector")
        if h.source == "fulltext":
            return h.score_breakdown.get("fulltext")
        if h.source == "rerank":
            return h.rerank_score
        if h.source == "caption":
            return h.score_breakdown.get("caption")
        return h.score  # fallback for non-fused single-source
    return [h for h in hits if (s := _get_score(h)) is not None and s >= threshold]
```

The test at task12.md:152-158 needs to set `score_breakdown={"vector": 0.5}` and assert against that.

#### G-P0-3: `searchMode` parameter missing — filter runs in wrong context
**Where:** task12.md (entire file — no `searchMode` reference).
**Problem:** FastGPT's `filterSearchResultsByScore` takes `searchMode: DatasetSearchModeEnum` and **skips the filter entirely** when `searchMode != embedding` (and not using rerank). The reasoning: in `fullTextRecall` mode, the items don't have an `embedding` typed score, so the filter would drop everything. rag-pipeline's `filter_by_score` would similarly drop everything if applied to a fulltext-only result list (since `score_breakdown["vector"]` would be missing).

**Why P0:** Without this guard, the filter is incorrect for non-embedding modes.

**Fix:** Add `search_mode: Literal["embedding", "fulltext", "mixed", "vector", "fullText"] | None = None` to `filter_by_score` and `subgraph_filter` / `orchestrator_filter`. When `search_mode` is provided and is not the embedding/vector mode, skip the filter (return input as-is) and add a warning to the returned `warnings` list.

### P1 (significant API/type mismatch)

#### G-P1-1: Token counting is `len(text)//2` heuristic; FastGPT uses tiktoken BPE
**Where:** task12.md:305-306 (docstring "简单 token 估算") + task12.md:312 (impl `max(len(text) // 2, 1)`).
**Problem:** FastGPT calls `countPromptTokensBatch` which uses tiktoken (real BPE). The heuristic `len(text) // 2` is calibrated for Chinese (1-2 chars per token) and is wrong for English / code (4 chars per token). For an English doc, the heuristic **over-counts tokens by 2x**, causing the budget to be exhausted prematurely and citations to be dropped silently.

**Why P1:** Silent data corruption. The test at task12.md:181-186 uses `text="a" * 4000` and asserts `~2000 tokens`, which is calibrated for the heuristic and would pass with the wrong implementation. A real-world English query would fail.

**Fix:** Add `rag.infra.llm.tokenize` (or similar) that wraps `tiktoken` (already a transitive dep via `langchain` / `openai`). Replace the heuristic. Update the test to use real text and assert against the real token count.

Alternative (minimal): add a `tokenizer: Callable[[str], int] | None = None` parameter to `filter_by_token_budget` (matches spec line 953-960 which already mentions `tokenizer=None`); use tiktoken by default if available, fall back to the heuristic. **Recommended:** add the parameter, default to tiktoken.

#### G-P1-2: No `usingSimilarityFilter` return flag
**Where:** task12.md (entire file — no return-flag concept).
**Problem:** FastGPT returns `{ results, usingSimilarityFilter: boolean }` so the caller knows whether the filter actually ran (and can surface this in the API response, see `defaultRecall/index.ts:188`). Without this flag, the `SearchResult.warnings` (which already has a `warnings: list[str]` field per `search.py:89`) cannot distinguish "filter ran and dropped N items" from "filter did not run because mode was fulltext".

**Why P1:** Loss of observability. Operator can't tell from the response why some chunks were dropped.

**Fix:** Either return a 3-tuple `(hits, warnings, usingSimilarityFilter)`, or add the flag to the existing `SearchResult.warnings` as a structured entry like `"similarity_filter: applied, dropped=N"`.

#### G-P1-3: Dedup normalization vs FastGPT's `q+a` regex-strip
**Where:** task12.md:247-253 (`_normalize` strips whitespace + lowercases) + task12.md:262-265 (`_qna_hash` uses `f"{norm_q}|{norm_a}"`).
**Problem:** task12.md's normalization is **less aggressive** than FastGPT's. FastGPT strips ALL non-letter/non-number (`/[^\p{L}\p{N}]/gu`); task12.md only strips whitespace and lowercases. The two will disagree on the dedup decision for inputs like:
- FastGPT considers `("Q?", "A!")` and `("Q", "A")` as the same (strips `?!`).
- task12.md considers them different (only normalizes whitespace + case).

Conversely, the test at task12.md:143-148 (`"  Hello  "` vs `"hello"`) only exercises the whitespace normalization, not the punctuation stripping.

**Why P1:** Silent dedup disagreement. A chunk in two lists with `q="?" + a="?"` and `q="" + a=""` would be deduped by FastGPT but not by task12.md.

**Fix:** Update `_normalize` to also strip punctuation. Or use a regex like FastGPT's. Update the test to cover punctuation.

#### G-P1-4: `pipeline/filter.py` plan duplicates `remove_duplicates` from `retrieval/trace.py`
**Where:** task12.md:11 (Create `filter.py`) + task12.md:47-49 (signature) + `retrieval/trace.py:50-79` (existing impl).
**Problem:** `retrieval/trace.py:50-79` already defines `remove_duplicates(docs, traces)`. task12.md wants a *second* `remove_duplicates` in `pipeline/filter.py` with signature `remove_duplicates(hits)` (single list). Same name, two packages, two signatures. Import collision risk: `from rag.pipeline.filter import remove_duplicates` vs `from rag.retrieval.trace import remove_duplicates`.

**Why P1:** Name collision is a refactoring smell. The two functions also have different dedup keys (q+a tuple identity vs q+a normalized hash) — a caller using one and then the other would get inconsistent results.

**Fix:** Re-export from `pipeline/filter.py`:
```python
from rag.retrieval.trace import remove_duplicates, RetrievalTrace  # noqa: F401
```
And update the signature to match `(docs, traces)`. This unifies the dedup logic.

Alternatively: keep `pipeline/filter.py::remove_duplicates` as a thin wrapper that builds trivial `RetrievalTrace` objects from a single list, but this is ugly.

### P2 (doc-only / cleanup)

#### G-P2-1: `using_re_rerank` flag is global, not per-call
**Where:** task12.md:282-296 (`filter_by_score(... using_re_rerank=False)`).
**Problem:** FastGPT's `usingReRank` is a **per-search** decision (line 60 of `defaultRecall/index.ts`: `usingReRank = inputUsingReRank && !!reRankQuery && !!getDefaultRerankModel()`). It's set once at the top of the search and passed down. task12.md's signature is fine (default arg), but the **test at task12.md:160-166** tests a *single call* with the flag, not the search-level gating. A test asserting the gating logic (flag is `True` only when `reRankQuery` is non-empty AND `rerank_model` is configured) is missing.

**Fix:** Add a test like:
```python
def test_filter_by_score_does_not_use_rerank_when_model_missing():
    """模拟 FastGPT: reRankQuery 为空 → using_re_rerank=False 即便显式传 True。"""
    # This test would belong in the orchestrator-level integration, not filter unit.
    pass  # See task 14 / 16 for end-to-end
```

#### G-P2-2: Token budget doesn't include `q` field
**Where:** task12.md:312 (`est_tokens = max(len(h.text) // 2, 1)`).
**Problem:** FastGPT counts `item.q + item.a` (the q and a are concatenated). `ScoredDocument` doesn't have a `q` field (per G-P0-1) but has a `text` field. Whether `text` is `q + a` or just `a` is ambiguous. Spec at line 805 says "text 长度" — implying `text` is the relevant field. Currently `text` likely is just `a` (the chunk content), and the `q` is the user's question (not part of the chunk). So this is OK if `text` = `a`. **But** if downstream prompts concatenate `q + text` for the LLM, the budget should count `q + text`, not just `text`. task12.md is silent on this.

**Fix:** Add a docstring note: "Estimates tokens for `text` only; if your downstream prompt concatenates `q + text`, add `q` to the budget separately or pre-concat."

#### G-P2-3: `test_subgraph_filter_uses_per_dataset_budget` is structurally weak
**Where:** task12.md:198-205.
**Problem:** Asserts `len(out) <= 4` and `len(out) >= 1` for 5 docs of 200 chars each (≈100 tokens each) with `per_dataset_token_budget=200`. The expected behavior is "200 tokens → 2-3 kept, but assertion is `<= 4`". A 4-doc result is 400 tokens, exceeding the budget. The test passes if 3 or 4 are kept.

**Why P2:** The upper bound is wrong / too loose. Should be `<= 3` (200 tokens / ~70 per doc = 2, plus min_keep = 1 → 3 max).

**Fix:** Tighten to `assert len(out) <= 3`.

#### G-P2-4: Step 6 cross-check (task12.md:399-401) is vague about wiring
**Where:** task12.md:399-401.
**Problem:** Says `subgraph.py` (Task 14) should call `subgraph_filter(..., per_dataset_token_budget=dataset.budget, using_re_rerank=reranker is not None)`. But `Dataset` (per `domain/dataset.py`) has no `budget` field. Where does `dataset.budget` come from? Is it `dataset.token_budget`? A per-dataset value? A shared global? Spec doesn't say.

**Fix:** Document the source: "where `dataset.budget` is `Dataset.token_budget: int` (to be added in task 3 amendment) or, if absent, `SearchRequest.max_tokens // len(dataset_ids)` (even split)."

### P3 (nice-to-have)

#### G-P3-1: Path inconsistency with `retrieval/trace.py`
**Where:** task12.md:11 (Create `pipeline/filter.py`) vs existing `retrieval/trace.py:50` (`remove_duplicates`).
**Problem:** Per the main plan tree (`2026-06-10-python-rag-pipeline.md:124-130`), `pipeline/` is for orchestration and `retrieval/` is for helpers. `remove_duplicates` is a *helper* (per its current placement in `trace.py`), so it arguably belongs in `retrieval/`. But the filter pipeline as a whole is *orchestration* (per the plan tree), so `filter.py` belongs in `pipeline/`. The current split is internally consistent; the only issue is that `remove_duplicates` is being **moved** by task12.md from `retrieval/` to `pipeline/` without explicit rationale.

**Fix:** Document in the docstring: "remove_duplicates is re-exported from `rag.retrieval.trace` for backward compatibility with the existing API. The canonical location is `rag.retrieval.trace`; this file is the orchestration-layer entry point."

#### G-P3-2: `__init__.py` re-exports
**Where:** (will be at) `src/rag/pipeline/__init__.py` and `src/rag/pipeline/filter.py`.
**Problem:** Convention in this repo: domain modules export public surface via `__all__`. `src/rag/retrieval/__init__.py` has `__all__ = ["RetrievalTrace", "ScoredDocumentLike", "remove_duplicates"]`. The new `pipeline/__init__.py` should have `__all__` listing all 6 functions.

**Fix:** When creating `src/rag/pipeline/__init__.py`, add:
```python
from rag.pipeline.filter import (
    remove_duplicates, filter_by_score, filter_by_token_budget,
    subgraph_filter, orchestrator_filter, filter_pipeline,
)
__all__ = [
    "remove_duplicates", "filter_by_score", "filter_by_token_budget",
    "subgraph_filter", "orchestrator_filter", "filter_pipeline",
]
```

#### G-P3-3: `__hash__` on `ScoredDocument` is not defined; cannot use in set
**Where:** `domain/document.py:39` (BaseModel) + `pipeline/filter.py:273` (`seen: set[str]`).
**Problem:** Not a real issue because the dedup uses `str` keys (md5 hash), not `ScoredDocument` instances. But the `Protocol` in `trace.py:24-31` (`ScoredDocumentLike`) only requires `chunk_id: object` — not hashable. If anyone tries to dedup by `chunk_id` (a more efficient design), `uuid.UUID` is hashable, so this works. **Not a real gap.** Mention for completeness.

#### G-P3-4: `min_keep` parameter on `filter_by_token_budget` is not passed through `subgraph_filter` / `orchestrator_filter`
**Where:** task12.md:298-322 (`filter_by_token_budget(hits, max_tokens, min_keep=1)`) + task12.md:324-341 (`subgraph_filter` doesn't expose `min_keep`).
**Problem:** Hardcoded `min_keep=1` at the call sites (lines 339, 357). If a future caller wants `min_keep=3` (e.g. for QA where 3 citations is the minimum usable answer), they can't.

**Fix:** Optional. Add `min_keep: int = 1` to `subgraph_filter` and `orchestrator_filter`. Low priority.

#### G-P3-5: No `model_copy` / immutability test for `filter_by_score` / `filter_by_token_budget`
**Where:** task12.md (no immutability test for these functions).
**Problem:** Only `remove_duplicates` is implicitly tested for non-mutation (it returns a new list). `filter_by_score` (list comprehension, new list) and `filter_by_token_budget` (also new list) are also non-mutating, but no test asserts it. A regression where someone adds `hits.sort()` in-place would not be caught.

**Fix:** Add:
```python
def test_filter_by_score_does_not_mutate_input():
    hits = [_doc(score=0.5), _doc(score=0.1)]
    before = [h.model_copy() for h in hits]
    filter_by_score(hits, threshold=0.3)
    assert all(a.score == b.score for a, b in zip(hits, before))
```

#### G-P3-6: No async / sync separation
**Where:** task12.md (all functions are sync).
**Problem:** FastGPT's `filterDatasetDataByMaxTokens` is **async** because of the worker-thread token counting. rag-pipeline's sync version with `len(text)//2` heuristic has no reason to be async, but if the heuristic is replaced with tiktoken (G-P1-1), the function will need to be async. The downstream caller (`orchestrator.py`, Task 14) needs to know this.

**Fix:** When implementing G-P1-1, declare `filter_by_token_budget` as `async def` and propagate the change to `subgraph_filter` / `orchestrator_filter` (also `async def`).

---

## 6. 实施顺序 (哪些先做)

In order of dependency:

1. **Resolve P0-1** (ScoredDocument.q/a already migrated to RetrievalTrace). Update task12.md to use the parallel-array signature. Re-export `remove_duplicates` from `retrieval/trace` rather than re-implementing. This is a design call before any code.

2. **Resolve P0-2** (threshold compares against raw embedding score, not RRF sum). Update `filter_by_score` to read from `score_breakdown[source]`. Update tests.

3. **Resolve P0-3** (add `searchMode` parameter to gate the filter). Update signatures of `subgraph_filter` and `orchestrator_filter`. Update `SearchRequest` if needed (or pass `search_mode` as a separate arg).

4. **Decide on token counting** (P1-1). If tiktoken is in scope, add `rag.infra.llm.tokenize` and async-ify the filter functions. If heuristic is OK for v1, add a `tokenizer: Callable | None = None` parameter to allow future migration without breaking the API.

5. **Add P1-2** (`usingSimilarityFilter` return flag or warning entry).

6. **Add P1-3** (normalize punctuation in `_normalize` to match FastGPT's regex). Update test.

7. **Apply P2-1 through P2-4** as a doc cleanup pass. The test at P2-3 needs tightening (200/70=2, plus min_keep=1 → 3 max, not 4).

8. **Optional: P3-1 through P3-6** in a follow-up commit.

After 1-6, the task is ready for the stub → test → implement → verify cycle as written in task12.md. Items 1-3 are blockers for any code merge; 4-6 are blockers for the test file to actually validate the FastGPT-compatible behavior. 7-8 are post-merge cleanup.

---

## Appendix A: Confirmed FastGPT call sites for the 3 filter functions

| File:line | Function | Inputs | Notes |
|---|---|---|---|
| `defaultRecall/result.ts:57-67` | `removeDuplicateSearchResults` | `data: SearchDataResponseItemType[]` | q+a regex-stripped, sha256 |
| `defaultRecall/result.ts:69-100` | `filterSearchResultsByScore` | `{ data, usingReRank, searchMode, similarity }` | uses typed `score[]` array; gated on `searchMode=embedding` |
| `defaultRecall/utils.ts:39-79` | `filterDatasetDataByMaxTokens` | `data, maxTokens` | async; tiktoken; greedy; first-item min_keep |
| `defaultRecall/index.ts:160-170` | (orchestrator) | — | dedup → score → token, fixed order |
| `defaultRecall/rerank.ts:84` | (rerank pre-step) | `textRecallResults` | dedup before rerank to save quota |
| `core/workflow/utils/index.ts:10-31` | `filterSearchResultsByMaxChars` | `list, maxTokens` | **different function** for workflow path; has `+500` overrun buffer |

3 filter functions in 2 files in the dataset-search path; 1 separate function in the workflow path. task12.md should model the dataset-search path; the workflow path is out of scope.

## Appendix B: `remove_duplicates` impl comparison

| Dimension | FastGPT `removeDuplicateSearchResults` | rag-pipeline `retrieval/trace.py::remove_duplicates` | task12.md plan |
|---|---|---|---|
| Signature | `(data)` | `(docs, traces)` | `(hits)` |
| Dedup key | `${q}${a}` regex-stripped, sha256 | `(trace.q, trace.a)` raw tuple | md5 of `q+a` normalized, fallback to `text` md5 |
| Normalization | strip non-letter/number (Unicode) | none | strip whitespace + lowercase |
| First-seen wins | yes (`Set` + `filter`) | yes (Python `set`) | yes (`seen` set) |
| Mutates input | no (`filter` returns new) | no (loop appends to `out`) | no (loop appends to `out`) |
| Empty input | returns `[]` | returns `[]` (loop doesn't execute) | returns `[]` (loop doesn't execute) |
| Length-mismatch handling | N/A (single arg) | raises `ValueError` | N/A (single arg) |
| Test coverage | 0 direct unit tests | 0 tests in `tests/` | 4 tests planned (lines 117-148) |

## Appendix C: Path inconsistency clarification (per prompt)

- `src/rag/pipeline/filter.py` (task12 target) — **does not exist**
- `src/rag/retrieval/trace.py` (existing dedup) — **exists**, 80 lines, defines `RetrievalTrace` + `remove_duplicates`
- Per the main plan tree (`2026-06-10-python-rag-pipeline.md:124, 130`):
  - `pipeline/` is for **request-shape orchestration** (filter, subgraph, orchestrator, fusion, rerank, etc.)
  - `retrieval/` is for **cross-cutting retrieval helpers** (decomposition, lazy_greedy, audit, citation_check, trace)
- The split is **intentional** and consistent. `remove_duplicates` belongs in `retrieval/`. The fact that task12.md wants a *second* `remove_duplicates` in `pipeline/` is the actual architectural question (see G-P1-4).
- **No path fix needed for `filter.py` itself**; the inconsistency is between the *plan tree* and the *current state of the repo* (the repo is incomplete — no `pipeline/` dir yet).
- The real question is: should `remove_duplicates` move from `retrieval/` to `pipeline/`, or should `pipeline/filter.py` re-export it? **Recommend: re-export** (no move).

## Appendix D: What task12.md gets right

To balance the critique:

1. **Filter ordering** (dedup → score → token) — exact match with FastGPT `defaultRecall/index.ts:160-170`.
2. **Subgraph / orchestrator budget split** — a useful abstraction that FastGPT doesn't have. rag-pipeline's per-dataset budget is a reasonable refinement; it prevents one chatty dataset from exhausting the global token budget.
3. **Stub-first discipline** — Step 0 (lines 36-93) provides complete function signatures, matching the audit #1 P1-1 fix.
4. **Backward compat shim** — `filter_pipeline` delegating to `orchestrator_filter` (lines 362-371) is clean. Old call sites won't break.
5. **`using_re_rerank` flag plumbing** — both `subgraph_filter` and `orchestrator_filter` accept the flag and pass it to `filter_by_score`. Matches FastGPT's `usingReRank` semantic (lines 60, 81 of `defaultRecall/index.ts`).
6. **Greedy token budget with first-item fallback** — exact match with FastGPT `defaultRecall/utils.ts:53-63`.
7. **`min_keep` parameter** — extends FastGPT's hardcoded `1` to a parameter, which is a small improvement.

These are the bones of a good filter pipeline. The P0/P1 gaps are fixable without changing the overall structure.
