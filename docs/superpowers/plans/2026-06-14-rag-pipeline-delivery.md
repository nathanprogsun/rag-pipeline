# RAG Pipeline Delivery Plan v2 (2026-06-14)

> **Status:** ACTIVE — supersedes the 2026-06-10 main plan (now marked `*-deprecated`).
> **Author:** Synthesized from 10 task audits (2026-06-14) + cross-task design note + alignment matrix.
> **Scope:** A3 (full spec, 4-6 weeks, M1-M4 milestones).
> **Audience:** Future Claude sessions on this project + project owner.

---

## 如何读这份文档

| 读者 | 应读 |
|---|---|
| **想了解项目当前状态** | §2 (现状) + §3 (架构) |
| **想接下一个 task** | §5 (M1-M4 里程碑) → 对应 task 文件 (`tasks/task{NN}.md`) |
| **想理解某个 P0 怎么来的** | §7 (Open P0 总览) → `audit/2026-06-14-task{NN}-alignment.md` |
| **想理解跨 task 签名决策** | `.agents/design/2026-06-14-cross-task-contracts.md` |
| **想理解与 FastGPT 的对齐状态** | `audit/2026-06-14-task11-20-fastgpt-alignment.md` |
| **想知道测试如何写** | `test-plan.md` |
| **想知道 v3 推什么** | §8 (Out of Scope) |

---

## 1. TL;DR

| 项 | 值 |
|---|---|
| 当前实现 | Ingest (file/URL/buffer/API) + Chunker (12-rule recursive) + trace helpers + Domain models |
| 旧 plan 与代码偏差 | 9 / 10 task docs (task 11-20) 标"已完成"但 0 deliverable code |
| 目标 scope | A3 (full spec) — 经 8 项决策 (2026-06-14) |
| 实施周期 | 4-6 周,4 个 milestone (M1-M4) |
| Open P0s(本轮) | 29(分布见 §7) |
| 跨 task 契约 | 9 个 contract(详见 `.agents/design/2026-06-14-cross-task-contracts.md`) |
| 旧 plan 状态 | `plans/2026-06-10-python-rag-pipeline.md` 标记 `*-deprecated`,内容保留 git history 可查 |

---

## 2. 现状盘点(2026-06-14)

### 2.1 已交付模块(`src/rag/`)

| 路径 | 内容 | 状态 |
|---|---|---|
| `ingest/pipeline.py` | 3 段异步管线 (Reader → Normalizer → Chunker) | ✓ 完成 |
| `ingest/source.py` | `IngestSource` tagged union: File / Url / Buffer / Api | ✓ 完成 |
| `ingest/types.py` | `DocMeta / TextDoc / Chunk / ChunkMetadata / IngestResult` | ✓ 完成 |
| `ingest/cli.py` | typer CLI: `rag-ingest ingest / ingest-url` | ✓ 完成 |
| `ingest/reader/dispatch.py` | async `dispatch_bytes` 路由 8 个 adapter | ✓ 完成 |
| `ingest/reader/extensions/*.py` | 8 个 `AsyncFormatAdapter`:txt/md/html/pdf/docx/pptx/csv/xlsx | ✓ 完成 |
| `ingest/normalizer/{base,no_op,structure}.py` | 仅 `NoOpNormalizer` + `StructureNormalizer`(可选) | ✓ 完成 |
| `ingest/chunker/{core,types}.py` | 12-rule 递归分块,per-chunk `heading_stack` | ✓ 完成 |
| `domain/{document,dataset,search,enums}.py` | `ScoredDocument` (含 `score_breakdown`) / `Dataset` / `SearchRequest/Result` | ✓ 完成(round 1 加 `score_breakdown`) |
| `retrieval/trace.py` | `RetrievalTrace` dataclass + `remove_duplicates` | ✓ 完成 |
| `infra/{pg,redis,llm}/` | SQLAlchemy async + Redis + LLM clients | ✓ 完成 |
| `error_codes.py`, `exception.py`, `config.py` | 5-组 StrEnum + RAGError | ✓ 完成 |
| `ingest/cli.py` | typer ingest CLI | ✓ 完成 |

### 2.2 未实现模块(本 plan 要补的)

| 路径 | 内容 | 任务 |
|---|---|---|
| `pipeline/fusion.py` | `intra_fusion` / `inter_dataset_fusion` | task 11 |
| `pipeline/filter.py` | `remove_duplicates` re-export + `filter_by_score` + `filter_by_token_budget` | task 12 |
| `pipeline/query_ext.py` | `QueryExtensionRunnable`(无 Decomposer per C) | task 13 |
| `pipeline/{subgraph,orchestrator,rerank,cite,parent_doc}.py` | 5 子模块(inline cite per E;pre-inter-fuse rerank per D) | task 14 |
| `retrieval/{audit,citation_check}.py` | JSONL audit + inline citation regex check | task 15 |
| `pipeline/full.py` | `build_full_pipeline` + `Pipeline.ainvoke(SearchRequest) → SearchResult` | task 16 |
| `cli/{search,eval,audit,cache,chunk}.py` | 5 新 subcommand(`ingest` 复用) | task 17 |
| `eval/retrieval_metrics.py` | 5 metrics:chunk/entity recall + MRR + NDCG + precision | task 18 |
| `eval/ragas.py` | RAGAS wrapper(读 `SearchResult.response` per C4)+ jaccard + compare_results | task 19 |
| `.github/workflows/ci.yml` + `Makefile` + `tests/conftest.py` + pyproject testcontainers | CI 6 阶段 + 80% coverage + weekly RAGAS | task 20 |

### 2.3 Domain 字段 / 签名 / 模块名 漂移(本 plan 锁定)

| 漂移点 | 旧 | 新(本 plan 锁定) | 来源 |
|---|---|---|---|
| `ScoredDocument.score` 字段语义 | 单一 float(融合后用) | 单 float(RRF sum)+ `score_breakdown: dict[str, float]` | round 1 P0-1, Contract 2 |
| `intra_fusion` 签名 | `(vector_hits, fulltext_hits, weights)` | `(query_groups: list[list[ScoredDocument]], weights, rrf_k)` | round 1 P0-2, Contract 1, decision I |
| `SearchResult.prompt` 字段 | `prompt: str` (LLM 输入) | `response: str` (LLM 输出,含 `[id](CITE)`) | Contract 4 |
| `pipeline.ainvoke` 签名 | `dict` 入参,`dict` 出参 | `SearchRequest → SearchResult`,typed DI (`PipelineDeps`) | Contract 3 |
| `with_cache` decorator | task 16 草案 | 废弃,直调 `Cache.get/set` | Contract 7 |
| Stage ordering | GlobalRerank post-fusion | pre-inter-fuse(text-only) | Contract 8, decision D |
| Citation 格式 | prefix block `[1] 来源:` | inline `[id](CITE)` | Contract 5, decision E |
| `QueryDecomposer` | spec 创新 | **删除** | Contract 9, decision C |
| `ScoredDocument` `q/a` 字段 | spec 中存在 | 早删除(已迁出至 `RetrievalTrace`)| 旧 doc 漂移 |

---

## 3. 架构(5 层)

### 3.1 Layer 1 — Ingest(`src/rag/ingest/`)

**对应 spec:** §6, §6.5

**职责:** 多源输入 → `IngestResult { chunks, title, doc_meta, warnings }`

**3 段异步管线:**
```
Reader (8 adapter) → Normalizer (NoOp / Structure) → Chunker (12-rule recursive)
```

**当前状态:** 已完整交付,含 8 个 async reader + 2 个 normalizer + 12-rule chunker。

**M1-M4 中要做的:** 无新功能,仅测试加固 + 集成测试桩。

---

### 3.2 Layer 2 — Domain(`src/rag/domain/`)

**对应 spec:** §3

**职责:** 纯数据模型,无业务逻辑;跨层 Pydantic 类型契约。

**核心类型:**
- `Chunk` (入库前) / `ScoredDocument` (召回后,带 `score_breakdown`)
- `Dataset` (含 `rrf_k: int = 60`, `vector_weight`, `fulltext_weight`)
- `SearchRequest` (4 子 config: `RetrievalConfig` / `GenerationConfig` / `ContextConfig` / `HistoryConfig`)
- `SearchResult` (含 `response`, `citations`, `_intermediate_hits` exclude)
- `Citation` (DTO)
- `RetrievalTrace` (dataclass, q/a 平行数组)

**当前状态:** 已交付,`score_breakdown` 在 round 1 加好,`SearchResult` 待 rename `prompt → response` + 加 `_intermediate_hits` + `citation_format`(M1 前置,见 §5.1)。

---

### 3.3 Layer 3 — Retrieval + Pipeline(`src/rag/pipeline/`, `src/rag/retrieval/`)

**对应 spec:** §7, §7.0, §8

**职责:** 查询 → 多路召回 → RRF 融合 → 过滤 → Rerank → 跨 dataset 融合 → Cite → Generation。

**目标状态(M2-M3 落地):**

```
                  ┌─── QueryExt (1 sub-module)
                  │
SearchRequest ──→ │  IntraFusion (task 11) ──┐
                  │  Rerank (text-only)      │ (pre-inter-fuse per D)
                  │  Re-fuse                 │
                  │  InterDatasetFusion     │
                  │  Filter (task 12)       │
                  │  ParentDoc (task 14)    │
                  │  Cite (inline per E)    │
                  │  Generate (LLM call)    │
                  └──────────────────────────┘
                                 ↓
                          SearchResult
```

**子模块清单(全部 M2-M3 交付):**
- `pipeline/fusion.py` — WRRF(Contract 1)
- `pipeline/filter.py` — dedup + threshold + token budget(Contract 2)
- `pipeline/query_ext.py` — LLM rewrite, no Decomposer(Contract 9)
- `pipeline/subgraph.py` — 请求体校验
- `pipeline/orchestrator.py` — 10-阶段状态机(Contract 8)
- `pipeline/rerank.py` — `QwenRerank` + `NoOpRerank`
- `pipeline/cite.py` — inline `[id](CITE)` parser + formatter(Contract 5)
- `pipeline/parent_doc.py` — parent chunk window expander(rag-pipeline 创新)
- `pipeline/full.py` — `build_full_pipeline(PipelineDeps) → Pipeline`(Contract 3, 7)
- `retrieval/audit.py` — JSONL append with `fcntl.flock`(task 15)
- `retrieval/citation_check.py` — regex verify inline citations(task 15)

---

### 3.4 Layer 4 — Eval(`src/rag/eval/`, `tests/eval/`)

**对应 spec:** §9, §16, §17

**职责:** 量化评估检索质量(L2)与生成质量(L3 RAGAS),支撑 CI 回归。

**目标状态(M4 交付):**
- `eval/retrieval_metrics.py` — 5 metrics: `chunk_recall / entity_recall / mrr / ndcg / precision`
- `eval/eval_runner.py` — `EvalRunner.run(goldset, pipeline) → EvalReport`
- `eval/ragas.py` — RAGAS wrapper,judge model pin 在 settings,**读 `SearchResult.response`**(per C4,非 `prompt`)
- `eval/regression.py` — `jaccard(t, t-1)` + `compare_results` baseline 对比
- `tests/eval/goldset.jsonl` — version + corpus_hash 校验
- `tests/eval/run_ragas.py` — weekly cron 入口

---

### 3.5 Layer 5 — Ops(`cli/`, `.github/`, `Makefile`, `pyproject.toml`)

**对应 spec:** §9.8, §11

**职责:** 用户面 CLI、CI 流水线、覆盖率门、回归门。

**目标状态(M3-M4 交付):**
- `cli/{search,ingest,eval,audit,cache,chunk}.py` — typer 6 subcommand
- `.github/workflows/ci.yml` — lint / unit / integration / on-merge / weekly / pre-release
- `Makefile` — `coverage` / `eval` / `lint` / `test` target
- `tests/conftest.py` — 真实 fixture(testcontainers PG + Redis)
- `pyproject.toml` — testcontainers dep + `[tool.coverage.*]` block + 4 module 覆盖目标

---

## 4. 关键技术决策(8 项已锁定)

| 决策 | 选择 | 来源 |
|---|---|---|
| Scope | **A3** full spec (4-6 周) | 2026-06-14 用户确认 |
| task 15 JSONL audit | **实施** | A3 隐含 |
| QueryDecomposer | **删除** | decision C |
| Rerank 顺序 | **pre-inter-fuse**(text-only) | decision D, 对齐 FastGPT `defaultRecall/rerank.ts:55-110` |
| Citation 格式 | **inline `[id](CITE)`** | decision E, 对齐 FastGPT `quote.ts` |
| Parent doc | **实施** | A3 隐含 |
| intra_fusion weights | **query variant 语义**(per-group weights) | decision I(round 1) |
| task 1-10 状态 | 已交付(本 plan 不重做) | 用户之前已确认 |

完整技术契约(9 个 contract)见 `.agents/design/2026-06-14-cross-task-contracts.md`。

---

## 5. M1-M4 里程碑(4-6 周)

### 5.1 M1 — 检索基础 (1-2 周)

**目标:** 落地 `fusion.py` + `filter.py`,加 3 个 domain 字段。

**前置改动(1 天):**
- `domain/search.py` rename `prompt → response` + 加 `_intermediate_hits: Field(exclude=True)` + 加 `citation_format: Literal["inline", "prefix"] = "inline"`

**任务清单:**
- 5a: `src/rag/pipeline/fusion.py` + `tests/unit/test_fusion.py` (8+ tests) — Contract 1, 2
- 5b: `src/rag/pipeline/filter.py` + `tests/unit/test_filter.py` (5+ tests) — Contract 2

**验收:**
- `pytest tests/unit/test_fusion.py tests/unit/test_filter.py` 全过
- Mypy strict 0 error, ruff 全过
- `filter_by_score` 读 `score_breakdown[source]` 而非 `.score`(test 覆盖)
- `intra_fusion(query_groups, weights=None, rrf_k=60)` 签名锁定(下游可调用)

---

### 5.2 M2 — 召回增强 + 审计 (1-2 周)

**目标:** 5 个 pipeline 子模块 + 2 个 audit 模块。

**任务清单:**
- 5c: `src/rag/pipeline/query_ext.py` + `tests/unit/test_query_ext.py` (5+ tests) — Contract 9
  - LLM rewrite, 调 `get_chat_model("MiniMax-M3")`(**非** phantom `get_m3_chat_model`)
  - Stage 2 embedding dedup
  - **无 Decomposer**(per C)
- 5d: 5 个 `src/rag/pipeline/{subgraph,orchestrator,rerank,cite,parent_doc}.py` + 5 个 test 文件 (3+ tests each) — Contract 3-6, 8
  - `orchestrator.py` 实现 10-阶段状态机(Contract 8)
  - `cite.py` inline parser(Contract 5)
  - `parent_doc.py` parent chunk window(rag-pipeline 创新)
- 5e: `src/rag/retrieval/{audit,citation_check}.py` + 2 个 test 文件 — task 15
  - JSONL append with `fcntl.flock`
  - Citation regex `\[(\d+)\]\(CITE\)` 解析

**验收:**
- 5c 调 LLM 用真实 chat model(非 placeholder)
- 5d `orchestrator.ainvoke(req)` 返回 `SearchResult`, 含 `response` + `citations` + `_intermediate_hits`
- 5e audit JSONL 不阻塞主流程
- 全部 Contract 4-6, 8-9 的 test contract 落地

---

### 5.3 M3 — 集成 + CLI (1-2 周)

**目标:** `build_full_pipeline` 串起所有子模块 + 6 个 CLI subcommand。

**任务清单:**
- 5f: `src/rag/pipeline/full.py` + `tests/integration/test_full_pipeline.py` (1 e2e test) — Contract 3, 7
  - `PipelineDeps` Pydantic 化
  - **不**用 `with_cache`, 直调 `Cache.get/set(key, layer, warnings)`
  - Rerank pre-inter-fuse(per D)
  - 1 个 happy-path e2e test with `FakeEmbed / FakeLLM / NoopCache`
- 5g: `src/rag/cli/{search,ingest,eval,audit,cache,chunk}.py` + `tests/cli/test_cli.py` (10+ tests) — task 17
  - typer 6 subcommand
  - `ingest` 复用 `src/rag/ingest/cli.py`
  - 失败路径用 `raise typer.Exit(code=1)`(非 `typer.echo(..., err=True)`)

**验收:**
- 1 个完整 e2e: `pipeline.ainvoke(req) → SearchResult` 在 fake 全栈下通过
- 6 个 CLI subcommand 都能跑
- 失败时 exit code = 1(`echo $?` 验证)
- 80% 单元测试覆盖率(从 M1 末 50% 提升)

---

### 5.4 M4 — Eval + CI (1-2 周)

**目标:** 5 metric + RAGAS + 完整 CI。

**任务清单:**
- 5h: `src/rag/eval/{retrieval_metrics,eval_runner}.py` + `tests/eval/test_retrieval_metrics.py` (5+ tests) — task 18
  - 5 metric 函数
  - goldset.jsonl schema 校验(version + corpus_hash)
  - entity_recall 改进(per audit P1-3, 不做 naive substring)
- 5i: `src/rag/eval/{ragas,regression}.py` + `tests/eval/test_ragas.py` (3+ tests) — task 19
  - RAGAS wrapper, judge model 从 settings pin
  - **读 `SearchResult.response`**(per C4)
  - **删除** `lazy_greedy_oracle.py` + 关联 test(per audit)
- 5j: `.github/workflows/ci.yml` + `Makefile` + `tests/conftest.py` + `pyproject.toml` (testcontainers) — task 20
  - 6 阶段 workflow: lint / unit (80% gate) / integration (testcontainers PG+Redis) / on-merge full eval / weekly RAGAS / pre-release
  - 4 module 覆盖目标:`rag.retrieval.lazy_greedy` → 重命名为 `rag.retrieval.citation_check` 等
  - `concurrency:` block 取消 superseded PR

**验收:**
- 5 metric 函数各自 ≥1 test
- RAGAS mock 跑通(不调真实 OpenAI)
- CI 在 PR 上能跑通(80% coverage gate 不阻断 random PR)
- Weekly cron 跑 RAGAS regression(不阻断 PR)
- `make eval` / `make coverage` / `make lint` 三个 target 都能用

---

## 6. 风险与依赖

### 6.1 关键路径

```
M1.5 (5b filter) → M2 (5c query_ext) → M3 (5f full) → M4 (5h eval_runner) → M4 (5i RAGAS) → M4 (5j CI)
```

任一阶段延期 1 周,总周期延 1 周。

### 6.2 风险

| 风险 | 概率 | 缓解 |
|---|---|---|
| LLM judge model 成本不可控 | 中 | RAGAS 强制 pin model + 缓存(per 5i)+ mock 跑 CI |
| testcontainers 启动慢,PR feedback 延迟 | 中 | M3 末加 unit + integration 拆分;integration 用 nightly 而非 per-PR |
| `intra_fusion` 性能瓶颈(百级 query variant) | 低 | 现阶段 N ≤ 5,O(N×K×log K) 可控;若需要,加 per-group top-K 截断 |
| FastGPT 后续版本更新导致契约漂移 | 中 | 9 个 Contract 锁定;FastGPT 升级时,先 audit 9 contract 再升 |

### 6.3 不在 M1-M4 内(本 plan 不解决)

- 多模态 fine-tuning
- OTel 分布式 tracing(FastGPT 风格)替代 JSONL audit
- Postgres `IngestDatasource = "url"` 落库
- LLM-based hallucination 检测(`CitationChecker` 仅 regex)
- JS/Go 跨语言 SDK
- v3 推迟项见 §8

---

## 7. Open P0 总览(29 个,跨 task)

完整 P0 列表见 `audit/2026-06-14-task{NN}-alignment.md`。下表为按 cluster 的总览。

| Cluster | 数量 | 受影响 task | 解决路径 |
|---|---|---|---|
| **C1: Phantom file / phantom test**(原"已完成"谎言) | 14 | 14, 16, 20 | 5d/5f/5j 实施时落地真实文件 + 测试 |
| **C2: Wrong line-range citation** | 0(本轮已修) | — | (已修) |
| **C3: Phantom import / module-load crash** | 6 | 13, 14, 16, 17, 18, 19, 20 | 实施时同步修 import;5a-5j 全清 |
| **C4: Score model mismatch** | 1 | 12 | 5b `filter_by_score` 改读 `score_breakdown`(per C2) |
| **C5: Cross-task signature coupling** | 4 | 14, 16, 18, 19 | 9 Contract 已锁;5d/5f/5h/5i 实施时按 contract 调 |
| **C6: LLM call pattern bug** | 4 | 13, 18, 19 | 5c/5h/5i 实施时改 phantom import + 修正 field |
| **C7: Stage ordering divergence** | 3 | 16, 18, 20 | 5f (rerank pre-fuse per D); 5h (eval timing 6 阶段); 5j (coverage target) |
| **C8: Eval / RAGAS wiring bug** | 3 | 19 | 5i 实施时:删 `lazy_greedy_oracle` + 读 `response`(per C4) + pin judge model |
| **C9: SCOPED OUT vs implementation status** | 4 | 14, 16, 18, 20 | 5d/5f/5h/5j 实施时把"未开始"改成"实施中"→"完成" |
| **C10: Field / signature drift** | 5 | 14, 16, 17, 18, 20 | 5d/5f/5g/5h/5j 实施时按 9 contract 修 |

**P0 收敛曲线(预测):**

| 阶段 | 剩余 P0 |
|---|---|
| M1 末 | 28 (5a-5b 修 1) |
| M2 末 | 14 (5c-5e 修 14) |
| M3 末 | 5 (5f-5g 修 9) |
| M4 末 | 0 (5h-5j 修 5) |

---

## 8. Out of Scope(本 plan 范围外 / v3 推迟)

明确**不在** A3 + M1-M4 范围,留待 v2 / v3:

### 8.1 v2 推迟(在 A3 范围,本 plan 不做)

| 项 | 推迟原因 | 重启条件 |
|---|---|---|
| `IngestDatasource = "url"` 落库 | spec §3 写明但 ingest pipeline 暂存"manual" | M2 后评估 |
| `image_caption` 多模态完整链路 | 任务 13 spec 创新,FastGPT 也有 image_caption 但耦合多 | A3 完后看 FastGPT 升级 |
| LLM 幻觉检测(`CitationChecker` 增强) | 当前仅 regex 验证 id 范围 | ragas 跑稳后 |

### 8.2 v3 推迟(超出 A3 范围)

| 项 | 说明 |
|---|---|
| OTel 分布式 tracing | 替代 JSONL audit,需要 OTel collector 基础设施 |
| 跨语言 SDK (JS/Go) | 需先稳定 Python API + OpenAPI 暴露 |
| 多模态 fine-tuning | 不是 RAG 范畴 |
| Postgres → ClickHouse 迁移 | 性能优化,非功能必需 |
| 完整 e2e UI testing(playwright) | 评估 UI 出现后再做 |
| QueryDecomposer(子查询拆词) | **已删除 per decision C**, 不重启 |
| `with_cache` decorator | **已删除 per Contract 7**, 不重启 |
| `ScoredDocument.q/a` 字段(老 doc 漂移) | **已迁出至 `RetrievalTrace`**, 不回迁 |
| `DocumentStructure` 独立 stage | **已在 round 1 删除**, 改为 chunker 内 per-chunk regex, 不重启 |

### 8.3 永远不做(明确边界)

- **对接 LLM API 之外的私有模型** — 限定 OpenAI / DashScope / VLLM 三家
- **替换 FastGPT 主仓** — rag-pipeline 是 library, FastGPT 是 app, 定位不同
- **重新发明检索算法** — 算法层面对齐 FastGPT, 创新点限于工程化(pipeline, eval, audit)

---

## 9. 参考资料(全部为内部链接)

| 文档 | 路径 | 用途 |
|---|---|---|
| 设计 spec(技术深度) | `docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` | 17 章节, 1668 行, 含 §0-§17 全部技术细节 |
| 旧主 plan(已 deprecated) | `docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` | git history, 不再更新 |
| 旧 sub-plan(已 deprecated) | `docs/superpowers/plans/2026-06-11-chunker-reader-refactor.md` | round 1 已交付, 保留 |
| 跨 task 契约 | `.agents/design/2026-06-14-cross-task-contracts.md` | 9 Contract, 5a-5j 实施时的接口契约 |
| 10 份 task audit | `docs/superpowers/plans/audit/2026-06-14-task{11-20}-alignment.md` | P0/P1 详细分析 |
| 10-task 总览 | `docs/superpowers/plans/audit/2026-06-14-task11-20-summary.md` | cluster 分类 + 优先级矩阵 |
| FastGPT 对齐矩阵 | `docs/superpowers/plans/audit/2026-06-14-task11-20-fastgpt-alignment.md` | input/output/logic/node 对照 |
| Test plan | `docs/superpowers/plans/test-plan.md` | 覆盖率 / 集成测试 / Eval 启动条件 |
| Task 文件(实施入口) | `docs/superpowers/plans/tasks/task{1-20}.md` | 每个 task 顶部有 Open P0s 表格 |

---

## 10. 维护规则

1. **本 plan 是 LIVE 文档** — 任何 contract 变更 = 改本 plan §4 + 改 design note。
2. **每个 task 完成后** — 更新对应 task 文件的 status banner(从"实施中"→"完成"),并把 P0 列表移到"已修复"。
3. **每个 milestone 完成后** — 更新 §5 的 P0 收敛曲线(剩余 P0 数)。
4. **每个 FastGPT 升级** — 重跑 9 contract audit,差异入本 plan §4 的"决策变更"区。
5. **任何 v3 项重新启用** — 移到 §8.1(v2 推迟),不直接入主 plan。

---

**最近一次更新:** 2026-06-14
**下次更新触发:** M1 末(完成 5a + 5b 后,更新 §5 验收记录)
