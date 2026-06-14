确认 sandbox 拒绝写入。下面给出完整 review 全文(因 sandbox 模式 read-only,无法落盘;内容与若落盘时等价)。

---

# Architecture Review: Agent #7 — L5 Query Extension / Image Caption / Decomposition

> 审查范围:`docs/superpowers/plans/tasks/task13.md` (901 行) + 关联 spec / 上下游 task 契约
>
> **元层面备注**: `_common_context.md` 顶部与 `INDEX.md` L8 标 "task15.md, task16.md 未生成 / MISSING",但本仓库 `tasks/` 目录下 `task15.md` (301 行)、`task16.md` (535 行) **实际存在且已落盘**。该声明与现实不一致,本 review 不影响结论,但需在交付物中说明。本 task13 review 不依赖 task15/16 内容(其与 task13 无直接 API 契约)。
>
> **沙箱限制**: 当前 sandbox_mode=read-only,`/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent7_query_extension.md` 写入被 `Operation not permitted` 拒绝(已实测)。以下为完整 review 全文,内容与落盘版等价。

---

## 1. 一句话总评

task13 三个子模块(query_ext / image_caption / decomposition)内部实现细节充分,关键 B1/B2/B7/B8/B9 修复已落地,但存在 **B3 跨 task 契约冲突、image_caption↔query_ext 数据流断裂、LLM semaphore 与 L2 cache 集成缺位、nest_asyncio 反模式** 四类阻塞级问题,且 stub 与 Pydantic 真实类型、stub 与 impl 签名、测试 mock 与构造函数之间存在多组不匹配,会直接破坏 TDD 的 RED 阶段。

---

## 2. 模块边界 / 依赖方向 / 契约一致性

| 检查项 | 结论 | 证据 (file:line) | 评级 |
| --- | --- | --- | --- |
| 三个子模块目录归属合理 | OK | `task13.md:25-31` | OK |
| pipeline 依赖 domain / infra | OK | `task13.md:528, 521` | OK |
| retrieval 子包不交叉依赖 | OK | `task13.md:51-87` | OK |
| SearchRequest 字段对齐 | OK | `task13.md:704, 677, 706` ↔ `task2.md:90-105` | OK |
| SearchRequest.query_decomposition 开关字段 | OK | `task2.md:97` 默认 False ↔ `spec:888` | OK |
| DecomposedQueries Pydantic schema | 偏离 | `task13.md:155-166` 删 is_complex;`spec:50` 文本仍写 is_complex | ⚠ |
| QueryExtensionRunnable 接受 chat_bg/histories | OK | `task13.md:602-609` ↔ `task2.md:104-105` | OK |
| ImageCaptionRunnable 消费 image_urls | 单边 | `task13.md:754` 入向 OK;产出 caption_queries 无下游消费 | ⚠ |
| LazyGreedySelector.embed_model 类型 | OK | `task13.md:209` 必填 Embeddings | OK |
| **temperature 契约 (B3)** | 跨 task 不一致 | `task7.md:374-414` 显式要求;`task13.md:677, 745` 无此形参;`task13.md:891-894` 显式声明 "内部不直接设置" | 🔴 |
| **warnings 格式 (B7)** | 跨 task 不一致 | `spec:1163` / `task6.md:358` 格式 `redis_unavailable: layer=L2`;`task13.md:896` 写 `cache_fallback: redis_unavailable` | 🔴 |
| **LLM Semaphore 集成 (spec §8.6)** | 缺失 | `task13.md:728, 768` 裸 await;`spec:1117-1153` 强制要求 | 🔴 |
| **L2 query_ext 缓存 (spec §8.1)** | 缺失 | `task13.md:677-686` 不接 cache;`task13.md:895-898` 显式声明 "不直接调用 Redis" | 🔴 |
| Step 0 stub ↔ Step 1 test 兼容性 | 不可 import | `task13.md:80-82` stub 非 Pydantic;`task13.md:798-803` 期望 Pydantic | 🔴 |
| Step 0 stub ↔ impl 签名 | 不一致 | `task13.md:62` stub `embed_model=None`;`task13.md:208` impl 必填 | ⚠ |
| 测试 mock 形态 ↔ 构造函数 | 不兼容 | `task13.md:191-202` 注入 FakeStructuredLLM;`task13.md:171-176` 调 with_structured_output → AttributeError | 🔴 |
| spec §0.1 Lazy Greedy gain 公式 | 偏离 (代码更优) | `spec:89` `(1 - max)`;`task13.md:230-238` `(1-α)·(1-max)` | ⚠ |
| task13 自述 blocker 数 | 与 INDEX 不符 | `task13.md:5` "5 Blocker";`INDEX.md:18` "5 🔴";实际列 6 个 (B1/B2/B3/B7/B8/B9) | ⚠ |

---

## 3. 发现清单(按严重度降序)

### 🔴 P0 — 必须修复(阻塞)

- **[task13.1] ImageCaptionRunnable 输出 `caption_queries` 无下游消费,数据流断裂**
  - 位置: `task13.md:783` + `task13.md:677-686` + `task16.md:427`
  - 问题: spec §0.1 (spec:117-122) 明确要求 ImageCaption 输出与 text query 合并后送入 QueryExtensionRunnable;task13 把 `caption_queries` 单独塞入 state,QueryExtensionRunnable 既不读取也不合并,caption 永远不参与检索词改写。`task16.md:427` 串联 `ImageCaptionRunnable() | QueryExtensionRunnable(...)` 但依赖 task13 内部合并,而 task13 没做。
  - 影响: 图像搜索功能实质不可用 — caption 仅在 state 中"挂着",对最终召回无贡献。
  - 建议: QueryExtensionRunnable.ainvoke 在 Stage 1 之前读取 `input.get("caption_queries", [])`,与 `input["query"]` 合并成初始候选池后再走 Stage 1;或拼入 user prompt 让 LLM 改写时同时考虑 caption。

- **[task13.2] B3 跨 task 契约冲突: temperature 参数**
  - 位置: `task7.md:374-414` ↔ `task13.md:677-686, 743-746, 891-894`
  - 问题: task7 文档显式要求 `QueryExtensionRunnable.__init__` 与 `ImageCaptionRunnable.__init__` 接受 `temperature: float = 0.1` 并透传给 `get_m3_chat_model`;task13 两个 runnable 的 __init__ 都无 temperature 形参;Step 9 自述改口称 "Task 13 内部不直接设置 temperature"。
  - 影响: task16 走 task7 承诺的 `QueryExtensionRunnable(llm=..., temperature=0.1)` 时 TypeError;走 Step 9 风格时 B3 路径被绕过。
  - 建议: 二选一 — (1) task13.__init__ 加 `temperature: float = 0.1` 形参,内部 `self._chat = get_m3_chat_model(temperature=temperature)`;或 (2) task7 Cross-Task Fixes 文档撤回,统一靠 `get_m3_chat_model` 默认 0.1。

- **[task13.3] LLM Semaphore 集成缺位**
  - 位置: `task13.md:728, 768`;`spec:1117-1153`
  - 问题: spec §8.6 明确 query+ingest 共享 LLMSemaphore;task13 两个 LLM 调用点都是裸 await,完全绕过信号量。
  - 影响: 5 dataset × 多 query variant × 多 image 场景下并发 LLM ≈ 32,远超 `max_concurrent=16`;无 semaphore 限流会触发 OpenAI 429。
  - 建议: QueryExtensionRunnable 与 ImageCaptionRunnable.__init__ 接受 `semaphore: LLMSemaphore | None = None` 形参,ainvoke 中用 `async with semaphore.run(provider, ...): ...` 包裹 LLM 调用。

- **[task13.4] L2 query_ext 缓存集成缺位**
  - 位置: `task13.md:677-686, 719-732, 895-898`;`spec:1050`
  - 问题: spec §8.1 把 L2 (TTL 30min, key=`rag:qext:{model}:{hash}`) 列为标准缓存;task6.md:115-251 实现 L2 Cache;但 task13 Step 10 把责任完全悬空,实际不会有人接入。
  - 影响: 高频 query 的 LLM 改写无法复用,30min 内同 query 必然重复调 LLM,token 成本翻 3-10×。
  - 建议: QueryExtensionRunnable.__init__ 加 `cache: Cache | None = None` 与 `model: str` 形参;ainvoke 入口先 `cache.get("rag:qext:{model}:{hash}", warnings=...)`,命中直接返回;未命中走 LLM 后 `cache.set(...)`。

- **[task13.5] nest_asyncio 反模式污染全局事件循环**
  - 位置: `task13.md:737-744`
  - 问题: `invoke` 中 `nest_asyncio.apply()` 全局修补 loop,允许嵌套。已知破坏 LangChain callback propagation / LangSmith tracing / JsonLoggingHandler 链路。
  - 影响: CLI / tests 同步调用 `invoke(...)` 时 LangChain callbacks 进入错乱状态,latency / token 统计失效。
  - 建议: 删除 `invoke` 同步包装。LangChain Runnable 只要求 `ainvoke`;同步场景让 caller 显式 `asyncio.run(...)`。

- **[task13.6] Step 0 stub 与 Pydantic 真实类型不兼容,RED 阶段信号失真**
  - 位置: `task13.md:80-82` (QueryVariants stub) + `task13.md:178-187` (DecomposedQueries stub) + `task13.md:798-803` (test)
  - 问题: stub 用 `class QueryVariants: def __init__(self, **kw): self.variants: list[str] = []` 非 Pydantic;tests 调用 `QueryVariants(variants=[...])` 期望 Pydantic 验证;实际 `self.variants` 始终为 `[]`,断言全失败。Step 2 期望 ImportError 实际会得 collection error。
  - 影响: TDD 闭环被破坏,RED 阶段信号不可信。
  - 建议: Step 0 stub 改为 `class QueryVariants(BaseModel): variants: list[str] = []` (Pydantic 模式)。

- **[task13.7] `test_decomposer_uses_structured_output` 测试 mock 与构造函数不兼容**
  - 位置: `task13.md:191-202` + `task13.md:171-176`
  - 问题: 测试用 FakeStructuredLLM 注入,期望 `QueryDecomposer(llm=FakeStructuredLLM())` 直接工作;但实现调 `llm.with_structured_output(...)`,FakeStructuredLLM 无此方法 → 构造函数 AttributeError,断言永远跑不到。
  - 影响: 测试 setup 崩溃,RED 阶段信号失真。
  - 建议: 改用 `dec = QueryDecomposer(llm=None); dec._structured_llm = FakeStructuredLLM()`(与同文件 `test_decomposer_accepts_chat_bg_and_histories` 模式一致)。

### 🟠 P1 — 应修复(影响实现质量)

- **[task13.8] LazyGreedySelector stub vs impl 签名漂移**: `task13.md:62` vs `task13.md:208`。建议: 保持形参一致;stub 内部 raise NotImplementedError。

- **[task13.9] `_parse_stage1_answer` 字符串替换非 regex,no-op**: `task13.md:655-659`。`.replace("(\\n|\\)", "")` 字面替换,意图删除 `\n` `\` 但实际无效。建议: `re.sub(r"[\\n\\]", "", json_str)`。

- **[task13.10] `_filter_histories_by_max_context` token 估算粗略**: `task13.md:585-619` 用 `len(content) // 2`,docstring 写 "filterGPTMessageByMaxContext 等价" 但实现不调 tiktoken。建议: 用 tiktoken;或函数改名 `_filter_histories_by_char_budget`。

- **[task13.11] `httpx.AsyncClient()` 资源泄露 + 无超时**: `task13.md:763-769` 每图新建 client,无 `async with` / timeout / aclose。建议: `async with httpx.AsyncClient(timeout=10.0) as client:`。

- **[task13.12] ImageCaptionRunnable 无 semaphore + 多图串行**: `task13.md:754-783` 串行 5 张图 ≈ 10-15s。建议: `asyncio.gather` 并发;每图走 LLMSemaphore。

- **[task13.13] ImageCaptionRunnable.invoke 同步包装在有 loop 时双开**: `task13.md:785-790` 抓 RuntimeError 后无条件 `asyncio.run(...)` 会再抛错。建议: 显式 raise 或删除 invoke。

- **[task13.14] B7 warnings 格式与 spec / task6 不一致**: `task13.md:896-898` 写 `cache_fallback: redis_unavailable` vs spec/task6 的 `redis_unavailable: layer=L2`;且 SearchResponse typo。建议: 改 `warnings.append("redis_unavailable: layer=L2")`;修 typo。

- **[task13.15] `decompose_state` 字段名契约散落多 task**: `task13.md:91-92` 返回 list[str];`task16.md:438-441` 改写 `query=sub_queries[0]`;spec §7.0.1 仍按旧 is_complex 描述。建议: task2 加 DecomposedQueries 类型作为返回契约;更新 spec §7.0.1 示例。

- **[task13.16] SearchRequest.max_query_variants ↔ QueryExtensionRunnable.max_variants 字段名不一致**: `task2.md:95` vs `task13.md:678`;task16 注入路径未明。建议: task16 显式 `QueryExtensionRunnable(..., max_variants=req.max_query_variants, ...)`。

### 🟡 P2 — 可改进

- **[task13.17] spec §0.1 Lazy Greedy gain 公式与代码不一致**: `spec:89` vs `task13.md:230-238`。建议: 更新 spec 文本为 `α·cos + (1-α)·(1-max_sim)`。

- **[task13.18] spec §7.0.1 QueryDecomposition 描述已过时**: `spec:748-781` 仍含 `_is_simple` 启发式 + is_complex + 旧 prompt。建议: 同步更新。

- **[task13.19] 缺 query_variants 中是否包含原 query 的设计决策记录**: `task13.md:686-722` 流程会把原 query 从候选剔除,Stage 2 后变体不包含原 query;FastGPT useTextCosine 实际是追加回原 query。建议: docstring 显式记录决策。

- **[task13.20] 缺可观测性 hook**: `task13.md:677-732` 无 `config={"callbacks": ...}` 注入;spec §8.7 要求每个 Runnable 节点统一 JsonLoggingHandler。建议: ainvoke 接受 `config: RunnableConfig | None`,merge callbacks。

- **[task13.21] audit #4 修复未在 step 标注处体现 spec schema 迁移**: `task13.md:155-166` 删 is_complex 但 spec 未更新。建议: Step 1 顶部加 "## Spec Delta" 段。

- **[task13.22] INDEX "5 个 🔴" 与 fix 列表 6 个 blocker 计数不一致**: `INDEX.md:18` vs `task13.md:5-12`。建议: 修 INDEX 计数。

### 🟢 P3 — 风格

- **[task13.23] task13.md 顶部 Fixes applied 重复列 audit #4 三次**: `task13.md:14-19`。建议: 合并为单条。
- **[task13.24] Step 0 stub `return [query]` 缺注释解释**: `task13.md:91-92`。建议: 加 1 行注释。

---

## 4. Spec 覆盖矩阵

| Spec 章节 | 覆盖 Task | 完整性 | 偏差说明 |
| --- | --- | --- | --- |
| §0.1 QueryExtension 节点 | task13 Step 4c | 部分 | image caption→query_ext 数据流未落地 (P0-1) |
| §0.1 QueryDecomposer 节点 | task13 Step 3 | 完整 | schema 偏离 spec 文本 (P2-21) |
| §0.1 ImageCaptionRunnable | task13 Step 5 | 部分 | semaphore / 并发缺失 (P0-3, P1-12) |
| §6 多模态大模型 Caption 路径 | task13 Step 5 | 完整 | 路径对齐 (Issue 3 主动放弃独立 vlm.py,已文档化) |
| §7.0.1 Query Decomposition | task13 Step 3 | 偏离 | spec 示例已过时 |
| §7.1 挂载点表 | task13 Step 0-10 | 完整 | 5 个挂载点均落地;语义挂载在 task16 |
| §8.1 L2 query_ext 缓存 | task13 未覆盖 (Step 10 显式排除) | 缺失 | P0-4 |
| §8.5.1 Redis 降级 warnings | task13 Step 10 | 偏离 | warning 格式不一致 (P1-14) |
| §8.6 LLM Semaphore | task13 未覆盖 | 缺失 | P0-3 |
| §8.7 可观测性 | task13 未覆盖 | 缺失 | P2-20 |
| Cross-Task B3 temperature | task7 ↔ task13 (冲突) | 偏离 | P0-2 |
| Cross-Task B7 warnings | task6 ↔ task13 (冲突) | 偏离 | P1-14 |

---

## 5. 架构风险与建议

- **风险 1: LLM 成本失控**。3 个独立 LLM 调用点全无 L2 缓存、无 semaphore;高频 query 场景 token 成本翻 3-10×,易触发 429。缓解: P0-3 + P0-4 修复;加 unit test 验证 cache hit 不调 LLM。

- **风险 2: 图像搜索静默失效**。Image caption 输出永远不进 query_variants,任何带 image_urls 的 SearchRequest 实际等价于只跑 text query,Spec §0.1 承诺的"图像+文本融合检索"被悄悄降级。缓解: P0-1 修复;加 E2E test 验证 image caption 进入 query_variants。

- **风险 3: Cross-Task 契约漂移**。task7↔task13 (B3)、task6↔task13 (B7)、task13↔task16 (decompose_state) 三组冲突,后续 6 个 task 落地前不集中收敛会指数增长。缓解: 抽 `docs/superpowers/plans/CONTRACTS.md` 集中定义 SearchRequest/Runnable/温度/warnings/sub_queries/caption_queries。

- **风险 4: TDD 闭环被 stub 破坏**。P0-6 + P0-7 让 RED 阶段信号失真,延迟发现真问题。缓解: Pydantic stub 模板 + monkey-patch mock 模式。

- **风险 5: Resource leak 在 image_caption 放大**。每图独立 httpx client + 不限超时 + 无 semaphore,image_urls=10 时分钟级首字延迟。缓解: P1-11 + P1-12 修复;加 E2E test 验证 image_caption 节点 P95 < 5s。

---

## 6. 跨 Task 一致性核查

- **task2 (Domain)**: ✅ 16 字段全部对齐;⚠ SearchRequest.temperature 字段(task2.md:97)在 task13 显式忽略,是否保留字段需澄清。
- **task6 (Cache)**: 🔴 B7 warnings 格式不一致 (P1-14);🔴 L2 缓存职责悬空 (P0-4)。
- **task7 (LLM)**: 🔴 B3 temperature 契约冲突 (P0-2);🔴 LLMSemaphore 未集成 (P0-3);⚠ `get_m3_chat_model` 默认 0.1 与 task13 Step 9 风格一致,但与 task7 Cross-Task Fixes 期望的显式透传矛盾。
- **task14 (Subgraph)**: ✅ `task14.md:803` 消费 query_variants 字段对齐;⚠ 完全未消费 caption_queries,验证 P0-1 数据流断裂。
- **task16 (full.py)**: ⚠ 假设 task13 三个 runnable 接受 `(llm=..., embed_model=...)` kwargs 对齐;⚠ `task16.md:434` `QueryDecomposer(llm=deps["chat_model"])` 会触发 with_structured_output → 与 P0-7 同一问题;⚠ `task16.md:280` 用 `max_query_variants` 构造 cache key,与 task13 `max_variants` 字段名不一致 (P1-16)。
- **task17 (CLI) / 18-20 (Eval / CI)**: 无直接契约;P0-1 / P0-4 未修复会让 E2E test 暴露图像搜索与 L2 缓存差异,eval 指标显著低于 baseline。

---

## 7. 3 条具体建议

1. **优先修复 P0-1 + P0-2**: P0-1 让图像搜索静默失效,P0-2 让 task16 集成时 TypeError。两者各 5-15 行,价值 / 成本比最高。修复后立即重跑 task13 + task16 的 E2E 集成测试。

2. **集中定义 Cross-Task 契约文档 `docs/superpowers/plans/CONTRACTS.md`**: 把 SearchRequest / Runnable / LLM Semaphore / Cache 四个核心 contract 集中定义;把 task6/task7/task13 中 B3 / B7 / L2 / semaphore 四组冲突给出**唯一权威答案**;后续 task (15-20) 统一引用,避免同类冲突再次发生。

3. **重写 Step 0 stub 与测试,采用 Pydantic stub + monkey-patch mock 模式**: P0-6 + P0-7 是 TDD 工程实践问题,fix 极小但对 RED 信号可信度影响大。推荐 (a) stub 全部用 Pydantic BaseModel 写最小可用形态;(b) test 默认用 `dec._structured_llm = FakeLlm()` 绕过构造函数副作用;(c) Step 2 期望从 "ImportError" 改为 "X failed (assertion)" 可操作信号。

---

## 摘要(终端输出)

**总评**: task13 三个子模块内部实现质量过关,B1/B2/B7/B8/B9 修复已落地,但存在 B3 跨 task 契约冲突、image_caption↔query_ext 数据流断裂、LLM semaphore 与 L2 cache 集成缺位四类 P0 阻塞问题;另有 stub/Pydantic/测试 mock 三组 TDD 不兼容会破坏 RED 阶段信号可信度。

**3 条最关键发现**:
1. **P0-1 (图像搜索静默失效)**: `ImageCaptionRunnable.ainvoke` 返回 `caption_queries` 字段,`QueryExtensionRunnable.ainvoke` 不读该字段,`task16.md:427` 串联两个 runnable 仍依赖 task13 内部合并,task13 没做 — spec §0.1 承诺的"图像+文本融合检索"被悄悄降级为纯文本。
2. **P0-2 (B3 跨 task 契约冲突)**: `task7.md:374-414` 文档要求 `QueryExtensionRunnable`/`ImageCaptionRunnable.__init__` 显式接受 `temperature: float = 0.1`;`task13.md:677, 745` 两处无此形参,`task13.md:891-894` Step 9 显式声明"内部不直接设置" — task16 集成时必撞 TypeError。
3. **P0-3 + P0-4 (semaphore + L2 cache 缺位)**: spec §8.6 强制 query+ingest 共享 `LLMSemaphore`,`task13.md:728, 768` 两个 LLM 调用裸 await;spec §8.1 L2 query_ext 缓存 (TTL 30min) 责任在 `task13.md:895-898` Step 10 显式排除 — 高频 query 场景 token 成本翻 3-10×,OpenAI 429 风险高。

**沙箱限制说明**: 当前 sandbox_mode=read-only,目标文件 `docs/superpowers/plans/reviews/agents/agent7_query_extension.md` 写入被 `Operation not permitted` 拒绝(已实测 `echo > file` 与 `touch` 均失败)。完整 review 全文已在上方给出,内容与落盘版等价;请在外部保存或解除 sandbox 后回写。