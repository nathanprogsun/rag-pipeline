# Task ↔ FastGPT Alignment Matrix (Tasks 11-20)

> **Date:** 2026-06-14
> **Status:** Snapshot after Step 1+2+4 cleanup (3 status re-tags, 5 line-range fixes, 9 contracts locked).
> **Source:** 10 task audit reports + cross-task design note (`.agents/design/2026-06-14-cross-task-contracts.md`).
> **Scope:** 每个 task 改完后,与 FastGPT 主仓 (`/Users/jung/pro/FastGPT`) 当前 main 分支的输入/输出/核心逻辑/节点对照。

## 如何读这张表

| 列 | 含义 |
|---|---|
| **Task** | rag-pipeline 任务编号 + 简述 |
| **Input** | rag-pipeline 实现的入参 (类型/必填) |
| **Output** | rag-pipeline 实现的出参 (类型) |
| **Core Logic** | 算法/数据流概要 (含 P0 决策落地) |
| **FastGPT nodes** | 实际对应的 FastGPT 文件:行号/函数名 |
| **Spec Δ** | rag-pipeline 与 FastGPT 的设计差异 (标 `[NEW]` 是 rag-pipeline 创新,`[+]` 是比 FastGPT 更正,`[-]` 是缺失/简化) |

> **约定:** FastGPT 主仓路径相对 `/Users/jung/pro/FastGPT/`,例如 `packages/global/core/dataset/search/utils.ts`。

---

## Per-Task Alignment Table

### Task 11: Fusion (intra + inter WRRF)

| 项 | 内容 |
|---|---|
| **Input** | `query_groups: list[list[ScoredDocument]]` (N 个 query variant 的合并结果); `weights: list[float] \| None` (per-variant trust); `rrf_k: int` (默认 60, 可由 `Dataset.rrf_k` 覆盖) |
| **Output** | `list[ScoredDocument]` 按 `score` 降序,新对象 (不修改入参),每个 chunk 带 `score_breakdown: dict[str, float]` |
| **Core Logic** | WRRF 公式: `score(c) = Σ_g w_g / (rrf_k + rank_g(c))` (rank 从 enumerate(start=1) 起); 重复 chunk_id 累加 score, `score_breakdown[source] = max(prev, raw_score)`; 单 query_group 也走 RRF (返回 `score = 1/(k+1)`,非 FastGPT 的 as-is 短路) |
| **FastGPT nodes** | `packages/global/core/dataset/search/utils.ts:5-70` 的 `datasetSearchResultConcat` (一行 N-list, 7 个 call site); `packages/global/core/dataset/type.ts:421-431` (typed score 数组定义) |
| **Spec Δ** | `[-]` 输出不是 typed `score: {type, value, index}[]` 而是单 `score: float` + `score_breakdown` 字典; `[+]` `rrf_k` 可 per-dataset 配置 (FastGPT 硬编码 60); `[-]` 无单 list as-is 短路; `[+]` query variant 语义 (B4 修正) — FastGPT 的 `concatWeightedRecallLists` 同时支持 query variant 融合和 source 融合, 不区分 |

**Call site pattern (FastGPT 7 复用):**
```
result.ts:43        concatRecallLists               uniform 1.0
result.ts:47        concatWeightedRecallLists       per-list weight
workflow/dispatch/dataset/concat.ts:30   inline  uniform 1.0
defaultRecall/index.ts:111  concatWeightedRecallLists   embeddingWeight / 1-embeddingWeight
defaultRecall/index.ts:115  concatWeightedRecallLists   imageCaption 同上
defaultRecall/index.ts:137  concatWeightedRecallLists   caption 0.3 / vector 0.7
defaultRecall/index.ts:149  concatWeightedRecallLists   text 1.0 / image 0.7-1.0
```

### Task 12: Filter Pipeline (dedup / threshold / token budget)

| 项 | 内容 |
|---|---|
| **Input** | `list[ScoredDocument]` (召回后); `threshold: float \| None`; `token_budget: int \| None`; `search_mode: Literal["embedding", "fulltext", "mixed"]` |
| **Output** | `list[ScoredDocument]` 过滤后; `using_similarity_filter: bool` (调用方可知是否真过滤) |
| **Core Logic** | (1) `remove_duplicates(docs, traces)` 按 `(q, a)` 键去重 (复用 `src/rag/retrieval/trace.py`); (2) `filter_by_score` 读 `score_breakdown[source]` (per Contract 2), 不是 `.score`; `search_mode != embedding` 时 filter 是 no-op; (3) `filter_by_token_budget` 启发式 (4 字符 ≈ 1 token), 显式 caveat 替代 tiktoken |
| **FastGPT nodes** | `packages/service/core/dataset/search/defaultRecall/result.ts:69-100` 的 `filterSearchResultsByScore` (有 `usingSimilarityFilter` 返回标志 + `searchMode` gate); `removeDuplicateInCorresponding` 路径 (chunk_id + text 标准化) |
| **Spec Δ** | `[+]` threshold 读 `score_breakdown` 而非 typed score 数组 (FastGPT 读 `embedding.value`); `[-]` 启发式 token 不如 tiktoken 精确; `[+]` dedup 键用 `(q, a)` 而非 chunk_id (FastGPT 用 chunk_id + 文本归一化) |

### Task 13: Query Extension (无 Decomposer)

| 项 | 内容 |
|---|---|
| **Input** | `query: str`; `ContextConfig` (含 `query_extension: bool`, `max_query_variants: int = 3`) |
| **Output** | `list[str]` (N 个 query 变体) |
| **Core Logic** | Stage 1: LLM rewrite (prompt 模板, 调用 `get_chat_model("MiniMax-M3")`, **非** phantom `get_m3_chat_model`); Stage 2 (可选): embedding dedup 过滤相似变体; **无 Decomposer** (per decision C) |
| **FastGPT nodes** | `packages/service/core/ai/functions/` 下的 query rewrite prompt; `multiQueryRecall` 在 `defaultRecall/index.ts` 调用 |
| **Spec Δ** | `[-]` 没有 `QueryDecomposer` (子查询拆词 + lazy greedy MMR 选择) — FastGPT 也无对应, **已删除 per decision C**; `[+]` Stage 2 embedding dedup (FastGPT 直接拿所有 rewrite) |

### Task 14: Subgraph + Orchestrator + Rerank + Cite + ParentDoc

| 项 | 内容 |
|---|---|
| **Input** | `SearchRequest` (4 子 config: RetrievalConfig/GenerationConfig/ContextConfig/HistoryConfig) + `PipelineDeps` (typed DI) |
| **Output** | `SearchResult` (response: str 含 `[id](CITE)`, citations: list[Citation], _intermediate_hits: list[ScoredDocument] exclude=True) |
| **Core Logic** | 10 阶段流水线 (per Contract 8): QueryExt → Recall → IntraFusion → Rerank(text-only, pre-inter-fuse) → Re-fuse(weights=[w, 1-w]) → InterDatasetFusion → Filter → ParentDoc → Cite(inline) → Generate; 图像 hit 不走 text rerank |
| **FastGPT nodes** | `packages/service/core/dataset/search/defaultRecall/index.ts:1-180` (the canonical orchestrator, 7 个 stage 串联); `defaultRecall/rerank.ts:55-110` (text-only rerank pre-fuse); `defaultRecall/quote.ts` (inline citation formatter) |
| **Spec Δ** | `[NEW]` `ParentDoc` (parent chunk 扩展窗口 — FastGPT 无对应, **保留 per decision F**); `[+]` pipeline 是单个 Python coroutine (FastGPT 是 workflow DAG of nodes); `[+]` `SearchResult.response` 含 inline `[id](CITE)` (per E, FastGPT 同样 inline 格式); `[-]` 5 个子模块都是单文件, FastGPT 拆成多个 dispatch node |

**Sub-module 映射:**

| rag-pipeline 模块 | FastGPT 节点 |
|---|---|
| `subgraph.py` (请求体校验) | (无对应 — FastGPT 用 Zod schema + workflow entry) |
| `orchestrator.py` (状态机) | `defaultRecall/index.ts` + `workflow/dispatch/dataset/` |
| `rerank.py` (QwenRerank + NoOpRerank) | `defaultRecall/rerank.ts` + `core/ai/functions/qwenRerank.ts` |
| `cite.py` (inline parser) | `defaultRecall/quote.ts` (similar, but frontend-rendered) |
| `parent_doc.py` | (无 — **rag-pipeline 创新**) |

### Task 15: Audit & CitationChecker

| 项 | 内容 |
|---|---|
| **Input (audit)** | `SearchRequest.audit: bool = True`; `RetrievalTrace` 流; `SearchResult._intermediate_hits` |
| **Input (citation_check)** | `SearchResult.response: str` (含 `[id](CITE)` 标记); `SearchResult.citations: list[Citation]` |
| **Output (audit)** | JSONL 行追加 (production 用 `fcntl.flock`); 字段: `query / dataset_id / latency_ms / cache_hits / rrf_score / citation_count` |
| **Output (citation_check)** | `CitationVerifyResult` (verified_count, missing_ids, unparseable_markers) |
| **Core Logic** | Audit: 旁路 channel, 不阻塞主流程 (FastGPT 风格), 但走文件而非 OTel/Mongo; Citation: regex `\[(\d+)\]\(CITE\)` 解析, verify each `id` 1-based in citations 范围 |
| **FastGPT nodes** | (无直接对应) FastGPT 用 3 套并存通道: OTel spans (`packages/service/common/otel/`); Mongo 业务审计 (`packages/service/core/dataset/audit/`); workflow `nodeResponse` 字段 |
| **Spec Δ** | `[NEW]` JSONL audit 是 rag-pipeline 创新 (FastGPT 不存 JSONL); `[NEW]` `CitationChecker` 后端 regex 验证 (FastGPT 前端 renderer 解析); `[-]` SCOPED OUT decision (per audit, 与 FastGPT 风格相近即合理); `[+]` `RetrievalTrace` dataclass 平行数组 (FastGPT 把 q/a 塞 result) |

### Task 16: build_full_pipeline

| 项 | 内容 |
|---|---|
| **Input** | `PipelineDeps` (Pydantic, frozen=True): llm/embedder/cache/vector_store/fulltext_store/audit_hook/rerank_client |
| **Output** | `Pipeline` (Protocol, 暴露 `async def ainvoke(req: SearchRequest) -> SearchResult`) |
| **Core Logic** | 串联 task 13/14/15 子模块; 显式 stage 顺序 (per Contract 8); 缓存策略: `Cache.get(key=hash(req.query + dataset_ids + retrieval.top_k), layer="search", warnings=req.warnings)` (直接调, **不**用 `with_cache` decorator per Contract 7); 失败: Redis 不可用 → 降级直连 + warnings append + metrics.increment, 不报错 |
| **FastGPT nodes** | `packages/service/core/dataset/search/index.ts:datasetSearch` (API 入口, 串起 defaultRecall); `packages/service/common/redis/` (cache layer) |
| **Spec Δ** | `[+]` `PipelineDeps` Pydantic 化 (FastGPT 依赖注入是 module-level singleton); `[-]` 无 `with_cache` decorator (per audit, 改直调); `[+]` stage 顺序显式列在 `__init__` 注释 (FastGPT 散在多个 `concatWeightedRecallLists` 调用里) |

### Task 17: CLI (typer)

| 项 | 内容 |
|---|---|
| **Input** | (CLI args) 子命令: `search / ingest / eval / audit / cache / chunk` |
| **Output** | (subprocess exit) JSON 或 text 格式; 失败 `typer.Exit(code=1)` (per audit P1-1) |
| **Core Logic** | typer-based 6 subcommand; ingest 复用 `src/rag/ingest/cli.py`; search/eval/audit 调 `build_full_pipeline`; cache 调 `Cache.{get,set,clear}`; chunk 调 `chunker.core` |
| **FastGPT nodes** | (无 CLI) FastGPT 是 Next.js app, 用户通过 `projects/app/src/pages/api/core/dataset/**` HTTP 端点访问; 没有 typer/argparse 入口 |
| **Spec Δ** | `[NEW]` CLI 是 rag-pipeline 便利层, FastGPT 不对应; `[-]` CLI 覆盖 ~3% FastGPT API 表面 (60+ dataset endpoints 中只覆盖 search/ingest/eval/audit/cache/chunk 6 个) |

### Task 18: Eval L2 (Gold Set + Synthetic + Retrieval Metrics)

| 项 | 内容 |
|---|---|
| **Input** | `goldset.jsonl` (per-query: query, relevant_chunk_ids, irrelevant_chunk_ids, entity_gold_set, version, corpus_hash, dataset_id); `SearchRequest` 模板 |
| **Output** | 5 个 metric 函数: `chunk_recall / entity_recall / mrr / ndcg / precision`; 聚合后 dict per query + overall |
| **Core Logic** | 对每条 gold: pipeline.ainvoke(req) → 召回 hits → 算 metric; chunk_recall: `len(relevant_chunk_ids ∩ retrieved_chunk_ids) / len(relevant)`; entity_recall: `|gold_entities ∩ retrieved_text_entities| / |gold|`; **version+corpus_hash 强制校验** (避免 UUID 漂移) |
| **FastGPT nodes** | `packages/service/core/evaluation/dataset/evalDataset.ts` (gold set eval, metric 类似); `packages/service/core/evaluation/constants.ts` (metric definitions) |
| **Spec Δ** | `[+]` `entity_recall` 是 rag-pipeline spec (FastGPT 无 entity 维度); `[+]` version+corpus_hash 强制校验 (FastGPT 直接 invalidate); `[-]` EvalRunner 不直接读 `pipeline.ainvoke` 返回, 必须等 task 14/16 落地 |

### Task 19: Eval L3 (RAGAS + Regression)

| 项 | 内容 |
|---|---|
| **Input** | goldset.jsonl (含 `ground_truth` answer); `SearchRequest` 模板; pinned RAGAS LLM-judge model |
| **Output** | RAGAS metrics: `faithfulness / answer_relevance / context_precision / context_recall`; regression: `jaccard(goldset_hits_t, goldset_hits_t-1)`, `compare_results` 输出 diff |
| **Core Logic** | Faithfulness: RAGAS(`response=result.response`, contexts=citations) — **必须读 `result.response`**, 不是 `result.prompt` (per Contract 4); regression: 比对两次跑的 hit set, 阈值 ±0.05 触发告警; judge model 强制从 settings 读, **不** hard-code `ChatOpenAI()` |
| **FastGPT nodes** | `packages/service/core/evaluation/ragas/` (RAGAS 集成, model 走 settings); `packages/service/core/evaluation/regression/` (回归基线) |
| **Spec Δ** | `[-]` **已删除** `lazy_greedy_oracle.py` (per audit, FastGPT 无 `lazy_greedy` 函数, oracle 是编造的); `[+]` faithfulness 明确读 `response` 而非 `prompt` (FastGPT prompt 字段名清晰); `[+]` judge model 在 settings 强制 pin + cache, 避免 CI 不可控成本 |

### Task 20: CI + Final Integration + Coverage

| 项 | 内容 |
|---|---|
| **Input (CI)** | PR push / on-merge / nightly / weekly cron |
| **Output (CI)** | pass/fail; coverage report; RAGAS regression diff; module-specific coverage gate |
| **Core Logic** | GitHub Actions: (a) `lint` (ruff + mypy strict); (b) `unit` (80% global gate + 4 module 目标); (c) `integration` (testcontainers PG + Redis); (d) on-merge: full eval (task 18 metrics); (e) weekly: RAGAS regression; (f) pre-release: full e2e |
| **FastGPT nodes** | `.github/workflows/` (FastGPT 现有 workflow); `vitest.config.ts` (coverage config); `packages/service/common/test/run.ts` (test bootstrap) |
| **Spec Δ** | `[+]` 80% global + 4 module 覆盖门 (FastGPT 无 module 级别); `[+]` weekly RAGAS regression cron (FastGPT 不跑 RAGAS); `[+]` testcontainers 替 docker compose (per audit, FastGPT 混用) |

---

## FastGPT 文件引用索引(按 rag-pipeline task 分组)

| rag-pipeline task | FastGPT 文件 |
|---|---|
| 11 Fusion | `packages/global/core/dataset/search/utils.ts` |
| 12 Filter | `packages/service/core/dataset/search/defaultRecall/result.ts` |
| 13 Query Ext | `packages/service/core/ai/functions/` (query rewrite); `packages/service/core/dataset/search/defaultRecall/index.ts` (multiQueryRecall) |
| 14 Orchestrator | `packages/service/core/dataset/search/defaultRecall/index.ts`; `packages/service/core/workflow/dispatch/dataset/` |
| 14 Rerank | `packages/service/core/dataset/search/defaultRecall/rerank.ts`; `packages/service/core/ai/functions/qwenRerank.ts` |
| 14 Cite | `packages/service/core/dataset/search/defaultRecall/quote.ts` |
| 16 Pipeline | `packages/service/core/dataset/search/index.ts`; `packages/service/common/redis/` |
| 18 Eval L2 | `packages/service/core/evaluation/dataset/evalDataset.ts`; `packages/service/core/evaluation/constants.ts` |
| 19 Eval L3 | `packages/service/core/evaluation/ragas/`; `packages/service/core/evaluation/regression/` |
| 20 CI | `.github/workflows/`; `vitest.config.ts` |

---

## Spec Δ 汇总(rag-pipeline 与 FastGPT 的设计差异)

| 标记 | 含义 | 数量 |
|---|---|---|
| `[NEW]` | rag-pipeline 创新, FastGPT 无对应 | 6 (ParentDoc, JSONL audit, CitationChecker, Stage 排序单文件化, CLI, entity_recall) |
| `[+]` | 比 FastGPT 更正/更严 | 8 (rrf_k 可配, score_breakdown, query variant 分离, Pydantic DI, version+corpus_hash 校验, 强制 pinned judge, 80%+coverage gate, weekly RAGAS) |
| `[-]` | 比 FastGPT 简化/缺失 | 7 (typed score 数组→单 float, 单 list as-is 短路, Decomposer 删除, 5 个子模块不拆 workflow, CLI 覆盖 3%, testcontainers vs docker compose, lazy_greedy_oracle 删除) |
| `[=]` | 完全对齐 | 0 — 所有 task 都有差异,这是不可避免的工程化产物 |

---

## 哪些 task 实际上不"对齐"FastGPT,而是**互补**

| task | 互补性 |
|---|---|
| 13 Decomposer 已删 | 不在 FastGPT,也不在 rag-pipeline (per decision C) |
| 14 ParentDoc | rag-pipeline 独有, FastGPT 不做 |
| 15 Audit (JSONL) | rag-pipeline 独有 (FastGPT 走 OTel/Mongo) |
| 15 CitationChecker | rag-pipeline 后端验证 (FastGPT 走前端) |
| 17 CLI | rag-pipeline 独有, FastGPT 用 Next.js API |

---

## 与 Step 1+2+4 修复的对接

| 修复 | 影响 task | 对齐状态变化 |
|---|---|---|
| task 11 P0-1 (score_breakdown 字段已加) | 11, 12, 16 | FastGPT `concatScore.find(type).value = max` 语义现在可表达 (虽然实现简化成 dict) |
| task 11 P0-2 (query variant 语义) | 11, 14, 16 | B4 修正锁定, 跨 task 签名不冲突 |
| task 11 P0-3 (line-range 修复) | 11 | 引用现在指向真实 spec 文件 |
| task 14/16/20 状态重标 | 14, 16, 20 | 文档真实反映代码状态 (未开始,不是已完成) |
| task 12/13/15/18/19 line-range 修复 | 12, 13, 15, 18, 19 | reviewer 可重现引用 |
| 9 个 Contract 锁定 | 11-20 | 跨 task 接口统一 (intra_fusion 签名, Pipeline.ainvoke 签名, SearchResult.response 重命名, _intermediate_hits, with_cache 删除, stage 顺序, QueryDecomposer 删除) |

---

## 仍待解的"对齐 gap"(已知,但属 A3 全量实施后正常产物)

1. **FastGPT 的 typed score 数组 vs rag-pipeline 单 float + dict** — 数据模型不同, 但通过 `score_breakdown` 字段补偿了 per-source 信息保留
2. **FastGPT 的 workflow DAG vs rag-pipeline 单 Python coroutine** — 架构选择, 接受 (5 个子模块都是单文件)
3. **FastGPT 不做 ParentDoc / JSONL audit / 后端 CitationChecker** — 创新点, 不补齐
4. **FastGPT 拆多个 dispatch node, rag-pipeline 5 子模块在单文件** — 单文件 vs 多文件, 实施风格差异, 不影响功能
5. **FastGPT 没有 RAGAS weekly regression** — rag-pipeline 更严, 优势
6. **FastGPT 没有 80%+coverage gate + 4 module 目标** — rag-pipeline 更严, 优势

---

## 验证命令

```bash
# 1. 10 个 audit 报告
ls -la /Users/jung/pro/rag-pipeline/docs/superpowers/plans/audit/

# 2. 锁定 status 的 3 个 task (应该都是 未开始)
grep -c "已完成" /Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task{14,16,20}.md

# 3. line-range 引用 (应该 0)
grep -nE "2026-06-10-python-rag-pipeline\.md:[2-9][0-9]{3}-" /Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/*.md

# 4. score_breakdown 字段落点
grep -n "score_breakdown" /Users/jung/pro/rag-pipeline/src/rag/domain/document.py

# 5. design note 存在
ls -la /Users/jung/pro/rag-pipeline/.agents/design/2026-06-14-cross-task-contracts.md
```

---

**维护者:** 此文档应随 5a-5j 实施进展更新 (每个 task 落地后, 更新该 task 行的 Output/Core Logic 列)。
