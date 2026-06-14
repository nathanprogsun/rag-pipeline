The sandbox is read-only; I cannot write the file directly. I'll deliver the full review content here so you can save it manually to `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent8_subgraph_orchestrator.md`. Other parallel agents (agent1-4, 7, 9) wrote to the same directory in their sessions, but mine has `sandbox_mode: read-only`.

---

# Architecture Review: Agent #8 — L5 Subgraph + Orchestrator + Rerank + Cite + Parent Doc

> 范围: task14.md (937 行, 主流水线编排节点)
> 备注: 系统提示称 task15/task16 缺失,但磁盘上两者均已存在(301/535 行),本 review 同步审 task16 中与 task14 接口相关的部分。
> 所有引用基于 `file:line`,审稿日期 2026-06-10。

---

## 1. 一句话总评

task14 的契约层有 1 个会直接破坏重排评分的 P0 公式错用(`intra_fusion(rrf_k=weight)`)与 1 个 spec §0.1 L881 强制要求的 `with_fallbacks` 未实现,加上 ParentDocExpander 死代码、SearchRequest.use_rerank 未消费 2 个 P0 缺口,实现层(TDD 流程、Stub 缺失、子图 filter 顺序)有 4 个 P1 偏差需在进入 task16 主流水线前收口。

---

## 2. 模块边界 / 依赖方向 / 契约一致性

| 检查项 | 结论 | 证据(file:line) | 评级 |
|--------|------|-----------------|------|
| 模块边界(orchestrator/subgraph/rerank/cite/parent_doc 分离) | OK, 5 文件各司其职 | task14.md:23-29 | OK |
| 依赖方向(orchestrator → subgraph → retriever) | OK, 单向 | task14.md:497-525, 808-832 | OK |
| 依赖方向(orchestrator → fusion/filter) | OK | task14.md:518-521 | OK |
| SearchRequest.rerank_weight 字段一致性 | 错位 — task14 说 "B12 0.7→0.5",但 task2 已 0.5 | task2.md:124 vs task14.md:212-231 | ⚠ |
| SearchRequest.rerank_weight 类型(float) | OK | task2.md:124, task14.md:228 | OK |
| `intra_fusion` 签名 `(query_groups, rrf_k)` 与调用方一致 | OK for subgraph | task11.md:25-32 vs task14.md:841-846 | OK |
| `intra_fusion` 签名与 RerankRunnable 调用一致 | 错位 — RerankRunnable 把 weight 当 rrf_k 传入 | task11.md:25-32 (rrf_k=60 damping) vs task14.md:103-110 (传入 self.weight=0.5) | 🔴 |
| Citation 编号全局一致(`[i+1]`) | OK | task14.md:325-330 + 333-340 | OK |
| ScoredDocument.image_path → Citation.image_path (H2) | OK | task14.md:159-169, 312-318 | OK |
| `SearchResult.failed_dataset_ids: list[uuid.UUID]` | OK, orchestrator 填充 | task2.md:153-155 vs task14.md:534-537 | OK |
| `SearchResult.warnings: list[str]` | OK, orchestrator 聚合 | task2.md:156 vs task14.md:533, 539 | OK |
| `use_global_rerank` 字段在 SearchRequest | OK | task2.md:132 vs task14.md:574-580 | OK |
| `parent_doc_window` 字段在 SearchRequest | OK | task2.md:131 vs task14.md:344-394 | OK |
| CitationChecker regex H6 `\[([\d,\s]+)\]` 与 cite.py 输出兼容 | OK | task15.md:24 vs task14.md:333-339 | OK |
| spec §0.1 L881 with_fallbacks 强制 | 未实现 | spec.md:881 vs task14.md:489-512 (RunnableLambda + try/except) | 🔴 |
| spec §7.1 子图顺序 "IntraFusion → Rerank → IntraFilter" | 顺序倒置 | spec.md:104 vs task14.md:843-852 (filter 在 _ainvoke 末尾) | 🔴 |
| spec §0.1 L226 缓存降级 → warnings | task16 已对齐 | spec.md:226, task16.md:75-85 | OK (跨 task) |
| task15 audit 与 task14 Orchestrator 接口 | OK, 旁路不破坏 SearchResult | task15.md:51-62 vs task14.md:507-541 | OK |
| task16 build_full_pipeline 串接 5 挂载点 | 部分实现 — ParentDocExpander 是 no-op;GlobalRerank 挂错位置 | task16.md:99-115 | 🔴 |
| `ChunkedCohereRerank.rerank` 签名 | OK, 与父类 task7 一致 | task7.md:321-330 vs task14.md:174-180 | OK |
| `Reranker` Protocol re-export 路径 | OK | task14.md:138-141 | OK |
| task6 rerank_key 签名与 task16 make_rerank_cache 兼容 | OK | task6.md:233 vs task16.md:90-95 | OK (跨 task) |

---

## 3. 发现清单(按严重度降序)

### 🔴 P0 — 必须修复(阻塞)

- **[B13 公式错用] RerankRunnable 把 `weight` 当 `rrf_k` 传入 `intra_fusion`,破坏 RRF 阻尼常量**
  - 位置: `task14.md:103-110`(RerankRunnable.ainvoke)与 `task14.md:595-605`(GlobalRerankRunnable.ainvoke)
  - 问题: `intra_fusion(query_groups, rrf_k)` 公式为 `1/(rrf_k + rank)`,`rrf_k` 是阻尼常量(标准 RRF=60,见 task11.md:17, 31)。task14 传 `rrf_k=self.weight=0.5`,公式退化为 `1/(0.5+rank)`。rank=1 时标准 RRF 得 1/61 ≈ 0.0164,当前实现得 1/1.5 ≈ 0.667,量级 ~40x 偏离。`task11.md:60-64` 的 `test_intra_wrrf_formula` 断言 `score = 1/(DEFAULT_RRF_K+1)`,与 task14 的 0.5 调用直接冲突,意味着 RerankRunnable 输出与 RRF-K 调参空间被冻结在 0.5 附近。
  - 影响: 整条重排-融合链路的分数基线失衡;rerank 调参(0.3/0.5/0.7)实际变成 rrf_k 调参(0.3/0.5/0.7),W_RRF 退化为"非标 RRF-K";RAGAS / Gold Set 复现 FastGPT 对比失真。
  - 建议: 在 fusion.py 增加 `weighted_rrf(query_groups, weights, rrf_k=60)` 路径(显式权重 × 1/(60+rank)),RerankRunnable 与 GlobalRerankRunnable 调此 API。

- **[C1 修正半实现] 子图异常隔离用 RunnableLambda + try/except,非 spec 强制要求的 `with_fallbacks`**
  - 位置: `task14.md:489-512`(DatasetOrchestrator.ainvoke)
  - 问题: spec §0.1 L881 明确要求"LangChain `RunnableParallel` + 每个 subgraph `with_fallbacks()` 做异常隔离"。task14 用 RunnableParallel 但**没有** with_fallbacks,改用 `_safe_run` 内 try/except 捕获 `Exception` 后返回 `{"filtered": [], "error": str(e), "dataset_id": ...}`。`Exception` 太宽,且自定义 dict 错误形态不进入 LCEL `RunnableError` 通道,task16 `JsonLoggingHandler.on_chain_error` (`task16.md:139-145`) 永远不被触发。
  - 影响: ① 失败 dataset 的 latency 不进入 `on_chain_error` 计时;② LangSmith / OTEL 追踪丢信号;③ 偏离 spec 协议。
  - 建议: 把 `_safe_run` 替换为 `subgraph.with_fallbacks([RunnableLambda(_error_fallback)])`,`_error_fallback` 返回标准错误 dict;`DatasetOrchestrator` 读 `result.get("error")` 逻辑不变。

- **[挂载点 P0] ParentDocExpander 死代码 — task14 实现完整, task16 挂载为 no-op**
  - 位置: `task14.md:344-394`(parent_doc.py 实现)vs `task16.md:111-119`(`async def expand_result(result): return result`)
  - 问题: task14 Step 4 写了 `ParentDocExpander.expand()`(async, 接受 hits 列表, batch SQL 拉取 siblings, 合并 text, 受 max_tokens 限制)。task16 挂载点函数体只有 `return result`——expander 永远不被调用,`parent_doc_window > 0` 是无声 no-op。
  - 影响: spec §0.1 L122-125 / §7.1 L867-868 显式声明 ③ ParentDocExpander 挂载点;`window>0` 必须工作。Eval L2 阶段测试 `parent_doc_window=2` 不会得到任何扩展。
  - 建议: task16 实现真正 `expand_result(result) -> SearchResult`:把 `result.citations` 拆为 `ScoredDocument` 列表(需要反向映射或 Orchestrator 输出 dict 而非 SearchResult),调 `await expander.expand(hits)`,再 `assemble_citations` 重组。

- **[契约 P0] `SearchRequest.use_rerank=False` 不被消费,重排仍会触发**
  - 位置: `task14.md:858-862`(subgraph 末尾 `if dataset.rerank_model and deps.get("reranker")`)
  - 问题: `SearchRequest.use_rerank: bool = True`(`task2.md:122`),`resolve_rerank_model`(`task2.md:154-160`) 设计语义为 `use_rerank=False → 返回 None,即使 dataset.rerank_model 非空也禁用`。task14 subgraph 直接看 `dataset.rerank_model`,完全忽略 `use_rerank`。
  - 影响: 业务上"这次不要 rerank"做不到;`resolve_rerank_model` 是 task2 公开 API 但 task14 无调用方,契约失守。
  - 建议: `build_dataset_subgraph`(`task14.md:807`)增加 `deps.get("use_rerank", True)` 门,False 时跳过 RerankRunnable 挂载;orchestrator 在初始化 subgraph 时按 SearchRequest 传 use_rerank。

- **[B12 误导] Step 0b 改 `SearchRequest.rerank_weight` 0.7→0.5 是 no-op,记录失真**
  - 位置: `task14.md:212-231`(Step 0b 增量补丁)
  - 问题: 头注释 `task14.md:2-3` 与 Step 0b docstring (`task14.md:217-222`) 都说 "0.7 → 0.5 对齐 FastGPT defaultReRankWeight",但 `task2.md:124` 已定义为 `rerank_weight: float = 0.5`。Step 0b 的 modify 不会触发任何 diff。
  - 影响: 主 plan 自检清单 `plans/2026-06-10-python-rag-pipeline.md:148-153` 把这条列入 "Type consistency ✓",实际没改东西;git diff 误判权重语义已对齐。
  - 建议: 把 Step 0b 改为"核对 SearchRequest.rerank_weight 已是 0.5,无需修改"或直接删除;commit message 移除 B12 字样。

### 🟠 P1 — 应该修复(影响行为但不阻塞)

- **[spec 偏差] 子图内 filter 顺序倒置 — filter 在 rerank 之前,违反 spec §7.1**
  - 位置: `task14.md:843-852`(subgraph `_ainvoke` 末尾 `filter_pipeline(fused, ...)`)vs spec `spec.md:104`
  - 问题: subgraph 内部对 `fused` 先调 `filter_pipeline` 再返回,然后用 `runnable | rerank_node` 拼 RerankRunnable。rerank 看到的是已过滤(阈值 + token 预算)后的 hits,过滤先杀掉了部分候选,rerank 无法对全量打分。spec 想让 filter 在 rerank 之后做"最终 token 预算裁剪"——位置不同,语义不同。
  - 影响: 命中子预算外的 chunk 永远进不到 rerank 视野,rerank 调优空间被前置 filter 截断;偏离 FastGPT `defaultRecall/index.ts` 行为。
  - 建议: 把 `filter_pipeline` 移到 `runnable | rerank_node | filter_node`,filter 作为 subgraph 最末节点。

- **[可观测性] 重排无 LLMSemaphore,无超时控制**
  - 位置: `task14.md:75-110`(RerankRunnable)与 `task14.md:573-664`(GlobalRerankRunnable)与 `task14.md:489-540`(Orchestrator)
  - 问题: ① RerankRunnable 调 `await self.reranker.rerank(...)` 未走 task7 `LLMSemaphore`(`task7.md:10, 26-50`),N 个 dataset 并发重排同时打 Cohere API 触发 429;② orchestrator 无 `asyncio.wait_for`,单个慢 subgraph 可永久阻塞;③ L4 rerank 缓存(`task16.md:90-95` 1h TTL)不解决 Cohere 限流。
  - 影响: 10+ dataset 场景下首屏延迟不可控,Cohere Tier-1 默认 10 req/s。
  - 建议: RerankRunnable 接 `semaphore: LLMSemaphore | None`,rerank 前 `async with semaphore:`;orchestrator `asyncio.wait_for(state, timeout=30.0)`(对齐 task7 FastGPT 默认)。

- **[可观测性] Orchestrator JSON 日志字段不完整**
  - 位置: `task14.md:489-540` 与 `task16.md:124-148`
  - 问题: 失败处理把 `"error"` 塞进 subgraph 输出 dict,但 JsonLoggingHandler 的 `on_chain_end` 只看 `output_keys: list`,不感知 `error`。
  - 建议: orchestrator 在正常路径外把 `failed_dataset_ids` 与 `warnings` 长度加到 outputs;或改用 `with_fallbacks` 让 `on_chain_error` 触发。

- **[TDD 缺口] Step 8 期望 "6 passed" 算错(实际应为 11),且 parent_doc 单测未定义**
  - 位置: `task14.md:930-933`
  - 问题: Step 8 期望 `6 passed (cite: 3 + orchestrator: 2 + rerank: 4 + parent_doc: 2)`,算术 3+2+4+2=11(不是 6),且 task14 全文无 `test_parent_doc` 任何用例。Step 8 注释的 "6 passed" 与实际定义不匹配。
  - 影响: CI 阶段 expected/factual 不一致;parent_doc 完全无测试覆盖。
  - 建议: 补齐 2 个 parent_doc 单测(去重 + window=0 短路),修正 Step 8 期望数为 11。

- **[TDD 违规] 缺 stub-first 步骤(与 audit #1 P1-1 不一致)**
  - 位置: `task14.md:240-258`(Step 1 cite)与 `task14.md:344-394`(Step 4 parent_doc)与 `task14.md:466-540`(Step 5 orchestrator)与 `task14.md:789-866`(Step 6 subgraph)
  - 问题: task15 (`task15.md:60-80`) 与 task16 (`task16.md:62-83`) 显式加了 Step 0 stub,因 audit #1 P1-1。task14 仅有 Step 0 (rerank.py) 一个 stub,cite / parent_doc / orchestrator / subgraph 的测试在 RED 阶段触发 ImportError(模块未创建)而非 assertion fail。pytest 把 ImportError 计入 error 而非 fail,RED 体验不一致。
  - 建议: 为 cite / parent_doc / orchestrator / subgraph 各加一个最小 stub(空函数 + 占位),保证 import 成功后再写 assertion。

- **[契约偏差] `weight == 1.0` 短路用浮点等价比较**
  - 位置: `task14.md:97-99`(RerankRunnable)与 `task14.md:631-633`(GlobalRerankRunnable)
  - 问题: `if self.weight == 1.0: fused_text = rerank_ranked` 是精确浮点比较。caller 传 1.0-epsilon(0.99999)会进 else 分支并触发 rrf_k=0.99999 破损公式,行为对调用者不直观。
  - 建议: 改为 `if self.weight >= 1.0 - 1e-9`,或拆出独立 `weight` / `disable_fusion: bool` 参数。

### 🟡 P2 — 建议修复(代码质量/语义)

- **[Cache 键漂移] RerankRunnable 内部去重改变输入,影响 L4 cache key 稳定性**
  - 位置: `task14.md:75-80`(`hits = remove_duplicates(hits)`)与 `task16.md:90-95`(`doc_ids = [h.chunk_id for h in inp.get("filtered", [])]`)
  - 问题: task16 `make_rerank_cache.key_fn` 用原始 `filtered` 算 `rerank_key`;RerankRunnable 进入后 `remove_duplicates` 改变 doc 列表,rerank 输入的 doc_ids 与 cache key 算出的 doc_ids 不一致。cache hit / miss 行为不同。
  - 建议: 在 `with_cache` 包装前对 input 做一次 `remove_duplicates`,或在 cache key_fn 中对 `doc_ids` 排序+去重。

- **[token 预算未启用] Subgraph filter `max_tokens=None`,parent_doc 默认 2000 失效**
  - 位置: `task14.md:849-851`
  - 问题: spec §7.5 L957-961 `filter_by_token_budget` 默认应启用,`max_tokens=None` 跳过 token 预算裁剪;task12 `subgraph_filter` 提供 `per_dataset_token_budget` 参数,task14 没传。
  - 建议: deps 增加 `per_dataset_token_budget`,默认 2000,传入 `filter_pipeline`。

- **[ChunkedCohereRerank 字符估算偏差] 中文 doc 1 token ≈ 1-2 chars,与英文 ~4 chars 差距大**
  - 位置: `task14.md:147-156`(`text2Chunks` 用 `max_chars = max_tokens * 2`)
  - 问题: 中文 1 token 实际约 1.5 chars,450 token 预算对纯中文 doc 实际能塞 ~675 chars,可能超 Cohere single-doc 上限。
  - 建议: 用 `tiktoken.encoding_for_model("rerank-english-v3.0").encode(text)` 精确切分,task7 已引入 `tiktoken`。

- **[重排分数未消费] `reranked` 解构为 `(orig_idx, _rscore)`,rscore 字段被丢弃**
  - 位置: `task14.md:91-98`
  - 问题: `_rscore` 显式忽略,只用 `enumerate(rerank, start=1)` 取 rank。Cohere 相关性分数(0~1)完全不参与融合,与 spec §0.1 L130 "WRRF 加权累加"精神偏离。
  - 建议: 引入 `weighted_rrf` API 显式乘 weight(见 P0 第一条建议)。

- **[测试覆盖] CohereRerank text2Chunks 路径无单测,`existsId` 抑制路径无单测**
  - 位置: `task14.md:131-205`(`ChunkedCohereRerank` 完整实现),测试在 `test_rerank.py:243-291`
  - 问题: 4 个 rerank 单测只测 `RerankRunnable` 与 FakeReranker 交互,`ChunkedCohereRerank.rerank` 的拆分/合并/去重逻辑无单测覆盖。
  - 建议: 加 `test_chunked_cohere.py`,mock 父类 `client.rerank`,验证 (a) 长 doc 被拆 (b) 同 orig_idx 多 chunk 取最高分 (c) 短 doc 不拆分。

### 🟢 P3 — 文档/小问题

- **Step 9 commit message 引用"subagent #8: 入口前去重 + 仅 source in (vector, fulltext) rerank" 但 Step 0c 测试名 `test_rerank_skips_caption_hits` 实际只验证 `any(h.source == "caption" for h in out["filtered"])` — 验证 caption 没被误改 source,没验证 caption 跳过 rerank 后得分是否被融合(实际拼回但无 WRRF 加分)。** 测试名与断言不完全对应。

- **CohereRerank 注释 `existsId 抑制`** 与 `task14.md:186-189` 注释 `# existsId 抑制:同 docId 重复,只保留分数最高那个` 一致,但 `best[orig_idx] = (score, chunk_i)` 取的 key 是 `orig_idx`(docId),不是 FastGPT 真正的 existsId(per-docId 集合)。命名 OK 但与 FastGPT 字面含义有微差。

- **`Citation.update_time` 在 assemble_citations 中从 `h.metadata.created_at` 取值** (`task14.md:316`),spec §3 `ChunkMetadata.created_at` 注释是"从 PG 回填"。ScoredDocument 路径里 `h.metadata.created_at` 一定是 None(回填未发生),update_time 永远是 None。建议在 assemble_citations 中显式做 `repo.get_chunk_meta(h.chunk_id)` 回填,或注释说明 update_time 由 L4 转换层填。

---

## 4. Spec 覆盖矩阵

| Spec 章节 | 覆盖 Task | 完整性 | 偏差说明 |
|----------|----------|:------:|----------|
| §0.1 L43-46 主流水线全景(12 默认 + 4 可选开关) | task14 + task16 | ⚠ | 4 开关中 `use_global_rerank`(②) 与 `parent_doc_window`(③) 在 task16 挂载点无效(no-op) |
| §0.1 L97-104 DatasetOrchestrator 节点树 | task14.md:486-541 | 🔴 | spec 要求 `with_fallbacks`;task14 用 try/except Lambda |
| §0.1 L122-125 ③ ParentDocExpander | task14.md:344-394 + task16.md:111-119 | 🔴 | task16 挂载点是 no-op |
| §0.1 L222 dataset 升版失效 | task16.md:73-83 | OK | `make_search_cache(..., dataset_versions=...)` |
| §0.1 L226 缓存降级 → warnings | task16.md:22-37 | OK | 失败 throwaway 抑制 |
| §7.0.2 ParentDocExpander | task14.md:344-394 | ⚠ | 实现完整,挂载失效 |
| §7.0.3 RetrievalAudit | task15.md:50-82 | OK | 旁路 jsonl,`tail(n)` 给 task17 |
| §7.1 子图顺序 IntraFusion → Rerank → IntraFilter | task14.md:843-852 | 🔴 | 实际是 IntraFusion → IntraFilter → Rerank |
| §7.2 第一层 RRF (intra_fusion) | task11.md:25-32 | OK | N-way RRF 跨 query_group,无 per-group 权重 |
| §7.2 WRRF 公式 (spec L914-918) | task11.md (新 API) | 🔴 | spec L900 旧 WRRF 公式未在代码中保留,task14 错用 rrf_k=weight |
| §7.3 第三层 RRF(跨 dataset) | task11.md:32-40 + task14.md:518 | OK | |
| §7.4 异常隔离表 | task14.md:489-540 + 75-110 | ⚠ | Rerank 失败跳过 OK;subgraph 失败未走 with_fallbacks |
| §7.5 过滤管线 | task12 + task14.md:849-851 | ⚠ | subgraph filter `max_tokens=None`,token 预算不启用 |
| §7.6 引用组装(prompt 模板) | task14.md:312-340 | OK | |
| §7.7 引用校验工具 | task15.md:84-150 | OK | CitationChecker,工具非节点 |
| §7.8 Prompt 模板管理 | task14.md (cite.py) | OK | |
| §8.1 L4 rerank 缓存 | task16.md:90-95 | OK | TTL 1h, key 由 `rerank_key(model, query, doc_ids)` |
| §8.2 key 规范 + §8.5 L3 dataset_version | task6 (search_key) + task16.md:73-83 | OK | 跨 task 一致 |
| §9.1 覆盖率 | n/a | n/a | task14 单测目标 ≥90% 未声明;rerank 4/cite 3/orchestrator 2/parent_doc 0/ChunkedCohereRerank 0 |
| §11 启动流程 | n/a (task1) | n/a | 与 task14 无关 |

---

## 5. 架构风险与建议

- **风险 1: 公式错用导致重排评估失真(P0)**
  - 缓解: 引入 `weighted_rrf(groups, weights, rrf_k=60)`,RerankRunnable 与 GlobalRerankRunnable 显式传 weight;task18 Gold Set 评测时校准 rrf_k=60 baseline。
- **风险 2: orchestrator 异常路径不走 LCEL tracing(P0)**
  - 缓解: 改用 `subgraph.with_fallbacks([RunnableLambda(_error_fallback)])`;`JsonLoggingHandler.on_chain_error` 真正触发;异常范围收窄到 `RunnableError`。
- **风险 3: 主流水线缺 ParentDoc 实际实现(P0)**
  - 缓解: task16 实施时把 `expand_result` 写完,需要 Orchestrator 返回 dict 而非 SearchResult,或在 SearchResult 旁增 `ScoredDocument` 字段。
- **风险 4: 跨 dataset 并发重排触发 Cohere 限流(P1)**
  - 缓解: RerankRunnable 接 LLMSemaphore;orchestrator `asyncio.wait_for` 全局 30s 超时;`make_rerank_cache` 提高 L4 hit rate。
- **风险 5: SearchRequest.use_rerank 失守(P0)**
  - 缓解: build_dataset_subgraph 接受 `use_rerank` 门;orchestrator 透传 SearchRequest 字段;task17 CLI search 新增 `--no-rerank` flag。
- **风险 6: filter 在 rerank 之前,重排调优空间被截断(P1)**
  - 缓解: subgraph 链调整为 `vec ‖ ft → intra_fusion → rerank → filter_pipeline`,filter 作为最末节点。
- **风险 7: Spec §7.2 旧 WRRF 公式与 task11 新 RRF API 不一致(P1)**
  - 缓解: spec §7.2 标注"已迁移至 N-way RRF (B4),权重由调用方显式乘",或新增 `weighted_rrf` 同步 spec §7.2 L914-918 公式。

---

## 6. 跨 Task 一致性核查

| 接口 | 上游/下游 | task14 实现 | 对接方期望 | 一致性 |
|------|-----------|------------|-----------|:------:|
| `intra_fusion(query_groups, rrf_k)` | task11 → task14 | task14.md:103-110 传 `rrf_k=weight=0.5` | task11.md:25-32 `rrf_k=60`(damping) | 🔴 语义错位 |
| `RerankRunnable.ainvoke(input)` | task14 subgraph → task16 cache | input=`{"filtered": [...], "query": "..."}` | task16.md:90-95 包装 in/out dict | OK |
| `ParentDocExpander.expand(hits)` | task14 → task16 | task14.md:381 `async def expand` | task16.md:111-119 不调用 | 🔴 死代码 |
| `Reranker.rerank(query, documents, top_k)` | task7 → task14 → task16 | task7.md:321 / task14.md:78 / task16.md:90 | tuple 协议一致 | OK |
| `ChunkedCohereRerank.rerank` 返回 | task14 → RerankRunnable | task14.md:199 `[(orig_idx, score)]` | RerankRunnable `for orig_idx, _rscore in reranked` | OK(忽略 score) |
| `SearchRequest.rerank_weight` | task2 → task14 | task2.md:124 `0.5`;task14.md:228 写 `0.5` | 无冲突,但 task14 Step 0b 是 no-op | ⚠ |
| `SearchRequest.use_rerank` | task2 → task14 | task2.md:122 定义;task14 完全不消费 | 契约失守 | 🔴 |
| `SearchRequest.use_global_rerank` | task2 → task14 → task16 | task2.md:132;task14.md:574;task16.md:103-109 | GlobalRerankRunnable OK,task16 挂载错位(after orchestrator) | 🔴 |
| `SearchRequest.parent_doc_window` | task2 → task14 → task16 | task2.md:131;task14.md:344;task16.md:111-119 no-op | window=0 默认 OK,window>0 失效 | 🔴 |
| `SearchResult.failed_dataset_ids` | task14 → task16 (audit) | task14.md:534-537;task15.md:64-71 | 字段一致 | OK |
| `SearchResult.warnings` | task14 + task16 (cache) | task14.md:539;task16.md:75-85 | spec §0.1 L226 期望聚合 | OK |
| `ScoredDocument.image_path` | task2 → task14 cite | task2.md:464-465;task14.md:316 | H2 修正 OK | OK |
| `rerank_key(model, query, doc_ids)` | task6 → task16 → task14 | task6.md:233;task16.md:90-95;task14.md:78 | key 依赖 doc_id 顺序,rerank 内部去重可能破坏稳定性 | ⚠ |
| `CitationChecker` regex H6 | task15 → LLM 回答 | task15.md:24;LLM 回答 `[1]` `[1,2,3]` `[1, 2, 3]` | cite.py 输出 `[i+1]`,与 regex 兼容 | OK |
| `SearchRequest.query_extension` | task2 → task13 → task16 | task2.md:127;task13;task16.md:84-86 | subgraph 读 `state.get("query_variants", [state.get("query", "")])` | OK |
| `resolve_rerank_model(req, dataset)` | task2 → task14 | task2.md:154-160;task14 未调用 | 契约失守 | 🔴 |
| `Reranker = NoOpRerank()` 兜底 | task7 → task14 | task7.md:328;task14.md:81 | 一致 | OK |
| `Orchestrator.ainvoke(state) -> SearchResult` | task14 → task16 | task14.md:506 returns SearchResult;task16.md:91 链 `pipeline | RunnableLambda(expand_result)` | LCEL 链接收 Pydantic 模型 | OK (但限制 expand_result 改写) |

---

## 7. 3 条具体建议

1. **在 fusion.py 引入 `weighted_rrf(query_groups, weights, rrf_k=60)` 函数,显式 2 参(weights + rrf_k),并更新 spec §7.2 同步公式。** RerankRunnable (`task14.md:103-110`) 与 GlobalRerankRunnable (`task14.md:595-605`) 调用 `weighted_rrf([rerank_ranked, text_hits], [self.weight, 1-self.weight], rrf_k=60)`,这样 `weight=0.5` 真的进入"加权和"路径,rrf_k=60 是阻尼常量。同步把 task14 Step 0b 的 "0.7→0.5" 注释删除或改为核对。

2. **重写 DatasetOrchestrator 异常路径为 `subgraph.with_fallbacks`,并在 spec §0.1 引用到位。** 把 `_safe_run` 内的 `try/except` 替换为 `subgraph.with_fallbacks([RunnableLambda(_err)])`,`_err` 返回 `{"filtered": [], "error": str, "dataset_id": ...}` 标准 shape;`DatasetOrchestrator` 读 `result.get("error")` 逻辑不变。这样 `JsonLoggingHandler.on_chain_error` 真的触发,LangSmith / OTEL 追踪可见,符合 spec §0.1 L881 协议。

3. **把 ParentDocExpander 与 GlobalRerank 在 task16 真正挂上,删除 no-op 桩。** ParentDocExpander 路径需要 Orchestrator 返回 dict(或在 SearchResult 旁增 `ScoredDocument` 字段),`expand_result` 改写为 `await expander.expand(...)` 然后 `assemble_citations` 重组。GlobalRerank 路径挪到 `inter_dataset_fusion` 之后、`filter_pipeline` 之前(挂载点 ②),用 `GlobalRerankRunnable` 而非 `RerankRunnable`。同时新增 `use_rerank: bool` 门贯穿 `build_dataset_subgraph`,让 `SearchRequest.use_rerank=False` 真正禁用重排。

---

# 摘要(终端输出)

**总评**: task14 整体边界清晰、5 文件分层合理、Cross-task 接口(task2/6/7/15/16)除 3 处外都对齐,但契约层有 5 个 P0 缺口与 4 个 P1 偏差需在进入 task16 主流水线集成前收口。

**3 条最关键发现**:

1. **[P0] RerankRunnable/ GlobalRerankRunnable 把 `weight=0.5` 当 `rrf_k` 传入 `intra_fusion`**(`task14.md:103-110, 595-605`):破坏 RRF 阻尼常量,公式从 `1/(60+rank)` 退化为 `1/(0.5+rank)`,score 量级 ~40x 偏离,W_RRF 名存实亡。task11 `test_intra_wrrf_formula` 与该调用直接冲突。
2. **[P0] spec §0.1 L881 强制要求的 `with_fallbacks` 未实现**(`task14.md:489-512`):用 RunnableLambda + try/except 替代,LCEL callback / tracing(`JsonLoggingHandler.on_chain_error`)丢信号,异常隔离偏离 spec 协议。
3. **[P0] ParentDocExpander 死代码**(`task14.md:344-394` 实现 vs `task16.md:111-119` no-op mount):`parent_doc_window > 0` 在端到端流水线里是无声空操作;同时 `SearchRequest.use_rerank` 字段在 task14 完全不消费,业务无法禁用重排。

> 注: 由于本会话 `sandbox_mode: read-only`,review 文件未能写入 `reviews/agents/agent8_subgraph_orchestrator.md`。完整 review 已在上方输出,请保存到该路径。