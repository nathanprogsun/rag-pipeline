Confirmed all key findings. The sandbox blocks all writes including the review file. I'll deliver the full review in this final response.

---

# Architecture Review: Agent #10 — L6/L7/L8 CLI + Eval + CI

> 范围:`task17` (CLI), `task18` (Eval L2), `task19` (Eval L3 / Regression), `task20` (CI + Coverage)
> 文件:`docs/superpowers/plans/tasks/task{17,18,19,20}.md`(行号以此为准)
> Spec:`docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` §9 / §14 / §17
>
> **状态更正**:Agent 启动说明声称 `task15.md, task16.md 缺失`,经核查两者均已落盘
> (301 / 535 行,见 `INDEX.md:14-15`)。本 review 因此能基于完整 L5+ 信息审查 CLI/Eval/CI。
>
> **沙箱限制**:本会话 `sandbox_mode=read-only`,无法写入 review 文件至
> `docs/superpowers/plans/reviews/agents/agent10_cli_eval_ci.md`(已 `touch` / `cat >` 两次试写
> 均 `Operation not permitted`)。完整 review 内容如下,可由用户手动落盘。

## 1. 一句话总评

CLI/Eval/CI 三层基本符合 spec §9、§14、§17,但存在三处 **P0 风险**:`task20` 的
`module_targets` 把 `rag.eval.robustness`(测试目录,非源码)误列为覆盖率目标(无效);
`task19` 的 RAGAS 版本约束 `ragas>=0.3,<0.4` 面临 EOL,且 `answers.append(result.prompt)`
使 faithfulness 指标语义失效;`task19` 的 L1 评测 `l1_metrics.py` 无单测、缺 spec §9.5.1
三项指标(jieba OOV / embedding cosine / 标题继承)。

## 2. 模块边界 / 依赖方向 / 契约一致性

| 检查项 | 结论 | 证据(file:line) | 评级 |
|------|------|-------------|------|
| CLI 与 library 双模式(H2 修正) | ⚠ CLI 显式调 `asyncio.run(_run())` 正确,但 `try: _loop = asyncio.get_running_loop()` (`task17.md:42-45`) 是死代码 — typer 命令体不在 loop 内,导入即丢弃 | `task17.md:42-45` | ⚠ |
| CLI 子命令(6 个) 与 spec §11 启动流程对齐 | OK search / ingest / eval / audit / cache / chunk 6 个齐;`search --chat-bg / --histories-file` (`task17.md:71-73`) 对齐 spec §6.2 | `task17.md:60-260` | OK |
| CLI 进度展示 | OK `ingest` 走 `[N/M] filename → chunks_count` (`task17.md:160-164`),对齐 audit #4 | `task17.md:160-164` | OK |
| CLI JSON 输出 / 退出码 | 🔴 全文 `typer.echo()` 文本输出,无 `--json` 选项;`search` 在 dataset 不存在时 `continue` 不返回非零(`task17.md:111`);CI consumer 不可解析 | `task17.md:107,111,156-175,209,218` | 🔴 |
| 配置文件 / env_file 加载 | 🔴 `from rag.config import settings` (`task17.md:48`) 直接 import,但 library/CLI 双模式(H2) 要求 `env_file` 显式注入未在 task17 落地 | `task17.md:48` | 🔴 |
| Eval L1(L1 组件级) 归属 | ⚠ spec §9.5.1 在 plan 未单列 task,L1 metrics 落在 task19 Step 4b (`tests/eval/l1_metrics.py`),与 L2/L3 同 task 共存。归属 task19 合理但缺独立单测 | `task19.md:275-302` | ⚠ |
| Eval L2 goldset.jsonl 维护 | 🔴 仅有 2 条 stub(`task18.md:320-321`),spec §14.3 目标 50-100 条;LLM 改写式增量无脚本 | `task18.md:320-321` | 🔴 |
| Eval L2 synthetic 可复现性 | 🔴 `random.sample(chunks, ...)` (`task18.md:242`) 未接 `random.seed()`,两次跑样本不同 → 指标不可比 | `task18.md:242` | 🔴 |
| Eval L2 retrieval_metrics | OK recall@K / precision@K / MRR / NDCG / Hit Rate + chunk/entity 双层 recall,subagent #5 修正落地 | `task18.md:144-196` | OK |
| Eval L3 RAGAS 4 指标 | ⚠ 4 指标在 `task19.md:179-200` 实现,但 `answers.append(result.prompt)` (`task19.md:202`) 把 prompt 字符串当作 LLM 答案,faithfulness 失去意义 | `task19.md:202` | 🔴 |
| Eval L3 报告格式 | ⚠ task19 仅 `print(result)` (`task19.md:207`),无 HTML/JSON/CSV 落盘,CI 无法消费;spec §9.6 "weekly 跑通输出报告" 未满足 | `task19.md:207` | ⚠ |
| Eval L3 Regression Testing | OK Jaccard ≥ 0.95 (subagent #4 修正) + 25 query 集(含 SQL 注入 / 2000 字符 / 空串 / 中英混合),`test_regression.py` 覆盖 query_extension 路径 | `task19.md:91-104, 107-130` | OK |
| Eval L3 robustness.py | OK typo / synonym / reorder 3 类 + HALLUCINATION_QUERIES 3 条 (spec §17) | `task19.md:213-260` | OK |
| CI 容器管理 | OK testcontainers 自动起 pgvector/pg16 + redis/7,避免端口冲突;`on-PR` 步骤不依赖 docker compose | `task20.md:38-40` | OK |
| CI 覆盖率阈值 | 🔴 `--cov-fail-under=80` (`task20.md:50`) 在 unit step 触发,只覆盖 unit 范围;H10 说 "合并 unit+integration",但 integration step 用 `--cov-append` 累加后才有完整覆盖,而 unit step 已 fail-fast;最终 coverage gate 跑在更晚 step | `task20.md:48-66` | 🔴 |
| CI 覆盖率模块目标 | 🔴 `rag.eval.robustness` (`task20.md:134`) 是 `tests/eval/robustness.py`(测试目录,非 `src/rag/`),`--cov=src/rag` 不会采集该路径,目标值无效 | `task20.md:113-135` | 🔴 |
| CI module path 正确性 | OK F2 P0 已修正:`rag.query.extension` → `rag.pipeline.query_ext`,`rag.audit.citation_check` → `rag.retrieval.citation_check` | `task20.md:8, 122-132` | OK |
| CI weekly RAGAS 稳定性 | ⚠ weekly job 在 schedule 触发 (`task20.md:75-99`),不在 PR 反馈循环;但 spec §9.6 "weekly 全量 + 抽检" 缺工件归档(`--junitxml=reports/...` 有,reports 路径未在 retention 配置) | `task20.md:73-99` | ⚠ |
| CI lint / type check | OK `ruff check src tests` + `mypy src` (`task20.md:68-69`) | `task20.md:67-69` | OK |
| CI 并发安全 | ⚠ on-PR / weekly 各自独立 job,但缺 `concurrency: group: ${{ github.workflow }}-${{ github.event.pull_request.number }}` 防止同一 PR push 触发多个 in-flight 容器 | `task20.md:12-99` | ⚠ |
| Audit/Cite 旁路挂载 (spec §0.1 L226 / §7.0.3) | OK task15 已落盘,CLI `rag audit --last=20` 通过 `RetrievalAudit.tail(n)` 接入;L6 trade-off (jsonl 非原子写) 文档化 | `task15.md:215-218` / `task17.md:217-219` | OK |
| cache hit_rate 在 CI 中退化 | 🔴 CI 默认 `--cov-append` 后无 `--cov-report=xml` 持久化覆盖详情;`caches_have_no_stale` 子步骤加了(subagent #7)但未做 L1/L2/L3/L4 命中率 gate | `task20.md:55-58` | 🔴 |
| 跨 task 契约: SyntheticQuestion | ⚠ Step 0 stub (`task19.md:36-40`) 与 Step 4e (`task19.md:385-392`) 是两个版本,Step 1 test (`task19.md:107-114`) 用 Step 0 schema;Step 4e 实施后,Step 0 stub 需同步覆盖 | `task19.md:36-40, 107-114, 385-392` | ⚠ |

## 3. 发现清单(按严重度降序)

### 🔴 P0 — 必须修复(阻塞)

- **[task20.1] `rag.eval.robustness` 是测试目录,被错列为 src 覆盖率目标**
  - 位置: `task20.md:113-135` (`[[tool.coverage.report.module_targets]]` 段第 5 项)
  - 问题: `rag.eval.robustness` 对应 `tests/eval/robustness.py` (`task19.md:213-260`),该路径不在 `--cov=src/rag` 采集范围。coverage 工具不会报错,但目标值 70% 永远不会被检查。
  - 影响: 模块目标 "70%" 形同虚设;后续 `rag.eval.*` 命名易蔓延。
  - 建议: 移除该项 target(测试目录无覆盖率硬性目标);若需为 `tests/eval/` 设目标,改用 `--cov=tests/eval` + 独立 job。

- **[task19.1] RAGAS 0.3.x 锁定 + `answers.append(result.prompt)` 让 faithfulness 失效**
  - 位置: `task19.md:60-69` (版本约束) + `task19.md:202` (`answers.append(result.prompt)`)
  - 问题 1: `ragas>=0.3,<0.4` 锁定 0.3.x,但 RAGAS 0.3.x 已 EOL;`from ragas.metrics import context_precision, context_recall, faithfulness, answer_relevancy` 路径在 0.4+ 仍兼容 import,但 `evaluate()` 签名在 0.4 改为 Experiment API;Step 0 注释 (`task19.md:60-69`) 承认 0.4 迁移路径,但未做"在 CI 镜像上实际 import 一次" 的 smoke test。
  - 问题 2: `result.prompt` 是构造给 LLM 的 prompt 字符串(`SearchResult.prompt`, `task2.md:147`),不是 LLM 实际生成的 `answer`。把 prompt 当 answer 喂给 RAGAS,faithfulness 会计算 "prompt 自我一致度" 而非 "answer vs context 的一致度",数值无意义。
  - 影响: weekly schedule 跑出来 0.99 不代表真优化;on-PR `test_regression` 通过但 RAGAS 指标失真。
  - 建议: (a) pin `ragas==0.3.5` 具体版本 + 加 `tests/unit/test_ragas_import.py` 显式 assert 上述 4 个 import 成功;(b) `run_ragas.py` 在 `pipeline.ainvoke(...)` 之后调 `chat_model.ainvoke(result.prompt)` 真正生成 answer,或通过 `LLMSemaphore` 控并发。

- **[task19.2] L1 评测缺 spec §9.5.1 三项指标 + 无单测**
  - 位置: `task19.md:275-302` (`l1_metrics.py` 内容) + `task20.md:88-93` (CI weekly L1 步骤)
  - 问题: spec §9.5.1 (`specs/2026-06-10-python-rag-pipeline-design.md:1283-1313`) 列出 L1 评测 5 项:chunk 长度分布 / 语义边界保持率 / 标题继承正确率 / embedding 同 section cosine / jieba OOV 率。`l1_metrics.py` 只实现前 2 项,缺后 3 项。CI weekly 跑 `tests/eval/l1_metrics.py` (`task20.md:91`) 由于该文件无 `def test_*` 函数,pytest 收集 0 个测试,step 静默 pass 0 tests。
  - 影响: spec §9.5.1 覆盖率约 40%;CI weekly 步骤无价值。
  - 建议: (a) 补 `title_inheritance_score(chunker, docs)` / `intra_inter_similarity_ratio(embed_model, chunks)` / `jieba_oov_rate(texts)`;(b) 把 L1 实现搬入 `src/rag/eval/l1_metrics.py`,新增 `tests/unit/test_l1_metrics.py` 含 3+ 测试,CI step 加 `--junitxml=reports/l1_metrics.xml` + 强制 `tests > 0` 校验。

- **[task20.2] CI coverage gate 在 unit 步骤 fail-fast,不合并 integration**
  - 位置: `task20.md:46-50` (Unit step) + `task20.md:52-58` (Integration step) + `task20.md:60-62` (Coverage gate)
  - 问题: H10 (主 plan L「CI coverage」) 说 "合并 unit+integration 覆盖率检查",但 YAML 实际顺序:unit step 自带 `--cov-fail-under=80` (`task20.md:50`),若 unit 覆盖率 ≥ 80% 即过,integration 不会"补"覆盖率;若 unit < 80% 立即 fail,根本不跑 integration。
  - 影响: 覆盖率阈值与 H10 决策不符;高 unit 覆盖 + 0 integration 也会通过。
  - 建议: 删 unit step 的 `--cov-fail-under=80`,只在最后 `Coverage gate` step 跑 `coverage report --fail-under=80`,且 unit + integration 步骤均加 `--cov-append`;或改用 `coverage combine` 合并 `.coverage.*` 文件后做 gate。

- **[task17.1] CLI `search` 在 dataset 不存在时不返回非零退出码**
  - 位置: `task17.md:108-113`
  - 问题: `if row is None: typer.echo(..., err=True); continue` — 进程 exit code 仍为 0。CI 调用 `rag search --dataset-ids=<bad-uuid>` 期望非零但拿到 0,误判为成功。
  - 影响: CI 误判、监控告警失效。
  - 建议: 加 `if not datasets: typer.echo("No valid datasets", err=True); raise typer.Exit(1)`(typer 内置 exit code 控制)。

- **[task18.1] goldset.jsonl 仅 2 条 stub,L2 指标无统计意义**
  - 位置: `task18.md:320-321`
  - 问题: spec §14.3 (`specs/...md:1636-1638`) 目标 50-100 条,Step 5 只写 2 条。L2 mean_recall@k / mean_mrr 跑 2 个样本的均值,标准误 > 0.3,几乎不能区分模型。
  - 影响: Eval L2 报告无可信结论;on-PR Recall 退化 2% block (spec §9.6) 在 n=2 下无法判定。
  - 建议: Step 5 改为"先 20 条 seed(由 owner 人工)+ 后续脚本 `gen_synthetic_queries` 跑 n=200 补足",CI 用 20 条人工集做 gate,full set 走 weekly。

### 🟠 P1 — 应当修复

- **[task17.2] CLI 全部输出 `typer.echo` 文本,无 JSON / 结构化选项**
  - 位置: `task17.md:107, 156-165, 175, 209, 218, 226`
  - 问题: agent brief "JSON 输出"需求未实现;所有命令纯文本。CI / 监控 / debug 工具无法解析。
  - 影响: 不可被 `jq` / Python 解析;CLI 沦为演示工具。
  - 建议: 加 `--json` 全局选项,命中时改 `print(json.dumps(...))`。

- **[task18.2] `random.sample(chunks, n)` 无 seed,合成数据不可复现**
  - 位置: `task18.md:242`
  - 问题: 两次 `gen_synthetic_queries(chunks, llm, n=50)` 选不同 chunk 样本,LLM 输出非确定,指标波动无法归因。
  - 影响: 同一 commit 两次 eval 结果可能 ±5%,无法判断"代码改坏" vs "采样波动"。
  - 建议: `def gen_synthetic_queries(chunks, llm, n, seed=42): random.seed(seed); sample = random.sample(...)`。

- **[task17.3] `try: _loop = asyncio.get_running_loop()` 死代码**
  - 位置: `task17.md:42-45`
  - 问题: typer 命令体在 sync 上下文,`asyncio.get_running_loop()` 一定抛 `RuntimeError`,分支 `_loop = None` 永不走;`asyncio.run(_run())` 才是有效路径。
  - 影响: 误导代码读者,以为有"共享 loop"分支。
  - 建议: 删 try/except,直接 `import asyncio`。

- **[task19.3] `run_ragas.py` 输出无 HTML/JSON/CSV 报告**
  - 位置: `task19.md:207`
  - 问题: spec §9.6 weekly "输出报告由 owner 决定";当前 `print(result)` 一次性终端输出,无 `.json` / `.html` / `.csv` 落盘,无法 diff、无法 review 历史。
  - 影响: 周报需手工抓取;无法做 trend 监控。
  - 建议: 加 `result.to_pandas().to_csv("reports/ragas-$(date).csv", index=False)` + JUnit XML 输出。

- **[task19.4] `EvalRunner` 用 row 内的 `top_k` 而非 arg,k 不可比**
  - 位置: `task18.md:216` (`k = r.get("top_k", 10)`)
  - 问题: 不同 row 用不同 k,mean metric 横比无意义;spec §9.5.2 默认 k=10。
  - 影响: goldset 里漏 `top_k` 字段的 row 走 10,显式写的走自己的,聚合均值含义模糊。
  - 建议: `EvalRunner(pipeline, goldset_path, top_k=10)` 接收 arg,row 覆盖仅作 debug log。

- **[task20.3] 缺 `concurrency:` group,同 PR 多次 push 触发并发 job**
  - 位置: `task20.md:12-30`
  - 问题: GitHub Actions 默认同 PR 多次 push 并行跑,各起一组 testcontainers 容器;共享 runner 内存(7GB) 易 OOM。
  - 影响: CI flaky,与主 plan "testcontainers 自动启停" 决策冲突。
  - 建议: 加 `concurrency: group: ${{ github.workflow }}-${{ github.ref }}; cancel-in-progress: true`。

- **[task20.4] 缺 `caches_have_no_stale` 之外的 L1/L2/L3/L4 命中率 gate**
  - 位置: `task20.md:55-58` (subagent #7 加的 step)
  - 问题: spec §8.7 (`specs/...md:1198-1206`) "缓存命中率" 是核心可观测指标,CI 仅有 staleness 单一断言;无"命中率 > X%" 阈值。
  - 影响: 缓存失效实现回归可能不被 CI 拦截。
  - 建议: 加 step 跑 `tests/integration/test_cache_hit_rate.py` 断言 `L1.hit_rate >= 0.5`(L1 重复 query 命中)。

### 🟡 P2 — 建议改进

- **[task17.4] `audit --last=20` 无 dataset / 时间 / 状态过滤**
  - 位置: `task17.md:217-219`
  - 问题: `RetrievalAudit.tail(n)` 全文读末尾 N 行,无法按 `dataset_id` / `ts > X` 过滤。10K+ 记录后 tail=20 变 O(N) 读。
  - 建议: 加 `--dataset-id` / `--since=2026-06-10` / `--status=failed` 选项,`tail` 加 `filter_fn` 参数。

- **[task17.5] `ingest` 路径无 checkpoint / resume,大批量失败回退难**
  - 位置: `task17.md:158-164`
  - 问题: 10K 文件目录,中途失败后 `--path` 重跑会重复 ingest 已成功文件。
  - 建议: 在 `IngestPipeline` 加 `ingest_directory` 带 `.ingest_checkpoint.json` 记录已处理文件。

- **[task18.3] `_cmd_eval_validate` 缺字段类型/格式校验**
  - 位置: `task17.md:193-201`
  - 问题: 只检查 required keys 存在,不验证 `relevant_chunk_ids` 是合法 UUID 列表、`difficulty ∈ {easy, medium, hard}`、`created_at` 是 ISO date。
  - 建议: 用 pydantic `GoldEntry` schema 校验;违例时具体到行号 + 字段。

- **[task19.5] `SyntheticQuestion` 在 task19 Step 0 / Step 4e 重复定义**
  - 位置: `task19.md:36-40` (Step 0) vs `task19.md:385-392` (Step 4e)
  - 问题: 两版 schema(无 min_length vs 有 min_length)并存,Step 1 test 用 Step 0 版,Step 4e 实施后需替换 stub。
  - 建议: Step 0 stub 加 `min_length=1` 与最终版一致,避免 Step 1/2 RED 阶段测空字符串语义错位。

- **[task20.5] Makefile "增加 eval / coverage" 步骤内容未规定**
  - 位置: `task20.md:6` (Files: `Modify: Makefile`)
  - 问题: 步骤正文无 `Makefile` 片段,只给 `git add Makefile`;spec §11.5 (`specs/...md:1484-1490`) 有 `make eval` 用法。
  - 建议: 显式列 target: `eval: uv run python tests/eval/run_ragas.py` / `eval-l2: uv run pytest tests/eval/retrieval_metrics.py -v`。

- **[task17.6] CLI `histories_file` 解析无 schema 校验**
  - 位置: `task17.md:78-83`
  - 问题: `histories = json.loads(p.read_text())`,坏 JSON 会让 `pipeline.ainvoke(...)` 在 QueryExtensionRunnable 内崩溃(不在 CLI 层),错误信息不友好。
  - 建议: CLI 解析后用 pydantic `list[dict[Literal["user","assistant"], str]]` 校验,失败 `raise typer.Exit(1)`。

- **[task18.4] `EvalRunner` 不上报 `failed_dataset_ids`**
  - 位置: `task18.md:202-235`
  - 问题: `SearchResult.failed_dataset_ids` (`task2.md:152`) 是关键失败信号,EvalRunner 跑 pipeline 但聚合时丢弃。
  - 影响: 评估报告"全 pass"可能因部分 dataset 静默失败。
  - 建议: 聚合时加 `failed_dataset_ids: list[str] = [...]` 字段,CI 可对空数组做断言。

- **[task17.7] `ingest_file` 返回 `int` 是 task17 改造 (audit #4 配套),但 task10 原本语义可能不同**
  - 位置: `task17.md:188-195`
  - 问题: task17 driver 段说 `IngestPipeline.ingest_file` 返回 `int`,但 task10 是否已规定 `int` 返回?需跨 task 验证。
  - 建议: 在 task17 Step 1 之前确认 task10 已是 `int` 返回,否则会破坏 task10 的 caller。

## 4. Spec 覆盖矩阵

| Spec 章节 | 覆盖 Task | 完整性 | 偏差说明 |
|----------|---------|--------|--------|
| §9.1 覆盖率目标 | task20 | ⚠ 部分 | spec 列 7 模块(domain/chunker/fusion/filter/cache/pg/llm);task20 仅追加 5 个新模块目标,未在 pyproject 重申原 7 模块 |
| §9.2 单元测试关键点 | task17/18/19 | OK | 5 关键点全有(chunker 12 级 / fusion RRF / filter md5 / cache_keys / cite prompt) |
| §9.3 集成测试 (testcontainers) | task20 | OK | 显式 testcontainers + pgvector/pg16 + redis/7 |
| §9.4 E2E 测试 | task16/17 | OK | task16 3 case, task17 4 case |
| §9.5.1 L1 组件级 | task19 Step 4b | 🔴 缺 3/5 | 实现 chunk_length_distribution + semantic_boundary_score;缺 jieba OOV / embedding 同 section cosine / 标题继承正确率 |
| §9.5.2 L2 检索级 | task18 | OK | 5 基础指标 + chunk/entity 双层 + EvalRunner |
| §9.5.3 L3 生成级 | task19 | ⚠ 部分 | RAGAS 4 指标 + task15 CitationChecker 覆盖 Citation Recall/Precision;但 `result.prompt` 喂 faithfulness 语义错位 (P0) |
| §9.5.4 L4 用户级 | — | OK(本期不实现) | spec 显式留未来 |
| §9.6 Eval 时机矩阵 | task20 | ⚠ 部分 | on-PR + schedule 覆盖;on-merge / nightly / pre-commit / pre-release 均未实现 spec 列项 |
| §9.7 Regression Testing | task19 | OK | Jaccard ≥ 0.95 + 25 query 集(含 SQL 注入 / 2000 字符 / 空串) |
| §9.8 CI | task20 | OK | on-PR testcontainers + coverage gate + regression + lint + type check + weekly RAGAS |
| §14.1 Gold Set 格式 | task18 Step 5 | OK | schema 对齐 |
| §14.2 Gold Set 版本管理 | task17 `_cmd_eval_validate` | ⚠ 弱 | 仅检查 required keys,UUID 格式 / 难度等级 / 日期格式未校 |
| §14.3 标注流程 (50-100 条) | task18 | 🔴 缺 | Step 5 仅 2 条 stub,无扩量脚本 |
| §17 鲁棒性 (typo/synonym/reorder) | task19 Step 4a | OK | 3 类 + 阈值 0.7 |
| §17 幻觉防御 | task19 Step 4a | OK | 3 query + 防御断言 |
| §17 时效性 / 多语言 / 安全 / 多跳 | — | OK(本期不实现) | spec 标"留作评估时再补" |

## 5. 架构风险与建议

- **风险 1:RAGAS 0.3 EOL + faithfulness 语义错位**
  - 缓解: pin `ragas==0.3.5`(具体 patch) + smoke test import + `run_ragas.py` 改用真实 LLM answer。

- **风险 2:goldset.jsonl 2 条,L2 指标无统计效力**
  - 缓解: Step 5 改造为"20 条人工 + 脚本扩 200 条 synthetic";CI on-PR 跑 20 条,weekly 跑全量。

- **风险 3:CI module_targets 把测试目录错列为 src 目标,目标值无效**
  - 缓解: 移除 `rag.eval.robustness`;若需为 test 目录设目标,改用独立 job + `--cov=tests/eval`。

- **风险 4:CI on-PR coverage fail-fast 在 unit step,违背 H10 "合并 unit+integration"**
  - 缓解: 删 unit step 的 `--cov-fail-under`,只保留最后 `coverage report --fail-under=80`。

- **风险 5:audit jsonl 在并发 search 时行交错 (task15 L6 trade-off)**
  - 缓解: `task15.md:215-218` 已文档化;CLI `rag audit --last=20` 默认接受。production 需加 `fcntl.flock`,但本期 debug 规模可接受。

- **风险 6:CLI 输出非结构化,不可被 CI / 监控消费**
  - 缓解: 加 `--json` 全局选项,`jq` 可解析;退出码统一(`0` 成功 / `1` 无结果 / `2` 错误)。

- **风险 7:L1 metrics 无单测,CI weekly step 静默 pass 0 tests**
  - 缓解: 实现补全 + `tests/unit/test_l1_metrics.py` ≥ 3 测试;CI step 加 `--junitxml` 强制检查 `tests > 0`。

- **风险 8:跨 task goldset row 字段名一致(task17 validator / task18 stub / task19 RAGAS reader 均用 `ground_truth_answer`),RAGAS HFDataset 列名 `ground_truth` 是 RAGAS 自己的命名约定,实际无 schema 冲突。但 `_cmd_eval_validate` 缺 UUID 格式 / 难度枚举校验仍是真实 gap。**
  - 缓解: 在 `src/rag/eval/goldset.py` 统一定义 `GoldEntry` pydantic schema,3 个 task 共同 import。

## 6. 跨 Task 一致性核查

| 冲突点 | 涉及 Task | 详情 |
|--------|---------|------|
| `build_full_pipeline` 签名 | task16 / task17 | `task16.md:390-401` 与 `task17.md:135-145` 两处调用,签名已统一为 `(datasets, deps, audit, top_k, max_tokens, parent_doc_window, use_decomposition, use_global_rerank)`(8 参);OK 一致。 |
| goldset row 字段名 | task17 / task18 / task19 | 三方均用 `ground_truth_answer`;task19 RAGAS 映射为 HFDataset 列 `ground_truth`(RAGAS 约定)。**实际无 schema 冲突**,但 `task17.md:193-201` 校验弱。 |
| `SyntheticQuestion` schema | task18 / task19 | `task18.md:31-36` (Step 0 stub) 与 `task18.md:265-274` (Step 4 实现) 版本一致(无 min_length);`task19.md:36-40` (Step 0 stub, 无 min_length) 与 `task19.md:385-392` (Step 4e, 加 min_length) 不一致。task19 内部需统一。 |
| `IngestPipeline.ingest_file` 返回类型 | task10 / task17 | `task17.md:188-195` 改 `-> int`,需在 task10 之前或并行同步;若 task10 是 `-> None`,task17 Step 3 实施时会破坏 task10 的 caller。**未跨 task 验证**。 |
| `chat_bg` / `histories` 字段 | task2 / task13 / task16 / task17 | `task2.md:134-135` 定义;`task16.md:71-72` 透传;`task17.md:71-73` CLI 透传。OK 三方一致。 |
| `use_global_rerank` 开关 | task2 / task14 / task16 | `task2.md:131` 定义;`task14.md:560-778` 实现节点 + 透传 lambda;`task16.md:400-409` 在 `build_full_pipeline` 接入。OK 一致。 |
| `RetrievalAudit` 接口 | task15 / task17 | `task15.md:64-104` 定义 `record / tail`;`task17.md:217-219` 调用 `tail(last)`。OK 一致。 |
| 模块路径 `rag.retrieval.citation_check` / `rag.pipeline.query_ext` | task15 / task20 | `task20.md:122-132` 显式修正 F2 P0,与 `task15.md:1-4` 路径对齐。OK 已修。 |
| Cache 命中率 gate | task6 / task20 | task6 实现 cache;`task20.md:55-58` 加 staleness 步骤(subagent #7),但缺 hit rate gate(spec §8.7 指标,未在 task20 落地)。 |
| L1 metrics 归属 | spec §9.5.1 / plan §20 | spec 标 L1,plan §「任务依赖图」无独立 task,落到 `task19.md:275-302`;归属 task19 合理但缺独立 task 文件 + 单测。 |

## 7. 3 条具体建议

1. **修复 task20 coverage 双错**:(a) 删 unit step 的 `--cov-fail-under=80`(`task20.md:50`),把 fail-under 移到合并后 `coverage report --fail-under=80` step (`task20.md:60-62`),与 H10 决策一致;(b) 删 `rag.eval.robustness` target(`task20.md:134`),`tests/eval/` 不在 src/ 覆盖范围。同步在 `tests/conftest.py` 加 fixture 验证 L1/L2/L3/L4 命中率。

2. **改造 task19 RAGAS faithfulness 数据流**:`run_ragas.py` 在 `pipeline.ainvoke(...)` 之后调 `LLMSemaphore.run("openai", chat_model.ainvoke(result.prompt))` 真正生成 answer,把 answer 喂 RAGAS。同时把 `ragas>=0.3,<0.4` 改为 `ragas==0.3.5` 具体 pin,加 `tests/unit/test_ragas_import.py` assert `from ragas.metrics import context_precision, context_recall, faithfulness, answer_relevancy` 成功,捕获 EOL 风险。

3. **统一 goldset schema 与扩量 + 补 L1 评测**:`src/rag/eval/goldset.py` 落 `GoldEntry` pydantic model(task17 校验、task18 stub、task19 RAGAS 输入三处共用),Step 5 扩到 20 条人工 + 脚本 `seed_goldset.py` 用 `gen_synthetic_queries` 跑 n=200 扩到 200+ 条;CI on-PR 跑 20 条人工做 Recall gate(阈值 0.7),weekly 跑全 200+ 条。同步补 `title_inheritance_score` / `intra_inter_similarity_ratio` / `jieba_oov_rate` 三函数到 `l1_metrics.py`,新增 `tests/unit/test_l1_metrics.py` ≥ 3 测试,补足 spec §9.5.1 五项指标。

---

## 终端摘要(≤ 500 字)

3 条最关键发现:

1. **`task20.md:113-135` 把 `rag.eval.robustness`(测试目录)列为 `--cov=src/rag` 覆盖率目标**,目标值永远不会被采集校验,形同虚设。同步 `task20.md:46-50` 的 unit step `--cov-fail-under=80` 在 unit 范围 fail-fast,违背主 plan H10 "合并 unit+integration 覆盖率"。
2. **`task19.md:60-69` + `task19.md:202`**:RAGAS 锁 `>=0.3,<0.4` 面临 0.3.x EOL,`answers.append(result.prompt)` 把 prompt 字符串当 LLM 答案喂 faithfulness,指标语义失效;`l1_metrics.py` 缺 spec §9.5.1 三项指标(jieba OOV / embedding cosine / 标题继承),无单测,CI weekly step 静默 pass 0 tests。
3. **`task18.md:320-321` goldset.jsonl 仅 2 条 stub** vs spec §14.3 目标 50-100 条;`task18.md:242` `random.sample` 无 seed,合成数据不可复现;`task17.md:108-113` CLI `search` 在 dataset 不存在时返回 0,CI 误判。

总评:CLI/Eval/CI 整体框架完整(spec §9.5/§9.6/§9.7/§9.8 均有覆盖),但 6 项 P0 集中在"覆盖率配置错误 + 评测数据流语义错位 + 样本量不足",需在实施 task18/19/20 之前修正,否则 weekly RAGAS 跑出来的指标不可信。