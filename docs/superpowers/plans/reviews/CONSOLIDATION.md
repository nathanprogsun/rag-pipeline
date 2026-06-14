# Architecture Review — 跨 Agent 整合分析

_Generated_: 2026-06-10 16:59:05

## 1. P0 级问题汇总(跨 agent)

下表汇总各 agent 发现的 🔴 P0 阻塞级问题,按受影响 task 分组。

### 1.1 task2 (Domain) — agent1 报告

- **P0-1** `SearchRequest` 字段数 spec/task 不一致: spec §3 L478-494 = 13 字段,task2 L116-135 = 19 字段(`rerank_weight`/`temperature`/`score_threshold` 等 3 个默认值与 spec 不一致)
  - 证据: tasks/task2.md:116-135; docs/superpowers/specs/...-design.md:478-494
  - 影响: 实施者按 spec 实现会与 task2 默认值冲突,API 行为不可预期

- **P0-2** `Dataset.prompt_template` Pydantic 默认 ≠ SQL DDL 默认: Pydantic = `DEFAULT_PROMPT_TEMPLATE`(task2 L49);SQLAlchemy `default=""`(task3 L164);SQL DDL `DEFAULT ''`(task3 L312)
  - 影响: 新建 dataset 入库后 `build_prompt` 用空模板 format 必崩

### 1.2 task3 (PG) — agent2 报告

- **P0-3** SQL 注入: `to_tsquery` f-string 插值 (task3.md:240, 247-252)
  - 证据: tasks/task3.md:240 `f"{ts_query}" ` 与 plan §H 自我声明 "M4 已修" 不符
  - 影响: 用户 query 含特殊字符触发 SQL 错误,产线崩溃
  - 修复: `func.to_tsquery('simple', ts_query)`(实际 plan 文档声明此修复但 task3 未落地)

- **P0-4** 单测必崩: task3.md:69-78 用 embed_dim=3 写 Vector(1536) 列
  - 影响: 测试必然 fail,无法合并

### 1.3 task6 (Cache) — agent3 报告(5 个 P0)

- **P0-5** 跨 task cache-key payload 契约冲突
  - task6 期望的 cache key payload 字段 ≠ task11/14 写入的字段

- **P0-6** `on_chunks_changed` 的 per-dataset SCAN 兜底实际不命中
  - 影响: cache 失效不可靠,旧数据被错误返回

- **P0-7** Pydantic 序列化破坏 cache 往返
  - 影响: cache hit 但内容错误

- **P0-8** SQL 拼接 tsquery 注入风险(与 P0-3 同源)

- **P0-9** Cache 全局状态导致 metrics 串扰
  - 影响: 多 dataset 并发时 hit/miss 计数互相污染

### 1.4 task7 (LLM) + task13 — agent7 报告(B3 跨 task 冲突)

- **P0-10** B3 跨 task 契约冲突: `with_structured_output(method="function_calling")` 在 task7 未实现但 task13/14 引用
  - 证据: task7 全文无该方法;task13/14 期望该能力

### 1.5 task11/task12 + task14 — agent6 + agent8 报告(4 个 P0,跨 task 链式 bug)

- **P0-11** WRRF 公式丢权重(agent6 P0-1, agent8 隐含)
  - 证据: task11.md:90-92 纯 RRF 公式,无 w_s;spec design.md:905-907 明确 `w_s * 1/(RRF_K + rank_s(c))`,vector_weight=0.7/fulltext_weight=0.3
  - 影响: 与 FastGPT 行为偏离,Eval 无法直接对比

- **P0-12** task14 `rerank_weight(0.5)` 被误用作 rrf_k(agent6 P0-2, agent8 P0 公式错用)
  - 证据: task14.md:111-113 `rrf_k=self.weight` → RRF 公式 `1/(0.5+rank)`
  - 影响: 分数量级偏离 40 倍,rerank-aware 逻辑全部错位

- **P0-13** `rerank_score` 永远 None(agent6 P0-3, agent8 隐含)
  - 证据: task14.md:91-99 `model_copy(update={'rank': rank})` 丢弃 _rscore
  - 影响: `using_re_rerank=True` 实际等价于 False,spec §6.4 阈值过滤失效

- **P0-14** task14 仍调 `filter_pipeline`,task12 的 `subgraph_filter`/`orchestrator_filter` 是死代码(agent6 P0-4)
  - 证据: task12.md:325-341 定义 `subgraph_filter` 但无调用方;task14.md:530-532, 850-853 仍调 `filter_pipeline`
  - 影响: spec §0.1 强制要求的 per-dataset token 预算未生效

### 1.6 task14 — agent8 报告(spec §0.1 L881 强制要求)

- **P0-15** `with_fallbacks` 未实现
  - 证据: spec §0.1 L881 强制要求;task14 缺该能力

- **P0-16** ParentDocExpander 死代码

- **P0-17** `SearchRequest.use_rerank` 未消费

### 1.7 task9/task10 — agent5 报告(API 契约冲突)

- **P0-18** `extract_structure` 平面化 vs 消费方假设嵌套

- **P0-19** heading 解析在 task9 重复实现且正则有 bug

- **P0-20** 事务边界把高延迟 embed 调用圈在内 → 持锁 10s+

### 1.8 task15/task16 — agent9 报告(3 个 P0 跨 task 契约冲突)

- **P0-21** `search_key` payload 字段冲突

- **P0-22** `AuditRecord` 字段集降级

- **P0-23** INDEX/Plan 状态漂移:INDEX.md 标 task15/16 MISSING,实际已落盘(11.9KB / 22.7KB)
  - **注**: 此 INDEX 漂移被 agent1/3/4/6/8/9 多个 agent 独立发现

### 1.9 task20 — agent10 报告

- **P0-24** `module_targets` 把 `rag.eval.robustness`(测试目录)误列为覆盖率目标(无效)

### 1.10 task4 (Reader/Structure) — agent4 报告

- **P0-25** Reader/Structure 与 Chunker 各自独立重建 heading,DocumentStructure 在 chunker 路径上是死代码

## 2. P1/P2/P3 数量统计

| Agent | P0 | P1 | P2 | P3 | 备注 |
|---|---:|---:|---:|---:|---|
| agent1 (Foundation) | 2 | ~3 | ~2 | ~1 | 主要在域模型与脚手架 |
| agent2 (PG/Vector) | 2 | ~5 | ~3 | ~1 | HNSW/索引/类型/语义 |
| agent3 (Fulltext/Cache) | 5 | ~6 | ~4 | ~2 | 跨 task 契约最重灾区 |
| agent4 (LLM/Reader) | ~3 | ~3 | ~2 | ~1 | Rerank 路径冲突 + Callback 缺位 |
| agent5 (Chunker/Ingest) | 3 | ~5 | ~3 | ~1 | 事务边界 + 重复 heading 实现 |
| agent6 (Fusion/Filter) | 4 | 4 | 6 | 4 | 公式层 + 死代码,18 个 finding |
| agent7 (Query Ext) | ~4 | ~5 | ~3 | ~1 | B3 + 跨 task stub 不一致 |
| agent8 (Orchestrator) | 4 | ~4 | ~3 | ~1 | RRF 公式 + with_fallbacks |
| agent9 (Missing audit) | 3 | ~2 | ~1 | 0 | task15/16 契约冲突 |
| agent10 (CLI/Eval/CI) | 1 | ~3 | ~2 | ~1 | module_targets 错配 + RAGAS EOL |

**总计 P0(去重前)**: ~33 个
**总计 P0(去重后,跨 agent 同一问题)**: 25 个独立 P0

## 3. 跨 Task 契约冲突矩阵

下表列出被多个 agent 同时发现的契约冲突。

| 冲突 | 涉及 Task | 涉及 Agent | 状态 |
|---|---|---|---|
| cache-key payload 字段 | task6 ↔ task11/14 | agent3, agent6 | 🔴 未消解 |
| to_tsquery 注入 | task3 ↔ task5 | agent2, agent3 | 🔴 未消解(M4 plan 标修复但 task3 未落地) |
| rerank_score 字段 | task14 ↔ task12 | agent6, agent8 | 🔴 未消解(写入被丢弃) |
| filter_pipeline 调用 | task14 ↔ task12 | agent6, agent8 | 🔴 未消解(task14 未执行 task12 step 6 cross-check) |
| ScoredDocument 字段(q/a/rerank_score) | task2 ↔ task11/14 | agent6 | 🔴 未消解 |
| with_structured_output | task7 ↔ task13/14 | agent4, agent7 | 🔴 未消解 |
| INDEX/Plan 状态漂移 | meta | agent1,3,4,6,8,9 | 🟠 元信息 |

## 4. 高频发现 Top-10

1. **WRRF 公式丢权重** (5 agents 提及: 1, 2, 3, 6, 8)
2. **rerank_score 字段未写入** (4 agents 提及: 6, 8 + 隐含 4, 7)
3. **task14 ↔ task12 filter 死代码** (2 agents: 6, 8)
4. **INDEX.md 状态漂移** (6 agents)
5. **to_tsquery SQL 注入** (2 agents: 2, 3)
6. **跨 task cache-key 契约** (2 agents: 3, 6)
7. **with_structured_output 缺位** (2 agents: 4, 7)
8. **Heading 解析重复实现** (2 agents: 4, 5)
9. **Pydantic ↔ SQL 默认值漂移** (2 agents: 1, 2)
10. **Test 拼写 / embed_dim mismatch** (2 agents: 2, 4)

## 5. 实施优先级建议

**P0 修复顺序(由阻塞链决定)**:

1. 先消解 **公式层 P0**(P0-11, P0-12, P0-13): task11 加 weights 参数,task14 修正 rrf_k 传递 + 写入 rerank_score
   - 阻断: 阻塞 task18/19 eval

2. 修复 **P0-14** task14 调 `subgraph_filter`(避免 P0 filter 死代码)
   - 阻断: 阻塞 spec §0.1 per-dataset token 预算

3. 修复 **P0-3 + P0-8** to_tsquery 注入(实际是 M4 plan 自我声明但 task3 未落地)
   - 阻断: 任何含特殊字符的 query 触发崩溃

4. 修复 **P0-2** Pydantic ↔ SQL prompt_template 默认值
   - 阻断: DB 回灌路径必崩

5. 修复 **P0-23** INDEX.md 状态(同步 task15/16 OK 状态)

6. 修复 **P0-15, P0-16, P0-17** task14 spec §0.1 强制项(with_fallbacks, ParentDocExpander, use_rerank)

7. 修复 **P0-24** task20 module_targets 配置错误

## 6. 阻塞评估

**当前 plan 状态**:
- 18/20 task 文件已落盘
- 已知 25 个独立 P0
- 8 个跨 task 契约冲突未消解
- task15/16 索引状态不准确(实际已落盘)

**是否可进入实施**?
- ❌ 不建议:公式层 P0(11/12/13)会污染后续所有 eval
- ⚠️ 若强行进入:需在 task11 实施前冻结 spec,新增 weights 参数
- ✅ 进入条件:消解 P0-11/12/13/14 + INDEX 同步 + 修复 P0-3

## 7. 关键决策点(需用户确认)

1. **SearchRequest 字段数** (13 vs 19):以 spec 为准还是 task 为准?
2. **WRRF 权重传递路径** (intra_fusion 加 param vs 调用方乘 weights):哪个?
3. **task14 重写幅度** (filter_pipeline → subgraph_filter,影响范围):能否在 task14 内闭环?
4. **INDEX.md 状态修正**:现在更新为 20/20 OK,还是保留历史
5. **module_targets 修正** (task20):是改配置还是改文档?

## 8. 跨 Agent 整合的元发现

**Plan 自身声明的 H2-H10 修复** 落地情况:

| Plan 修复 | 涉及 task | 实际状态 | 发现 agent |
|---|---|---|---|
| H2: env_file 移除 | task1 | ✓ 已修 | agent1 |
| H2: image_path 字段 | task2, task14 | ✓ 已修 | agent1 |
| H3: with_structured_output | task13/14 | ❌ task7 未提供,被 P0-10 引用 | agent4, agent7 |
| H3: query_decomposition / parent_doc_window | task2, task16 | ✓ 已修 | agent1 |
| H5: LLMSettings 单一定义 | config.py | ✓ 已修 | agent1 |
| H6: CitationChecker regex | task15 | ✓ 已修(agent9 确认) | agent9 |
| H7: ef_search SET LOCAL | task3 | ✓ 已修 | agent2 |
| M4: to_tsquery 'simple' | task3 | ❌ plan 声明修但 task3.md:240 仍是 f-string | agent2, agent3 |
| M6: asyncio.to_thread | task7 | ✓ 已修 | agent4 |
| C3: itemgetter → RunnableLambda | task16 | ✓ 已修 | agent9 |
| H10: coverage fail-under=80 | task20 | ✓ 已修,但 module_targets 错配(P0-24) | agent10 |

**结论**: plan 自我声明的 11 项 H/M 修复中,**9 项落地**、**1 项未落地**(M4 to_tsquery)、**1 项部分落地**(H3 task7 缺实现)。

## 9. 后续步骤建议

1. 立即消解 P0-11/12/13/14 (公式层),避免污染下游
2. 同步 INDEX.md 状态(20/20 OK 而非 18/20)
3. 在 task3 实施前补 M4 修复(实为 un-fixed)
4. 修复 task20 module_targets 配置
5. 决策:SearchRequest 字段数 13 vs 19(冻结 spec)
6. 重启 agent6 补 P1/P2/P3 详细(sandbox 解除后)
