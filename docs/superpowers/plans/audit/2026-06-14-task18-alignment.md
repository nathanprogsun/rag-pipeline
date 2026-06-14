# Task 18 Alignment — Eval L2 (Gold Set + Synthetic + Retrieval Metrics)

> Audit date: 2026-06-14
> Auditor: 3-way alignment (task18.md ↔ rag-pipeline source ↔ FastGPT canonical)
> Scope: `task18.md` claims about `tests/eval/{retrieval_metrics,synthetic,goldset}.jsonl` + `EvalRunner` orchestrator vs. what FastGPT actually does for end-to-end evaluation vs. what currently exists in rag-pipeline.

## TL;DR

| Dimension | Finding |
|---|---|
| Path `tests/eval/` | **Does not exist.** No `tests/eval/` directory, no `retrieval_metrics.py`, no `synthetic.py`, no `goldset.jsonl`, no `run_ragas.py`. The plan tree (`2026-06-10-python-rag-pipeline.md:155-160`) lists 5 files; none exist. Task 18 is **未实现 (not yet implemented)**, not refactored — even though main plan lists it as "OK" (`2026-06-10-python-rag-pipeline.md:209`). |
| FastGPT's eval is L3, not L2 | FastGPT's `packages/global/core/app/evaluation/` is **end-to-end LLM-as-judge** (per-item `accuracy` / `relevance` / `semanticAccuracy` / `score` from a `evalModel` judge LLM, see `evalItemSchema.ts:43-46`). It does **not** compute `recall@k` / `mrr` / `ndcg`. The "gold set + synthetic + retrieval metrics" L2 layer has no direct FastGPT analogue. task18.md is building a layer FastGPT does not have. |
| Gold set format divergence (severe) | FastGPT uses **CSV** with a generated header `*q,*a,history` (or `var1,var2,...,*q,*a,history` when app has variables) — see `packages/global/core/app/evaluation/utils.ts:3-10`. Schema is `(q, a, history, [var...])` per row. task18.md uses **jsonl** with `(id, query, relevant_chunk_ids, irrelevant_chunk_ids, ground_truth_answer, tags, difficulty, created_at, annotated_by, expected_entities)`. **No chunk-level ground truth exists in FastGPT's eval** — it has only `expectedResponse` (the answer string), not `relevant_chunk_ids`. |
| Synthetic query generation | FastGPT has **no synthetic query generator**. task18.md is original. Spec section 9.5.2 (`2026-06-10-python-rag-pipeline-design.md:1311-1329`) shows the same LLM-driven approach: `for chunk in random.sample(chunks, n): question = await llm.ainvoke(...)`. |
| Metrics list | task18.md: `recall@k`, `precision@k`, `mrr`, `ndcg@k`, `hit_rate` (5) + chunk/entity-level recall (2) = **7 metrics**. FastGPT: 1 composite `score` per item (mean of 3 judge scores). **No per-k precision/recall in FastGPT.** Spec table at `2026-06-10-python-rag-pipeline-design.md:1346-1354` matches task18.md's 5 metrics exactly. |
| CI integration | Spec at `2026-06-10-python-rag-pipeline-design.md:1388-1397` prescribes a 6-stage Eval timing matrix: pre-commit / on-PR / on-merge / nightly / weekly / pre-release. task18.md **does not mention CI integration at all** — it only commits the module. Spec §9.8 (`1432-1442`) shows the GitHub Actions yaml. rag-pipeline repo: no `.github/workflows/ci.yml` (need to verify). |
| Statistical significance | **Completely absent.** Neither task18.md nor the spec mentions confidence intervals, paired t-tests, Wilcoxon, bootstrap, or any significance test. The spec's "Recall 退化 >2% block" threshold (line 1393) is a fixed-percentage rule with no statistical justification. For a 50-100 query gold set, ±5% recall variance from HNSW tie-breaker is normal; a 2% block is noise. |
| Hard-negative construction | task18.md:316-321 `goldset.jsonl` has `relevant_chunk_ids: []` and `expected_entities: [...]` only — no `irrelevant_chunk_ids` filled. Spec §9.5.2: hard negatives come from `random.sample` of other chunks. **task18.md's stub leaves irrelevant_chunk_ids empty** — neither real hard negatives nor auto-generation. |
| Test coverage of stub | task18.md:65-134 has 10 unit tests, all bound to stub-returned `0.0` so the first RED pass is mechanical. No tests for `EvalRunner.run` aggregate behavior, no fixture goldset of realistic size, no property-based tests. |

**Headline P0**: task18.md is an **independent new module** (no FastGPT analogue for L2 retrieval metrics), and the spec's `recall@k` / `mrr` / `ndcg` requirements are a rag-pipeline-specific design, not a port. This is fine in principle, but three P0 gaps make the current task18.md unsignoffable as written:

1. **Line-range citation is wrong** (task18.md:3 → `2026-06-10-python-rag-pipeline.md:4384-4558` does not exist; the plan is 505 lines). The actual eval section in the spec is at `2026-06-10-python-rag-pipeline-design.md:1307-1397` (L2/L3 sections) and the plan tree listing at `2026-06-10-python-rag-pipeline.md:155-160, 209`.
2. **`goldset.jsonl` stub leaves `relevant_chunk_ids: []`** — there is no way to verify chunk-level recall without a real gold set. The "50-100 条" target from the spec is unachievable from the stub.
3. **No CI / regression / statistical test plan** — the spec's 6-stage timing matrix (§9.6) and the "Recall 退化 >2% block" gate (§9.6 on-PR row) are not reflected in task18.md at all. Without these, the deliverable is a library, not an integrated eval loop.

---

## 1. FastGPT 实现 (with file:line citations)

### 1.1 What "evaluation" means in FastGPT

`grep -rln "retrieval.*metric\|recall@k\|precision@k\|ndcg" --include="*.ts" packages/` → **zero matches**. The "evaluation" module in FastGPT is **end-to-end LLM-as-judge**, not retrieval-quality metrics.

### 1.2 Canonical evaluation module layout

| File | Role |
|---|---|
| `packages/global/core/app/evaluation/type.ts` | TS types: `EvaluationSchemaType`, `EvalItemSchemaType`, `evaluationType`, `listEvalItemsItem`. Per-item fields include `question`, `expectedResponse`, `response`, `responseTime`, `accuracy`, `relevance`, `semanticAccuracy`, `score` (3 judge scores + average). |
| `packages/global/core/app/evaluation/constants.ts` | `EvaluationStatusEnum` (`queuing/evaluating/completed` = 0/1/2), `EvaluationStatusMap`, `evaluationFileErrors` (i18n error message). |
| `packages/global/core/app/evaluation/api.ts` | Pagination/list/retry/update request body types only. |
| `packages/global/core/app/evaluation/utils.ts` | `getEvaluationFileHeader(appVariables?)` — returns the CSV header string `*q,*a,history` (no app variables) or `var1,var2,...,*q,*a,history` (with variables). |
| `packages/service/core/app/evaluation/evalSchema.ts` | Mongoose `Evaluation` model: `teamId`, `tmbId`, `appId`, `usageId`, `evalModel`, `name`, `createTime`, `finishTime?`, `score?`, `errorMessage?`. (One row per evaluation **run**, not per item.) |
| `packages/service/core/app/evaluation/evalItemSchema.ts` | Mongoose `eval_items` model: `evalId`, `question`, `expectedResponse`, `globalVariables?`, `history?`, `response?`, `responseTime?`, `finishTime?`, `status`, `retry`, `errorMessage?`, `accuracy?`, `relevance?`, `semanticAccuracy?`, `score?` (average). |
| `packages/service/core/app/evaluation/mq.ts` | BullMQ `evaluationQueue` with `attempts: 3, backoff: exponential, delay: 1000`. Worker `getEvaluationWorker(processor)` with `concurrency: serviceEnv.EVAL_CONCURRENCY` and `removeOnFail.count: 1000`. `addEvaluationJob({evalId})` is dedup-by-id. |
| `packages/service/core/app/evaluation/utils.ts` | `parseEvaluationCSV(rawText)` using `Papa.parse({skipEmptyLines, transformHeader: trim})`. `validateEvaluationFile(rawText, appVariables)` checks header against `getEvaluationFileHeader(...)`, checks `dataLength > 1`, max 1000 rows, per-row required fields (prefixed with `*`), per-row variable validation (length / number range / select enums). |
| `packages/service/support/permission/evaluation/auth.ts` | Permission check for evaluation operations. |
| `projects/app/src/web/core/app/api/evaluation.ts` | Client SDK: `postCreateEvaluation(file, name, evalModel, appId, percentListen)`, `getEvaluationList`, `deleteEvaluation`, `getEvalItemsList`, `deleteEvalItem`, `retryEvalItem`, `updateEvalItem`. |
| `projects/app/src/pages/dashboard/evaluation/index.tsx` | List page: paginated eval list, polling 10s while running, click to open detail modal. |
| `projects/app/src/pages/dashboard/evaluation/create.tsx` | Create form: name, evalModel (LLM selector), appId (app selector), file upload (CSV). Calls `postCreateEvaluation` to `/proApi/core/app/evaluation/create`. |
| `projects/app/src/pageComponents/app/evaluation/DetailModal.tsx` | Per-item view: list (left, 2/3) of `evalItems` paginated with status & score; detail (right, 1/3) with question, standard_response, app_response, errorMessage. Export to CSV. |

### 1.3 What the **3 judge scores** are

Per-item fields (`evalItemSchema.ts:43-46`):
```ts
accuracy: Number,           // how accurate is the response vs expectedResponse
relevance: Number,          // how relevant to the question
semanticAccuracy: Number,   // semantic equivalence
score: Number,              // average of the 3
```

The actual scoring LLM is selected by `evalModel` (free-form string, defaults to first LLM in list, `create.tsx:49`). The judge prompt is not in this repo's search results (likely in a controller that consumes the BullMQ processor — possibly in `pro/` submodule, which is private). **What is observable:** the schema is a *judge output*, not a *retrieval metric*. There is no concept of "did we retrieve the right chunk".

### 1.4 What the gold set looks like (FastGPT)

**CSV format**, generated by `getEvaluationFileHeader` (`packages/global/core/app/evaluation/utils.ts:3-10`):
```
*q,*a,history
"什么是 RAG?","RRF 是 Reciprocal Rank Fusion...",""
```

- `*` prefix = required field (per `utils.ts:61-72` `requiredFields.filter(({header}) => header.startsWith('*'))`).
- Order: `[variables..., *q, *a, history]` (variables are taken from `app.chatConfig.variables`; required variables are `*`-prefixed).
- Up to **1000 rows** per file (`utils.ts:47-50`).
- Parsed with `Papa.parse` (no `header: true`, header is at index 0 and stripped by code at index 1+).
- No `relevant_chunk_ids` field. **No chunk-level ground truth.**

### 1.5 What the per-item storage looks like

```ts
{
  evalId: ObjectId,          // parent run
  question: String,          // required
  expectedResponse: String,  // required (the gold answer)
  history: String,
  globalVariables: Object,
  response: String,          // filled by judge LLM
  responseTime: Date,
  status: Number,            // 0/1/2
  retry: Number,             // default 3
  finishTime: Date,
  accuracy: Number,          // filled by judge
  relevance: Number,         // filled by judge
  semanticAccuracy: Number,  // filled by judge
  score: Number,             // average of above 3
  errorMessage: String,
}
```

**No `chunk_id` anywhere.** Retrieval happens inside the chat pipeline (`packages/service/core/dataset/search/...`) and is not observed by the eval module. Eval is purely a black-box judge of `response` vs `expectedResponse`.

### 1.6 What the BullMQ processor does

`packages/service/core/app/evaluation/mq.ts` only defines the queue/worker **wiring** (`getQueue`, `getWorker`, `addEvaluationJob`, `checkEvaluationJobActive`, `removeEvaluationJob`). The actual `processor` function is passed in by the consumer (likely `pro/admin` in the private submodule, not visible). The `removeOnFail.count: 1000` + `attempts: 3` + `exponential backoff` are the only observable config.

### 1.7 What the UI flows are

| File | Flow |
|---|---|
| `create.tsx` | Form → `postCreateEvaluation` POST to `/proApi/core/app/evaluation/create` (FormData: `file` + JSON `data: {name, evalModel, appId}`). Polls upload progress. On success → toast + redirect to list. On `evaluationFileErrors` or `aiPointsNotEnough` → inline error. |
| `index.tsx` | List page. `usePagination(getEvaluationList, {pollingInterval: 10000, pollingWhenHidden: true})`. When all runs are complete + no errors, polling stops. Click row → opens `EvaluationDetailModal`. |
| `DetailModal.tsx` | Per-item drill-down. Left list: `evalItems` with status (queuing/evaluating/completed/error) and `score * 100`. Right detail: question / standard_response / app_response / errorMessage. Edit item (when status is queuing or error). Export to CSV via `downloadFetch('/api/proApi/core/app/evaluation/exportItems?evalId=...')`. Retry item (when error). Delete item. |

### 1.8 What is **not** in FastGPT

- No `recall@k`, `precision@k`, `ndcg`, `mrr`, `hit_rate` calculation.
- No `relevant_chunk_ids` field anywhere.
- No synthetic query generator.
- No entity-level metrics.
- No "Jaccard ≥ 0.95" regression test.
- No `goldset.jsonl`.
- No `tests/eval/` style directory at all.
- No statistical significance testing of any kind.

**Conclusion on FastGPT's eval:** it is a *manual-annotation* loop where humans write `q`/`a` CSV pairs, an LLM-as-judge scores the app's response against the expected answer, results are browsed in a dashboard, and CSV is exported. **No retrieval-quality measurement.** This is task19 territory (RAGAS), not task18 (L2).

---

## 2. rag-pipeline 当前状态

### 2.1 Path check

```
$ ls /Users/jung/pro/rag-pipeline/tests/eval/
ls: cannot access ... : No such file or directory

$ find /Users/jung/pro/rag-pipeline -type d -name "eval" -not -path "*/.venv/*" -not -path "*/.mypy_cache/*" -not -path "*/.pytest_cache/*"
(no results)

$ find /Users/jung/pro/rag-pipeline -type f -name "retrieval_metrics*"
(no results)

$ find /Users/jung/pro/rag-pipeline -type f -name "goldset*"
(no results)

$ find /Users/jung/pro/rag-pipeline -type f -name "synthetic.py"
(no results)
```

**`tests/eval/` does not exist.** The plan tree (`2026-06-10-python-rag-pipeline.md:155-160`) lists 5 files under `tests/eval/`:
- `goldset.jsonl` — missing
- `synthetic.py` — missing
- `retrieval_metrics.py` — missing
- `regression.py` — missing (per spec §9.7, this is task 20 territory but lives in same dir)
- `run_ragas.py` — missing (task 19)

None exist. The actual `tests/` layout:
```
tests/
├── AGENTS.md
├── __init__.py
├── conftest.py
├── data/         (sample fixtures for readers/chunkers)
├── integration/  (8 test files: test_llm_live, test_pg_connection, test_chunk_repo, test_ingest_e2e, test_vector_retrieval, test_fulltext_retrieval, test_cache)
└── unit/         (multiple test files for: domain, rag_error, cache_*, rerank, reader/, chunker/, ingest/, llm_config)
```

**Task 18 is a spec-only document at this point.** Main plan marks it "OK" (`2026-06-10-python-rag-pipeline.md:209`) but the directory and all 5 files are absent.

### 2.2 No eval code anywhere in rag-pipeline

```
$ grep -rln "EvalQuery\|EvalRunner\|retrieval_metrics\|goldset\|hit_rate" /Users/jung/pro/rag-pipeline --include="*.py" | grep -v venv
(no results)

$ grep -rln "ndcg\|mrr\|recall_at_k\|precision_at_k" /Users/jung/pro/rag-pipeline --include="*.py" | grep -v venv
(no results)
```

The only "metric" code in rag-pipeline is:
- `tests/unit/chunker/test_chunker_quality_regression.py` — chunker quality metrics (`chunk_count`, `avg_valid_len`, `heading_stack_coverage`, `bad_boundary_rate`) — unrelated to retrieval.
- `tests/unit/test_cache_metrics.py` — L1/L2 cache hit/miss counters — unrelated.

No retrieval metrics code, no EvalQuery schema, no EvalRunner, no gold set, no synthetic query generator.

### 2.3 Plan's "OK" status is misleading

`2026-06-10-python-rag-pipeline.md:209`:
```
| 18 | Eval L2 — Gold Set + Synthetic + Retrieval Metrics | [task18.md](./tasks/task18.md) | OK |
```

Per the audit #1 (parallel audits #136), "OK" is a status used loosely — the plan marks a task as OK if its spec+task file are written, regardless of code. The actual implementation is missing. This is consistent with task11.md's status (also "OK", no code) and task13/15/16/17/19/20 (also "OK" or "in_progress", mostly missing).

### 2.4 Spec sections that exist (but are referenced from the wrong place)

| spec section | content | lines |
|---|---|---|
| §9.5.2 | L2 检索级评测 — gold set format, synthetic gen, metrics table (5 metrics: Recall@K, Precision@K, MRR, NDCG@K, Hit Rate) | 1307-1359 |
| §9.5.3 | L3 生成级评测 — RAGAS 四指标 (faithfulness, answer_relevancy, context_precision, context_recall) + extension (citation_recall/precision, hallucination_rate, SelfCheckGPT) | 1361-1386 |
| §9.6 | Eval 时机矩阵 — 6 stages: pre-commit / on-PR / on-merge / nightly / weekly / pre-release | 1388-1397 |
| §9.7 | Regression Testing — Jaccard ≥ 0.95 on fixed 20-30 query set, addresses HNSW tie-breaker non-determinism | 1399-1430 |
| §9.8 | CI yaml — `pytest tests/unit --cov=src/rag --cov-fail-under=80`, `pytest tests/integration`, `pytest tests/eval/regression.py`, ruff + mypy | 1432-1442 |
| §16 | Gold Set 标注与维护 — file format, version management, annotation process | 1609-1652 |
| §6.5.5 | Embedding A/B 对比 — `ab_embedding_compare(old_model, new_model, goldset)` | 730-747 |

These are well-specified. task18.md, by contrast, only implements the metric *functions* and the EvalRunner orchestrator — not the gold set maintenance workflow (§16), not the regression test (§9.7), not the A/B test (§6.5.5), and not the CI yaml (§9.8). **task18.md is ~25% of the spec's eval story.**

---

## 3. task18.md 关键声明清单

| # | Claim (file:line) | Concrete content |
|---|---|---|
| C-1 | task18.md:11-16 | Create: `tests/eval/__init__.py`, `tests/eval/goldset.jsonl`, `tests/eval/retrieval_metrics.py`, `tests/eval/synthetic.py`, `tests/integration/test_eval_l2.py` |
| C-2 | task18.md:21-23 | Step 0 stub for `__init__.py` |
| C-3 | task18.md:25-41 | Step 0 stub for `retrieval_metrics.py` — 5 basic metrics (`recall_at_k`, `precision_at_k`, `mrr`, `ndcg_at_k`, `hit_rate`) + 2 subagent #5 (`chunk_level_recall`, `entity_level_recall`), all return `0.0` |
| C-4 | task18.md:44-56 | Step 0 stub for `synthetic.py` — `SyntheticQuestion` Pydantic schema (subagent #4 fix from `list[dict]`) with `expected_entities` field; `gen_synthetic_queries(chunks, llm, n=50)` returns `[]` |
| C-5 | task18.md:58-61 | Step 0 stub for `goldset.jsonl` — 1 row with empty `relevant_chunk_ids` and `expected_entities: []` |
| C-6 | task18.md:64-134 | Step 1 test file: 10 tests (5 basic metric correctness + 2 chunk/entity + 1 entity-empty + 2 synthetic pydantic) |
| C-7 | task18.md:144-243 | Step 3 implementation: `retrieval_metrics.py` with full metric functions + `EvalRunner(pipeline, goldset_path)` class with `async run() -> dict` returning per-query and aggregate metrics |
| C-8 | task18.md:247-315 | Step 4 implementation: `synthetic.py` with `SyntheticQuestion` Pydantic schema (4 fields) + `gen_synthetic_queries` that LLM-asks for question + 3-5 entities per chunk, returns `list[SyntheticQuestion]` |
| C-9 | task18.md:319-322 | Step 5: 2-row `goldset.jsonl` (g-001, g-002) with `relevant_chunk_ids: []` and `expected_entities` filled |
| C-10 | task18.md:326-329 | Step 6: `uv run pytest tests/integration/test_eval_l2.py -v` — expect 10 passed |
| C-11 | task18.md:333-336 | Step 7: `git commit -m "feat(eval): L2 retrieval metrics + chunk/entity-level recall + SyntheticQuestion schema"` |
| C-12 | task18.md:3 | **Wrong line range:** "Extracted from `2026-06-10-python-rag-pipeline.md` (lines 4384-4558)" — file is only 505 lines. Actual eval sections: spec file at `2026-06-10-python-rag-pipeline-design.md:1307-1397` and plan tree at `2026-06-10-python-rag-pipeline.md:155-160, 209`. |
| C-13 | task18.md:201-202 | `EvalRunner.__init__` uses `self.pipeline.ainvoke({"query": ..., "dataset_ids": []})` — assumes pipeline has a `dataset_ids` parameter (empty list = search all). This is task 14/16's `build_full_pipeline` contract; task18 depends on it but doesn't validate. |
| C-14 | task18.md:213-214 | `result.citations` field — assumes pipeline returns a `citations` attribute. The spec has `ScoredDocument` (task 2) and `Citation` in cite step (task 15); the EvalRunner must reach inside the pipeline's return shape, but task18.md doesn't define what shape `result` has. |
| C-15 | task18.md:219 | `k = r.get("top_k", 10)` — k comes from the gold set row, not from a config. No global default. |
| C-16 | task18.md:341-353 | RAGAS handoff section claims Task 19 will read `goldset.jsonl.expected_entities` and `irrelevant_chunk_ids` for `context_entities_recall` and `noise_sensitivity`. task19.md has not been audited; handoff is by convention only. |

---

## 4. 三向差异矩阵

| Aspect | task18.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **Path / module location** | `tests/eval/{retrieval_metrics,synthetic}.py` + `tests/eval/goldset.jsonl` + `tests/integration/test_eval_l2.py` | **Path does not exist.** `tests/eval/` not present. | `packages/{global,service}/core/app/evaluation/` (L3 end-to-end judge, not L2) |
| **Gold set format** | **jsonl**, schema: `id, query, relevant_chunk_ids[], irrelevant_chunk_ids[], ground_truth_answer, expected_entities[], tags[], difficulty, created_at, annotated_by` | (no file) | **CSV**, schema: `[*var1, ...], *q, *a, history`. No `relevant_chunk_ids`. |
| **Per-row size limit** | none specified; spec says 50-100 (design.md:1643) | n/a | 1000 rows max (`utils.ts:47-50`) |
| **Chunk-level ground truth** | `relevant_chunk_ids: list[str]` of chunk UUIDs | n/a | **No equivalent.** `expectedResponse` is a free-text gold answer, not a chunk list. |
| **Hard negative storage** | `irrelevant_chunk_ids: list[str]` of chunk UUIDs in the gold set itself | n/a | **No equivalent.** No hard negative concept in eval. |
| **Entity-level ground truth** | `expected_entities: list[str]` (subagent #4 addition) | n/a | **No equivalent.** |
| **Synthetic query generation** | LLM-driven, `for chunk in random.sample(chunks, n): question = await llm.ainvoke(prompt with chunk.text)` + parse JSON `{question, entities}` | n/a | **No synthetic query generator.** |
| **Hard negative auto-gen** | In spec §9.5.2 only (not in task18.md): `random.sample([c.id for c in chunks if c.id != chunk.id], k=3)`. task18.md's goldset.jsonl has `irrelevant_chunk_ids: []` — no auto-gen, no manual. | n/a | n/a |
| **Metrics: Recall@K** | `len(hits[:k] & relevant) / len(relevant)` | (no code) | **Not present.** |
| **Metrics: Precision@K** | `len(hits[:k] & relevant) / k` | (no code) | **Not present.** |
| **Metrics: MRR** | `mean(1/rank_of_first_relevant)` | (no code) | **Not present.** |
| **Metrics: NDCG@K** | `DCG/IDCG` with graded relevance dict | (no code) | **Not present.** |
| **Metrics: Hit Rate** | `1 if any relevant else 0` | (no code) | **Not present.** |
| **Metrics: chunk_level_recall** | `len({hits} & {relevant_chunk_ids}) / len(relevant_chunk_ids)` | (no code) | **Not present.** |
| **Metrics: entity_level_recall** | substring match in concatenated hits[:k] text | (no code) | **Not present.** |
| **EvalRunner orchestrator** | `EvalRunner(pipeline, goldset_path)` with `async run() -> dict` returning per-query and aggregate metrics | (no code) | **No equivalent.** Eval is single-shot per app+CSV. |
| **Per-row k** | `k = r.get("top_k", 10)` from goldset row | n/a | No `k` — `topK` is from `app.chatConfig` (per-app, not per-query). |
| **Pipeline interface assumed** | `await self.pipeline.ainvoke({"query": ..., "dataset_ids": []})` returns object with `.citations: list[Citation]` (each has `.chunk_id, .content`) | n/a | Internal `datasetSearchResultConcat` (`packages/global/core/dataset/search/utils.ts:5-7`) returns `SearchDataResponseItemType[]` with `score: {type,value,index}[]` and `id: string` |
| **CI integration** | **Not mentioned.** Step 7 is `git commit`, no GitHub Actions yaml. | n/a | Eval is a separate Pro feature behind the `pro/` submodule. No public-facing CI yaml. |
| **Eval timing matrix (§9.6)** | **Not mentioned.** Spec defines 6 stages (pre-commit / on-PR / on-merge / nightly / weekly / pre-release) with thresholds. task18 only covers the *metric* layer, not the *timing*. | n/a | BullMQ async evaluation; "on-PR" / "nightly" / "weekly" timing not enforced. |
| **Regression test (§9.7)** | **Not mentioned.** Jaccard ≥ 0.95 on 20-30 fixed queries is a separate concern (task 20). | n/a | Not present. |
| **Statistical significance** | **Not mentioned.** No CIs, t-tests, Wilcoxon, bootstrap. | n/a | Not present. |
| **Threshold (Recall 退化 >2% block)** | **Not mentioned.** Spec says block but doesn't say how to detect "regression" — point estimate comparison only. | n/a | Not present. |
| **Embedding A/B test (§6.5.5)** | **Not mentioned.** `ab_embedding_compare(old_model, new_model, goldset)` is a stub in spec, not in task18. | n/a | Not present. |
| **RAGAS handoff (Task 19)** | Mentioned at task18.md:341-353 (table) — `context_entities_recall` and `noise_sensitivity` will reuse `entity_level_recall` and `irrelevant_chunk_ids`. | n/a (task19 also unimpl) | RAGAS not used by FastGPT. |
| **Stub-first discipline (audit #1 P1-1)** | Step 0 has stubs that return `0.0` so import succeeds. | (no module) | N/A (no eval layer in FastGPT) |

---

## 5. 修复建议 (P0 → P1 → P2 → P3, 每条带具体文件:行号)

### P0 (blocker before sign-off)

#### G-P0-1: Line-range citation is wrong
**Where:** task18.md:3 → `docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` lines 4384-4558.
**Problem:** The plan file is **505 lines total** (verified with `wc -l`). Lines 4384-4558 do not exist. The actual eval sections are:
- Spec §9.5.2 L2 检索级评测: `docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md:1307-1359`
- Spec §9.5.3 L3 生成级评测: same file `:1361-1386`
- Spec §9.6 Eval 时机矩阵: same file `:1388-1397`
- Spec §9.7 Regression Testing: same file `:1399-1430`
- Spec §9.8 CI yaml: same file `:1432-1442`
- Spec §16 Gold Set 标注与维护: same file `:1609-1652`
- Plan tree listing: `2026-06-10-python-rag-pipeline.md:155-160` (the `tests/eval/` directory tree) and `:209` (the OK status row).

**Why P0:** A task doc that points reviewers to non-existent file content is a sign-off blocker. Reviewer cannot reproduce the citation check.
**Fix:** Update task18.md:3 to read:
```
> Extracted from `/Users/jung/pro/rag-pipeline/docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` (§9.5.2 lines 1307-1359, §16 lines 1609-1652)
> and plan tree `2026-06-10-python-rag-pipeline.md:155-160`.
```

#### G-P0-2: `goldset.jsonl` stub has empty `relevant_chunk_ids` and no real hard negatives
**Where:** task18.md:58-61 (Step 0 stub) and task18.md:319-322 (Step 5 real data).
**Problem:** Both the stub and the "real" goldset have `"relevant_chunk_ids": []` and `"irrelevant_chunk_ids": []`. This means:
- The stub goldset (g-stub) has 0 relevant chunks → `recall_at_k` always returns 0.0 → metric functions can't be validated end-to-end.
- The "real" goldset (g-001, g-002) is identical to the stub structurally — only `query` and `expected_entities` are filled.
- There is no chunk-level ground truth to verify `chunk_level_recall`. The test `test_chunk_level_recall_matches_uuid_set` (line 100-104) uses inline literals, not the goldset.
- The "50-100 条" target from spec §16.1 line 1643 is unachievable from this stub.

**Why P0:** task18.md is supposed to deliver a gold set + metric + runner pipeline. Without real `relevant_chunk_ids` for at least 5-10 chunks, the deliverable is unverifiable.

**Fix options:**
- **A. Bootstrap from a fixture:** Before writing the goldset manually, run `tests/integration/test_ingest_e2e.py` (already exists) on `tests/data/sample.pdf` (already exists) to produce a known set of chunks. Use their UUIDs as `relevant_chunk_ids` for 2-3 hand-written queries. The goldset will be valid because the UUIDs are deterministic from the fixture.
- **B. Add a fixture-driven goldset generator:** `tests/eval/conftest.py` with `@pytest.fixture` that returns a goldset with realistic UUIDs from the test data, separate from the `goldset.jsonl` file.
- **C. Make the goldset a placeholder with a TODO:** Add `TODO(nathan): bootstrap from ingest_e2e fixture; see spec §16.1` and ensure the implementation can still be tested via the inline literals in the test file.

**Recommended:** A + C combined. Ship a `goldset.jsonl` with 2-3 real entries (fixture-derived UUIDs) and document the remaining 50-100 entry goal as a follow-up.

#### G-P0-3: `EvalRunner.run` pipeline interface is not specified
**Where:** task18.md:212-215 (`result = await self.pipeline.ainvoke({"query": r["query"], "dataset_ids": []})`).
**Problem:** The runner assumes:
- Pipeline has `.ainvoke({query, dataset_ids})` — defined by task 14/16's `build_full_pipeline`. Not yet implemented.
- Pipeline return value has `.citations: list[Citation]` — defined by task 15's cite step. Not yet implemented.
- Each `Citation` has `.chunk_id: UUID` and `.content: str` — defined by task 15. Not yet implemented.

`EvalRunner` therefore **cannot be tested** until tasks 14, 15, 16 are merged. The unit tests (Step 1) cover only the metric *functions* (which take a `hits` list directly), not the orchestrator. The orchestrator has no test.

**Why P0:** A 60-line orchestrator class with no test is dead code at delivery time. The first time it runs end-to-end, three unimplemented dependencies must align.

**Fix options:**
- **A. Make `EvalRunner` accept an injected `search_fn: Callable[[str], Awaitable[list[ScoredDocument]]]` instead of a full pipeline.** Then tests can mock the function. The "pipeline" is just `lambda q: search_fn(q) → list[ScoredDocument]` in the simplest case.
- **B. Add a smoke test that mocks the pipeline and asserts EvalRunner aggregates correctly.** Use `unittest.mock.AsyncMock` to simulate `ainvoke` returning a fake object with `.citations`.
- **C. Defer EvalRunner to a follow-up task** that explicitly depends on tasks 14/15/16. Mark this task as "metric functions only" and rename it "Eval L2 — Retrieval Metrics (functions only)".

**Recommended:** A + B. Inject a `search_fn` so the dependency surface is small (1 callable). Add a mocked smoke test. This is the minimum to make EvalRunner reviewable.

### P1 (significant API/type/scope mismatch)

#### G-P1-1: No CI / regression / statistical test plan
**Where:** Spec §9.6 (lines 1388-1397) and §9.7 (lines 1399-1430) and §9.8 (lines 1432-1442) — none reflected in task18.md.
**Problem:** The spec defines 6 eval stages (pre-commit / on-PR / on-merge / nightly / weekly / pre-release) with stage-specific thresholds ("Recall 退化 >2% block"). task18.md stops at "git commit" with no CI yaml, no regression test, no statistical significance test.
**Why P1:** A library without a runner is half the work. The spec's "Eval L2 + L3" deliverable (per §9.5 title) is the integrated pipeline, not just the functions.
**Fix:** Add to task18.md or to a follow-up task:
- A `.github/workflows/eval.yml` invoking `uv run pytest tests/integration/test_eval_l2.py --goldset=tests/eval/goldset.jsonl` on PR.
- A `tests/eval/regression.py` with the Jaccard ≥ 0.95 test from spec §9.7 (this is also task 20's concern; clarify ownership).
- A note in the docstring of `EvalRunner.run` that the current output is a *point estimate* and that the user must implement their own statistical test if they need significance.

#### G-P1-2: `goldset.jsonl` schema has no `version` / `corpus_hash` / `dataset_id`
**Where:** task18.md:320-321 (real goldset) and spec §16.2 lines 1635-1644.
**Problem:** The spec explicitly addresses the "chunks 重 ingest 时 UUID 改变 → 旧 goldset 失效" problem. task18.md's goldset has no version marker, no corpus hash, no `dataset_id`. When the corpus is re-ingested, the user has no programmatic way to know the goldset is stale.
**Why P1:** The spec calls out this exact failure mode. Omitting the version field is a step backward from spec.
**Fix:** Add a header to `goldset.jsonl` (jsonl doesn't natively support headers, so either prepend a sentinel line `# version: 1, corpus_hash: ..., dataset_id: ..., created_at: ...` or add a parallel `goldset.meta.json` sidecar). Validate in `validate_evaluation_file` (analogue to FastGPT's `validateEvaluationFile`) that the meta matches the current corpus.

#### G-P1-3: `entity_level_recall` uses naive substring match
**Where:** task18.md:186-197.
**Problem:** The implementation does `ent.lower() in corpus.lower()`. This is brittle:
- "AI" matches "available", "maintain", "training", "fail" (substring).
- "Rust" matches "frustrate", "trust".
- No word-boundary, no lemma, no stopword handling.
- Multi-word entities ("vector search") are matched as a literal substring — "vector" alone would also match.

**Why P1:** The test `test_entity_level_recall_substring_match` (line 106-112) only checks "Python" and "Rust" against short sentences, so it passes by accident. Real-world gold sets will hit the substring-matches-subword bug.
**Fix:** Use word-boundary regex: `re.search(rf"\b{re.escape(ent.lower())}\b", corpus.lower())`. Add a test for the "AI" / "available" disambiguation case. Document in docstring.

#### G-P1-4: `gen_synthetic_queries` silently swallows LLM failures
**Where:** task18.md:292-313.
**Problem:** The implementation has a bare `try: ... except Exception: continue`. This means:
- LLM JSON parse failures are dropped silently — the user gets fewer than `n` questions without any signal.
- Network errors, auth errors, rate limits — all dropped.
- No counter, no logging, no retry.

**Why P1:** Synthetic query generation is a long-running batch job. Silent data loss will make the goldset incomplete and the user will not know why.
**Fix:** Add a `gen_synthetic_queries_verbose` variant or a callback that returns `(questions, errors)`. Log a summary at the end: `logger.info("gen_synthetic_queries: %d/%d succeeded, %d JSON parse errors, %d LLM errors", ...)`. Add a `--strict` mode that raises on the first failure.

#### G-P1-5: `EvalRunner` has no test
**Where:** task18.md:201-243 — class definition only.
**Problem:** The orchestrator class has 60+ lines and no test. The 10 unit tests in `test_eval_l2.py` only cover the metric *functions* (which take `hits, relevant, k` directly). `EvalRunner` requires a pipeline + goldset file, neither of which exist.
**Why P1:** Without an `EvalRunner` test, the first end-to-end run will surface bugs in (a) JSONL parsing, (b) per-query aggregation, (c) per-metric averaging, (d) handling of missing `top_k`. Each of these is a single-pass code path with no safety net.
**Fix:** Add a test using a temporary goldset file and an `AsyncMock` pipeline:
```python
async def test_eval_runner_smoke(tmp_path):
    goldset = tmp_path / "g.jsonl"
    goldset.write_text('{"id":"g1","query":"q","relevant_chunk_ids":["c1"],"top_k":3}\n')
    pipeline = AsyncMock()
    pipeline.ainvoke.return_value = type("R", (), {"citations": [
        type("C", (), {"chunk_id": uuid.UUID("c1"), "content": "..."})()
    ]})()
    runner = EvalRunner(pipeline, str(goldset))
    report = await runner.run()
    assert report["n_queries"] == 1
    assert report["mean_recall@k"] == 1.0
```

### P2 (doc-only / cleanup)

#### G-P2-1: `expected_entities` is in 2 places without a clear contract
**Where:** task18.md:60 (stub) and `:320-321` (real goldset) — both list `expected_entities: []`. Subagent #5 docstring at line 188-191 says it's for "key entity" recall. RAGAS handoff at line 350-351 says it's for `context_entities_recall`.
**Problem:** The field is overloaded: it's both a metric input (for `entity_level_recall`) and a RAGAS input (for `context_entities_recall`). The gold set annotation flow doesn't know which one to optimize for (e.g., is "RRF" a key entity, or is "倒数排名融合" a key entity, or both?).
**Fix:** Add a comment in the goldset docstring: "expected_entities is a list of *lowercase* terms. Used by entity_level_recall (L2) and by RAGAS context_entities_recall (L3). The annotator should pick terms that are *both* (a) in the chunk text and (b) representative of the chunk's content." Or split into `entity_keywords` (for L2 recall) and `entity_reference` (for RAGAS).

#### G-P2-2: `r.get("top_k", 10)` silently uses default 10
**Where:** task18.md:219.
**Problem:** If a goldset row omits `top_k`, the metric uses 10. This is fine, but the spec doesn't say what the default should be. The pipeline's `RetrievalConfig.top_k` (task 2) defaults to 10 (`tests/unit/test_domain.py:53`), so 10 is consistent. But this is implicit.
**Fix:** Hoist `DEFAULT_TOP_K = 10` to a module-level constant and import from `src/rag/domain/search.py` if available. Add to docstring: "Per-query `top_k` overrides the default; default matches `RetrievalConfig.top_k`."

#### G-P2-3: Stub `goldset.jsonl` row has `annotated_by: "nathan"` and `created_at: "2026-06-10"` hardcoded
**Where:** task18.md:60 and `:320-321`.
**Problem:** These fields suggest the gold set is hand-annotated by nathan, but in reality it will be a mix of (a) hand-annotated, (b) auto-synthesized, (c) imported from prior runs. The schema doesn't distinguish.
**Fix:** Add a `source: "manual" | "synthetic" | "imported"` field. Default to `"manual"`. Update `gen_synthetic_queries` to write its output with `source: "synthetic"`.

#### G-P2-4: `SyntheticQuestion` schema lacks `dataset_id`
**Where:** task18.md:257-269.
**Problem:** A synthetic question is generated from a chunk (which has a `dataset_id`), but the resulting `SyntheticQuestion` only stores `relevant_chunk_id`. The dataset association is lost.
**Why P2 (not P1):** If the corpus has multiple datasets and the user re-runs synthetic gen on a different dataset, the questions can't be filtered back to the right dataset. Deferable.
**Fix:** Add `dataset_id: str | None = None` to `SyntheticQuestion`. Update `gen_synthetic_queries` to read `chunk.dataset_id` and populate.

#### G-P2-5: `chunk_level_recall` ignores the `k` parameter on empty hits
**Where:** task18.md:179-184.
**Problem:** The implementation uses `hits[:k]` and `set(hits[:k])`. If `hits` is shorter than `k`, it silently uses `hits[:len(hits)]`. That's correct, but the docstring says "以 chunk UUID 集合重合度计算" without clarifying the truncation. The test at line 100-104 uses `hits = ["c-1", "c-2", "c-3"]` and `k=3`, which doesn't exercise the "hits shorter than k" or "hits longer than k" cases.
**Fix:** Add a test: `assert chunk_level_recall(["c-1", "c-2", "c-3", "c-4"], {"c-1"}, k=3) == 1.0` (truncation), and `assert chunk_level_recall(["c-1", "c-2"], {"c-1", "c-3"}, k=5) == 0.5` (k larger than hits).

### P3 (nice-to-have)

#### G-P3-1: No `__all__` export in `tests/eval/__init__.py`
**Where:** task18.md:21-23.
**Problem:** The stub `__init__.py` is a single docstring. If downstream code does `from tests.eval import EvalRunner`, it will need to be re-exported.
**Fix:** Add `from .retrieval_metrics import (recall_at_k, precision_at_k, mrr, ndcg_at_k, hit_rate, chunk_level_recall, entity_level_recall, EvalRunner)` and `__all__ = [...]`.

#### G-P3-2: `recall_at_k` and `precision_at_k` use `set` intersection — duplicate chunk_ids in `hits` are silently deduped
**Where:** task18.md:152-159.
**Problem:** If a pipeline returns the same `chunk_id` twice (e.g., a reranker + vector both surface the same chunk), the set intersection undercounts. The metric is "did any rank contain this chunk?", not "how many ranks contain this chunk?".
**Why P3:** This is arguably correct behavior — a chunk is either relevant or not. But it diverges from MRR (which counts the *first* occurrence) and from NDCG@K (which would count both occurrences with discounted gain). Documenting the choice in a docstring is enough.
**Fix:** Add a docstring sentence: "Duplicate chunk_ids in hits are deduped via set; for position-sensitive variants, see MRR / NDCG@K."

#### G-P3-3: `mrr` and `hit_rate` ignore the `k` parameter
**Where:** task18.md:162-175.
**Problem:** `mrr` and `hit_rate` don't take `k`; they consider the full hits list. If the user passes a hits list of length 100 but wants MRR@10, they need to slice first. This is a UX issue, not a bug.
**Fix:** Add `k: int | None = None` to both. If `k` is given, use `hits[:k]`. Document the difference between `mrr` and `mrr@k`.

#### G-P3-4: `gen_synthetic_queries` uses `chunk.text[:500]` — magic number
**Where:** task18.md:290.
**Problem:** The 500-char truncation is a magic number. Larger chunks (e.g., from `test_ingest_e2e` with 1000-token chunks) lose half their content. Smaller chunks are over-padded.
**Fix:** Hoist `SYNTHETIC_PROMPT_CHUNK_CHARS = 500` to module-level constant. Or use a token-based cap (e.g., `chunk.text[:2000] if len(chunk.text) > 500 else chunk.text`).

#### G-P3-5: No `__repr__` on `EvalRunner`
**Where:** task18.md:201-243.
**Problem:** Debugging a runner is hard without `__repr__`. Standard.
**Fix:** Add `def __repr__(self): return f"EvalRunner(pipeline={self.pipeline!r}, goldset={self.goldset_path!r}, n_rows={len(self.rows)})"`.

---

## 6. 实施顺序 (哪些先做)

In order of dependency:

1. **Resolve P0-1** (line-range citation). Trivial doc fix; do first so peer review can find the actual eval sections.
2. **Resolve P0-2** (`goldset.jsonl` real entries). Bootstrap from `tests/ingest_e2e` fixture or add a fixture-driven `goldset.jsonl`. Without this, the deliverable is unverifiable.
3. **Resolve P0-3** (EvalRunner pipeline interface). Either inject a `search_fn` callable or add a smoke test with `AsyncMock`. This makes the orchestrator reviewable independently of tasks 14/15/16.
4. **Apply P1-1** (CI / regression / statistical test plan). Either expand task18 to include a `tests/eval/regression.py` stub + a `ci.yml` snippet, or create a follow-up task. **Strongly recommended to expand task18** so the eval layer ships as a complete story.
5. **Apply P1-2** (goldset version / corpus hash). Quick add — a parallel `goldset.meta.json` sidecar with `version`, `corpus_hash`, `dataset_id`, `created_at`. Validate in a new `validate_goldset` helper.
6. **Apply P1-3** (entity_level_recall word boundary). Change `in` to `re.search(rf"\b{...}\b", ...)`. Add a test for "AI" / "available" disambiguation.
7. **Apply P1-4** (synthetic.py error handling). Add logging + summary return. Low-effort, high-value.
8. **Apply P1-5** (EvalRunner smoke test). Add `test_eval_runner_smoke` with AsyncMock + tmp_path. 20 lines.
9. **Apply P2-1, P2-2, P2-3, P2-4, P2-5** as a doc/cleanup pass.
10. **Optional: P3-1 through P3-5** in a follow-up commit.

After 1-8, the task is ready for the stub → test → implement → verify cycle as written. Items 1-3 are blockers for any code merge; 4-8 are blockers for the spec's "Eval L2 integrated pipeline" promise; 9-10 are post-merge cleanup.

---

## Appendix A: FastGPT eval module — full file inventory

| File | LOC | Role |
|---|---|---|
| `packages/global/core/app/evaluation/type.ts` | 51 | TS types |
| `packages/global/core/app/evaluation/constants.ts` | 22 | `EvaluationStatusEnum`, `evaluationFileErrors` |
| `packages/global/core/app/evaluation/api.ts` | 20 | Pagination/CRUD request bodies |
| `packages/global/core/app/evaluation/utils.ts` | 10 | `getEvaluationFileHeader(appVariables?)` |
| `packages/global/test/core/app/evaluation/utils.test.ts` | (3.6KB) | Unit test for header generation |
| `packages/service/core/app/evaluation/evalSchema.ts` | 57 | Mongoose `Evaluation` model |
| `packages/service/core/app/evaluation/evalItemSchema.ts` | 56 | Mongoose `eval_items` model (3 judge scores + average) |
| `packages/service/core/app/evaluation/mq.ts` | 83 | BullMQ queue + worker setup |
| `packages/service/core/app/evaluation/utils.ts` | 152 | `parseEvaluationCSV`, `validateEvaluationFile` |
| `packages/service/support/permission/evaluation/auth.ts` | 56 | Permission check |
| `projects/app/src/web/core/app/api/evaluation.ts` | 55 | Client SDK: 7 endpoints |
| `projects/app/src/pages/dashboard/evaluation/index.tsx` | (list page) | Paginated list, 10s polling |
| `projects/app/src/pages/dashboard/evaluation/create.tsx` | (form page) | Upload CSV + select evalModel + appId |
| `projects/app/src/pageComponents/app/evaluation/DetailModal.tsx` | 577 | Per-item view, list+detail, edit/retry/delete/export |

**No retrieval-quality metric anywhere.** No `relevant_chunk_ids`. No `recall@k`. No `mrr`. No `ndcg`.

## Appendix B: Spec sections that are not in task18.md

| spec section | content | task18 status |
|---|---|---|
| §9.5.2 L2 检索级评测 | gold set format, synthetic gen, 5 metrics | **Implemented** (partial — no §16 maintenance, no entity disambiguation) |
| §9.5.3 L3 生成级评测 | RAGAS 四指标 + extension | **Deferred to task19** (table at task18.md:341-353) |
| §9.6 Eval 时机矩阵 | 6 stages with thresholds | **Not in task18** |
| §9.7 Regression Testing | Jaccard ≥ 0.95 | **Not in task18** (probably task20) |
| §9.8 CI yaml | GitHub Actions | **Not in task18** (probably task20) |
| §16 Gold Set 标注与维护 | file format, version mgmt, annotation process | **Not in task18** |
| §6.5.5 Embedding A/B 对比 | `ab_embedding_compare` | **Not in task18** |
| §17 RAG 特有评测维度 | 6 dimensions (robustness, timeliness, multilingual, security, hallucination, multi-hop) | **Not in task18** |

task18.md covers ~25% of the spec's eval story (1 of 8 sections, 1 of 6 timing stages, 0 of 1 CI yaml).

## Appendix C: pipeline.ainvoke() contract cross-reference

`EvalRunner.run` (task18.md:213) assumes the pipeline's `ainvoke` returns an object with `.citations`. Tracking this through the spec:

- `src/rag/pipeline/orchestrator.py` (task 14) is the orchestration layer.
- `src/rag/pipeline/cite.py` (task 15) is the citation layer.
- `build_full_pipeline` (task 16) chains them.

The return shape is not yet defined. task18.md assumes it without specifying. **Resolution:** defer EvalRunner to a follow-up task that explicitly depends on tasks 14/15/16, OR inject a `search_fn` so EvalRunner doesn't need a full pipeline.

## Appendix D: Path inconsistency clarification

- `tests/eval/retrieval_metrics.py` (task18 target) — does not exist
- `tests/eval/synthetic.py` (task18 target) — does not exist
- `tests/eval/goldset.jsonl` (task18 target) — does not exist
- `tests/eval/__init__.py` (task18 target) — does not exist
- `tests/integration/test_eval_l2.py` (task18 target) — does not exist
- `src/rag/retrieval/trace.py` (sibling module) — exists, defines `RetrievalTrace` + `remove_duplicates`
- Per main plan tree (`2026-06-10-python-rag-pipeline.md:155-160`), `tests/eval/` is a single directory for 5 files (goldset, synthetic, retrieval_metrics, regression, run_ragas).
- The split is **intentional**. task18.md is correct on the path.
- **No path fix needed**; the inconsistency is between the *plan tree* and the *current state of the repo* (the repo is incomplete).
