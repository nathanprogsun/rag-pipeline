# Task 11 Alignment — Fusion (intra + inter WRRF)

> Audit date: 2026-06-14
> Auditor: 3-way alignment (task11.md ↔ rag-pipeline source ↔ FastGPT canonical RRF)
> Scope: `task11.md` claims about `src/rag/pipeline/fusion.py` vs. what FastGPT actually does vs. what currently exists in rag-pipeline.

## TL;DR

| Dimension | Finding |
|---|---|
| Path `src/rag/pipeline/fusion.py` | **Does not exist.** Directory `src/rag/pipeline/` is missing entirely. Task 11 is **未实现 (not yet implemented)**, not refactored — even though main plan lists it as "OK" (`2026-06-10-python-rag-pipeline.md:202`). |
| Path inconsistency flagged in prompt | Confirmed. Task11.md targets `src/rag/pipeline/fusion.py` (line 13) but sibling module `src/rag/retrieval/trace.py` lives under `retrieval/`. Plan tree (`2026-06-10-python-rag-pipeline.md:119-136`) places `fusion.py` under `pipeline/` and `trace.py` under `retrieval/` — this is *intended*, not a bug, but task11.md's Step 0 stub does **not** touch trace.py at all. |
| Algorithmic correctness | task11.md WRRF formula `score = Σ_g w_g / (rrf_k + rank_g)` matches FastGPT `weight * (1 / (60 + rank))` exactly, including the `60` constant (FastGPT hardcodes 60 — not configurable; rag-pipeline wants it per-dataset via `Dataset.rrf_k`). |
| Single-list fast-path | **Mismatch.** FastGPT returns `arr[0].list` *as-is* (no RRF recompute, no copy). task11.md says "返回副本" — task11 is *stricter* than FastGPT on immutability. Acceptable, but divergent. |
| Score merge on duplicate `chunk_id` | **Major mismatch.** FastGPT preserves per-typed scores (`{type, value, index}[]` in `SearchDataResponseItemType.score`, see `packages/global/core/dataset/type.ts:421-431`) and takes `max` per type, then *sums* `rrfScore`. rag-pipeline `ScoredDocument.score` is a single `float` (see `src/rag/domain/document.py:52`) — task11.md silently overwrites it with the summed RRF score, **losing the per-source raw scores**. This is a fundamental type-level divergence. |
| `rrf_k` configurability | FastGPT **hardcodes** `60` (no parameter). task11.md / spec demand per-dataset configurability. rag-pipeline side is more correct, but the deviation is real and should be acknowledged. |
| `weights` parameter | FastGPT passes `weight` per list in the *outer* call. task11.md moves the per-group weight inside `intra_fusion(query_groups, weights=...)`. Signature diverges from FastGPT shape. |
| `len(candidates) <= self.k` fast-path | task11.md says "k 在本层无意义 (intra_fusion 不截断)" (line 175-176) but still documents the fast-path from subagent #5. Comment is self-contradictory — the fast-path is never actually triggered. Cleanup needed. |

**Headline P0**: FastGPT's RRF output is a `SearchDataResponseItemType` with a **typed `score: {type, value, index}[]` array**; task11.md's `ScoredDocument` has a single `float score` and the implementation overwrites it with summed RRF — **the per-source `embedding` / `fulltext` / `reRank` raw scores are lost in the rag-pipeline fusion step**. This is a type-level algorithmic gap, not a doc nit.

---

## 1. FastGPT 实现 (with file:line citations and code snippets)

### 1.1 Canonical RRF function

**File:** `packages/global/core/dataset/search/utils.ts`

Signature (lines 5-7):
```ts
export const datasetSearchResultConcat = (
  arr: { weight: number; list: SearchDataResponseItemType[] }[]
): SearchDataResponseItemType[] => { ... }
```

Key behaviors:

| Line(s) | Behavior | Snippet |
|---|---|---|
| 8 | Filter empty lists | `arr = arr.filter((item) => item.list.length > 0);` |
| 10 | Empty-result short-circuit | `if (arr.length === 0) return [];` |
| 11 | **Single-list short-circuit returns input as-is (no copy, no RRF)** | `if (arr.length === 1) return arr[0].list;` |
| 21 | **Hardcoded k = 60** | `const score = weight * (1 / (60 + rank));` |
| 25-32 | **Score merge: per-type `max`** | `const sameScore = concatScore.find((item) => item.type === dataItem.type); if (sameScore) { sameScore.value = Math.max(...); } else { concatScore.push(dataItem); }` |
| 35-39 | **rrfScore sum across lists** | `map.set(data.id, { ...record, score: concatScore, rrfScore: record.rrfScore + score });` |
| 51 | Sort by `rrfScore` desc | `mapArray.sort((a, b) => b.rrfScore - a.rrfScore);` |
| 53-69 | Inject `rrf` typed score into the per-item `score[]` array | mutates or pushes `{ type: SearchScoreTypeEnum.rrf, value, index }` |

**Critical type detail** (`packages/global/core/dataset/type.ts:421-431`):
```ts
score: z.array(z.object({
  type: z.enum(SearchScoreTypeEnum).meta({ description: '评分类型' }),
  value: z.number().meta({ description: '评分值' }),
  index: z.number().meta({ description: '索引' })
})).meta({ description: '评分列表' })
```
`score` is a **list of typed entries**, not a single float. `SearchScoreTypeEnum` (`packages/global/core/dataset/constants.ts:276-281`) has 4 variants: `embedding`, `fullText`, `reRank`, `rrf`.

### 1.2 Callers (all paths go through one function)

1. **`packages/service/core/dataset/search/defaultRecall/result.ts:43-45`** — `concatRecallLists` (uniform weight = 1, used for *intra* multi-query merging inside one source)
2. **`packages/service/core/dataset/search/defaultRecall/result.ts:47-51`** — `concatWeightedRecallLists` (per-list `weight`, used for *intra* cross-source fusion, e.g. embedding+fulltext)
3. **`packages/service/core/workflow/dispatch/dataset/concat.ts:30-35`** — workflow `datasetQuoteQA` aggregator (uniform weight, inter-collection)
4. **`packages/service/core/dataset/search/defaultRecall/index.ts:111-118, 137-146, 149-158`** — 3 explicit call sites in the default-recall pipeline; this is where the multi-stage *intra* (text embedding + text fulltext + imageCaption + imageVector → text final) fusion is built. **There is no separate `intra` vs `inter` function** — it's the same `datasetSearchResultConcat` invoked multiple times with different `weight` payloads.

**Conclusion on topology:** FastGPT has **one RRF function used N times**. There is no Python-style "intra + inter" split. The "intra vs inter" framing in task11.md and the spec is a rag-pipeline abstraction; FastGPT's call sites chain multiple invocations.

### 1.3 RRF call site signature pattern

```ts
// packages/service/core/dataset/search/defaultRecall/index.ts:111-114
const textRecallResults = concatWeightedRecallLists([
  { weight: embeddingWeight, list: textEmbeddingRecallResults },
  { weight: 1 - embeddingWeight, list: textFullTextRecallResults }
]);
```

- `embeddingWeight` defaults to `0.5` (line 49 of `index.ts`).
- `weight: 0` for an empty list means that list is filtered out at the function entry (line 8 of `utils.ts`).

### 1.4 `k` is hardcoded 60

Searched entire FastGPT repo for `rrfK`, `rrf_k`, `RRF_K` — zero matches. The `60` in `60 + rank` is a magic literal. There is no per-dataset override path. This is a known limitation, not a misread.

### 1.5 Single-list behavior

`utils.ts:11` returns `arr[0].list` *as-is*. Confirmed by the unit test at `packages/global/test/core/dataset/search/utils.test.ts:301-318`:
```ts
const input = [{ weight: 1.0, list: items1 }];
const result = datasetSearchResultConcat(input);
expect(result).toEqual(items1); // exact same reference
const rrfScore = result[0].score.find((s) => s.type === SearchScoreTypeEnum.rrf);
expect(rrfScore).toBeUndefined(); // no RRF score attached in single-list case
```

This is intentional: single-list results don't need an `rrf` typed score entry because the rank already implies order. **No copy is made — if the caller mutates the returned list, they mutate the input.** task11.md's "返回副本" claim is *stricter* than FastGPT.

### 1.6 Score-merge semantics on duplicate `id`

`utils.ts:25-32`:
```ts
const concatScore = [...record.score];
for (const dataItem of data.score) {
  const sameScore = concatScore.find((item) => item.type === dataItem.type);
  if (sameScore) {
    sameScore.value = Math.max(sameScore.value, dataItem.value);
  } else {
    concatScore.push(dataItem);
  }
}
```
- For each existing item, walk the new list's score entries.
- Same `type` → **take `max` value**.
- New `type` → append.
- The RRF `rrfScore` is summed across lists (line 38).

**Critical:** the per-source `embedding` / `fullText` / `reRank` raw scores are **preserved**, with `max` collapsing duplicates. The final `rrf` typed entry is added on top.

---

## 2. rag-pipeline 当前状态

### 2.1 Path check

```
$ ls /Users/jung/pro/rag-pipeline/src/rag/pipeline/
ls: cannot access ... : No such file or directory
```

**`src/rag/pipeline/` does not exist.** The plan tree (`2026-06-10-python-rag-pipeline.md:119-130`) lists `fusion.py` under `pipeline/`, but the directory was never created.

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

**No fusion.py exists anywhere.** `find /Users/jung/pro/rag-pipeline -name "fusion.py"` → no results. `find /Users/jung/pro/rag-pipeline -name "test_fusion.py"` → no results.

### 2.2 No fusion code anywhere

```
$ grep -rn "intra_fusion\|inter_dataset_fusion\|WRRF\|RRF" /Users/jung/pro/rag-pipeline/src/
src/rag/infra/pg/fulltext_store.py:20:      向量 + 全文并行检索 → RRF 融合 → LLM；``RunnableConfig`` 支持 tracing。  # docstring only
src/rag/domain/search.py:18:    rerank_weight: float = 0.5  # RRF 混合权重, 向量侧与 rerank 侧各占 0.5
src/rag/domain/document.py:40:    """召回结果: RRF 公式需要 score + rank 同时存。"""
```

**`intra_fusion` and `inter_dataset_fusion` are not defined anywhere in the codebase.** Task 11 is a spec-only document at this point.

### 2.3 Path inconsistency confirmed (per prompt)

`src/rag/retrieval/trace.py` exists (80 lines), defines `RetrievalTrace` and `remove_duplicates` per the docstring. Per the main plan tree (`2026-06-10-python-rag-pipeline.md:132-136`), this is *intentional*:

```
├── retrieval/
│   ├── decomposition.py    # Query Decomposer
│   ├── lazy_greedy.py      # Submodular Query Selection
│   ├── audit.py
│   └── citation_check.py
```

So `retrieval/` is where cross-cutting retrieval helpers (decomposition, submodular selection, dedup) live. `pipeline/` is where request-shape orchestration (subgraph, orchestrator, fusion, rerank, filter) lives. **The split is consistent with the plan.** However:

- `src/rag/retrieval/trace.py` is the *only* file currently in `retrieval/`. The other 3 listed files don't exist either.
- The trace.py file is not "a fusion helper" — it's a dedup helper. task11.md's `fusion.py` belongs to a different concern.

This is **not a bug**, but if the audit wants to flag it, the finding is: "the plan's 2-directory split (`pipeline/` for orchestration, `retrieval/` for helpers) is currently empty for orchestration (no `pipeline/`, no `retrieval/{decomposition,lazy_greedy,audit,citation_check}.py`)."

### 2.4 `ScoredDocument.score` is a single `float` (not a typed array)

`src/rag/domain/document.py:49-60`:
```python
class ScoredDocument(BaseModel):
    chunk_id: uuid.UUID
    dataset_id: uuid.UUID
    text: str
    score: float                # <-- single float
    rank: int
    source: Literal["vector", "fulltext", "caption", "rerank"]
    modality: Literal["text", "image_caption"] = "text"
    image_path: str | None = None
    metadata: ChunkMetadata
    embedding: list[float] | None = None
    rerank_score: float | None = None
```

There is **no typed-score array** comparable to FastGPT's `score: {type, value, index}[]`. task11.md's implementation (lines 188-201) overwrites `.score` with the summed RRF float on duplicate, which **discards the source raw score** for the merged chunk. See gap G-P0-1 below.

### 2.5 `Dataset.rrf_k` exists (per-dataset k)

`src/rag/domain/dataset.py:28`:
```python
rrf_k: int = 60
```

Field is defined but **never read** — `grep -rn "rrf_k" src/` shows only the definition site. The default plan in `2026-06-10-python-rag-pipeline.md:258` confirms `rrf_k per-dataset 可配`. task11.md's caller-side `intra_fusion(..., rrf_k=dataset.rrf_k)` pattern is the intended wiring.

### 2.6 Vector / fulltext weights exist but are unused for fusion

`src/rag/domain/dataset.py:30-31`:
```python
vector_weight: float = 0.7
fulltext_weight: float = 0.3
```

And `src/rag/domain/search.py:18`:
```python
rerank_weight: float = 0.5
```

`grep -rn "vector_weight\|fulltext_weight" src/` → no reads. These are declared but no consumer yet exists (which is what task 11 is supposed to enable).

---

## 3. task11.md 关键声明清单

| # | Claim (file:line) | Concrete content |
|---|---|---|
| C-1 | task11.md:13 | Create `src/rag/pipeline/fusion.py` |
| C-2 | task11.md:22-37 | Stub signature: `intra_fusion(query_groups, rrf_k=60) -> list[ScoredDocument]`, `inter_dataset_fusion(hits, rrf_k=60) -> list[ScoredDocument]` |
| C-3 | task11.md:23 | `DEFAULT_RRF_K = 60` (Cormack 2009) |
| C-4 | task11.md:6-7 | **B4 fix**: `intra_fusion` takes `query_groups: list[list[ScoredDocument]]` (not the old `(vector_hits, fulltext_hits)` two-list shape). Local rank via `enumerate(start=1)` per group, RRF sum across groups on same `chunk_id`. |
| C-5 | task11.md:9 | subagent #5 boundary: `len(candidates) <= self.k: return list(candidates)` fast-path on both functions (return copy, don't mutate input) |
| C-6 | task11.md:158 | `intra_fusion(query_groups, weights: list[float] \| None = None, rrf_k=60)` — **per-group weight parameter** (P0-11 fix, audit #6) |
| C-7 | task11.md:165-171 | WRRF formula: `score(c) = Σ_g w_g / (rrf_k + rank_g(c))`; default `w_g = 1.0`; caller passes `[vector_weight, fulltext_weight]` or `[self.weight, 1.0 - self.weight]` |
| C-8 | task11.md:177-201 | Implementation: build `all_hits`; short-circuit empty; build `by_chunk: dict[uuid.UUID, ScoredDocument]`; per group + per rank compute `w_g / (rrf_k + rank)`; on duplicate chunk_id, `existing.score + score` (sum, not max-per-type); `model_copy` for immutability; sort by `score` desc |
| C-9 | task11.md:203-225 | `inter_dataset_fusion(hits, rrf_k=60)`: same pattern but **no `weights` parameter** (datasets are equal-weight) |
| C-10 | task11.md:129-137 | Test `test_inter_does_not_mutate_input` requires return copy (post-call `hits == before`) |
| C-11 | task11.md:99-115 | Two tests assert `dataset.rrf_k` overrides default (k=30, k=10) |
| C-12 | task11.md:3 | Cross-reference `docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` lines **2416-2542** — **this range is wrong**; the file is only 506 lines. The actual fusion section is the spec at `docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md:902-925` and the plan tree listing at `2026-06-10-python-rag-pipeline.md:122, 202, 258, 276`. |
| C-13 | task11.md:175-176 | Comment block says "intra_fusion 不截断, 仍按'不修改入参'复制" — the "复制" (copy) mention is honest, but the `self.k` reference in the same paragraph is a leftover from subagent #5 template and is meaningless for `intra_fusion`. |
| C-14 | task11.md:250-252 | Step 6 cross-check: ensure `subgraph.py` / `orchestrator.py` (Task 14) call `intra_fusion` with the new `query_groups` signature. (Out of scope for task 11 implementation, but a pre-merge dependency.) |

---

## 4. 三向差异矩阵

| Aspect | task11.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **Path / module location** | `src/rag/pipeline/fusion.py` (new file) | **Path does not exist.** No `src/rag/pipeline/` dir. | `packages/global/core/dataset/search/utils.ts` (one function) |
| **Function signature(s)** | Two functions: `intra_fusion(query_groups, weights, rrf_k)` + `inter_dataset_fusion(hits, rrf_k)` | None | One function: `datasetSearchResultConcat({weight, list}[])` |
| **k value default** | `DEFAULT_RRF_K = 60`, overridable per-call via `rrf_k` param | `Dataset.rrf_k: int = 60` declared but unread | **Hardcoded `60`** literal, no parameter, no override |
| **k configurability** | Per-call parameter; caller passes `dataset.rrf_k` (task11.md:8, 99-115) | Field exists, not wired | None |
| **Weighting scheme** | intra: per-group `weights: list[float] \| None`; inter: equal-weight | Field `vector_weight=0.7, fulltext_weight=0.3` declared on `Dataset` | Per-list `weight: number` (any caller-supplied float); no intra/inter split |
| **Score output format** | `ScoredDocument.score: float` (single number) | `ScoredDocument.score: float` | `SearchDataResponseItemType.score: {type, value, index}[]` (typed array) |
| **Score-merge on duplicate chunk_id** | **Sum** of RRF contributions; raw source score overwritten | (no implementation) | **Per-type max** for typed scores + **sum** for `rrfScore` typed entry; preserves `embedding`/`fullText`/`reRank` raw entries |
| **Empty input short-circuit** | `if not all_hits: return []` (intra) + `if not hits: return []` (inter) | (none) | `if (arr.length === 0) return [];` |
| **Single-list short-circuit** | "返回副本" via `model_copy` in `by_chunk` write path (always copies); no explicit single-list fast-path. Single-element list is processed normally through the loop, so it gets `score = 1.0/(k+1)`. | (none) | **Direct return** of `arr[0].list` as-is (no RRF, no copy, no `rrf` typed entry added). `result === arr[0].list` reference-equality. |
| **Tie-breaking / sort stability** | Python `sorted()` is stable; ties broken by **insertion order** (first-seen chunk_id in `dict` iteration) | (none) | `Array.sort` stable since ES2019; same semantics |
| **Mutability of inputs** | Explicit: `model_copy(update={...})` for all writes; test `test_inter_does_not_mutate_input` asserts post-call list equality | (none) | **No copy** for single-list; multi-list path also does not copy the source lists (writes into a fresh `Map`); but the *returned* objects have a new `rrfScore` typed entry injected, so input item references are mutated via `score.find(...).value = ...` (line 57) |
| **local rank semantics** | `enumerate(start=1)` **per group** (task11.md:189) — this is the B4 fix | (none) | `rank = index + 1` **per list in the outer loop** (utils.ts:19-20) — semantically identical to per-group enumerate when each outer-array element is a list |
| **WRRF per-group weight location** | Inside `intra_fusion` as `weights[g_idx]` | (none) | Per-list `weight` in the *outer* call payload; same per-group effect |
| **Stub-first discipline (audit #1 P1-1)** | Step 0 stub returns `[]` (line 30, 37) so module is importable | (no module exists) | N/A |
| **Topo: 1 dataset × N query variants** | "intra_fusion 不再区分 vector/fulltext 两路 — 真实拓扑是 1 dataset × N 个 query variant" (task11.md:163-164) | (no implementation) | FastGPT DOES distinguish vector/fulltext: `defaultRecall/index.ts:111-118` uses `concatWeightedRecallLists` with `embeddingWeight` and `1-embeddingWeight` for the embedding+fulltext pair. There is no separate "N query variants" path in the intra-dataset RRF step; query extension happens *before* recall and produces multiple text queries that go through the same RRF path via `multiQueryRecall`. |
| **`rrf` typed entry on output** | Not modeled — `ScoredDocument.score` is float | (none) | Added in `utils.ts:53-69` for multi-list result; absent for single-list result |
| **Sort key** | `key=lambda x: x.score, reverse=True` (task11.md:201, 225) | (none) | `(a, b) => b.rrfScore - a.rrfScore` (utils.ts:51) |
| **Index field in output** | Not modified (relies on input `rank` from first group) | (none) | `rrfScore.index = index` after sort (utils.ts:58) |
| **Caller-side wiring** | task11.md:252 defers to Task 14 (`subgraph.py`/`orchestrator.py`) | `Dataset.rrf_k`, `vector_weight`, `fulltext_weight` declared but unread | N/A |

---

## 5. 修复建议 (P0 → P1 → P2 → P3, 每条带具体文件:行号)

### P0 (blocker before sign-off)

#### G-P0-1: Score merge on duplicate chunk_id loses per-source raw scores
**Where:** task11.md:194-200 (the `intra_fusion` implementation) and `:219-224` (`inter_dataset_fusion`).
**Problem:** `existing.score + score` overwrites the single-float `ScoredDocument.score` with the RRF sum. FastGPT's per-type `max` merge preserves `embedding` / `fullText` / `reRank` raw entries in `score[]`. rag-pipeline's `ScoredDocument` has no equivalent place to hold them.
**Why P0:** If a downstream filter (task 12) wants to apply a *raw similarity* threshold (not RRF threshold), the per-source raw scores are gone after fusion. This is a real algorithmic divergence.
**Fix options (pick one before implementation):**
- **Option A (fastest):** Add a `score_breakdown: dict[Literal["vector", "fulltext", "caption", "rerank"], float]` field to `ScoredDocument` (default `{}`). Fusion populates each entry on first sight, takes `max` on duplicate. The `score` field becomes the RRF sum for sort.
- **Option B (closer to FastGPT):** Replace `ScoredDocument.score: float` with `score: list[ScoreEntry]` where `ScoreEntry = {type, value, index?}`. Bigger blast radius — affects ingest, retrieval, rerank, cite. Likely out of scope for task 11; defer to task 2 amendment.
- **Option C (minimal):** Keep `score: float` as RRF sum, add `source_score: float` (the *raw* score from the source the chunk was last seen in). Loses multi-source info, but at least preserves one raw value. **Recommend against this** — information loss is asymmetric.

**Recommended:** Option A. Add `score_breakdown: dict[str, float] = Field(default_factory=dict)` to `src/rag/domain/document.py:39-60`. Update task11.md implementation to write `score_breakdown[source] = max(score_breakdown.get(source, -inf), raw_score)` on each sighting. Tests need a new case asserting that `fused[0].score_breakdown == {"vector": 0.9, "fulltext": 0.7}` when both sources contribute.

#### G-P0-2: `weights` parameter conflicts with B4 "no longer distinguishes vector/fulltext"
**Where:** task11.md:6-7 (B4) and task11.md:158, 168-171 (weights list).
**Problem:** The B4 fix says "intra_fusion 不再区分 vector/fulltext 两路" — i.e. each entry of `query_groups` is a query variant, not a source type. But the docstring at line 170-171 says the caller passes `[vector_weight, fulltext_weight]`. The two claims are contradictory: query variants are *not* the same thing as vector/fulltext sources. In a real topology (1 dataset × N query variants × 2 sources), you need 2N groups, not N, to be able to weight per-source AND per-variant.
**Why P0:** The parameter is misnamed and the test cases at lines 58-97 don't exercise the B4 topology — they only test 1 or 2 groups. The whole signature design is unstable.
**Fix:** Re-read what topology is being modeled. Two valid resolutions:
- **A. Keep "query variant" semantics:** `intra_fusion(query_groups: list[list[ScoredDocument]], weights: list[float] | None = None)`. Each `query_groups[g]` is *one* query variant's combined result (already merged across vector+fulltext upstream by a separate helper). `weights[g]` is the per-query-variant trust weight (e.g. lower for paraphrased queries). In this case, the "vector_weight / fulltext_weight" comment in line 170 is wrong and should be deleted.
- **B. Keep "source" semantics:** `intra_fusion(query_groups: list[list[ScoredDocument]], weights: list[float])` where groups are *sources* (vector, fulltext, caption, rerank, etc.). Then B4's "1 dataset × N query variant" language should be revised — the "N query variants" merge is a *separate* step that happens before this RRF (or as an outer wrapper).

**Recommended:** Resolve in spec call-out, not in code. Both A and B are defensible; the issue is task11.md silently conflates them.

#### G-P0-3: task11.md:3 line-range citation is wrong
**Where:** task11.md:3 → `docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` lines 2416-2542.
**Problem:** The plan file is **506 lines total**. Lines 2416-2542 do not exist. The actual fusion discussion is in the spec file (`docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md:902-925` for intra RRF, `:935`+ for inter RRF, `:133-144` for the topology diagram, `:374-376` for the constants, `:403` for `Dataset.rrf_k`, `:489` for `rerank_weight`).
**Why P0:** A task doc that points reviewers to non-existent file content is a sign-off blocker. Reviewer cannot reproduce the citation check.
**Fix:** Update task11.md:3 to cite the spec file ranges. (P1 acceptable if P0 is reserved for runtime issues.)

### P1 (significant API/type mismatch)

#### G-P1-1: `inter_dataset_fusion` is per-call, not per-dataset
**Where:** task11.md:32-37, 117-128, 203-225.
**Problem:** Signature is `inter_dataset_fusion(hits: list[ScoredDocument], rrf_k=60)`. But the "inter dataset" concept in the spec (line 144) is `score(c) = Σ_dataset 1/(rrf_k+rank)` — that is, each dataset contributes one RRF-ranked list, and the fusion happens *across* datasets. The current signature takes a *flat* list of `ScoredDocument` and re-ranks them with a single `enumerate(start=1)`. This implicitly assumes the caller has already merged all datasets into a single list and lost the per-dataset group identity.
**Why P1:** If a chunk appears in dataset A and dataset B, the per-dataset rank in each is needed to compute the right RRF. With the current signature, the rank is computed as "position in the flat merged list" — which is *not* the same as per-dataset rank. The function is effectively just a re-sorter with RRF weighting of 1, not a true "inter-dataset" RRF.
**Fix:** Match the `intra_fusion` topology. Signature should be:
```python
def inter_dataset_fusion(
    dataset_groups: list[list[ScoredDocument]],   # one list per dataset
    weights: list[float] | None = None,           # default = equal
    rrf_k: int = DEFAULT_RRF_K,
) -> list[ScoredDocument]:
```
Or accept a `dict[uuid.UUID, list[ScoredDocument]]` keyed by `dataset_id`. Either way, the test at task11.md:117-128 only exercises a single dataset, so it doesn't catch this.

#### G-P1-2: Single-list behavior diverges from FastGPT and is undertested
**Where:** task11.md:175-201 (intra) and task11.md:211-225 (inter).
**Problem:** task11.md returns a *new* `ScoredDocument` (via `model_copy`) with `score = 1.0/(rrf_k+1)` for a 1-element group, while FastGPT returns the input list *as-is* (no RRF, no `rrf` typed entry). The current task11.md tests don't cover this case. The divergence is intentional per the "返回副本" promise (good) but the consequence — that `score` is overwritten with RRF even when there's no actual fusion — is not documented.
**Why P1:** If task 12's filter pipeline reads `fused[0].score` thinking it's the source score, it'll get the RRF score instead. This is silent data corruption at the boundary.
**Fix:** Add tests:
```python
def test_intra_single_group_returns_cloned_with_rrf_score():
    hits = [_doc("...0001", score=0.95, source="vector")]
    fused = intra_fusion([hits])
    # input score 0.95 is LOST — fused.score is RRF 1/61
    assert fused[0].score == pytest.approx(1.0 / 61)
    # fusion returns a new ScoredDocument instance, not the input ref
    assert fused[0] is not hits[0]
    # but the source is preserved
    assert fused[0].source == "vector"
```
Document in docstring: "intra_fusion always normalizes score to RRF; pass through `score_breakdown` (per P0-1 fix) to preserve raw source scores."

#### G-P1-3: `rrf_k` reference in `intra_fusion` signature line 158-160 is *both* before and after `weights` in arg order — implementer confusion
**Where:** task11.md:156-160 stub vs. task11.md:158 final implementation.
**Problem:** The stub at line 25-30 has `intra_fusion(query_groups, rrf_k=60)`. The implementation at line 156-160 inserts `weights` *before* `rrf_k`: `intra_fusion(query_groups, weights=None, rrf_k=60)`. This is fine, but the tests at lines 99-115 use positional+keyword mix that depends on the order. If the implementation orders them differently, tests break.
**Why P1:** Drift between stub and final signature is exactly the "stub-first 违反" the audit is meant to catch. The fix is to keep stub and final signatures identical.
**Fix:** Update the stub in Step 0 (task11.md:25-30) to match the final signature exactly:
```python
def intra_fusion(
    query_groups: list[list[ScoredDocument]],
    weights: list[float] | None = None,
    rrf_k: int = DEFAULT_RRF_K,
) -> list[ScoredDocument]:
    return []
```

### P2 (doc-only / cleanup)

#### G-P2-1: Self-contradictory comment block
**Where:** task11.md:175-176.
**Problem:** "边界: 全部 query_group 拼接后不超过 k 时直接返回副本 (subagent #5). k 在本层无意义 (intra_fusion 不截断), 仍按'不修改入参'复制." The "k 在本层无意义" sentence is correct; the first sentence about "fast-path" is misleading because no such fast-path exists in the implementation below (lines 177-201). The "k" reference is a template leftover from subagent #5 (which was about *truncation*, not immutability).
**Fix:** Replace the whole comment with:
```python
# 边界: 无任何候选时直接返回空列表; 返回新对象 (model_copy), 不修改入参。
```

#### G-P2-2: `test_inter_does_not_mutate_input` is a one-shot check
**Where:** task11.md:129-137.
**Problem:** The test only checks `hits == before` for the input list. It does not check that the `ScoredDocument` instances inside the list are unmodified at the field level. The implementation uses `model_copy(update={...})` so field-level mutation is prevented, but a defensive test is cheap.
**Fix:** Add `assert all(d.score == 0.0 for d in hits)` to confirm raw scores are untouched on the input.

#### G-P2-3: Missing `pytest.approx` or `abs(...) < 1e-6` pattern consistency
**Where:** task11.md:62, 72, 81, 97, 107, 115, 127.
**Problem:** All assertions use `abs(x - y) < 1e-6`. The conftest may or may not import `pytest.approx`. Standardize.
**Fix:** Either add `import pytest` (already imported at line 45) and use `pytest.approx(1.0/61)`, or leave as is and add a comment that the tolerance is intentional. Low priority.

#### G-P2-4: Spec and plan agree on k=60 default; document in `__init__`
**Where:** task11.md:23 vs. `src/rag/domain/dataset.py:28` vs. spec `2026-06-10-python-rag-pipeline-design.md:374`.
**Problem:** Three places redundantly document the constant 60. If the value ever changes, three edits are needed.
**Fix:** Make `DEFAULT_RRF_K` a re-export of `Dataset.model_fields["rrf_k"].default`, or define it once in `src/rag/config.py` and import from both `domain/dataset.py` and `pipeline/fusion.py`. P2 because it's stylistic, not algorithmic.

### P3 (nice-to-have)

#### G-P3-1: `weights` length assertion
**Where:** task11.md:184.
**Problem:** The implementation does `assert len(weights) == len(query_groups)`. This is an `assert` (stripped under `-O`) and a `RuntimeError` only at runtime. A `ValueError` is more user-friendly.
**Fix:** Replace with explicit `if len(weights) != len(query_groups): raise ValueError(...)`. Then add a test case for the error.

#### G-P3-2: `inter_dataset_fusion` should also accept a `weights` parameter for symmetry
**Where:** task11.md:203-225.
**Problem:** Spec says "dataset 间等权" (spec:144), but a user might want to weight datasets by per-dataset trust (e.g., boost a hand-curated dataset). FastGPT achieves this by varying the per-list `weight` in the *outer* call, but task11.md's inter function has no such hook.
**Fix:** Add an optional `weights: list[float] | None = None` parameter to `inter_dataset_fusion`, defaulting to equal weight. Not required by spec; nice-to-have.

#### G-P3-3: No `__all__` export declaration
**Where:** (would go in) `src/rag/pipeline/__init__.py`.
**Problem:** Convention in this repo: domain modules export their public surface via `__all__`. `src/rag/retrieval/__init__.py` exists (159 bytes) and should have one too.
**Fix:** When `src/rag/pipeline/__init__.py` is created, declare `__all__ = ["intra_fusion", "inter_dataset_fusion", "DEFAULT_RRF_K"]`.

---

## 6. 实施顺序 (哪些先做)

In order of dependency:

1. **Resolve P0-2** (intra_fusion signature semantics: query variants vs sources). This is a design call that has to happen before any code is written, because it determines the test data structure for P0-1's `score_breakdown` field.

2. **Resolve P0-1** (score_breakdown on `ScoredDocument`). Amend `src/rag/domain/document.py:39-60`. This is a domain-layer change that task 2 (already "OK") should retroactively accept. Update task11.md:194-200 to use `score_breakdown` with `max`-per-source semantics.

3. **Fix P0-3** (line-range citation) before peer review of the doc.

4. **Fix P1-1** (`inter_dataset_fusion` signature to take `dataset_groups: list[list[ScoredDocument]]`). Update the test at task11.md:117-128 to actually pass per-dataset groups.

5. **Fix P1-3** (stub and final signatures must match). Edit task11.md:25-30.

6. **Add P1-2 tests** (single-group immutability + score overwrite documentation).

7. **Apply P2-1, P2-2, P2-3, P2-4** as a doc cleanup pass.

8. **Optional: P3-1, P3-2, P3-3** in a follow-up commit if time allows.

After 1-6, the task is ready for the stub → test → implement → verify cycle as written in task11.md. Items 1-3 are blockers for any code merge; 4-6 are blockers for the test file to actually validate the B4 topology. 7-8 are post-merge cleanup.

---

## Appendix A: Confirmed FastGPT call sites for `datasetSearchResultConcat`

| File:line | Function wrapper | Weight scheme | Purpose |
|---|---|---|---|
| `packages/service/core/dataset/search/defaultRecall/result.ts:43-45` | `concatRecallLists` | uniform `1.0` | intra-source query-variant merge |
| `packages/service/core/dataset/search/defaultRecall/result.ts:47-51` | `concatWeightedRecallLists` | per-list `weight: number` | cross-source (embedding vs fulltext) merge; filters `weight > 0` |
| `packages/service/core/workflow/dispatch/dataset/concat.ts:30-35` | inline | uniform `1.0` | workflow `datasetQuoteQA` aggregator |
| `packages/service/core/dataset/search/defaultRecall/index.ts:111-118` | `concatWeightedRecallLists` | `embeddingWeight` + `1-embeddingWeight` | text embedding + text fulltext → text recall |
| `packages/service/core/dataset/search/defaultRecall/index.ts:115-118` | `concatWeightedRecallLists` | `embeddingWeight` + `1-embeddingWeight` | imageCaption embedding + imageCaption fulltext → imageCaption recall |
| `packages/service/core/dataset/search/defaultRecall/index.ts:137-146` | `concatWeightedRecallLists` | `0.3` + `0.7` (caption vs vector, conditional) | imageCaption + imageVector → image recall |
| `packages/service/core/dataset/search/defaultRecall/index.ts:149-158` | `concatWeightedRecallLists` | `1.0` (text) + `0.7`/`1.0` (image, conditional) | text + image → final recall |

7 distinct call sites, **all using the same `datasetSearchResultConcat`** with different weight payloads. No "intra vs inter" function split exists in FastGPT.

## Appendix B: `ScoreTypeEnum` comparison

| Dimension | FastGPT `SearchScoreTypeEnum` | rag-pipeline `ScoredDocument.source` |
|---|---|---|
| Variants | `embedding`, `fullText`, `reRank`, `rrf` (4) | `vector`, `fulltext`, `caption`, `rerank` (4) |
| Naming | camelCase, prefers `fullText` (Pascal-ish) | snake_case literals |
| `rrf` as a type | Yes (added on RRF result) | No (RRF is the *overwriting* value) |
| Per-typed value | `{type, value, index}` array | Single `float` |

The 4 source categories are equivalent; the structural shape is the divergence.

## Appendix C: Path inconsistency clarification (per prompt)

- `src/rag/pipeline/fusion.py` (task11 target) — does not exist
- `src/rag/retrieval/trace.py` (sibling module) — exists, defines `RetrievalTrace` + `remove_duplicates`
- Per the main plan tree (`2026-06-10-python-rag-pipeline.md:119-136`):
  - `pipeline/` is for **request-shape orchestration** (subgraph, orchestrator, fusion, rerank, filter, query_ext, image_caption, parent_doc, cache_decorator, full, cite)
  - `retrieval/` is for **cross-cutting retrieval helpers** (decomposition, lazy_greedy, audit, citation_check)
- The split is **intentional** and consistent. `trace.py` is currently the only file in `retrieval/` because the other 3 listed files haven't been created yet.
- `fusion.py` belongs in `pipeline/` per the plan tree, **not** in `retrieval/`. task11.md is correct on the path.
- **No path fix needed**; the inconsistency is between the *plan tree* and the *current state of the repo* (the repo is incomplete).
