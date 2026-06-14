Sandbox 限制: `sandbox_mode=read-only`,任何路径写入均被拒绝(已用 `/tmp` / cwd / `$HOME` / 目标路径四路径验证,均 `Operation not permitted`)。以下为完整 review 内容,用户可手动落盘到 `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent4_llm_reader.md`。

---

# Architecture Review: Agent #4 — L2/L3 LLM Client + Reader

> 范围: `task7.md` (LLM Clients + Semaphore) + `task8.md` (Reader + Document Structure)
> 上游输入: spec §6.5.3, §6.5.4, §8.6, §8.7, §8.8, §13; 主 plan §1442–1646
> 上游假设: `LLMSettings` 单一定义在 `config.py` (主 plan §176)
> 旁注: `INDEX.md` 标注 task15/16 缺失,经核对实际已落盘(`task15.md` 11.9KB, `task16.md` 22.7KB),不阻塞本 review。

## 1. 一句话总评
两个 task 主体结构完整、修复链可追溯,但存在 3 类实质问题:Spec 与实现存在 2 处 Rerank 路径冲突(自研 CohereRerank vs `langchain-cohere`);`JsonLoggingHandler` 仅注册 stdlib `logging`,未接入 LangChain Callback,spec §8.7 的 stage/latency_ms/tokens 字段无法产出;Reader/Structure 与 Chunker 各自独立重建 heading,`DocumentStructure` 在 chunker 路径上实际是死代码。

## 2. 模块边界 / 依赖方向 / 契约一致性

| 检查项 | 结论 | 证据(file:line) | 评级 |
|---|---|---|---|
| 模块边界: `infra/llm/` 与 `pipeline/` 解耦 | OK,`infra/llm/` 不引用 pipeline | task7.md:13–18 | OK |
| 模块边界: `ingest/` 依赖 `domain/` 而非反之 | OK | task8.md:18, 30; spec §1 约定 | OK |
| 依赖方向: `LLMSettings` 单一定义 | OK | task7.md:48 `from rag.config import LLMSettings`; 主 plan §176 | OK |
| 依赖方向: `BaseMeta` 在 ingest 而非 domain | ⚠ 选型不一致:domain 全 Pydantic,task8 用 `@dataclass` | task8.md:73 `@dataclass class BaseMeta` | ⚠ |
| 依赖方向: `JsonLoggingHandler` 在 `infra/llm/chat.py` 中 | ⚠ 副作用侵入:import 即修改 root logger | task7.md:122–127 | ⚠ |
| 契约: `LLMSemaphore.run(provider, coro)` | OK,与 spec §8.6 一致 | task7.md:57–66 | OK |
| 契约: `get_chat_model(temperature=0.1, timeout=30, max_retries=0)` | OK (B3 / subagent #2 修复已落地) | task7.md:146–160 | OK |
| 契约: `get_m3_chat_model` 用 ChatOpenAI 替代 vlm.py | OK,符合"vlm.py 移除"决策 | task7.md:170–185; 主 plan §200 | OK |
| 契约: `with_structured_output(method="function_calling")` | ⚠ task7 chat.py 未实现,仅 task13/14 引用 | task7.md 全文无该方法 | ⚠ |
| 契约: Rerank = `langchain-cohere` (spec) vs 自研 (task7) | 🔴 spec §1:151 明示,task7 用 `cohere.AsyncClient` 自实现 | spec:151; task7.md:204–218 | 🔴 |
| 契约: `Reranker` Protocol = `list[tuple[int, float]]` | OK | task7.md:201–205 | OK |
| 契约: `BaseMeta` 字段 (`datasource`, `filename`) | ⚠ 缺 `dataset_id` 字段;task10 走参数旁路 | task8.md:73–76; task10.md:176 | ⚠ |
| 契约: `DocumentStructure` 与 chunker 输入对齐 | 🔴 chunker 不消费,在 task9 自建 `heading_path` 栈 | spec §6.5.3; task9.md:286–308 | 🔴 |
| Spec 一致性: 30s timeout 跨 task | OK,task7/3/16 共用同一常量 | task7.md:6, 158; task3.md | OK |
| Spec 一致性: max_concurrent=16 与 spec 并发预算 | OK,13 LLM 调用 < 16 | spec §8.6 末尾; task7.md:44 | OK |

## 3. 发现清单

### 🔴 P0 — 必须修复(阻塞)

- **[task7/spec]** Rerank 实现路径与 spec 冲突
  - 位置: `task7.md:201–218`; `spec:2026-06-10-python-rag-pipeline-design.md:151`; `task14.md:142–149`
  - 问题: spec §1 明确写 `调用 langchain-cohere CohereRerank (替代自实现)`,task7 用 `cohere.AsyncClient` 重写;task14 注释 `task7 src/rag/infra/llm/rerank.py 统一定义`,但 task14 又 `from rag.infra.llm.rerank import ...` 同时新建 `rerank_chunk.py`,语义混乱
  - 影响: (a) spec 与实现偏差,审计时无法对照;(b) `langchain-cohere` 已封装 retry/timeout,自实现需自行补齐(目前 CohereRerank 缺 timeout/retry,task7.md:208–217 整段无重试)
  - 建议: 选其一路径并落到 spec——若选 `langchain-cohere`,task7 改用 `from langchain_cohere import CohereRerank` 适配 `Reranker` Protocol;若保留自实现,spec 行 151 同步改写

- **[task7/observability]** `JsonLoggingHandler` 未接入 LangChain Callback,spec §8.7 不可观测
  - 位置: `task7.md:78–127`; `spec:1203`
  - 问题: `JsonLoggingHandler` 是 `logging.Handler` 子类,只接管 stdlib `logging.info(...)`;spec §8.7 要求字段 `{"ts", "stage", "latency_ms", "tokens", "cache_hit"}` 需由 LangChain Callback 在 on_chain_start/on_llm_end 等钩子里产出
  - 影响: (a) LangSmith 走 `os.environ.setdefault("LANGSMITH_TRACING", "true")` 需 `LANGCHAIN_API_KEY` 才上报,本地/CI 无 key 静默失效;(b) `latency_ms` / `tokens` / `cache_hit` 三个关键字段无法产出,运维/成本归因无数据
  - 建议: 新增 `class JsonLoggingCallback(BaseCallbackHandler)`,在 `on_chain_start/end`、`on_llm_end`(读 `response.llm_output.token_usage`)、`on_chain_end`(累计 latency) 采集并写入 jsonl

- **[task8/chunker]** `DocumentStructure` 在 chunker 路径上是死代码
  - 位置: `task8.md:117–149`; `task9.md:286–308`; `spec:666–687`
  - 问题: task8 的 `extract_structure` 解析 `heading_tree` (flat list,无 children 嵌套);task9 的 `_step_headings` 独立构建 `heading_path: list[str]` 栈,完全不读 `DocumentStructure`。spec §6.5.3 明示 "heading_tree 序列化为 JSON 存 metadata"
  - 影响: (a) heading 解析跑两次(extract_structure 一次 + _step_headings 一次),无收益;(b) task8/structure.py 沦为废弃模块
  - 建议: 删除 task8 的 `extract_structure` heading 部分,只输出 `has_code_blocks` / `has_tables` / `list_nesting_depth` / `page_count`;heading 由 chunker 统一产出;或者让 chunker 接收 `DocumentStructure` 并消费 `heading_tree.children`(需先把 flat 改回真树)

### 🟠 P1 — 建议修复(中风险)

- **[task7/embed]** `_RetryableEmbeddings` 与 langchain-openai 内置 `max_retries` 叠加
  - 位置: `task7.md:189–197` (tenacity 3 次) + `OpenAIEmbeddings` 内部默认 `max_retries=2`
  - 问题: chat 显式 `max_retries=0`,embed 未做对应设置,tenacity × langchain = 最多 6 次重试
  - 影响: 一次 embed 失败最坏 ~63s,期间持 semaphore 槽位
  - 建议: `_RetryableEmbeddings.__init__` 显式 `super().__init__(..., max_retries=0)`

- **[task7/observability]** `configure_json_logging()` 在模块 import 时即调用,副作用侵入
  - 位置: `task7.md:122–127`
  - 建议: 改为显式入口,CLI / FastAPI lifespan 启动时显式调用,`--json-logging` flag 或 `RAG_JSON_LOGGING=true` 触发

- **[task7/rate-limit]** `_check_rpm` 唤醒时 thundering herd
  - 位置: `task7.md:67–77`
  - 问题: window 满时所有协程算得近似相同的 `sleep_for`,唤醒同步
  - 建议: `sleep_for` 叠加 `random.uniform(0, 0.5)` jitter

- **[task7/rerank]** `CohereRerank` 无超时/重试
  - 位置: `task7.md:208–217`
  - 建议: `RerankRunnable` (task14) 入口 `asyncio.wait_for(rerank, timeout=30.0)` 包一层

- **[task8/reader]** Reader 全部 `encoding="utf-8"`,无编码探测
  - 位置: `task8.md:82, 86, 100, 106`
  - 建议: `charset-normalizer` 在 utf-8 失败时重试探测

- **[task8/reader]** Reader 全部同步,大文件会阻塞事件循环
  - 位置: `task8.md:79–110`
  - 建议: 提供 `async def aread_file(p)` 包装 `loop.run_in_executor(None, read_file, p)`

- **[task8/reader]** Reader 无 magic-bytes 探测
  - 位置: `task8.md:79–110`
  - 建议: dispatch 前 `python-magic` 探测真实类型,签名不匹配时 `ValueError`

- **[task8/structure]** `DocumentStructure` 缺关键实现:`list_nesting_depth` 与 `page_count` 仅声明不填充
  - 位置: `task8.md:121–128`(model) vs `task8.md:140–149`(`extract_structure` 未填)
  - 建议: list_nesting_depth 用缩进层级映射;page_count 改为 BaseMeta 字段,从 PDF reader 填

- **[task8/structure]** `extract_structure` 的 `heading_tree` 是 flat list,`children` 永远空
  - 位置: `task8.md:132–137, 145–148`
  - 建议: 加 `_nest(flat)` 构造真树,或删除 `children` 字段避免误导

- **[task7/embed/token]** 无 token 用量统计与限流
  - 位置: 整个 `task7.md` 无 token 计量点
  - 建议: `TokenTrackingCallback(BaseCallbackHandler): on_llm_end` 累加到 `LLMSemaphore._token_count[provider]`

### 🟡 P2 — 可改进(低风险)

- `[task8/model]` `BaseMeta` 用 `@dataclass` 而非 Pydantic `BaseModel` — `task8.md:73–76`,建议改 Pydantic
- `[task7/init]` 缺失 `tests/unit/test_chat.py` / `test_embed.py` / `test_rerank.py` — `task7.md:14` 仅列 semaphore 测试,chat/embed/rerank 无覆盖
- `[task8/reader]` Reader 无文件大小上限 — 全部 `read_*` 函数
- `[task8/reader]` `read_json` 行为含 list/dict 两种约定,文档缺失 — `task8.md:105–111`
- `[task7/observability]` Verification checklist #4 时序脆弱 — `task7.md:285` `time.sleep(6)`,CI 繁忙时偶发失败
- `[task7/cross-task]` task13.md:893 引用 `get_openai_chat_model`,task7 实际叫 `get_chat_model` — 命名漂移
- `[task7/spec]` `M3-multimodal` 模型名疑似占位 — `task7.md:181`
- `[task7/semaphore]` lazy-create per-provider semaphore 用了 magic 16 — `task7.md:60–62`
- `[task7/rerank]` `NoOpRerank` 合成 score `1.0 - i * 0.01`,i 大时为负 — `task7.md:220–222`,建议改 `1.0 / (i + 1)`

## 4. Spec 覆盖矩阵

| Spec 章节 | 覆盖 Task | 完整性 | 偏差说明 |
|---|---|---|---|
| §6.5.1 复杂 PDF(简化) | task8.md:88 `read_pdf` 用 pypdf | ✅ 完整 | 接受 spec 决策 |
| §6.5.2 表格 chunk 策略 | task9 | ✅ 完整 | task8 输出 markdown,具体切分 task9 负责 |
| §6.5.3 文档结构提取 | task8.md:117–149 | ⚠ 部分 | `list_nesting_depth` / `page_count` 仅声明未填;`heading_tree` flat |
| §6.5.4 增量更新原子性 | task10 | ✅ 完整 | reader 返回 BaseMeta 含 filename |
| §8.6 并发控制 | task7.md:43–80 | ✅ 完整 | LLMSemaphore + per-provider + 60s RPM |
| §8.7 可观测性 | task7.md:78–127 | 🔴 偏差 | JsonLoggingHandler 是 stdlib handler,非 Callback;`stage`/`latency_ms`/`tokens`/`cache_hit` 无采集 |
| §8.8 Ingest 异步化 | task10 + task7 semaphore | ✅ 完整 | 共享全局 semaphore |
| §1:151 Rerank = `langchain-cohere` | task7.md:201–222 | 🔴 偏差 | 自研 `cohere.AsyncClient`,与 spec 不同 |
| §1:200 vlm.py 移除 | task7.md:170–185 | ✅ 完整 | 通过 ChatOpenAI + M3 base_url 实现 |
| 主 plan §200 `temperature 不支持 0.0` | task7 B3 + 跨 task12/13 同步 | ✅ 完整 | task2/12/13 均 B3 标记 |
| 主 plan §204 `with_structured_output method="function_calling"` | task7.chat.py 无 | ⚠ 缺 | 仅 task13/14 调用,factory 未注入默认 method |

## 5. 架构风险与建议

- **风险 1**: `LLMSemaphore` 限流与 LangChain 内部重试叠加,embed 路径形成 6 次重试 — 缓解: `_RetryableEmbeddings` 显式 `max_retries=0`
- **风险 2**: Spec 描述的可观测性与 task7 实现严重错位,task16 集成会冲突 — 缓解: 重构为 `BaseCallbackHandler` 子类
- **风险 3**: Reader/Structure 与 Chunker 重复实现 heading 解析,`DocumentStructure` 沦为死代码 — 缓解: 二选一(P0-3)
- **风险 4**: Rerank 路径(spec vs 自研)与实现不一致,自研路径无 retry/timeout — 缓解: 选 `langchain-cohere`
- **风险 5**: Reader 同步 + 无编码探测 + 无大小限制,中文 GBK / 大文件场景静默失败 — 缓解: P1 三个修复合并
- **风险 6**: task7 仅测了 semaphore,chat/embed/rerank 工厂无单测 — 缓解: 补充 3 个 test_*.py

## 6. 跨 Task 一致性核查

| 主题 | task7/8 立场 | 其他 task 立场 | 一致性 |
|---|---|---|---|
| Rerank 入口 | `src/rag/infra/llm/rerank.py` 自研 Protocol | task14.md:149 import, task14 新建 `rerank_chunk.py` re-export | ⚠ 双 re-export |
| `temperature=0.1` 透传 | task7.md:155, 183 工厂默认 | task2/12/13 接受参数 | ✅ B3 同步完成 |
| `with_structured_output(method="function_calling")` | task7 工厂未注入 method | task13.md:203, 618 调用 | ⚠ caller 显式传 method,易遗漏 |
| `timeout=30.0` 常量 | task7.md:158, 184 (chat); embed 未设 | task3.md; task16 orchestrator 复用 | ✅ task3/7/16 统一(embed 缺位) |
| `LLMSemaphore(LLMSettings)` 单例 | task7.md:79 | task10.md:139 共享 | ✅ |
| `BaseMeta.filename` | task8.md:74 | task10.md:176 走参数 | ⚠ task10 未消费 BaseMeta |
| Reader 返回值 | task8 `(text, BaseMeta)` | task10.md:134 解构 (text, meta) | ✅ |
| `DocumentStructure` 消费方 | task8 输出 | task9 自建 `heading_path` 栈;task10 序列化 metadata | 🔴 死代码 |
| `M3-multimodal` 模型名 | task7.md:181 | task13.md:746 默认参数 | ⚠ 命名占位 |
| `JsonLoggingHandler` 调用入口 | task7.md:122–127 import 时自动调用 | task17 CLI、task16 orchestrator 尚未接管 | ⚠ 双重注册风险 |

## 7. 3 条具体建议

1. **重组可观测性实现**: 拆为 `JsonLoggingCallback(BaseCallbackHandler)`(在 on_chain_start/end、on_llm_end 中采集 stage/latency_ms/tokens)+ `JsonLogSink`(写 jsonl)。task16 入口通过 `RunnableConfig(callbacks=[JsonLoggingCallback()])` 注入,移除 import 时副作用调用,改为 CLI 显式 `setup_logging()`。

2. **解决 `DocumentStructure` 死代码问题**: 选 (a) 路径——删除 task8 heading 解析,`extract_structure` 只输出 `has_code_blocks` / `has_tables` / `list_nesting_depth` / `page_count`(page_count 改为 reader `BaseMeta` 字段);heading 解析由 task9 唯一负责,task9 内部仍用 heading_path 栈计算 chunk 的 `parent_title`。spec §6.5.3 的 "heading_tree 序列化为 JSON 存 metadata" 承诺由 chunker 端统一产出。

3. **Rerank 路径归一**: 与 spec §1:151 对齐,使用 `langchain-cohere` 的 `CohereRerank` 适配 `Reranker` Protocol——`LCCohereRerank(protocol adapter)` 内部转调 `langchain_cohere.CohereRerank`,在调 `compress_documents` 处加 `asyncio.wait_for(..., timeout=30.0)` 兜底,task14 取消 `rerank_chunk.py` 双 re-export。

---

## 终端摘要(≤500 字)

**Sandbox 限制**: 全部路径写入被拒(read-only),review 内容已在上方完整给出,需手动落盘至 `docs/superpowers/plans/reviews/agents/agent4_llm_reader.md`。

**3 条最关键发现**:
1. **P0 Rerank 路径与 spec 冲突**(`task7.md:201–218` vs `spec:151`): spec 要求 `langchain-cohere`,task7 用 `cohere.AsyncClient` 自研,task14 又新建 `rerank_chunk.py` 双 re-export;自研路径无 retry/timeout,与 chat 路径风格不一致。
2. **P0 可观测性错位**(`task7.md:78–127` vs `spec §8.7`): `JsonLoggingHandler` 是 stdlib `logging.Handler`,非 `BaseCallbackHandler`,spec 要求的 `stage`/`latency_ms`/`tokens`/`cache_hit` 字段无任何钩子产出;LangSmith 仅靠 env,缺 API key 静默失效。
3. **P0 `DocumentStructure` 死代码**(`task8.md:117–149` vs `task9.md:286–308`): task9 的 `_step_headings` 独立建 `heading_path` 栈,完全不读 `DocumentStructure`;`heading_tree` 实际是 flat list(`children` 永远空),且 `list_nesting_depth` / `page_count` 仅声明未填。

**总评**: 主体结构完整、修复链可追溯(B3 / subagent #2 / audit #1-2 均落地),但 3 处 P0 偏差(spec vs 实现)需在 task16 Build Full Pipeline 前对齐,否则集成期返工成本高。