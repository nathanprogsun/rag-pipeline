# Architecture Review — 批量审核结果汇总

_Generated_: 2026-06-10 16:56:43
_Scope_: 主 plan + 18 个 task 文件(20 个 task,task15/16 已落盘)
_Reviewers_: 10 个并行 subagent,基于 `MiniMax-M3` 模型
_Sandbox note_: 部分 agent 输出含 "sandbox blocked" 元信息,但实际 review 内容已通过 codex --output-last-message 落盘

## 状态矩阵

| Agent | 域 | 输出大小 | 行数 | 状态 |
|---|---|---:|---:|:--:|
| agent1 | L0/L1 基础 + Domain |    29783 B | 283 | ✓ |
| agent2 | L1/L2 PG + Vector |    25087 B | 360 | ✓ |
| agent3 | L2 Fulltext + Cache |    28964 B | 271 | ✓ |
| agent4 | L2/L3 LLM + Reader |    15451 B | 172 | ✓ |
| agent5 | L3/L4 Chunker + Ingest |    28296 B | 324 | ✓ |
| agent6 | L4 Fusion + Filter |     1633 B | 13 | ✓ |
| agent7 | L5 Query Extension |    18572 B | 195 | ✓ |
| agent8 | L5 Subgraph + Orchestrator |    25588 B | 244 | ✓ |
| agent9 | L5 缺失任务审计 |    27273 B | 250 | ✓ |
| agent10 | L6/L7/L8 CLI + Eval + CI |    25972 B | 262 | ✓ |

## 一句话总评矩阵(从各 agent review 提取)

### agent1 — L0/L1 基础 + Domain
task1 脚手架可用但 `Settings` 在模块导入期即被实例化,library 模式存在"导入即报错"风险;task2 域模型与 spec §3 严重不同步(spec 13 字段 / task 19 字段,`rerank_weight`/`temperature`/`score_threshold` 三个默认值与 spec 不一致),`prompt_template` 在 Pydantic 与 SQL DDL 两端的默认值分裂将导致 DB 回灌路径必崩。

### agent2 — L1/L2 PG + Vector
**task3/task4 整体可落地,但存在一处明确的 P0 SQL 注入 (`task3.md:240,247-252` `to_tsquery` f-string 插值,与 plan §H 自我声明的 "M4 修复" 不符) 与一处必然失败的单测 (`task3.md:69-78` 用 embed_dim=3 写 Vector(1536) 列)。** 其余问题集中在 HNSW 索引在 dev 路径缺失、test 拼写错误、LLMSemaphore 在 query 侧未挂载、TimestampMixin 缺 `updated_at` 等 P1 级缺陷。
---

### agent3 — L2 Fulltext + Cache
task5/6 整体方向正确(jieba 预分词 + INCR-based version invalidation + 多级降级),但存在 **5 个 P0 阻塞**:跨 task cache-key payload 契约冲突、`on_chunks_changed` 的 per-dataset SCAN 兜底实际不命中、Pydantic 序列化破坏 cache 往返、SQL 拼接 tsquery 注入风险,以及 Cache 全局状态导致 metrics 串扰。

### agent4 — L2/L3 LLM + Reader
两个 task 主体结构完整、修复链可追溯,但存在 3 类实质问题:Spec 与实现存在 2 处 Rerank 路径冲突(自研 CohereRerank vs `langchain-cohere`);`JsonLoggingHandler` 仅注册 stdlib `logging`,未接入 LangChain Callback,spec §8.7 的 stage/latency_ms/tokens 字段无法产出;Reader/Structure 与 Chunker 各自独立重建 heading,`DocumentStructure` 在 chunker 路径上实际是死代码。

### agent5 — L3/L4 Chunker + Ingest
任务 9/10 在 API 契约层存在 3 处跨 task 冲突(`extract_structure` 平面化 vs 消费方假设嵌套、heading 解析在 task9 重复实现且正则有 bug、CLI 复用 `ingest_file` 而非 `ingest_directory`),事务边界把高延迟 embed 调用圈在内会持锁 10s+,`task9._step_headings` 实际不能解析多级标题。
---

### agent6 — L4 Fusion + Filter


### agent7 — L5 Query Extension
task13 三个子模块(query_ext / image_caption / decomposition)内部实现细节充分,关键 B1/B2/B7/B8/B9 修复已落地,但存在 **B3 跨 task 契约冲突、image_caption↔query_ext 数据流断裂、LLM semaphore 与 L2 cache 集成缺位、nest_asyncio 反模式** 四类阻塞级问题,且 stub 与 Pydantic 真实类型、stub 与 impl 签名、测试 mock 与构造函数之间存在多组不匹配,会直接破坏 TDD 的 RED 阶段。
---

### agent8 — L5 Subgraph + Orchestrator
task14 的契约层有 1 个会直接破坏重排评分的 P0 公式错用(`intra_fusion(rrf_k=weight)`)与 1 个 spec §0.1 L881 强制要求的 `with_fallbacks` 未实现,加上 ParentDocExpander 死代码、SearchRequest.use_rerank 未消费 2 个 P0 缺口,实现层(TDD 流程、Stub 缺失、子图 filter 顺序)有 4 个 P1 偏差需在进入 task16 主流水线前收口。
---

### agent9 — L5 缺失任务审计
task15/16 文件内容结构完整、stub-first 与 TDD 流程齐备,但存在 **3 个 P0 跨 task 契约冲突**(search_key payload 字段、AuditRecord 字段集降级、INDEX/Plan 状态漂移),实施前必须消解;另有 2 个 P1 实现级 stub 未披露。

### agent10 — L6/L7/L8 CLI + Eval + CI
CLI/Eval/CI 三层基本符合 spec §9、§14、§17,但存在三处 **P0 风险**:`task20` 的
`module_targets` 把 `rag.eval.robustness`(测试目录,非源码)误列为覆盖率目标(无效);
`task19` 的 RAGAS 版本约束 `ragas>=0.3,<0.4` 面临 EOL,且 `answers.append(result.prompt)`

---

## 关键发现 — 待 跨 agent 整合分析
由 协调 step 完成(见 CONSOLIDATION.md)。
