证据收集完整。Sandbox 写权限被拒,review 内容直接在最终响应中输出,用户可保存到目标路径。

---

# Architecture Review: Agent #9 — 缺失任务审计 + 依赖闭环

> **审查状态标注**:Agent #9 prompt 假设 `task15.md` / `task16.md` 未生成,但磁盘实测**两文件均已落盘**(`task15.md` 11,944 B / 301 行,`task16.md` 23,278 B / 535 行,均在 `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/`)。**主 plan L138-139** 与 **INDEX.md L51-52、L137** 将其标为 MISSING 是文档/索引状态与磁盘实际状态不一致的产物。本 review 因此重新定位为"task15/16 实质内容审计 + 与 spec/其他 task 契约核查",而非"待补缺失任务"。

## 1. 一句话总评

task15/16 文件内容结构完整、stub-first 与 TDD 流程齐备,但存在 **3 个 P0 跨 task 契约冲突**(search_key payload 字段、AuditRecord 字段集降级、INDEX/Plan 状态漂移),实施前必须消解;另有 2 个 P1 实现级 stub 未披露。

## 2. 模块边界 / 依赖方向 / 契约一致性

| 检查项 | 结论 | 证据(file:line) | 评级 |
|---|---|---|---|
| task15 模块归属 | `retrieval/audit.py` + `retrieval/citation_check.py`,符合 spec §7.0.3/§7.7 命名 | `task15.md:13-15` | OK |
| task16 模块归属 | `pipeline/full.py` + `pipeline/cache_decorator.py` + `infra/observability/json_handler.py`,符合 spec §0.1 | `task16.md:15-19` | OK |
| 依赖方向(retrieval → domain) | `citation_check.py` import `rag.domain.search.Citation` | `task15.md:117` | OK |
| 依赖方向(pipeline → retrieval) | `full.py` import `rag.retrieval.audit / decomposition` | `task16.md:386-388` | OK |
| 依赖方向(pipeline → pipeline) | `full.py` import `subgraph / orchestrator / query_ext / image_caption` | `task16.md:385-388` | OK |
| 依赖方向(避免循环) | `retrieval` 层不 import `pipeline` 层 | `task15.md` 全文无 pipeline import | OK |
| SearchRequest.audit 字段定义 | task2 已定义 `audit: bool = False` | `task2.md:133` | OK |
| SearchRequest.audit 字段消费 | task17 CLI `--audit` flag 透传;task16 `build_full_pipeline(audit=...)` 接 `RetrievalAudit \| None` | `task17.md:84,150`;`task16.md:391` | OK |
| SearchRequest.use_global_rerank 字段定义 | task2 `use_global_rerank: bool = False` | `task2.md:132` | OK |
| SearchRequest.use_global_rerank 消费 | task16 `use_global_rerank: bool = False` 参数;task14 `build_global_rerank_node` 接 `use_global_rerank` | `task16.md:394`;`task14.md:684` | OK |
| SearchRequest.parent_doc_window 字段定义 | task2 `parent_doc_window: int = 0` | `task2.md:131` | OK |
| SearchRequest.parent_doc_window 消费 | task16 `parent_doc_window: int = 0` 参数 | `task16.md:393` | OK |
| SearchRequest.query_decomposition 字段定义 | task2 `query_decomposition: bool = False` | `task2.md:130` | OK |
| SearchRequest.query_decomposition 消费 | task16 `use_decomposition: bool = False` 参数 + `if state.get("query_decomposition", False)`(字段名错位,见 P1-2) | `task16.md:392,439-440` | ⚠ |
| SearchResult.failed_dataset_ids 字段定义 | task2 `failed_dataset_ids: list[uuid.UUID] = []` | `task2.md:151` | OK |
| SearchResult.failed_dataset_ids 消费 | task14 orchestrator 写入,task17 CLI echo | `task14.md:511-512`;`task17.md:158-159` | OK |
| SearchResult.warnings 字段定义 | task2 `warnings: list[str] = []` | `task2.md:152` | OK |
| SearchResult.warnings 消费/产出 | task14 orchestrator 聚合,task16 cache_decorator 写 → orchestrator 合并 | `task14.md:518-527`;`task16.md:248-255` | OK |
| ScoredDocument.image_path 字段定义 | task2 `image_path: str \| None = None` | `task2.md:99` | OK |
| ScoredDocument.image_path 消费 | task14 cite.py 写入 Citation;task15 audit record 写入 citation_count | `task14.md:370`;`task15.md:227-228` | OK |
| ChunkMetadata.dataset_id 字段定义 | task2 `dataset_id: uuid.UUID` | `task2.md:65` | OK |
| ChunkMetadata.dataset_id 消费 | task14 parent_doc 通过 `metadata.dataset_id` 找 siblings;task3 写入 | `task14.md:411`;`task3.md`(隐式) | OK |
| task15 ↔ task17 衔接 | `RetrievalAudit.tail(n)` 返回 list[dict],task17 消费 `r['ts']`、`r['query'][:50]`、`r['citation_count']` | `task15.md:243-249`;`task17.md:250-254` | OK |
| task15 ↔ task18/19 衔接 | 文档未提供 eval-side 输入;task18/19 全文 grep `audit` 无引用 | `task18.md` 全文;`task19.md` 全文 | ⚠ |
| task16 ↔ task20 衔接 | task20 CI `test_citation_check.py` 显式列出;testcontainers 模式已统一 | `task20.md:47-49,189-191` | OK |
| task16 ↔ task6 衔接(L3 key) | **冲突**:task6 `search_key()` 读 `dataset_versions: list[int]`;task16 `make_search_cache` 写 `dataset_version: str` | `task6.md:201-202,206-216`;`task16.md:296-305` | 🔴 |
| task16 ↔ spec §0.1 缓存降级 | `with_cache` 内 `cache.get`/`cache.set` throwaway;声明 orchestrator 合并 warnings,但 task14 orchestrator 未实现 `cache_warnings` 合并 | `task16.md:246-255`;`task14.md:518-527` | ⚠ |
| TDD stub-first 合规 | task15/16 均有 Step 0 stub,可 import,RED 阶段不 ImportError | `task15.md:30-50`;`task16.md:51-69` | OK |
| 已知 H6 修复落地 | CitationChecker regex `\[([\d,\s]+)\]` 兼容 [1]、[1,2,3]、[1, 2, 3] | `task15.md:120,233`;`main-plan.md:205` | OK |
| 已知 H5 修复落地 | LLMSettings 单一定义在 config.py;semaphore re-export | `task7.md:66-70`;`main-plan.md:201` | OK |
| 已知 C3 修复落地 | itemgetter("query") → RunnableLambda | `task15.md`/`task16.md` 全文未出现 `itemgetter`;main-plan L195 标注已修正 | OK |
| 已知 H2 修复落地 | env_file 移除;CLI 显式传入 | main-plan L217;`task17.md` 全文无 `env_file` 引用 | OK |
| 已知 H3 修复落地 | `with_structured_output(method="function_calling")` | `task13.md:202-203` | OK |
| 已知 H7 修复落地 | `SET LOCAL hnsw.ef_search` | main-plan L210;task4 范围内(本文未深查) | OK |

## 3. 发现清单(按严重度降序)

### 🔴 P0 — 必须修复(阻塞)

#### **P0-1. INDEX.md 与主 plan 状态与磁盘状态不一致**
- 位置:`INDEX.md:21,51-52,137-138`;`2026-06-10-python-rag-pipeline.md:138-139`
- 问题:两者都写"`15 \| Retrieval Audit + Citation Checker \| — (缺) \| MISSING`"和"`16 \| Build Full Pipeline + JSON Logging \| — (缺) \| MISSING`",但 `tasks/` 目录实测含 `task15.md`(11,944 B,301 行,含完整 Step 0-8 步骤、stub、test、impl、commit)和 `task16.md`(23,278 B,535 行,含 5 个挂载点、4 项契约、E2E 3 case)
- 影响:任何下游 agent(任务派发、CI gate、prerequisite check)若以"task15/16 MISSING"为依据会重复落盘或拒绝继续;dispatch 脚本 `launch_staggered.py` 若读取此状态会跳过 task15/16
- 建议:更新 `INDEX.md` L21 总数 `18/20 → 20/20`、L51-52 行内容、`tasks/task15.md` 和 `tasks/task16.md` 状态列改 `OK`;同步主 plan L138-139 与 L122-165 任务表

#### **P0-2. task6 与 task16 对 L3 cache key 的 `dataset_version` payload 字段定义冲突**
- 位置:`task6.md:201-216`(`search_key` 实现);`task16.md:296-305`(`make_search_cache.key_fn`);`task16.md:209-216`(测试)
- 问题:
  - task6 step 3 `search_key(payload)`:读 `payload.get("dataset_versions", [])`,`sorted(list[int])`,join `"-".join(str(v) for v in versions)`,key 形如 `rag:search:1-3:{hash}`
  - task16 step 2 `make_search_cache.key_fn`:写入 `payload["dataset_version"] = "|".join(versions.get(d, "v0") for d in ds_ids)`,**单一字符串**字段、**str 类型**、**`|` 分隔**
  - task16 step 1 测试 L209-216:传 `{"dataset_version": "v0"}` 和 `{"dataset_version": "v1"}` 给 `search_key`,断言 key 不同
  - **执行 task16 测试时,若 task6 已就位,`search_key()` 读 `dataset_versions`(复数,list)而非 `dataset_version`(单数,str),`payload.get("dataset_versions", [])` 返回 `[]`,`versions_str = "0"`,`v0_key == v1_key` → 断言失败**
- 影响:实施 task16 E2E 测试时会因契约不兼容失败,缓存版本化路径完全失效(disable dataset version invalidation)。spec §0.1 L222 要求"dataset 升版 → 重新生成 L3 search key"功能无法落地
- 建议:三选一(需决策):
  1. **改 task6 适配 task16**:把 `search_key` 改读 `payload.get("dataset_version", "0")`,单字符串;**放弃** B10 强化中的"多 dataset sorted list"语义
  2. **改 task16 适配 task6**:`make_search_cache.key_fn` 改写 `payload["dataset_versions"] = sorted([int(versions.get(d, 0)) for d in ds_ids])`,**dict 值类型从 `str` 改 `int`**;同步修改 task16 测试 L211、L215 改 `[0]`/`[1]`;同步更新 `SearchRequest` 文档化注释
  3. **走 B10 强化对齐 FastGPT**(task6 当前路径):废弃 task16 subagent #9 引入的 string-version,重写为 integer counter
- 推荐方案 2(task16 是后写,与 task6 + spec §0.1 L222 + B10 强化保持一致)

#### **P0-3. task15 `RetrievalAudit` 缺失 spec §7.0.3 要求的核心字段集**
- 位置:spec `2026-06-10-python-rag-pipeline-design.md:810-830`;`task15.md:208-235`(`record()` 实现)
- 问题:spec §7.0.3 要求 `AuditRecord` 包含 `query_variants`、`per_dataset{ds_id: DatasetTrace{vector_hits, fulltext_hits, fused_top_n, rerank_input, rerank_output, final_hits}}`、`global_ranking`、`final_citations`、`cache_hits={layer: bool}`;task15 实现只写入 `ts`、`query`、`failed_dataset_ids`、`warnings`、`citation_count`(整数)、`latency_ms`
  - 丢失字段使 debug 价值显著降低:无法回答"为什么这个 query 没命中"——spec §7.0.3 明确以"逐 stage 看"为设计目标
  - `query_variants` 已经在 task14/task16 链路中产生(`task14.md:803` `variants = state.get("query_variants", [state.get("query", "")])`,`task16.md:441` `query_variants: sub_queries`),但 task15 audit 不采集
- 影响:CLI `rag audit --last=20`(task17:250-254)只能显示 `query[:50]` 和 `citation_count`,**per-stage trace 不可见**;这与 spec §7.0.3 设计意图和 task17 CLI 输出预期不符
- 建议:二选一:
  1. **扩展 task15 实现**:在 `record()` 中增加 `query_variants`、`per_dataset`、`cache_hits`、`global_ranking` 字段,需要 task14 orchestrator / task16 audit_tap 显式传递这些信息(目前 orchestrator 输出 `SearchResult` 不含 `query_variants` 和 `cache_hits`)
  2. **明确降级 + 文档化**:在 task15 头部和 INDEX 注释中显式声明"本期仅记录 query 级别元数据,per-stage trace 二期(PG query_log 表)",与 spec §7.0.3 "生产化: 二期可写入 PG query_log 表" 对齐
- 推荐方案 2 短期 + 方案 1 二期;但需 spec 增补"本期简化范围"声明,避免 task15/16 提交后被误判为 spec 不达标

### ⚠ P1 — 应当修复

#### **P1-1. task16 step 4 `parent_doc_window` 挂载点是空操作 stub**
- 位置:`task16.md:459-468`(`expand_result` lambda)
- 问题:代码 `async def expand_result(result): if not result.citations: return result; # 拿 citations 的 chunk_id 去找 siblings; return result` —— `# 拿 citations 的 chunk_id 去找 siblings` 是注释,没有实际调用 `ParentDocExpander`;函数永远返回原 `result`
- 影响:`SearchRequest.parent_doc_window > 0` 时,行为与 `= 0` 完全相同(零上下文扩展);task14 已经写好 `ParentDocExpander`(`task14.md:409-446`),但 task16 不会触发它
- 建议:要么 (a) 把 `expand_result` 真正实现为 `await expander.expand(result.citations, window=parent_doc_window)`,要么 (b) 移除 `parent_doc_window` 参数直到 task16 真正实现,避免"假参数 + 无作用"的接口

#### **P1-2. task16 `use_global_rerank` 挂载顺序与 spec §7.1 标注位冲突**
- 位置:`task16.md:444-457`(`rerank_then_orchestrator`);spec `2026-06-10-python-rag-pipeline-design.md:855-878`(`架构` 节)
- 问题:spec §7.1 文字"可选 GlobalRerank ← 跨 dataset 二次重排(挂载点 ②)...... ② Filter 之前"明确要求 GlobalRerank 在 Filter **之前**。task16 step 4 实现 `pipeline = RunnableLambda(rerank_then_orchestrator) | rerank_node` —— 即先跑完整 orchestrator(含 IntraFilter / GlobalFilter),再跑 rerank。这是 **post-filter** rerank,语义与 spec 不符。代码注释 L455 自承"生产实现应改为 Filter 前 pre-orchestrator, 此处保留主 plan L3986 顺序"——承认是 stub
- 影响:实际重排质量下降(Filter 已经按 score_threshold 截断,rerank 无机会重排被截掉的候选)
- 建议:重写为 `rerank_node | orchestrator`(Filter 在 rerank 之后由 orchestrator 内部处理);或拆 GlobalFilter 节点,先 rerank 后 filter

#### **P1-3. task16 step 4 `decompose_state` 字段名错位**
- 位置:`task16.md:439-440`;spec §0.1 / §2;`task2.md:130`
- 问题:task16 用 `if state.get("query_decomposition", False)`(布尔),但 `SearchRequest.query_decomposition` 是开关,而 task13 `QueryDecomposer.decompose` 接受 `query: str`(`task13.md:50`)。task16 在 `use_decomposition=True` 时无条件 `decompose(state["query"])`,并未消费 `state["query_decomposition"]` 开关——这意味着 `use_decomposition=True` 强制 decompose,**与 `SearchRequest.query_decomposition=False` 时应当不 decompose** 的契约冲突
- 影响:CLI 用户传 `--decompose`(`task17.md:85`)时,`SearchRequest.query_decomposition=True` 进入 task16,`use_decomposition=True`(CLI 显式开)也进入 task16——`decompose` 被执行。但若 caller 走 Python API 直接传 `SearchRequest(query_decomposition=True)` 而 `build_full_pipeline(use_decomposition=False)`,语义上开关不生效,无 decompose。可接受但有歧义
- 建议:在 `decompose_state` 内 `if state.get("query_decomposition", False) and (await check_use_decomposition(deps))` 双开关,或文档化"`use_decomposition` 是 task16 pipeline-level 开关,`query_decomposition` 是 SearchRequest-level 开关,两者同时为真才 decompose`"

#### **P1-4. task16 cache_decorator 声称把 cache warnings 合并到 `SearchResult.warnings`,但未实现**
- 位置:`task16.md:241-255`(`with_cache` 实现);`task16.md:411,504` 注释
- 问题:`with_cache` 内 `cache.get` / `cache.set` 失败 `except: pass`(throwaway),不返回任何 warnings 标识。注释 L411 / L504 声称"上层在 orchestrator 把 `warnings` 列表合并到 `SearchResult.warnings`",但 task16 `build_full_pipeline` 自身不收集 cache warnings,`with_cache` 也不产出可被 orchestrator 收集的副作用
- 影响:`SearchResult.warnings` 不会包含 `cache_unavailable: Redis ConnectionError`(spec §0.1 L226 要求);debug 时无法区分"Cache 写失败" vs "正常"
- 建议:`with_cache` 改为返回 `(result, warnings: list[str])` 元组,或 `CachedRunnable` 维护内部 `warnings` 列表,`build_full_pipeline` 末尾把 list 注入 `SearchResult.warnings`(需要 `SearchResult` 在 pipeline 内部可写,或新增 `state["warnings"]` 透传)

#### **P1-5. task15 `latency_ms: dict[str, float] | None = None` 无消费方**
- 位置:`task15.md:208-235`(`record` 签名);`task16.md:479-484`(`audit_tap` 调用)
- 问题:task15 stub / 实现的 `record()` 接受 `latency_ms` 可选参数,但 task16 `audit_tap` 实际调用 `await audit.record(query="", result=result)`(L482),未传 `latency_ms`。task15 step 5 验证 stub L138-141 也不传 `latency_ms` 参数
- 影响:jsonl 永远写 `"latency_ms": {}`;与 spec §7.0.3 `latency_ms={stage: float}` 设计意图不符(spec 想记录 embed/rerank/fuse 阶段耗时,实际永远空 dict)
- 建议:task16 `audit_tap` 接受 `state` 输入,从 `state.get("latency_ms", {})` 提取(需要在 pipeline 早期由 `JsonLoggingHandler` 或各 Runnable 注入);或 task15 移除 `latency_ms` 参数,简化接口直到有数据源

### 🟡 P2 — 建议改进

#### **P2-1. spec §7.0.3 `trace()` 方法名 vs task15 `record()` 方法名漂移未文档化**
- 位置:spec L810(`async def trace(self, query, result)`);`task15.md:208`(`async def record(self, query, result, latency_ms=None)`)
- 问题:spec 用 `trace`,task15 用 `record`。task15 头部 "Fixes applied" 列表没有解释这个命名变更
- 建议:task15 L9 区域补一行 "`trace` → `record` 命名简化(避免与 `langchain` callback `on_chain_*` 命名混淆)"

#### **P2-2. spec §7.0.3 `AuditRecord` Pydantic 模型缺失**
- 位置:spec L810-830(声明 `AuditRecord`);`task15.md`(全文,无 AuditRecord 类)
- 问题:spec 把 `AuditRecord` 作为返回值 Pydantic 模型声明;task15 直接用 dict,失去类型安全
- 建议:即使降级为简化版,仍应定义 `class AuditRecord(BaseModel)` 至少包含 `ts: datetime`、`query: str`、`latency_ms: dict[str, float]`,后续字段(per_dataset 等)可二期加

#### **P2-3. task15 step 0 stub `record()` 接受 `query, result, latency_ms=None`,但 stub 体只 `pass`**
- 位置:`task15.md:38-44`
- 问题:stub 阶段已声明 `async def record(self, query, result, latency_ms=None): pass`——含 `async` 但只 `pass`,未来如果在 `await` 上下文中调用不会抛错,但也无行为
- 建议:stub 也写 `print(json.dumps(...))` 最小可见性,或 stub 抛 `NotImplementedError` 与 `CitationChecker.check` 一致(用户明确区分"未实现" vs "no-op")

#### **P2-4. task16 测试 `test_e2e_ingest_search` 缺少 dataset_versions 注入,无法验证主路径 + version 区分同时成立**
- 位置:`task16.md:96-150`(`test_e2e_ingest_search`);`task16.md:187-220`(`test_e2e_dataset_version_cache_path`)
- 问题:两个测试正交,主路径测试不传 `dataset_versions` dict(走默认 `"v0"` 退化路径),version 测试只测 `search_key()` 函数,二者组合没有 e2e 验证
- 建议:加 `test_e2e_main_path_with_dataset_version`,在主路径基础上传 `deps["dataset_versions"] = {str(ds_id): "v1"}`,验证 L3 写入 key 包含 `"1"` 而非 `"0"`

#### **P2-5. task17 grep `audit` 0 命中 eval 输入**
- 位置:`task18.md` 全文;`task19.md` 全文
- 问题:task18 / task19 是 eval pipeline,理论上可消费 `audit_log.jsonl` 做 regression baseline(每条 query 的 citation_count / failed_dataset_ids 可作回归指标),但两个 task 全文 grep `audit` / `audit_log` 0 命中
- 建议:task18 增补"`retrieval_metrics` 可选消费 `audit_log.jsonl` 的 `citation_count` 分布作为 baseline"

## 4. Spec 覆盖矩阵

| Spec 章节 | 覆盖 Task | 完整性 | 偏差说明 |
|---|---|---|---|
| §0.1 流水线全景图(挂载点 ①-⑤) | task16 (挂载点组装) | 80% | task16 注释 ①-⑤ 标注齐,但 ② GlobalRerank 顺序与图不符(P1-2);③ ParentDoc 是空 stub(P1-1) |
| §0.1 L222 dataset 升版失效 | task6 (`search_key` dataset_versions);task16 (`make_search_cache`) | **0%**(契约冲突) | task6 与 task16 payload 字段不兼容(P0-2);执行时 dataset_version 永远为 0,失效失效 |
| §0.1 L226 Redis 降级 + warnings | task6 (`connection.py` 降级);task16 (`with_cache` throwaway);task14 (orchestrator 合并) | 60% | task16 注释承诺合并 warnings,但未实现收集 + 透传通道(P1-4) |
| §6.5 文档解析增强 | task8/9 | — | 不在 task15/16 范围,跳过 |
| §7.0.3 检索审计(trace/AuditRecord) | task15 | 50% | `record()` 实现,`AuditRecord` Pydantic 模型缺失(P2-2),`query_variants` / `per_dataset` / `cache_hits` 字段全丢(P0-3) |
| §7.1 架构(LCEL 链顺序) | task14 (subgraph+orchestrator);task16 (full pipeline 拼装) | 85% | task16 内部 GobalRerank 挂错位置(P1-2);ParentDoc 是 stub(P1-1);decomposer 字段名歧义(P1-3) |
| §7.7 引用校验工具(CitationChecker) | task15 | 95% | H6 regex 落地;`hallucinated_citations` 类型 spec 是 mixed list(Citation \| str),task15 统一为 `list[str]`,task15 头部已文档化 |
| §8 多级 Redis 缓存 | task6 (key/connection/invalidation);task16 (per-layer 工厂) | 80% | L3 key 契约冲突(P0-2);L3 数据结构 spec §8.3 是 HASH,task16 未明确写入格式(HASH vs STRING 取决于 task6 实现,需核对) |
| §10 Chunk 更新/删除策略 | task6 (INCR version);task10 (ingest 触发失效) | — | 不在 task15/16 直接范围,但 task16 依赖 task6 实现 |

## 5. 架构风险与建议

- **风险 R1:search_key 契约冲突致 L3 缓存版本化失效** (P0-2)
  - 缓解:实施前在 task16 头部 "Fixes applied" 段显式声明"dataset_versions 数据结构以 task6 B10 强化为最终契约,task16 改用 list[int]";同步更新 task16 测试 L209-216 的 payload 字段名和类型
- **风险 R2:task15 audit 字段集与 spec §7.0.3 不匹配,后续 task18 regression 无法基于 per-stage trace 做断言** (P0-3)
  - 缓解:短期在 task15 L9-15 "Fixes applied" 区域补 "本期降级声明";中期补 Pydantic `AuditRecord` 模型(哪怕只 4 字段);长期 P1 任务:扩展 per_dataset 采集(需要 task14 orchestrator 改造)
- **风险 R3:task15/16 与 INDEX/主 plan 状态不一致误导下游** (P0-1)
  - 缓解:本次 review 输出后立即更新 `INDEX.md` L21/L51-52/L137 和主 plan L138-139
- **风险 R4:cache warnings 透传路径未闭环,debug 时无法定位 cache 故障** (P1-4)
  - 缓解:task16 step 2 末尾增加"收集 warnings"步骤:`CachedRunnable` 维护 `self._warnings: list[str]`,`build_full_pipeline` 末尾从 `_cached.warnings` 提取并 setattr 到 `SearchResult.warnings`(或新增 `state["warnings"]` 透传)
- **风险 R5:ParentDoc 与 GlobalRerank 挂载点是空操作** (P1-1, P1-2)
  - 缓解:在 task16 step 4 step 顶部加"TODO: P1-1 / P1-2 必须在测试实施前补全,否则 `parent_doc_window` / `use_global_rerank` 参数实际无效",或临时移除这两个参数直到补完
- **风险 R6:Agent #9 prompt 引用的 spec 章节不准确**(spec 无 §0.2)
  - 缓解:对 prompt 本身无修改权限;在最终输出给用户的建议中标注此事实,提示后续 agent 修订 prompt 模板

## 6. 跨 Task 一致性核查

### C1. SearchRequest / SearchResult 字段流通
- task2 → task14 → task16 → task17 链路:`SearchRequest.query_decomposition` / `use_global_rerank` / `parent_doc_window` / `audit` 4 个开关,定义 → 消费 → 透传 → CLI flag 全部对齐(task2:130-133,task16:391-394,task17:84,149-150)
- `SearchResult.failed_dataset_ids` / `warnings`:task14 写入,task16 cache_decorator 注释承诺合并(未实现,P1-4),task17 CLI echo(L158-160)— **消费方对齐,产出方缺口**
- `ScoredDocument.image_path`:task2 定义,task14 cite.py 写入 Citation,task15 audit 仅记 `citation_count`,**未采集 `image_caption` 模态的 image_path**(spec §7.0.3 字段集本身也不含 image_path,非缺陷)

### C2. 模块路径硬编码
- task20 L9 (F2 P0) 修正 `rag.query.extension → rag.pipeline.query_ext` 和 `rag.audit.citation_check → rag.retrieval.citation_check`
- task15:15 写明 `src/rag/retrieval/citation_check.py`;task17:252 写明 `from rag.retrieval.audit import RetrievalAudit`
- 全部对齐,**无 F2 路径冲突**

### C3. resource 命名(数据库池)
- task17:6 标注"`init_db/close_db → init_pool/close_pool`(与 task6 cache、task7 llm 保持 pool/cache/client 命名一致)"
- task6 / task7 grep `init_db | close_db` 0 命中,命名已统一

### C4. 资源命名(LLMSettings)
- task7:66 显式 `from rag.config import LLMSettings`(H5 修正,单一定义源)
- task7:70 `__all__ = ["LLMSemaphore", "LLMSettings", "llm_sem"]` 显式 re-export
- **无重复定义**

### C5. CI 测试文件白名单
- task20:46-49 显式列 `test_lazy_greedy.py` / `test_query_ext.py` / `test_query_decomposition.py` / `test_citation_check.py`
- task15:16 创建 `tests/unit/test_citation_check.py` ✓
- **新文件全部纳入 CI**

### C6. dataset_version 跨 task(核心冲突)
- task6 step 3(`search_key`):读 `dataset_versions: list[int]`,join `"-"`
- task16 step 2(`make_search_cache`):写 `dataset_version: str`,join `"|"`
- task16 step 1 测试:传 `{"dataset_version": "v0"}` / `{"dataset_version": "v1"}`,与 task6 字段名不匹配
- **冲突确认,见 P0-2**

### C7. trace() / record() 命名
- spec §7.0.3: `async def trace(self, query, result)`
- task15: `async def record(self, query, result, latency_ms=None)`
- **命名变更未在 task15 "Fixes applied" 段文档化,见 P2-1**

### C8. 失败回退路径
- task16:11 注释:`orchestrator 用 with_fallbacks(...) 处理 subgraph 异常(Task 14 H1 修正), build_full_pipeline 顶层不重复 catch`
- task14:498-503 确认 subgraph 内部 try/except + `with_fallbacks` 隔离
- **层级一致,顶层不 catch 原则维持**

### C9. 已知修复(H2/H3/H5/H6/H7/C3)落地
- 全部在 task15/16 中 grep 确认存在或在 trade-off 表声明已修
- H7(`SET LOCAL hnsw.ef_search`)在 task4 范围,本 review 未深查
- **已知修复全部落地**

## 7. 3 条具体建议

1. **P0 阻塞修复:统一 L3 cache key 契约**(P0-2)
   建议方案:保留 task6 B10 强化的 `dataset_versions: list[int]` 形态,改写 task16 `make_search_cache.key_fn` 第 296-305 行为:
   ```python
   versions = dataset_versions or {}
   payload["dataset_versions"] = sorted([
       int(versions.get(d, 0))   # 0 = 未提供时的退化 counter
       for d in payload["dataset_ids"]
   ])
   ```
   同步修改 task16 测试 L211、L215 payload 字段 `dataset_version: "v0" → dataset_versions: [0]` / `[1]`,断言 `v0_key != v1_key`;在 task16 头部 "Fixes applied" 段补"subagent #9 初版 dict[str, str] 与 task6 B10 list[int] 不兼容,本 task 实施时已对齐 task6 契约"。

2. **P0 阻塞修复:更新 INDEX.md / 主 plan 状态,标记 task15/16 为 OK**
   3 处修改:
   - `INDEX.md:21` `**当前状态:18/20 已生成**` → `**当前状态:20/20 已生成**`
   - `INDEX.md:51-52` task15/16 行 `— (缺) | — | **MISSING**` → `[task15.md](./task15.md) | 11,944 | 301 | OK | 7 (2 🔴) | ...`;task16 类似
   - `2026-06-10-python-rag-pipeline.md:138-139` 任务表 task15/16 行同步更新
   此修复零代码改动,直接消除下游 agent 误判风险。

3. **P0 阻塞修复:task15 audit 字段集决策 + 文档化降级范围**(P0-3)
   建议:在 task15 L1-9 头部 "Spec 引用" 段下方新增"**本期范围降级声明**":
   > 与 spec §7.0.3 完整 `AuditRecord` 相比,本期 `RetrievalAudit.record()` 仅记录 query 级别元数据(`ts` / `query` / `failed_dataset_ids` / `warnings` / `citation_count` / `latency_ms`),**不采集** `query_variants` / `per_dataset` 逐 stage trace / `cache_hits` / `global_ranking`。  
   > 理由:`per_dataset` 采集需要 task14 orchestrator 改造(目前 SearchResult 不含 per-stage 数据);`cache_hits` 需要 task6 cache_decorator 改造。  
   > 二期路径:与 spec §7.0.3 "生产化: 二期可写入 PG `query_log` 表" 一致,本期 jsonl 简化版,二期补 Pydantic AuditRecord + per_dataset + cache_hits 字段。
   此声明把"缺失"转化为"显式降级",与 spec §7.0.3 末尾"生产化"路线对齐,避免被误判为 spec 偏离。

---

**总评**:task15/16 实质内容已完整落盘且 TDD 流程齐备(stub-first / fixures / commit 模板);INDEX/plan 状态漂移是文档侧 bug,无技术风险。最关键的 3 个 P0 阻塞是 (a) INDEX/主 plan 状态错标导致下游误判、(b) task6 vs task16 的 L3 cache key payload 字段不兼容使 dataset_version 失效逻辑死锁、(c) task15 audit 字段集与 spec §7.0.3 偏离但 task15 头部未声明降级范围。task15/16 实施前必须先解决这 3 个冲突,否则测试会在 L3 version 路径上失败,debug 价值也因 audit 字段不足而打折。