# Task 15 Alignment — Audit & CitationChecker (spec §0.1 旁路 + §7.0.3 + §7.7)

> Audit date: 2026-06-14
> Auditor: 3-way alignment (task15.md ↔ rag-pipeline source ↔ FastGPT canonical patterns)
> Scope: `task15.md` 状态 (SCOPED OUT 2026-06-13) 是否合理; 若恢复, 对应 FastGPT 既有 / 缺位模式; 实现候选路径与 P0/P1 风险。

## TL;DR

| Dimension | Finding |
|---|---|
| 任务状态 | **SCOPED OUT 合理**。`src/rag/retrieval/` 下确实只有 `__init__.py` / `trace.py`,**没有** `audit.py` / `citation_check.py`;`tests/unit/` 下**没有** `test_citation_check.py`。重构 plan `2026-06-11-chunker-reader-refactor.md` 把 Task 15 重新分配为 Chunker 入口, 跳过本任务, 这与 task15.md 自身 status 行一致。 |
| 旁路审计在 FastGPT 的对位 | **无 JSONL 文件审计**。FastGPT 检索侧没有 "写 audit_log.jsonl" 旁路。它有 3 套并存的可观测通道: (a) MongoDB `TeamAuditCollectionName=operationLogs` 业务审计 (LOGIN/APP/DATASET 操作); (b) OpenTelemetry `withActiveSpan` 跨调用 span (`packages/service/common/tracing/client.ts`); (c) workflow dispatch 自带的 `nodeResponse` 元数据 (`concatLength` 等)。没有 "trace(query, result) → AuditRecord → jsonl" 模式。 |
| 引用校验 (CitationChecker) 在 FastGPT 的对位 | **完全没有等价物**。FastGPT 把 `SearchDataResponseItemType.score` 暴露给前端, LLM 端按 `[1] [2]` 占位引用, 后端不做 "越界/未用" 校验。`DatasetCiteItemSchema` (`packages/global/core/dataset/type.ts:444`) 是渲染用, 不是校验用。 |
| task15.md 实现可行性 | 5 个 `test_citation_check.py` 用例在 rag-pipeline 现状下能跑通 (依赖 `Citation` DTO 与正则, 两者都已就位); 但**挂在主流程上的旁路 audit (`RetrievalAudit.record`)** 需要先有 `SearchResult` 流水线 (Task 14) 才能挂上, 当前 `SearchResult` 只在 `domain/search.py` 定义了 schema, 真实 producer 不存在。 |
| 现有 `RetrievalTrace` 与 task15 设计的兼容性 | **有冲突**。task15.md:14 旧描述里说 `RetrievalTrace` 字段为 `query / dataset_id / chunk_id / score / source / latency_ms`, 与实际 (`q: str | None` + `a: str | None`) 完全不符。即便 task15 恢复, audit 字段集需重写为 `q / a` 平行数组风格, 不复用 "逐 chunk 写 trace" 模型。 |
| Spec 引用范围 | task15.md:28 引用 `2026-06-10-python-rag-pipeline.md` lines 3627-3783。**该 plan 文件仅 505 行**, 引用行号无效; spec 文本 §7.0.3 / §7.7 在重构 plan 中未迁移, 无法直接核对。 |

**Headline P0**: task15.md 的 `RetrievalAudit.record` 设计为逐请求写 JSONL, 但 FastGPT 没有这种模式, rag-pipeline 也没有持久化层 (无 Mongo, 无本地 DB) — 该实现会**新增一个 FastGPT 不存在的 side-effect**, 需要先回答 "rag-pipeline 是不是要比 FastGPT 多一套本地调试审计" 这个产品问题, 否则 P0 阻塞无法消除。

---

## 1. FastGPT 现状 (与 "审计 / 引用校验" 相关的代码)

### 1.1 三套并存的可观测通道 (无 JSONL 旁路)

| 通道 | 位置 | 形态 | 检索侧是否使用 |
|---|---|---|---|
| 业务审计 (operationLogs) | `packages/service/support/user/audit/schema.ts` + `packages/global/support/user/audit/constants.ts` | Mongo 集合, 字段: `tmbId / teamId / timestamp / event (enum) / metadata`。事件枚举: `LOGIN / CREATE_APP / CREATE_DATASET / UPDATE_DATASET / ...` 共 50+ 业务动作。**与检索 trace 无关**。 | 否 — 事件枚举中没有 SEARCH / RECALL / RERANK 之类 |
| OpenTelemetry span | `packages/service/common/tracing/client.ts:96-130` (`withActiveSpan`); 入口 `packages/service/common/tracing/index.ts` 导出 `getTraceLogContext / setSpanError` | 跨调用的 active span, 由 `configureTracingFromEnv` 注入 OTel exporter。Span 名称 + attributes, sample ratio 默认 prod 0.01, dev 1.0。 | `grep -rn "withActiveSpan" packages/service/core/dataset/` → **0 hit**。检索侧未挂 trace span。 |
| workflow `nodeResponse` 元数据 | `packages/service/core/workflow/dispatch/dataset/concat.ts:50-53` | dispatch 返回结构里 `nodeResponse: { concatLength: number }` 等键值; 写入 chat item 落库时存为 response 节点 metadata。 | 是 — 是 FastGPT 唯一 "检索完成事实" 的结构化痕迹; 但不可流式 tail, 不分 query 维度, 只按 chat item 维度。 |

**JSONL 文件审计 = 0 处**。`grep -rln "jsonl\|JSONL" packages/` → 0 hit (仅 i18n JSON 文件)。`grep -rln "audit_log" packages/` → 0 hit (仅 i18n)。`grep -rln "side.channel\|observ" packages/service/core/dataset/` → 0 hit。

### 1.2 业务审计事件枚举摘录 (对照检索侧)

`packages/global/support/user/audit/constants.ts:32-` `AuditEventEnum` 摘录:
```ts
LOGIN, CREATE_INVITATION_LINK, JOIN_TEAM, CHANGE_MEMBER_NAME, KICK_OUT_TEAM,
CREATE_APP, UPDATE_APP_INFO, MOVE_APP, ...
CREATE_DATASET, UPDATE_DATASET, DELETE_DATASET,
CREATE_COLLECTION, UPDATE_COLLECTION, ...
UPLOAD_FILE, UPLOAD_FILE_LINK, ...
SYNC_VECTOR, SYNC_VECTOR_START, SYNC_VECTOR_SUCCESS, SYNC_VECTOR_FAILED
```

**`SYNC_VECTOR_*` 是入向 (ingest) 的审计**, 唯一与 dataset 沾边的事件, 但语义是 "把数据灌入向量库", 不是 "检索结果记录"。

### 1.3 引用的产出与流转 (无后端校验)

引用的 schema (`packages/global/core/dataset/type.ts:407-455`):

```ts
SearchDataResponseItemSchema: {
  id, updateTime, q, a, chunkIndex, datasetId, collectionId, sourceId, sourceName,
  score: z.array(z.object({ type, value, index })),   // typed score
  ...
}
SearchDataResponseQuoteItemSchema: pick(id, chunkIndex, datasetId, collectionId, sourceId, sourceName, score)
DatasetCiteItemSchema: { _id, q, a, imagePreivewUrl, history, updateTime, index, updated }
```

`SearchDataResponseQuoteItemType` 是渲染给前端的对象, `DatasetCiteItemType` 是历史引用落库形状 (按 `_id` 存)。**两者都不是校验语义**。`score` 数组里 `index` 字段提供排序位, 但 LLM 生成 `[1] [2]` 占位时, FastGPT 端**没有任何代码去检查 LLM 的输出引号是否越界** (例如 LLM 写了 `[5]`, 但 `quoteList` 只有 3 个)。`grep -rln "validateCitation\|checkCitation\|invalid.*cite" packages/` → 0 hit。

LLM 端 prompt 模板 (`packages/global/core/ai/prompt/dataset.const.ts`, `getDatasetSearchToolResponsePrompt`) 在 system prompt 里直接告诉 LLM "引用编号对应上方提供的资料", 不做后端越界检查。

### 1.4 dispatch 流水线的 `nodeResponse` (替代审计的次优解)

`packages/service/core/workflow/dispatch/dataset/concat.ts` (line 30-54, 引用上文):

```ts
const rrfConcatResults = datasetSearchResultConcat(
  quoteList.map((list) => ({ weight: 1, list }))
);
return {
  data: { [NodeOutputKeyEnum.datasetQuoteQA]: await filterSearchResultsByMaxChars(rrfConcatResults, limit) },
  [DispatchNodeResponseKeyEnum.nodeResponse]: { concatLength: rrfConcatResults.length }
};
```

类似地 `dispatchDatasetSearch` 在 `packages/service/core/workflow/dispatch/dataset/search.ts` 也有 `nodeResponse` 键记录每次检索。**这是 FastGPT 把 "检索发生过 + 用了多少 token" 落库的唯一通道**, 但**不分 query 维度**, 不可 tail, 不可按 dataset 维度聚合, 不可读出 latency。

### 1.5 OpenTelemetry: 装了但检索侧未用

`packages/service/package.json` 依赖 `@fastgpt-sdk/otel`, `packages/service/common/tracing/` 完整提供 `configureTracing / withActiveSpan / setSpanError` API。但**检索核心代码 `packages/service/core/dataset/search/` 内 0 处调用 `withActiveSpan`**。这是 FastGPT 当前已知 gap: OTel 已就位, 检索层未挂。

这是与 rag-pipeline task15 设计**有重叠**的一点 — 两者都在试图补 "检索 trace" 通道, 但 FastGPT 的修法是 span (跨调用), rag-pipeline task15 的修法是 JSONL (单文件), 二者不互斥。

---

## 2. rag-pipeline 当前状态

### 2.1 文件系统核实

```
$ ls /Users/jung/pro/rag-pipeline/src/rag/retrieval/
__init__.py
__pycache__/
trace.py
```

```
$ find /Users/jung/pro/rag-pipeline -name "audit.py" -o -name "citation_check.py" -o -name "test_citation_check.py"
(无结果)
```

```
$ ls /Users/jung/pro/rag-pipeline/tests/unit/
chunker/  conftest.py  core/  domain/  ingest/  normalizer/  reader/
test_cache_keys.py  test_cache_metrics.py  test_cache_settings.py
test_domain.py  test_llm_config.py  test_rag_error.py  test_rerank.py
```

确认 task15.md 顶部 "实际实现" 段描述:
- `src/rag/retrieval/` 下无 `audit.py` / `citation_check.py` — **属实**。
- `tests/unit/` 下无 `test_citation_check.py` — **属实**。
- 旁路审计能力由 `src/rag/retrieval/trace.py::RetrievalTrace` 承担最小子集 — **属实** (但需注意: `RetrievalTrace` 是 `remove_duplicates` 的去重键载体, 不是审计 sink)。

### 2.2 `RetrievalTrace` 实际签名 (与 task15 旧描述冲突)

`src/rag/retrieval/trace.py:34-47` (前文已读):
```python
@dataclass(frozen=True)
class RetrievalTrace:
    q: str | None = None
    a: str | None = None
```

`task15.md:11` (旧描述, 已删除, task15.md:26 提到) 说字段为 `query / dataset_id / chunk_id / score / source / latency_ms` — **6 个字段全错**。当前实际只有 2 个字段 (q / a), 用于 `remove_duplicates` 的 `(q, a)` 元组去重。

这意味着即便 task15 恢复, `RetrievalAudit.record` 写入的 `query / dataset_id / chunk_id / score / source` 等字段**没有上游来源**:
- `dataset_id / chunk_id / score / source` 在 `ScoredDocument` 上有 (且与 `RetrievalTrace` 平行数组对齐);
- `query` 在 `RetrievalTrace.q` 上有, 但**该字段是 query 变体** (task13 QueryExtension 的子查询), 不一定是顶层 `SearchRequest.query`;
- `latency_ms` 在 `RetrievalTrace` 上**没有**对应字段, 需要 caller 自行测时。

### 2.3 `SearchRequest.audit` 开关已埋, 但 producer 缺失

`src/rag/domain/search.py:55-65`:
```python
class SearchRequest(BaseModel):
    query: str
    dataset_ids: list[uuid.UUID]
    image_urls: list[str] = []
    use_global_rerank: bool = False
    audit: bool = False   # <-- 顶层开关已定义
    ...
```

`audit: bool` 字段已存在, 但 `grep -rn "req.audit\|\.audit" src/rag/` → **0 hit**。开关**没有 producer** (即没有任何代码读它), 也没有 consumer (没有任何代码检查它), 是死字段。

### 2.4 `SearchResult` schema 已就位 (audit 的输入)

`src/rag/domain/search.py:85-91`:
```python
class SearchResult(BaseModel):
    citations: list[Citation]
    prompt: str
    failed_dataset_ids: list[uuid.UUID] = []
    warnings: list[str] = []
```

`task15.md:290-291` (Step 4 注释) 说 `record(query, result, ...)` 读 `result.citations / failed_dataset_ids / warnings` — **这三者在 schema 上都有, 但 `SearchResult` 没有任何代码生产它**。它是 "Task 14 build_full_pipeline" 完成后才能被填实的占位类型。

### 2.5 `Citation` DTO 与 task15 测试兼容

`src/rag/domain/search.py:79-85`:
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

task15.md:103-106 (测试夹具) 用 `Citation(chunk_id, dataset_id, source_name, content, score)` 5 个必填 + `update_time` 可选 — **字段名一致, 顺序一致, 5 个测试可以照搬跑过**。这与 task11 的 fusion 那种 "类型根本不兼容" 不同, citation check 这块**接口对齐是 OK 的**。

### 2.6 `ScoredDocument` 已剥离 `q / a`

`src/rag/domain/document.py:49-60` (task11 audit 阶段已读), 字段集 `chunk_id / dataset_id / text / score / rank / source / modality / image_path / metadata / embedding / rerank_score`, **没有 q / a**。这与 `RetrievalTrace` 的 "平行数组" 模式一致: chunk 形状不绑 query, query 上下文走 `RetrievalTrace`。

---

## 3. task15.md 关键声明清单

| # | Claim (file:line) | Concrete content |
|---|---|---|
| C-1 | task15.md:3 | 状态: SCOPED OUT (2026-06-13 同步) — 重构分支未交付 |
| C-2 | task15.md:7-8 | "未交付。`src/rag/retrieval/` 下只有 `__init__.py` / `trace.py`, 不存在 `audit.py` / `citation_check.py`" — 经核实属实 |
| C-3 | task15.md:9 | "重构 plan 2026-06-11-chunker-reader-refactor.md 重新编号任务:其 Task 15 = Chunker 入口" — 待 cross-check, 见 §4 G-P2-1 |
| C-4 | task15.md:11 | "旁路审计相关能力由 `trace.py::RetrievalTrace` 承担最小子集 (仅 `q` / `a` 字段)" — 属实, 但需注意 `RetrievalTrace` 不是 audit, 是 dedup 键 |
| C-5 | task15.md:14 | "原 `RetrievalTrace` 字段为 `query / dataset_id / chunk_id / score / source / latency_ms`" — **过期**; 实际是 `q / a` |
| C-6 | task15.md:14 | "ReaderError.code 按 6 域分组" — **过期**; 实际拆 5 个 StrEnum |
| C-7 | task15.md:28 | 引 plan `2026-06-10-python-rag-pipeline.md` lines 3627-3783 — **行号无效** (该 plan 仅 505 行) |
| C-8 | task15.md:43 | spec §0.1: "主流水线 Cite 之后旁路挂载 `RetrievalAudit`, 写 `audit_log.jsonl`" — 找不到 spec 原文 (lines 3627+ 不存在) |
| C-9 | task15.md:44 | spec §7.0.3: "`trace(query, result) -> AuditRecord`, 写 jsonl 文件, CLI `rag audit --last=20`" — 同样找不到 spec 原文 |
| C-10 | task15.md:45 | spec §7.7: "引用校验工具, 工具而非流水线节点, 由 caller 在 LLM 生成回答后显式调用" — 找不到 spec 原文 |
| C-11 | task15.md:51-84 | Step 0 stub: `RetrievalAudit` + `CitationCheckResult` + `CitationChecker` 占位签名 |
| C-12 | task15.md:92-153 | Step 1: 5 个 TDD 测试 (normal / hallucinated / unused / comma / space-separated) |
| C-13 | task15.md:171-223 | Step 3: `CitationChecker.check(llm_response, citations) -> CitationCheckResult`, 4 项指标 recall/precision/hallucinated/unused, H6 regex `\[([\d,\s]+)\]` |
| C-14 | task15.md:238-258 | Step 4: `AuditRecord` Pydantic 模型, 12 字段 (ts/query/query_variants/per_dataset/cache_hits/global_ranking/final_citations/failed_dataset_ids/warnings/citation_count/latency_ms) |
| C-15 | task15.md:261-329 | Step 4 续: `RetrievalAudit.record(...)` 异步方法, 写 `Path.open("a")` 追加 JSONL |
| C-16 | task15.md:322-328 | `tail(n=20)` 读最近 n 行, 给 Task 17 CLI `rag audit --last=20` 用 |
| C-17 | task15.md:344-351 | commit 标题 "feat(retrieval): audit (jsonl trace) + citation checker" |

---

## 4. 三向差异矩阵

| Aspect | task15.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **任务状态** | SCOPED OUT 2026-06-13, 重构分支未交付 | **状态属实**, 与文件树一致 | N/A (FastGPT 永久实现状态) |
| **JSONL 旁路 audit sink** | `audit_log.jsonl` 文件, `Path.open("a")` 追加 | 无; `trace.py` 仅是 dedup 键, 不写盘 | **无**。FastGPT 没有这个模式, 也未使用过 |
| **业务审计通道** | 未涉及 | 无 Mongo, 无 audit collection | `operationLogs` Mongo 集合, 50+ `AuditEventEnum`, 唯独不含 SEARCH 检索 trace |
| **OpenTelemetry 跨调用 trace** | 未涉及 | 无 OTel SDK 依赖 | 已装 (`@fastgpt-sdk/otel`), 但**检索侧 0 调用** `withActiveSpan` |
| **workflow `nodeResponse` 元数据** | 未涉及 | 无 workflow runtime, 无 nodeResponse | `concat.ts:50-53` 等 4 处落库 metadata, 唯一 "检索完成事实" 痕迹 |
| **Citation 引用校验 (后端)** | `CitationChecker.check(llm_response, citations)`, recall/precision/hallucinated/unused 4 指标 | 无 `citation_check.py`, 但 `Citation` DTO 字段对齐, 测试可直跑 | **无后端校验**。LLM 端 prompt 约束, 不做越界检查; 前端只渲染 |
| **Citation 引用越界占位** | `hallucinated_citations: list[str]` 含 `"<invalid:n>"` | N/A | N/A |
| **`RetrievalTrace` 字段集** | task15.md 旧描述 6 字段 (`query / dataset_id / chunk_id / score / source / latency_ms`), 已被标过期 | 实际 `q: str \| None` + `a: str \| None` (dataclass, frozen) | `SearchDataResponseItemType` 含 `q / a / datasetId / score / ...` 在 *item* 上, 平行数组模型无对位 |
| **Audit 字段集 (12 字段)** | `ts / query / query_variants / per_dataset / cache_hits / global_ranking / final_citations / failed_dataset_ids / warnings / citation_count / latency_ms` | 无 audit schema; `SearchResult` schema 含 `failed_dataset_ids / warnings / citations`, 与 audit 字段集部分重叠 (3 字段) | N/A |
| **SearchRequest.audit 开关** | 未提及 (task15 文档没说) | `audit: bool = False` 已埋在 `SearchRequest` (search.py:64) | 无 — FastGPT 检索入口无 `audit` 标志位 |
| **异步 vs 同步** | `RetrievalAudit.record` 标 `async def`, 但实现内是同步 `Path.open("a").write(...)` | N/A | OTel span 是同步返回; Mongo 审计写入是同步 await |
| **持久化 (file vs DB vs in-mem)** | 单文件 JSONL, 无锁 (L6 trade-off 文档化) | N/A | Mongo 集合 (业务审计) / OTel 后端 (OTLP) / 落库 nodeResponse |
| **CLI `rag audit --last=20`** | Task 17 依赖, 读 `tail(20)` | 无 CLI | 无 CLI (仅 admin 后台展示) |
| **stub-first 模式** | Step 0 stub `record` 空实现 + `check` 抛 `NotImplementedError` | N/A | N/A |
| **Pydantic model** | `AuditRecord` / `CitationCheckResult` 均为 Pydantic `BaseModel` | `ScoredDocument` / `Citation` / `RetrievalConfig` 均为 Pydantic; 风格一致 | N/A (TypeScript + Zod) |
| **path 一致性** | `src/rag/retrieval/{audit.py, citation_check.py}` + `tests/unit/test_citation_check.py` | `src/rag/retrieval/` 下无 audit / citation_check, `tests/unit/` 下无对应测试 | `packages/service/support/user/audit/` + `packages/global/support/user/audit/` 双层 (Mongo + constants) |
| **正则 `\[([\d,\s]+)\]`** | H6 修正, 支持 `[1]` `[1,2,3]` `[1, 2, 3]` | N/A | 无对位 |

---

## 5. 修复建议 (P0 → P1 → P2 → P3, 每条带具体文件:行号)

### P0 (产品决策 / 阻塞决策)

#### G-P0-1: JSONL 旁路 audit 在 FastGPT 缺位的前提下, rag-pipeline 是否需要新增?
**Where:** task15.md:43-45 (spec §0.1 / §7.0.3 引用), task15.md:319-321 (`Path.open("a")` 实现)。
**Problem:** FastGPT 用 3 套并存通道 (Mongo 业务审计 / OTel / nodeResponse), 但**没有任何一处写 JSONL 旁路**。rag-pipeline 引入 JSONL 旁路意味着新增一个 FastGPT 不存在的 side-effect, 这与 "借鉴 FastGPT" 的项目定位形成张力。可能解释:
- (a) rag-pipeline 把 "本地 debug 审计" 视为 FastGPT 缺位的简化补足, 不期望 production 用;
- (b) 计划后期接入 OTel (FastGPT 已装 SDK), JSONL 临时;
- (c) 计划后期接入 Mongo 业务审计, JSONL 临时;
- (d) rag-pipeline 是 demo, JSONL 是 demo 工具, 不考虑 production。

task15.md 没回答这个问题。**不解决 P0-1, audit 实现无法评审通过**。
**Why P0:** 写盘路径在 L6 trade-off 注释里被明确接受 ("并发写可能行交错, production 可加 fcntl.flock"), 但**没有说明"非 production 走什么"**。如果是非 production 调试用, JSONL + tail 够用; 如果是 production 也要用, JSONL 不可接受, 需要走 OTel / Mongo 通道。决策影响所有 P1+ 实现的取舍。
**Fix options:**
- **Option A (推荐):** 在 spec call-out 显式声明 "JSONL 是本地 debug 工具, production 路径在 §X.Y (后续 spec) 定义, JSONL 路径 L6 trade-off 仅适用于 dev"。不阻塞实现, 但留 hook。
- **Option B:** 改走 OTel SDK (`opentelemetry-sdk` Python), 复用 FastGPT `withActiveSpan` 模式 (虽然实现语言不同, 但通道一致)。需要新加 `requirements.txt` 依赖。
- **Option C:** 完全 SCOPED OUT, 等 OTel/Mongo 通道在 rag-pipeline 立项后再恢复。
- **Recommended:** Option A (最小变更) 或 Option C (彻底 SCOPED OUT)。当前 SCOPED OUT 状态 (Option C 弱化版) 是**合理 default**, 维持即可。

#### G-P0-2: spec 引用行号 3627-3783 不存在
**Where:** task15.md:28。
**Problem:** 引 `2026-06-10-python-rag-pipeline.md` lines 3627-3783 — **该文件仅 505 行**。整段引用无效。task15.md 顶部 "历史溯源" 段已自行警告 "行号仅供历史溯源, 该 plan 当前版本仅 505 行, 原 §7.7 / §7.0.3 章节文本在重构 plan 中未迁移"。但警告不解除引用无效的事实。
**Why P0:** 评审人无法从引用定位 spec 原文, 验证 task15 字段集 / 行为是否对齐 spec。这是审计的"无法 reproduce citation check" 阻塞。
**Fix:** 把 spec 引用从行号改为文件名 + 章节标题 (无行号):
```
Spec 引用:
- §0.1 流水线全景图 (主 plan 设计阶段文本, 重构 plan 未迁移, 文本已不可考)
- §7.0.3 检索审计 (同上)
- §7.7 引用校验工具 (同上)
```
或从 git history (`git log -p -- docs/superpowers/plans/2026-06-10-python-rag-pipeline.md`) 找回原 §7.0.3 / §7.7 文本, 单独存档为 `.agents/design/2026-06-10-spec-extracts.md` 备查。
**Recommended:** 第二个方案 (提取存档), 因为 task15 现状的全部"该有 12 字段" / "该有 4 指标" 都是从原 spec 推, 原文丢失后, 字段集是否完整无依据。

#### G-P0-3: `RetrievalTrace` 字段集与 task15 旧描述不一致
**Where:** task15.md:14 (旧描述) vs `src/rag/retrieval/trace.py:34-47` (实际) vs `src/rag/domain/document.py:49-60` (ScoredDocument)。
**Problem:** task15 旧描述说 `RetrievalTrace` 字段为 `query / dataset_id / chunk_id / score / source / latency_ms` — 实际只有 `q / a`。如果 task15 恢复, `RetrievalAudit.record` 写入的 `query` 字段**没有直接来源**:
- `q` 是 query 变体 (来自 task13 QueryExtension 的子查询, 不一定是顶层 query);
- `query` (顶层) 来自 `SearchRequest.query`;
- `latency_ms` 在 `RetrievalTrace` 上**完全没有**, 需要 caller 自行测时, 并在 `record` 时传入。
**Why P0:** 字段集对不上, 实现者会无意识地从 `ScoredDocument` 派生, 制造一个 "新的 audit 字段集" 与 "trace 平行数组" 双重结构, 后续 cleanup 困难。
**Fix:** task15 恢复前, 重写 Step 4 注释 (task15.md:286-296) 明确每个 audit 字段的来源:
```
query:        SearchRequest.query (顶层)
query_variants: RetrievalTrace.q 的 unique list (从所有 trace 聚合)
per_dataset:   caller 自行从 SearchResult.citations.groupby(dataset_id) 聚合
cache_hits:   caller 注入 (Cache 层未实现, 默认全 False)
global_ranking: 来自 inter_dataset_fusion 输出 (task 11 未实现, 默认 [])
final_citations: SearchResult.citations
failed_dataset_ids: SearchResult.failed_dataset_ids
warnings:     SearchResult.warnings
latency_ms:   caller 测时, dict[stage_name, ms]
```
**Recommended:** 等 task14 落地后, 在 task15 Step 4 重写注释前, 同步修正此处 (P0 转 P1)。

### P1 (实现 / 接口差异)

#### G-P1-1: `CitationChecker` 接口与 FastGPT prompt 约束不对位
**Where:** task15.md:79-83 (stub) + :187-190 (实现 docstring) + :196 (check 方法签名)。
**Problem:** `CitationChecker.check(llm_response, citations)` 假设:
- `llm_response` 文本含 `[n] [n,m,k]` 引用占位;
- `citations` 是按展示顺序排列的引用列表 (1-based index)。
这与 FastGPT prompt 约束 (`getDatasetSearchToolResponsePrompt`) 不一致 — FastGPT prompt 实际用类似 `【1】` (方括号中文) 或 `[1]` (ASCII) 都可能, 且 `score.index` 是 0-based 排序位, 不是 1-based 引用号。rag-pipeline 这边 `Citation` 没有 `index` 字段, 等于默认 "list 顺序 = 引用号"。

**Why P1:** 如果 LLM prompt 里写的是 `【1】` (中文方括号), `\[([\d,\s]+)\]` 不会匹配, recall=0, precision=0, 误判为完全幻觉。当前实现是 "技术正确" 但 "产品不对" — 应当与 prompt 约束同步。
**Fix options:**
- (a) regex 扩展为 `[\[【]([\d,\s]+)[\]】]`, 兼容中英文方括号;
- (b) 在 prompt 层强制 `[n]` 格式, 并文档化;
- (c) Citation DTO 加 `index: int` 字段, 显式声明引用号 (1-based)。

**Recommended:** (b) + (c) 组合。task14 prompt 模板与 task15 CitationChecker 同步定。

#### G-P1-2: `AuditRecord` 12 字段过多, 与 rag-pipeline 现状差距大
**Where:** task15.md:238-258 (AuditRecord 定义)。
**Problem:** 12 字段中至少 5 个没有现成 producer:
- `query_variants` — task13 (QueryExtension) 未实现;
- `per_dataset` — task11 (Fusion) 未实现, 无法分组;
- `cache_hits` — `Cache` 抽象刚起步 (test_cache_*.py 3 个), 没有 L1-L4 实现;
- `global_ranking` — task11 (Fusion) 未实现, `inter_dataset_fusion` 仍是空头;
- `latency_ms` — 无 stage 测时框架。

5 字段在实现时**全部会默认空** (`Field(default_factory=...)`), 写出的 audit record 永远是 "12 字段中 5 字段空" 的稀疏对象, 对后续 query_ext / metrics 聚合无信息量。
**Why P1:** 不会让代码跑不起来, 但会让 audit log "看起来全, 实际空", 误导后续读者。
**Fix:** 把 AuditRecord 拆成 2 阶段:
- **Phase 1 (与 task15 一起交付):** 7 字段 — `ts / query / final_citations / failed_dataset_ids / warnings / citation_count / latency_ms`。这 7 个都有现成 producer (或可低成本补 producer)。
- **Phase 2 (等 task11/13/cache 落地后):** 加 `query_variants / per_dataset / cache_hits / global_ranking` 4 字段, 通过 AuditRecord model versioning (`model_config = ConfigDict(version="v2")`) 区分。

**Recommended:** Phase 1 拆解, 配套 task15.md:238-258 文档化 "Phase 2 字段保留 schema 位但当前 default 空"。

#### G-P1-3: `SearchRequest.audit` 死字段
**Where:** `src/rag/domain/search.py:64` (`audit: bool = False`)。
**Problem:** 字段已存在, 无 producer / consumer。
**Why P1:** 与 G-P0-1 联动。如果 P0-1 决定走 JSONL, `audit: bool` 应当被 `RetrievalAudit.record(...)` 调用点 (即主流水线 Cite 之后) 读取; 如果 SCOPED OUT, 字段保留 (default False) 即可, 不算 bug。
**Fix:** 取决于 P0-1 决策:
- 走 JSONL: 主流水线加 `if req.audit: await audit.record(...)`, 给 P0-1 留出口;
- SCOPED OUT: 字段保留, 加 docstring "Reserved for future audit (task15, currently SCOPED OUT)"。

### P2 (文档 / 重构 plan 对齐)

#### G-P2-1: 重构 plan 把 Task 15 重新分配为 Chunker, 旧 task15 应在 plan tree 里彻底删除而非 SCOPED OUT 注释
**Where:** task15.md:9-10 ("重构 plan `2026-06-11-chunker-reader-refactor.md` 重新编号任务:其 Task 15 = Chunker 入口")。
**Problem:** 当前 task15.md 既保留旧 "Audit & CitationChecker" 内容 (steps 0-7, ~350 行), 又在 status 行标 SCOPED OUT。读者 (尤其是 reviewer) 会困惑: 这是 "任务存在但暂停" 还是 "任务被新任务替代"? 两种处理方式各有利弊:
- "保留 + SCOPED OUT": 历史可追溯, 实现细节有备份;
- "彻底删除 + 指 refactor plan": 干净, 但失去 task15.md 的可重用价值 (5 个测试用例 + 1 个实现骨架)。
**Why P2:** 不是技术问题, 是 plan 治理问题。
**Fix:** 在 `2026-06-11-chunker-reader-refactor.md` 顶部加 "Tasks SCOPED OUT from 2026-06-10 plan" 段, 列出本 task15 + 引 task15.md, 标明 "保留 task15.md 是为了未来恢复时直接复用"。当前 task15.md 顶部 SCOPED OUT 注释可保留, 但要明确说 "保留依据: refactor plan X.Y 段"。

#### G-P2-2: 过期字段描述与实际不一致
**Where:** task15.md:14 ("`RetrievalTrace` 字段为 `query / dataset_id / chunk_id / score / source / latency_ms`"), task15.md:14 ("ReaderError.code 按 6 域分组")。
**Problem:** 6 字段 `query / dataset_id / chunk_id / score / source / latency_ms` 与实际 `q / a` 2 字段不符; 6 域 `encoding / parse / not_found / permission / too_large / unsupported` 与实际 `ReaderErrorCode` 9 值 (`src/rag/error_codes.py`) 不符。task15.md 自身 status 段已标 "过时期描述修正", 但**正文里这两句没改**。
**Why P2:** 误导读者。当前 status 段已写明 "统一在此处标 SCOPED OUT", 但正文里残留旧描述, 一旦 reviewer 跳读会拿到错误信息。
**Fix:** 在 task15.md 顶部 "状态: SCOPED OUT" 段加粗: "**正文 §Step 0-7 中的字段名 (`query / dataset_id / chunk_id / score / source / latency_ms`) 与当前 `src/rag/retrieval/trace.py` 不符, 仅供参考; 恢复时必须重写 Step 4 `record` 方法的字段映射注释**"。

#### G-P2-3: Step 0 stub 的 `record` 标 `async def` 但实现内是同步写盘
**Where:** task15.md:59 (`async def record(self, query, result, latency_ms=None): pass`)。
**Problem:** `pass` 看不见, 但 Step 4 (task15.md:275) 实际是 `with self.log_path.open("a") as f: f.write(...)` — 同步阻塞 I/O。标 `async def` 会让 caller `await audit.record(...)` 等待, 期望非阻塞, 实际阻塞主线程。
**Why P2:** 旁路审计的本意是 "不阻塞主流程" (spec §0.1), 同步 I/O 写盘 + JSONL append 模式 + 1 次请求 1 次 fsync = 每次检索多 0.5-2ms 阻塞。production 量级会显著拖慢主流水线。
**Fix options:**
- (a) 改同步 `def record(...)`, caller 改 `audit.record(...)` (不带 await), 文档化 "record 同步阻塞, 由 caller 决定是否包 `asyncio.to_thread`";
- (b) 改 `async def record(...)` 但内部 `await asyncio.to_thread(self._sync_record, ...)`, 保持 async 接口;
- (c) 走 `aiofiles` 依赖。
**Recommended:** (b)。接口稳定, 实现非阻塞。

### P3 (nice-to-have / 实现细节)

#### G-P3-1: `tail(n)` 全文件读对小 log 可接受, 大 log 会 OOM
**Where:** task15.md:322-328 (`tail(self, n=20)` 实现)。
**Problem:** `lines = f.readlines()` 一次性读全文件, 然后 `lines[-n:]`。`audit_log.jsonl` 在 production 量级 (1k QPS × 24h × 30d = 25 亿行) 会爆内存。当前文档说 "debug 规模可接受" — 与 P0-1 决策联动即可。
**Why P3:** 与 P0-1 决策相关。如果 P0-1 选 Option A (本地 debug 工具), 接受; 如果选 Option B (走 OTel), 此问题自然消解。
**Fix:** 待 P0-1 决策后定。

#### G-P3-2: `CitationCheckResult` 用 Pydantic 但 `hallucinated_citations: list[str]` 用占位符而非真实越界对象
**Where:** task15.md:182 (字段定义) + :221 (实现 `f"<invalid:{i+1}>"`)。
**Problem:** 设计为 `list[str]` (占位符) 而非 `list[Citation]` 或 `list[int]`, 与 `unused_citations: list[Citation]` 形状不对称。设计理由 (task15.md:33) 是 "越界时没有真实 Citation 可引用", 逻辑 OK, 但 Pydantic 序列化时, 后续消费者需要 parse `f"<invalid:n>"` 字符串, 反序列化体验差。
**Why P3:** 不影响功能, 但影响可观测性。
**Fix:** 改 `list[dict]` 形态, 字段为 `{"raw": "[5]", "reason": "out_of_range", "expected_size": 3}`。更结构化, 但增加 schema 复杂度。trade-off 决定。

#### G-P3-3: `AuditRecord` 字段命名不一致 (`query` vs `query_variants`)
**Where:** task15.md:247-248 (`query: str` + `query_variants: list[str]`)。
**Problem:** `query` 是顶层, `query_variants` 是子查询, 命名 OK, 但 `query_variants: list[str]` 不与 `RetrievalTrace.q: str | None` 对齐 (`RetrievalTrace.q` 是单个变体, 平行数组模型下, query_variants 应是 `set(trace.q for trace in traces) if traces else []`)。直接 `list[str]` 类型不强制 unique, caller 容易传重复。
**Why P3:** 不影响功能, 增加类型严格性的小改进。
**Fix:** 加 `field_validator` 去重 + 排序。

---

## 6. 实施顺序 (SCOPED OUT 维持 / 恢复, 哪种推荐)

**Recommendation: 维持 SCOPED OUT。** 理由:

1. **FastGPT 没有 JSONL 旁路, rag-pipeline 引入是 design risk** (P0-1)。决策未消解前, 实现评审无法通过。
2. **Task 14 (build_full_pipeline) 尚未落地** — `SearchResult` schema 有但无 producer, audit 挂载点不存在。
3. **5 个 AuditRecord 字段无 producer** — 即便 task15 恢复, 写出的 audit record 永远是稀疏空对象, 价值有限。
4. **CitationChecker 5 个测试可独立保留** — 它们只依赖 `Citation` DTO, 不依赖主流程。`tests/unit/test_citation_check.py` 可单独保留为 "future feature test" 而无需实现, 这是 SCOPED OUT 状态下唯一可低成本保留的资产。

**如果必须恢复**, 顺序:
1. 解决 P0-1 (产品决策: JSONL 还是 OTel 还是 Mongo)
2. 解决 P0-2 (找回 spec 原文, 备查存档)
3. 解决 P0-3 (重写 `record` 字段映射注释, 对齐实际 `RetrievalTrace` / `ScoredDocument` / `SearchRequest`)
4. 实现 P1-1 (CitationChecker prompt 约束同步)
5. 拆 AuditRecord Phase 1 / Phase 2 (P1-2)
6. 决定 `SearchRequest.audit` 字段命运 (P1-3)
7. Step 0 stub → Step 1 RED → Step 3-4 GREEN → Step 6 commit (与 task11 同模式)
8. 引用 `task15.md:14` 过期描述清理 (P2-2)
9. 修 `record` async/sync 阻塞 (P2-3)
10. P3 收尾

恢复后, task15 仍是 P0-1 决策的派生任务 — 推荐维持 SCOPED OUT, 把 P0-1 决策延后到 "rag-pipeline 引入 OTel" 或 "rag-pipeline 引入 Mongo 业务审计" 的明确立项。

---

## Appendix A: FastGPT 审计通道全清单

| 通道 | 写入形态 | 检索侧 | Sample | Latency 字段 | CLI / 读法 |
|---|---|---|---|---|---|
| `TeamAudit` Mongo (`operationLogs`) | 50+ `AuditEventEnum`, 无 SEARCH 类 | 否 | 100% (业务事件) | 无 | admin 后台查询 |
| OTel span | 跨调用 active span | 否 (0 调用) | prod 0.01, dev 1.0 | span duration 自带 | OTel 后端 (Jaeger 等) |
| `DispatchNodeResponseKeyEnum.nodeResponse` | dispatch 返回 metadata 键 | 是 (4 处: dataset-search / dataset-concat / agent-sub-dataset) | 100% | 无 (无 stage 测时) | 落库到 chat item, 不可 tail |
| (JSONL 旁路) | — | — | — | — | — |

FastGPT 检索 trace 实际上**只通过 nodeResponse 留存**, 但 nodeResponse 不可按 query 维度检索, 不可聚合, 是 "存在但不可用" 的状态。`packages/service/common/tracing/client.ts` 提供 OTel 能力, 但**数据集检索核心代码 0 调用** — 这是一个未补的 gap, 与 task15 的"补 audit"目标在产品意图上一致, 但实现路径 (JSONL vs OTel) 不同。

## Appendix B: `SearchDataResponseItemType` 字段集 vs rag-pipeline `Citation` DTO

| FastGPT (`packages/global/core/dataset/type.ts:407-455`) | rag-pipeline (`src/rag/domain/search.py:79-85`) | Notes |
|---|---|---|
| `id: string` | `chunk_id: uuid.UUID` | 类型差异 (string vs UUID) |
| `q: string` | `content: str` | 命名差异 (q = question, content = 通用) |
| `a: string \| undefined` | (无) | FastGPT 有 Q+A 对, rag-pipeline 折成单一 content |
| `imageId / imageDescMap / imagePreviewUrl` | `image_path: str \| None` | 字段集不同 |
| `chunkIndex: number` | (无) | FastGPT 有序位; rag-pipeline 用 list 序 |
| `datasetId / collectionId / sourceId / sourceName` | `dataset_id: UUID` (+ `source_name: str`) | 缺 `collection_id` / `source_id` |
| `score: {type, value, index}[]` | `score: float` | 类型差异 (typed array vs single float) — 与 task11 fusion 的 G-P0-1 是同一个问题 |
| `updateTime: Date` | `update_time: datetime \| None` | OK |
| `indexes` (可选) | `metadata: ChunkMetadata` | FastGPT 走 dict 字段; rag-pipeline 走 Pydantic model |

Citation 字段集**基本可对位**, 但缺 `collection_id` / `source_id` / `q` / `a`。这意味着 task15 的 `CitationChecker.check(llm_response, citations)` 在 rag-pipeline 端**只看得到 `source_name` / `content` / `score`**, 无法展示 "来自哪个 collection", 影响 debug 价值。

## Appendix C: `CitationCheckResult` 4 指标定义精度

| 指标 | task15.md:196-223 定义 | 数学公式 | 边界 case |
|---|---|---|---|
| `recall` | "有效引用 / 总引用" | `len(valid_idx) / max(len(cited_idx), 1)` | `cited_idx=[]` → `recall=0/1=0` (空响应 recall=0, 不合理) |
| `precision` | "提供 citations 中被引用比例" | `len(used) / max(len(citations), 1)` | `citations=[]` → `precision=0/1=0` (空 citations precision=0, OK) |
| `hallucinated_citations` | 越界引用, `"<invalid:n>"` 占位 | `cited_idx - valid_idx` | `n=1, citations=[]` → 1 hallucinated |
| `unused_citations` | 提供但未引用 | `citations - used` | `citations=[a,b], cited=[1]` → unused=[b] |

**Recall 公式 0/0 兜底问题**: `cited_idx=[]` 时, `max(len(cited_idx), 1)` 让分母为 1, recall=0。但语义上, LLM 没引用应当是 "recall 不适用" (N/A) 而非 "recall=0"。task15.md:212 写法 (默认 0) 在 `test_normal_citation` 等正向用例看不出问题, 但在 "LLM 不输出任何引用" 的边界下会假报幻觉。
**Why P3:** 不影响 P0/P1 决策, 但实现后需要补一个 `test_no_citations_in_response` 用例, 显式确认 recall 期望值 (0 vs N/A)。

## Appendix D: `RetrievalTrace` 与 `ScoredDocument` 平行数组模型分析

```
list[ScoredDocument]      list[RetrievalTrace]
[doc0, doc1, doc2, ...]   [trace0, trace1, trace2, ...]
```

`remove_duplicates(docs, traces)` 用 `zip(docs, traces, strict=True)` 强制等长, 校验 `len(docs) != len(traces)` 抛 `ValueError` (`trace.py:67-69`)。

**task15 恢复时的 audit 写入方式冲突**:
- task15.md Step 4 设计的 `record(query, result, ...)` 接收 1 个 query + 1 个 SearchResult, 输出 1 条 audit record;
- 1 条 audit record 隐含把 1 个 SearchResult 的所有 `citations` 都归到 1 个 query 下, 而 `RetrievalTrace` 平行数组模型下, 1 个 query 可能产生 N 个 query 变体 (task13 QueryExtension), 每个变体各自召回到不同 docs;
- 如果 caller 把 N 个变体的 results 都传入 `record`, 需要 caller 自行合并 1 个 audit record (聚合 query_variants / per_dataset / cache_hits) — 这是 caller 的负担, 不在 task15 实现内。

**这是 P0-3 的细化**: `record` 字段映射注释必须显式说明 caller 责任。task15 当前没写。

---

## 总结

1. **SCOPED OUT 状态合理** — 与文件树一致, 与重构 plan 一致, 与产品决策未消解的现状一致。
2. **FastGPT 没有 JSONL 旁路** — 这是 rag-pipeline task15 引入的新模式, 不是 "FastGPT 缺位补足" 而更接近 "新增 side-effect", 需 P0-1 决策。
3. **`RetrievalTrace` 字段已简化 (q/a)** — task15 旧描述 6 字段全错, 恢复时 Step 4 注释必须重写, 不与 trace.py 现状对位会让 audit log 实际可写但语义含糊。
4. **`CitationChecker` 5 个测试与 `Citation` DTO 接口兼容** — 接口对齐 OK, 可作为 SCOPED OUT 状态下唯一可保留的资产 (`tests/unit/test_citation_check.py` 单独保留为 "future feature test")。
5. **`SearchRequest.audit` 是死字段** — 与 P0-1 决策联动, 维持或激活二选一。
6. **如要恢复, 先消解 P0-1 + P0-2 + P0-3** — 这三个是评审阻塞; P1/P2/P3 可在实现 PR 里顺带修。
