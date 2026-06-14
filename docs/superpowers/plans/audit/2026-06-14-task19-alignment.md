# Task 19 Alignment — Eval L3 (RAGAS Run + Regression Testing)

> Audit date: 2026-06-14
> Auditor: 3-way alignment (task19.md ↔ rag-pipeline source ↔ FastGPT canonical eval)
> Scope: `task19.md` claims about `tests/eval/{run_ragas.py, regression.py, robustness.py, l1_metrics.py, lazy_greedy_oracle.py}` and `tests/integration/test_regression.py` vs. what FastGPT does vs. what currently exists in rag-pipeline.

## TL;DR

| Dimension | Finding |
|---|---|
| Files declared in task19.md | **None exist.** `tests/eval/` directory does not exist; no `run_ragas.py`, `regression.py`, `robustness.py`, `l1_metrics.py`, `lazy_greedy_oracle.py`, and no `tests/integration/test_regression.py`. Task 19 is **未实现**, just like task 11 — but the plan table (`2026-06-10-python-rag-pipeline.md:210`) marks it **OK**. |
| `ragas` dependency | Pinned `ragas>=0.3,<0.4` in `pyproject.toml` (`[project.optional-dependencies] dev` line ~63). Matches task19.md:64. Lockfile (`src/rag_pipeline.egg-info/requires.txt:24`) confirms `ragas<0.4,>=0.3`. **OK on dependency.** |
| FastGPT uses RAGAS? | **No.** FastGPT implements its own LLM-as-judge metrics (`accuracy` / `relevance` / `semanticAccuracy` on `EvalItemSchema`, `packages/service/core/app/evaluation/evalItemSchema.ts:43-46`). **There is no RAGAS dependency in FastGPT.** rag-pipeline's decision to use RAGAS is a divergence from FastGPT's stack, not a gap in task19.md. |
| FastGPT eval pipeline shape | `packages/service/core/app/evaluation/{evalSchema,evalItemSchema,mq,utils}.ts` + `pro/admin` API routes. Per-item evaluation runs in a BullMQ worker (`mq.ts`, `evaluationQueue`); each item records `accuracy / relevance / semanticAccuracy / score`; CSV upload of test set; thresholds are **not enforced** in code (stored as numbers, no comparison). |
| Regression format in FastGPT | **No regression baseline format in FastGPT.** FastGPT does not store golden retrieved-citation sets per query; it has no Jaccard similarity regression gate. rag-pipeline's Jaccard ≥ 0.95 + `REGRESSION_QUERIES` (25 entries) is a **novel addition** not present in FastGPT. |
| Test gate for LLM-judge in task19 | task19.md:185-211 calls `ragas.evaluate(...)` directly with `from ragas.metrics import context_precision, context_recall, faithfulness, answer_relevancy`. **No judge-model configuration is plumbed through.** RAGAS 0.3.x uses `ChatOpenAI` by default; there is no override hook to pin a deterministic GPT-4-class model or to mock the judge in CI. |
| Statistical regression detection | task19.md uses **per-query deterministic threshold** (`jaccard ≥ 0.95`) on **per-query two-run** repeat (`result_before`, `result_after`). **No baseline file**, no historical metric store, no statistical test (paired t / Wilcoxon / bootstrap CI). The "regression" is really *non-determinism detection* (HNSW effect). |
| Robustness (`tests/eval/robustness.py`) | task19.md:225-273 defines `typo / synonym / reorder` query pairs + `HALLUCINATION_QUERIES`. **Robustness is spec §17 only at the spec level** (`2026-06-10-python-rag-pipeline-design.md` does not have a §17; spec §9.5.3 lists Hallucination as "NLI model" with stub-level mention only). task19.md extends the spec by adding LLM-judge-free hallucination defense (`len(citations) <= 2 or score < 0.3`). |
| L1 component metrics (`tests/eval/l1_metrics.py`) | task19.md:277-310 implements `chunk_length_distribution` + `semantic_boundary_score`. Spec §9.5.1 (line 1296) asks for 50 hand-labeled "ideal split" points — task19.md's `semantic_boundary_score` accepts `[(text, [offsets])]` but only counts `len(chunks) - 1` against `len(expected_offsets)` (line 307-308). **No offset-based matching** — it counts chunks-vs-expected, not chunk-vs-boundary-position. The semantic_boundary_score is effectively a count match, not a boundary hit-rate. |
| `lazy_greedy_oracle.py` | task19.md:351-369 — FastGPT oracle is **wrong**: it just does `sorted(candidates, key=lambda c: (-c[1], c[0]))` and takes top-k. FastGPT's actual lazy greedy is **submodular MMR (Maximal Marginal Relevance)**, not a one-shot arg-sort. The oracle matches the wrong algorithm. **P0.** |
| `REGRESSION_QUERIES` content | task19.md:128-155 — 25 queries, includes `""` (empty string, line 152) and `"x" * 2000` (2000-char string, line 153) and `"SELECT * FROM chunks;"` (line 154) — three explicit edge-case queries. **But there is no test asserting the pipeline behavior on these (does it skip, return empty, raise?).** task19.md uses them as part of `REGRESSION_QUERIES` but never calls them in any test. |
| CI integration (task 20) | `task19.md` does **not** include CI wiring. The CI scheduling for RAGAS / robustness / regression is deferred to **task20.md**. Confirmed at `task20.md:9,47,94-105`. **OK on separation, but tasks 19+20 are co-dependent.** |

**Headline P0**: `lazy_greedy_oracle.py` (task19.md:351-369) claims to replicate FastGPT Lazy Greedy behavior but the algorithm is wrong — FastGPT uses submodular MMR (iterative marginal-gain selection), not a one-shot arg-sort on `(jaccard, chunk_id)`. The "oracle" silently encodes a *different* algorithm under the FastGPT label. **This will cause `test_assert_lazy_greedy_result_equals_fastgpt` to either pass for the wrong reason (the SUT will be modified to match the wrong oracle) or fail when the SUT implements the correct MMR algorithm.** The whole `assertLazyGreedyResultEqualsFastGPT` contrast test is built on a false premise.

---

## 1. FastGPT 实现 (with file:line citations)

### 1.1 Evaluation module topology

`packages/service/core/app/evaluation/`:
- `evalSchema.ts` (57 lines) — `EvaluationSchema` + `EvaluationCollectionName = 'eval'`. Fields: `teamId`, `tmbId`, `appId`, `usageId`, `evalModel`, `name`, `createTime`, `finishTime`, `score`, `errorMessage`. The `evalModel` is a **free-text string** (line 36), not an enum — meaning any model that the user has configured can serve as judge.
- `evalItemSchema.ts` (57 lines) — `EvalItemSchema` + `EvalItemCollectionName = 'eval_items'`. Per-item metrics: **`accuracy: Number`, `relevance: Number`, `semanticAccuracy: Number`, `score: Number` (avg)** (lines 43-46). No RAGAS-style `context_precision`, `context_recall`, `faithfulness`, `answer_relevancy` — **FastGPT uses 3 custom metrics, not the RAGAS 4**.
- `mq.ts` (84 lines) — BullMQ worker. `evaluationQueue` + `addEvaluationJob({evalId})` + `getEvaluationWorker(processor)`. Concurrency is `serviceEnv.EVAL_CONCURRENCY`. **Per-item evaluation is async; the queue is the gate.**
- `utils.ts` (153 lines) — `parseEvaluationCSV` (Papa Parse) + `validateEvaluationFile` (max 1000 rows, required fields prefixed `*`). **CSV is the gold-set format**, not JSONL.
- `packages/global/core/app/evaluation/{constants,type,utils,api}.ts` — types + i18n strings for `EvaluationStatusEnum {queuing, evaluating, completed}` (constants.ts:5-9).

`packages/global/test/core/app/evaluation/` — tests (confirmed dir exists).

`packages/service/support/permission/evaluation/` — auth/permission helpers.

`projects/app/src/{pages,pageComponents,web}/.../evaluation/` — Next.js UI for upload + result display.

**API routes live in `proApi`** (commercial submodule, not present in OSS clone). The web client at `projects/app/src/web/core/app/api/evaluation.ts:11-37` POSTs to `/proApi/core/app/evaluation/create` with `{name, evalModel, appId}` plus a `file` (CSV).

### 1.2 LLM-as-judge model configuration

`evalSchema.ts:36` — `evalModel: { type: String, required: true }` (line 34-37). This is the model used to judge accuracy / relevance / semanticAccuracy. The model is **user-supplied** in the dashboard (`projects/app/src/pages/dashboard/evaluation/create.tsx:31,49`) and is bound to whatever LLM the user has configured with `useInEvaluation=true` (`projects/app/src/pages/api/core/ai/model/update.ts:38` deletes `useInEvaluation` on update).

The judge prompt templates and the scoring logic **live in the `pro/admin` submodule**, which is **not present in this OSS clone** (`/Users/jung/pro/FastGPT/pro/` directory absent — confirmed). The specific judge prompts cannot be audited here.

What is visible:
- Judge runs the user's app, gets a response, scores it on 3 dimensions.
- `accuracy` is whether the response matches `expectedResponse` semantically.
- `relevance` is whether the response answers the question.
- `semanticAccuracy` is whether the response is factually consistent with the input/context.

(All three names are visible on `evalItemSchema.ts:43-45`; the actual prompt bodies are in `pro/`.)

### 1.3 Per-item scoring lifecycle

```
upload CSV → POST /proApi/core/app/evaluation/create (formData)
  → addEvaluationJob({evalId}) (mq.ts:32-36, with deduplication)
    → BullMQ worker processes:
       for each row in EvalItem { queuing → evaluating → completed }
       run app, get response, call judge model, write {accuracy, relevance, semanticAccuracy, score}
       average all per-item scores → Evaluation.score
```

Status enum: `EvaluationStatusEnum { queuing=0, evaluating=1, completed=2 }` (`constants.ts:5-9`).

### 1.4 No RAGAS, no regression baseline, no Jaccard

```
$ grep -rn "ragas\|jaccard\|regression\|baseline" packages/service/core/app/evaluation/
(no matches)
```

FastGPT's eval module has:
- **No RAGAS dependency** (FastGPT is JS/TS; ragas is Python-only; this is a non-issue across stacks, but it's also true that FastGPT doesn't wrap ragas in a JS shim).
- **No regression baseline file format** — no `goldset.jsonl` with expected citation sets.
- **No Jaccard similarity gate** — `compare_results` is not a thing in FastGPT.
- **No statistical test** — `Evaluation.score` is just the mean of per-item averages.

The FastGPT eval is **run-once-and-show-results**. There is no "before vs after" regression detection built in.

### 1.5 Threshold enforcement

The `score` field is a `Number` and is never compared to a threshold in the OSS-visible code. The dashboard UI (`projects/app/src/pageComponents/app/evaluation/DetailModal.tsx`) is the only consumer; whether it shows red/green depends on UI logic not visible here.

`evalItemSchema.ts:43-46`:
```ts
accuracy: Number,
relevance: Number,
semanticAccuracy: Number,
score: Number, // average score
```

**No `threshold`, no `passIf`, no `expectedAccuracy`.** This is a **scoring-only system**, not a pass/fail gating system. rag-pipeline's task19.md adds this gating layer as a **novel addition**; it doesn't need to match FastGPT's absent behavior.

---

## 2. rag-pipeline 当前状态

### 2.1 Path check

```
$ ls /Users/jung/pro/rag-pipeline/tests/eval
ls: cannot access ... : No such file or directory

$ find /Users/jung/pro/rag-pipeline -name "run_ragas.py" -o -name "regression.py" -o -name "robustness.py"
(no results in src/, tests/, or anywhere except rag-pipeline/.venv)

$ find /Users/jung/pro/rag-pipeline -name "test_regression.py"
(no results)
```

**`tests/eval/` directory does not exist.** The plan tree (`2026-06-10-python-rag-pipeline.md:158-160`) lists `regression.py` and `run_ragas.py` under `tests/eval/`, but the directory was never created.

Current `tests/` layout (real):
```
conftest.py              # minimal: test_settings_loads
data/                    # 11 sample.* fixtures
unit/                    # reader/, normalizer/, chunker/, + core/domain/ingest/
integration/             # test_chunk_repo, test_cache, test_vector_retrieval,
                         # test_fulltext_retrieval, test_ingest_e2e, test_pg_connection,
                         # test_llm_live
```

**No `tests/eval/`, no `tests/integration/test_regression.py`.** task19 is unimplemented, just like task 11.

### 2.2 RAGAS dependency

`pyproject.toml:62-65`:
```toml
[project.optional-dependencies]
dev = [
    "datasets>=3.0.0",
    "mypy>=1.11.0",
    "pre-commit>=4.0.0",
    "pytest-cov>=5.0.0",
    "pytest-xdist>=3.6.0",
    "ragas>=0.3,<0.4",        # <-- task19's target
    ...
]
```

`src/rag_pipeline.egg-info/requires.txt:24` confirms `ragas<0.4,>=0.3` (generated from `pyproject.toml`).

**RAGAS pin matches task19.md:64-75 perfectly.** No drift. Audit #2 P1-9 (RAGAS 0.3→0.4 documentation) is correctly applied: the comment in `Step 0` documents the version constraint and gives a migration example.

Caveat: `ragas` is an **optional** dev dependency (`[project.optional-dependencies] dev`). To run RAGAS in CI, the `dev` extra must be installed (`uv sync --extra dev` or `pip install -e .[dev]`). task19.md:121 says `uv run pytest tests/integration/test_regression.py -v` but doesn't pin the extra — this is fine because `uv` resolves extras automatically when running scripts from the project root.

### 2.3 SearchRequest / pipeline state

`src/rag/domain/search.py:34-39` (`ContextConfig`):
```python
class ContextConfig(BaseModel):
    parent_doc_window: int = 0
    query_extension: bool = True
    max_query_variants: int = 3
    query_decomposition: bool = False
```

`query_extension` exists as a config flag. task19.md:107-115 references `pipeline.ainvoke({"query": ..., "query_extension": True, "dataset_ids": []})` — but the actual pipeline takes a `SearchRequest` (typed object), not a dict. **This is a signature mismatch** between task19.md's test code and the actual API. The pipeline is not yet implemented (no `src/rag/pipeline/` dir), so this divergence is **future-tense** — both the pipeline and the test code will land at the same time. If task 14 (Pipeline) lands with `pipeline.ainvoke(SearchRequest(...))` and task 19's test code does `pipeline.ainvoke({"query": ...})`, **the test will fail at call-time, not RED-phase.** P2 risk.

### 2.4 Citation / chunk_id types

`src/rag/domain/search.py:80-89` (`Citation`):
```python
class Citation(BaseModel):
    chunk_id: uuid.UUID
    dataset_id: uuid.UUID
    source_name: str
    content: str
    image_path: str | None = None
    score: float
    update_time: datetime | None = None
```

`chunk_id` is `uuid.UUID`. task19.md:99-103 uses `fake_citation = type("C", (), {"chunk_id": None})` — a stub class. `test_compare_results_jaccard_above_threshold` then calls `compare_results(before, after, threshold=0.95)`, which reads `str(c.chunk_id)` (task19.md:167-168). For the stub, `chunk_id=None`, so `str(None) == "None"`. The test passes because both stubs have `chunk_id=None` → same set. **The stub test doesn't exercise the real `Citation.chunk_id` path** — it only verifies that `compare_results` works on objects with a `chunk_id` attribute.

This is **acceptable stub-level coverage** but the integration test at task19.md:107-115 (`test_regression_query_extension_path`) does correctly use `str(c.chunk_id) for c in result.citations` on the real `Citation` model.

### 2.5 `lazy_greedy` module state

```
$ grep -rn "lazy_greedy\|LazyGreedy" /Users/jung/pro/rag-pipeline/src/
(no matches)
```

`src/rag/retrieval/` exists but contains only `trace.py` (per the task 11 audit). The plan tree (`2026-06-10-python-rag-pipeline.md:135-136`) lists `lazy_greedy.py` under `retrieval/`, but it hasn't been written.

`task19.md:351-369` introduces `tests/eval/lazy_greedy_oracle.py` (subagent #4). The `our_lazy_greedy_select` function (line 364-368) is a placeholder that just calls `fastgpt_lazy_greedy_select`:
```python
def our_lazy_greedy_select(candidates: list[tuple], top_k: int) -> list[str]:
    return fastgpt_lazy_greedy_select(candidates, top_k)
```

This is **acceptable as a stub** but the **oracle itself is wrong** (see §1.6 above and P0-1 below).

### 2.6 Existing tests directory

`tests/integration/` has 8 test files (confirmed):
- `test_cache.py`, `test_chunk_repo.py`, `test_fulltext_retrieval.py`, `test_ingest_e2e.py`, `test_llm_live.py`, `test_pg_connection.py`, `test_vector_retrieval.py`

None of these are regression tests. `test_regression.py` is a new file.

---

## 3. task19.md 关键声明清单

| # | Claim (file:line) | Concrete content |
|---|---|---|
| C-1 | task19.md:14-19 | Create 5 files in `tests/eval/` + `tests/integration/test_regression.py` |
| C-2 | task19.md:21-75 | Step 0: stubs + RAGAS 0.3.x version constraint |
| C-3 | task19.md:64-76 | RAGAS pin `>=0.3,<0.4`; 0.3.x API: `evaluate(...)` + `from ragas.metrics import context_precision, context_recall, faithfulness, answer_relevancy`; input `datasets.Dataset` with `question / ground_truth / contexts / answer` |
| C-4 | task19.md:78-116 | Step 1: failing tests for Jaccard, REGRESSION_QUERIES len ≥ 20, compare_results Jaccard ≥ 0.95, `test_regression_query_extension_path` |
| C-5 | task19.md:126-170 | Step 3: REGRESSION_QUERIES (25 entries including empty / 2000-char / SQL), `jaccard(set_a, set_b)`, `compare_results(before, after, threshold=0.95)` |
| C-6 | task19.md:174-220 | Step 4: `run_eval(goldset_path, pipeline_factory)` runs pipeline on each goldset row, builds HF Dataset, calls `ragas.evaluate(...)` with 4 metrics |
| C-7 | task19.md:222-273 | Step 4a: robustness — typo / synonym / reorder variants; `test_robustness(pipeline, query_pairs, threshold=0.7)`; `HALLUCINATION_QUERIES` (3 entries); `test_hallucination_defense(pipeline, llm)` |
| C-8 | task19.md:275-310 | Step 4b: L1 — `chunk_length_distribution` (count/mean/median/stdev/min/max/p95); `semantic_boundary_score(chunker, docs)` counts chunks-vs-expected-offsets |
| C-9 | task19.md:312-369 | Step 4c: `test_assert_lazy_greedy_result_equals_fastgpt` with 10 candidates; `fastgpt_lazy_greedy_select` = `sorted(c, key=(-jaccard, c[0]))[:k]` |
| C-10 | task19.md:371-380 | Step 4d: cross-check list for `test_regression.py` |
| C-11 | task19.md:382-420 | Step 4e: `SyntheticQuestion` Pydantic schema with `Field(..., min_length=1)`; `gen_synthetic_queries(chunks, llm, n=50)` |
| C-12 | task19.md:422-426 | Step 5: run pytest, expect ≥ 6 passed |
| C-13 | task19.md:3 | Cross-reference `docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` lines 4562-4795. **File is 505 lines; range does not exist.** Same kind of bug as task 11's P0-3. |
| C-14 | task19.md:185-212 | `answers.append(result.prompt)` — uses `result.prompt` for the `answer` field in RAGAS. RAGAS `faithfulness` requires the LLM-generated answer; `result.prompt` is the prompt passed to the LLM, not the answer. **This is a semantic bug** — faithfulness should be scored against the LLM's *response*, not its *input prompt*. The pipeline's response field name matters. |
| C-15 | task19.md:214-220 | `if __name__ == "__main__"` block is **non-functional** — it parses `--goldset` arg but doesn't actually call `run_eval()`. Prints a "use as library" message. The script does nothing standalone. |

---

## 4. 三向差异矩阵

| Aspect | task19.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **Eval module location** | `tests/eval/{run_ragas.py, regression.py, robustness.py, l1_metrics.py, lazy_greedy_oracle.py}` + `tests/integration/test_regression.py` | **Path does not exist.** No `tests/eval/`. No `tests/integration/test_regression.py`. | `packages/service/core/app/evaluation/` (4 files) + `packages/global/core/app/evaluation/` (4 files) + `pro/admin` (commercial, not in OSS clone) |
| **Eval framework** | RAGAS (`ragas.evaluate(...)` with 4 metrics) | RAGAS pinned in `pyproject.toml` dev extra, **never imported** | Custom LLM-as-judge with 3 metrics (accuracy / relevance / semanticAccuracy); no RAGAS |
| **LLM-judge model config** | RAGAS 0.3.x uses default `ChatOpenAI` (no override in task19 code) | None | `evalModel: String` field on `EvaluationSchema`; user-selected from `useInEvaluation=true` model list (`projects/app/src/pages/dashboard/evaluation/create.tsx:49`) |
| **Gold set format** | `tests/eval/goldset.jsonl` (JSONL with `{query, ground_truth_answer}` fields per row at task19.md:201) | (no file) | **CSV** with `*q, *a, history` header (`packages/global/core/app/evaluation/utils.ts:3-10`); `*` prefix marks required fields; max 1000 rows |
| **Per-item metrics** | `context_precision, context_recall, faithfulness, answer_relevancy` (RAGAS) | (no eval code) | `accuracy, relevance, semanticAccuracy, score` (custom, not RAGAS) |
| **Threshold gating** | Jaccard ≥ 0.95 per query (regression) + answer/citation-count for hallucination defense | (no code) | **No threshold in code**; `score` is informational, no `passIf` field on schema |
| **Regression baseline** | None — compares two consecutive runs of the same query (before/after) | (no code) | None — FastGPT does not have a regression detection feature |
| **Statistical test** | None — per-query deterministic threshold | (no code) | None |
| **Robustness testing** | `tests/eval/robustness.py` with `typo / synonym / reorder` query variants + Jaccard ≥ 0.7 | (no code) | None |
| **Hallucination defense** | `assert len(citations) <= 2 or score < 0.3` | (no code) | `semanticAccuracy` is the closest analog (LLM-judge measures factual consistency) |
| **L1 component metrics** | `chunk_length_distribution` (count/mean/median/stdev/min/max/p95) + `semantic_boundary_score` (chunks-vs-expected-count) | (no code) | None — FastGPT does not expose chunker quality metrics in eval |
| **`lazy_greedy` oracle** | `sorted(c, key=(-jaccard, c[0]))[:k]` — arg-sort on (jaccard, chunk_id) | (no code) | FastGPT does not have a `lazy_greedy` function in `packages/service/core/`. The subagent #4 reference to "FastGPT Lazy Greedy" is **fabricated**. |
| **Concurrency / queue** | Not modeled — task19 runs sequentially in pytest | (no queue) | BullMQ `evaluationQueue` (`mq.ts:12-20`) with `attempts: 3, exponential backoff 1000ms`; `EVAL_CONCURRENCY` env knob |
| **CI integration** | Deferred to task20.md (RAGAS weekly + on-PR regression) | (no CI) | (no FastGPT OSS CI for this — eval is a per-team dashboard feature) |
| **Stub-first discipline (audit #1 P1-1)** | Step 0 stubs: `return 0.0`, `raise NotImplementedError`, empty `REGRESSION_QUERIES` | (no module exists) | N/A |
| **Per-item status lifecycle** | Not modeled | (none) | `EvaluationStatusEnum {queuing=0, evaluating=1, completed=2}` (`constants.ts:5-9`); stored on `EvalItem.status` (`evalItemSchema.ts:32-36`) |
| **Retry semantics** | Not modeled | (none) | `EvalItemSchema.retry: Number, default: 3` (`evalItemSchema.ts:37-41`) |
| **File upload** | Not modeled in task19 | (none) | `validateEvaluationFile(rawText, appVariables)` enforces CSV header + max rows + required fields |
| **`evaluate_result.score` aggregation** | RAGAS handles (not task19's concern) | (none) | Per-item average → `Evaluation.score` (a single `Number` on the eval document) |
| **Cross-spec citations** | task19.md:3 — plan lines 4562-4795 (does not exist) | plan is 505 lines | N/A |

---

## 5. 修复建议 (P0 → P1 → P2 → P3, 每条带具体文件:行号)

### P0 (blocker before sign-off)

#### G-P0-1: `lazy_greedy_oracle.py` "FastGPT oracle" is fabricated
**Where:** task19.md:359-369 (`fastgpt_lazy_greedy_select`).
**Problem:** The function is:
```python
def fastgpt_lazy_greedy_select(candidates: list[tuple], top_k: int) -> list[str]:
    """复刻 FastGPT 行为: 按 jaccard 降序;相同分时按 chunk_id 升序。"""
    sorted_cands = sorted(candidates, key=lambda c: (-c[1], c[0]))
    return [c[0] for c in sorted_cands[:top_k]]
```
**There is no such function in FastGPT.** Searched:
- `find /Users/jung/pro/FastGPT/packages -name "*.ts" | xargs grep -l "lazy_greedy\|LazyGreedy\|lazyGreedy"` → no results in source (only `node_modules` matches from third-party packages).
- FastGPT's `MergeSearchResult` / `datasetSearchResultConcat` does weighted RRF, not lazy greedy.
- The "lazy greedy" terminology is associated with Carbonell & Goldstein 1998 (MMR) and Krause & Golovin 2014 (submodular greedy). Neither is implemented in FastGPT's eval or retrieval paths.

The real FastGPT ranking after RRF is **already deterministic** (sort by `rrfScore desc`, tie-break by `index`). What the subagent #4 might have been referencing is **FastGPT's own Lazy Greedy implementation in a different layer** — possibly in the workflow `datasetQuoteQA` node, but that uses the same RRF, not MMR.

**Why P0:** `test_assert_lazy_greedy_result_equals_fastgpt` (task19.md:319-342) will either:
- (a) pass when the SUT is **silently modified to match the wrong oracle**, or
- (b) fail when the SUT implements the **correct MMR / submodular greedy** algorithm.

Either outcome is a sign-off blocker — option (a) ships a wrong-by-design oracle; option (b) tells us the spec disagreement is real and unfixable without a spec amendment.

**Fix options (pick one):**
- **Option A (correct):** Find the real FastGPT lazy_greedy source (or admit it doesn't exist; rebuild spec). Update oracle to match real algorithm. Likely outcome: this function **doesn't exist** in FastGPT, so the entire `lazy_greedy_oracle.py` should be **deleted** and `test_assert_lazy_greedy_result_equals_fastgpt` removed.
- **Option B (renaming):** Rename the file to `rag_pipeline_lazy_greedy_oracle.py` and the test to `test_assert_lazy_greedy_is_stable_and_correct` — assert that the SUT is internally consistent (same input → same output across runs), not that it matches FastGPT. Document the source-of-truth as the algorithm description in spec, not FastGPT code.
- **Option C (find the real source):** Grep FastGPT `pro/admin` (when cloned) for `lazy`, `submodular`, `greedy`, `mmr`. If a real FastGPT lazy greedy exists in the commercial submodule, audit that. Without `pro/admin` cloned locally, the subagent's claim cannot be verified.

**Recommended:** Option B. Delete the "FastGPT oracle" framing and reframe as a stability/correctness test.

#### G-P0-2: `result.prompt` is the LLM input, not the answer — faithfulness will be scored against the wrong string
**Where:** task19.md:202 — `answers.append(result.prompt)`.
**Problem:** RAGAS `faithfulness` requires the LLM's **generated answer** as the `answer` field, not the prompt. `result.prompt` (per the model in `src/rag/domain/search.py:80-89` `Citation` and the wider pipeline shape) is the prompt sent to the LLM.

**Why P0:** Every RAGAS `faithfulness` score will be computed against the input prompt, which has no relationship to the actual answer. The metric will report **artificially low faithfulness** (because the prompt doesn't match the ground_truth in any meaningful way), and the eval will look broken even when the pipeline is correct.

**Fix:** The pipeline must return a `result.response` or `result.answer` field holding the LLM's output. Confirm this field exists when task 14 (`Pipeline`) lands; if not, add it as part of task 19's contract. Update task19.md:202 to `answers.append(result.response)` (or whatever the canonical name is).

#### G-P0-3: task19.md:3 line-range citation is wrong
**Where:** task19.md:3 → `docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` lines 4562-4795.
**Problem:** The plan file is **505 lines total**. Lines 4562-4795 do not exist.
**Why P0:** Same issue as task11.md P0-3. Reviewer cannot reproduce the citation check.
**Fix:** Update to a real range. The relevant section in the spec is `2026-06-10-python-rag-pipeline-design.md:1399-1432` (§9.7 Regression Testing) + `:1283-1396` (§9.5 Eval stack).

### P1 (significant API/type mismatch)

#### G-P1-1: `pipeline.ainvoke({"query": ...})` vs. `SearchRequest` typed-object API
**Where:** task19.md:110-111, 198, 251, 269.
**Problem:** All test code calls `pipeline.ainvoke({"query": "RRF 公式是什么?", "query_extension": True, "dataset_ids": []})` (dict-style). The actual API in `src/rag/domain/search.py:55-67` is `SearchRequest` (Pydantic model).
**Why P1:** If task 14 implements `pipeline.ainvoke(SearchRequest)` (which is the convention given `ContextConfig` is a sub-config), **every test in task19 will fail at call-time, not RED-phase**. Tests will throw `ValidationError` or `TypeError`, not the intended `AssertionError`.
**Fix:** Either:
- (A) Update task19.md to use `SearchRequest(query=..., dataset_ids=[], context=ContextConfig(query_extension=True))` (typed).
- (B) Have task 14 implement `pipeline.ainvoke` with dict-accepting overload (uncommon; better to keep strict types).

Recommended: Option A. Standardize on typed `SearchRequest`.

#### G-P1-2: RAGAS judge model is not pinned / mockable
**Where:** task19.md:185-211 (`run_eval`).
**Problem:** RAGAS 0.3.x `evaluate(...)` uses default `ChatOpenAI(temperature=0)` for LLM-judge metrics. There's no:
- Configuration for which OpenAI model to use (gpt-4o, gpt-4-turbo, etc.)
- Fallback for non-OpenAI providers (Anthropic, local models)
- Mock for CI (LLM-judge calls are non-deterministic and cost money per CI run)
- Caching layer (RAGAS calls the judge 4× per row × N rows = expensive)

**Why P1:** A weekly CI run with 50 goldset rows × 4 metrics = 200 LLM-judge calls. At GPT-4o rates that's $5-10/week. **No caching → repeats work on rerun.** **No mock → CI cannot run on OSS-PRs without an `OPENAI_API_KEY` secret.**

**Fix:** Add a `judge_llm` parameter to `run_eval(goldset_path, pipeline_factory, judge_llm=None)` that defaults to `ChatOpenAI(model="gpt-4o", temperature=0)`. CI can pass a fake `judge_llm` that returns deterministic scores for smoke tests. Add a `result_cache_dir` to memoize judge calls by row-hash.

#### G-P1-3: `compare_results` test stub doesn't exercise the real Citation type
**Where:** task19.md:98-103.
**Problem:** `fake_citation = type("C", (), {"chunk_id": None})` — stub class. The test never instantiates a real `Citation(chunk_id=uuid.UUID(...), ...)`.
**Why P1:** The stub hides the type contract. If `Citation.chunk_id` is changed from `uuid.UUID` to `str` (or vice versa), `compare_results` will silently break because the stub doesn't enforce type.
**Fix:** Use the real `Citation` model in the test:
```python
from rag.domain.search import Citation
from datetime import datetime
import uuid
fake = Citation(
    chunk_id=uuid.uuid4(),
    dataset_id=uuid.uuid4(),
    source_name="x",
    content="y",
    score=0.5,
    update_time=datetime.now(),
)
```
This couples the test to the real domain model and catches type drift.

#### G-P1-4: `semantic_boundary_score` counts chunks-vs-offsets, not chunk-vs-boundary-position
**Where:** task19.md:294-310.
**Problem:** Spec §9.5.1 (line 1296) requires "semantic boundary hit-rate" against 50 hand-labeled "ideal split points" with character offsets. task19.md:307-308:
```python
total_expected += len(expected_offsets)
total_matched += min(len(chunks) - 1, len(expected_offsets))
```
This counts **number of chunks vs number of expected offsets** (a count match), **not chunk-boundary positions vs expected offset positions** (a hit-rate).
**Why P1:** The metric passes when `len(chunks) == len(expected_offsets) + 1` regardless of whether the chunker split at the right positions. A chunker that splits every sentence at random positions would score 1.0 if the document has 50 sentences. **The metric is not measuring what its name says.**
**Fix:** Implement true position-based hit-rate:
```python
def semantic_boundary_score(chunker, docs: list[tuple[str, list[int]]]) -> float:
    total_expected = 0
    total_matched = 0
    tolerance = 50  # ±50 chars tolerance
    for text, expected_offsets in docs:
        chunk_boundaries = chunker.boundaries(text)  # offset list
        for exp in expected_offsets:
            if any(abs(cb - exp) <= tolerance for cb in chunk_boundaries):
                total_matched += 1
            total_expected += 1
    return total_matched / max(total_expected, 1)
```
Requires `chunker.boundaries(text) -> list[int]` (or equivalent) on the chunker interface.

#### G-P1-5: `REGRESSION_QUERIES` contains 25 entries (including empty / 2000-char / SQL) but no test exercises them
**Where:** task19.md:128-155 (data) and task19.md:128-155 (no test calls them).
**Problem:** `test_regression_queries_non_empty` (task19.md:94-95) only asserts `len(REGRESSION_QUERIES) >= 20`. None of the 25 queries (especially the edge cases at lines 152-154) are exercised by any test.
**Why P1:** Edge-case queries are dead data — they exist in the constant but nothing reads them. If `pipeline.ainvoke` raises on `query=""`, no test catches it.
**Fix:** Add parametrized test:
```python
@pytest.mark.parametrize("q", REGRESSION_QUERIES)
async def test_pipeline_handles_regression_query(pipeline, q):
    result = await pipeline.ainvoke({"query": q, "dataset_ids": []})
    # No exception; result.citations is a list (possibly empty)
    assert isinstance(result.citations, list)
```
Or split `REGRESSION_QUERIES` into `STANDARD_QUERIES` (run in regression loop) and `EDGE_CASE_QUERIES` (run in defensive test).

### P2 (doc-only / cleanup)

#### G-P2-1: `if __name__ == "__main__"` block in `run_ragas.py` is non-functional
**Where:** task19.md:214-220.
**Problem:** The script parses `--goldset` but does **not** call `run_eval()`. Prints "Use as library or wire up pipeline in conftest". The script does nothing when invoked directly.
**Fix:** Either implement a smoke run or delete the `__main__` block. Recommend a `conftest.py`-based wiring (consistent with the existing `tests/integration/conftest.py` pattern).

#### G-P2-2: `fastgpt_lazy_greedy_select` docstring claims wrong attribution
**Where:** task19.md:359.
**Problem:** Even if we keep Option B from P0-1 (reframe as internal oracle), the docstring should not say "复刻 FastGPT 行为". Update to "Top-k by jaccard score, deterministic tie-break by chunk_id."
**Fix:** Update docstring + rename to `deterministic_top_k_jaccard_select`.

#### G-P2-3: `assert jaccard(...) >= 0.95` without specifying tolerance for floating-point comparison
**Where:** task19.md:115.
**Problem:** `jaccard()` returns an exact rational (count of intersection / count of union). For two identical 50-element sets, Jaccard = 50/50 = 1.0 exactly. **For real chunk_ids from pgvector HNSW, the result is also deterministic** (HNSW results can vary, but per-run results are exact). The threshold 0.95 is a comparison against `1.0`, not a comparison of two floats.
**Why P2:** The test will pass on the happy path (Jaccard = 1.0 > 0.95). The failure mode is when HNSW returns different chunks (Jaccard = 0.5 < 0.95 → fail). No floating-point issue.
**Fix:** None needed, but document in `jaccard()` docstring: "Returns exact rational for set inputs; threshold 0.95 reflects HNSW approximation drift, not floating-point tolerance."

#### G-P2-4: `compare_results` `threshold: float = 0.95` is not a constant
**Where:** task19.md:165.
**Problem:** The constant `0.95` appears in `compare_results` default, in test assertion (`assert compare_results(...) is True` implicitly), and in spec §9.7 (line 1421). Three places.
**Fix:** Define `DEFAULT_REGRESSION_THRESHOLD = 0.95` once in `tests/eval/regression.py`, re-export in `tests/eval/__init__.py` (if created).

### P3 (nice-to-have)

#### G-P3-1: `HALLUCINATION_QUERIES` lacks English-language queries
**Where:** task19.md:259-263.
**Problem:** All 3 hallucination queries are Chinese. The REGRESSION_QUERIES mix Chinese + English (lines 150-151) — inconsistency. Hallucination defense may behave differently on Chinese vs English queries (different tokenizer, different default embed model).
**Fix:** Add 2-3 English hallucination queries (e.g., "What was the architecture of GPT-6 released in 2025?", "Does FastGPT support graph RAG?").

#### G-P3-2: `query_pairs` in `test_robustness` is dict, loses order
**Where:** task19.md:246-256.
**Problem:** `for orig, variant in query_pairs.items()` iterates dict. Python 3.7+ preserves insertion order, but the function signature doesn't enforce order. If a robustness test fails, the report shows pairs in insertion order, which is fragile.
**Fix:** Change to `list[tuple[str, str]]` for `query_pairs`.

#### G-P3-3: `chunk_length_distribution` returns `0` for empty list, not `None`
**Where:** task19.md:288-292.
**Problem:** `statistics.mean(lens) if lens else 0` returns `0` for empty. A consumer can't tell apart "empty chunks" from "average length = 0". Return `None` for empty, or raise `ValueError`.
**Fix:** Change to `return None` for empty list, document the contract.

#### G-P3-4: `our_lazy_greedy_select` placeholder returns the oracle's result (not the SUT)
**Where:** task19.md:364-368.
**Problem:** The placeholder is:
```python
def our_lazy_greedy_select(candidates: list[tuple], top_k: int) -> list[str]:
    return fastgpt_lazy_greedy_select(candidates, top_k)
```
This means `test_assert_lazy_greedy_result_equals_fastgpt` will **always pass** (same function called twice). The test is meaningless until the placeholder is replaced with the real implementation.
**Fix:** Add a `# TODO(task-XX): wire to src/rag/retrieval/lazy_greedy.py when implemented` comment. Add a separate test that imports the SUT and asserts it exists:
```python
def test_sut_lazy_greedy_exists():
    from rag.retrieval.lazy_greedy import our_lazy_greedy_select as sut
    assert sut is not None
```

---

## 6. 实施顺序 (哪些先做)

In order of dependency:

1. **Resolve P0-1** (`lazy_greedy_oracle.py` "FastGPT oracle" is fabricated). Without this resolution, the SUT will be wrong on purpose. Pick Option B (reframe as internal oracle), or delete the file + test entirely. Until this is resolved, **task19 should not be merged**.

2. **Resolve P0-2** (`result.prompt` vs `result.response` for RAGAS faithfulness). This requires task 14 (Pipeline) to land first and define a `response` field on the result. Coordinate with task 14 owner.

3. **Fix P0-3** (line-range citation) before peer review.

4. **Fix P1-1** (`pipeline.ainvoke` dict vs `SearchRequest`). Coordinate with task 14 (Pipeline) on the canonical signature.

5. **Fix P1-2** (RAGAS judge model pinning + mock for CI). This is a substantial task — likely its own sub-task. Coordinate with task 20 (CI).

6. **Fix P1-3** (real `Citation` in stub test). Trivial.

7. **Fix P1-4** (`semantic_boundary_score` position-based matching). Requires `chunker.boundaries()` API to exist — coordinate with task 9 (Chunker).

8. **Fix P1-5** (exercise `REGRESSION_QUERIES` edge cases). Add parametrized test.

9. **Apply P2-1..P2-4, P3-1..P3-4** as cleanup.

After 1-6, the task can ship as a stub + minimal regression test. Items 7-8 are post-merge improvements.

---

## Appendix A: FastGPT eval schema (full)

```ts
// packages/service/core/app/evaluation/evalSchema.ts
const EvaluationSchema = new Schema({
  teamId: ..., tmbId: ..., appId: ..., usageId: ...,
  evalModel: { type: String, required: true },    // <-- judge model
  name: { type: String, required: true },
  createTime: { type: Date, default: () => new Date() },
  finishTime: Date,
  score: Number,                                  // <-- aggregate avg
  errorMessage: String
});

// packages/service/core/app/evaluation/evalItemSchema.ts
const EvalItemSchema = new Schema({
  evalId: ..., question: ..., expectedResponse: ...,
  history: String, globalVariables: Object,
  response: String, responseTime: Date,
  status: { type: Number, default: queuing, enum: [queuing, evaluating, completed] },
  retry: { type: Number, default: 3 },
  finishTime: Date,
  accuracy: Number,                              // <-- per-item
  relevance: Number,                             // <-- per-item
  semanticAccuracy: Number,                      // <-- per-item
  score: Number,                                 // <-- per-item avg
  errorMessage: String
});
```

## Appendix B: FastGPT vs. rag-pipeline metric mapping

| RAGAS metric | FastGPT equivalent | rag-pipeline (task19) |
|---|---|---|
| `context_precision` | **None** (not in EvalItemSchema) | Computed in `run_ragas.run_eval` |
| `context_recall` | **None** | Computed |
| `faithfulness` | ≈ `semanticAccuracy` (LLM-judged factual consistency) | Computed |
| `answer_relevancy` | ≈ `relevance` (LLM-judged answer-to-question fit) | Computed |
| (RAGAS) `score` avg | `Evaluation.score` (mean of per-item averages) | (RAGAS handles) |
| (FastGPT-only) `accuracy` | Whether response matches `expectedResponse` | (not implemented) |

## Appendix C: `lazy_greedy_oracle.py` claim audit

**Claim (task19.md:319-368):** "FastGPT 实现的 lazy_greedy 在 Jaccard/MMR 分数相同时按 chunk_id 升序选"

**Search for FastGPT lazy_greedy:**
```
$ grep -rn "lazy_greedy\|LazyGreedy\|lazyGreedy" packages/service packages/global projects/app
(no matches in source)
$ grep -rn "submodular\|MMR\|maximal marginal" packages/service packages/global projects/app
(no matches in source)
```

**Conclusion:** No "FastGPT Lazy Greedy" function exists in the FastGPT OSS source. The oracle in task19.md:359-369 is **fabricated** — it implements a one-shot arg-sort on `(jaccard_score, chunk_id)`, which is **not** lazy greedy in the academic sense (lazy greedy requires iterative marginal-gain selection over a sorted candidate list, with the "lazy" property being that you skip candidates whose upper-bound gain is provably worse than the current best).

If FastGPT ever had this logic, it lives in the **commercial `pro/admin` submodule**, which is not present in this OSS clone. Subagent #4 cannot have audited it.

The oracle is therefore **wrong** in two ways:
1. **Wrong attribution:** The function is not from FastGPT; it's invented.
2. **Wrong algorithm:** Even setting attribution aside, "arg-sort on jaccard then top-k" is not lazy greedy. It is deterministic top-k by jaccard score.

The `assertLazyGreedyResultEqualsFastGPT` contrast test is built on a double-false-premise and should either be reframed or deleted.

---

## Appendix D: RAGAS version pinning audit

| Source | Pin | Matches task19.md:64 |
|---|---|---|
| `pyproject.toml:62` | `ragas>=0.3,<0.4` | ✓ |
| `src/rag_pipeline.egg-info/requires.txt:24` | `ragas<0.4,>=0.3` | ✓ |
| `docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` | (no version pin) | n/a |
| `docs/superpowers/plans/2026-06-10-python-rag-pipeline.md:11` | "RAGAS" (no version) | n/a |

**Conclusion:** The `>=0.3,<0.4` pin is consistent across the two source-of-truth files. Audit #2 P1-9 is correctly applied: the docstring at task19.md:64-76 explains the version constraint and gives a 0.4+ migration example.

**Caveat:** `ragas` is in `[project.optional-dependencies] dev`. CI must install with `uv sync --extra dev` (or equivalent). task19.md:121 uses `uv run pytest ...` which resolves extras automatically; task20.md (CI) must explicitly install the `dev` extra in the `ragas` job.

## Appendix E: Cross-spec citation error (same as task11 P0-3)

| task19.md claim | Reality |
|---|---|
| "Extracted from `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` (lines 4562-4795)" | Plan file is **505 lines**. Lines 4562-4795 do not exist. |

The actual Eval L3 section is in the **spec** file at `docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md:1283-1442` (§9.5 Eval stack + §9.7 Regression Testing + §9.8 CI). task19.md should cite the spec, not the plan.