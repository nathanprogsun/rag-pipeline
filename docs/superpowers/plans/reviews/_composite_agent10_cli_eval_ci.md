# 共同上下文(必读)

你是一位资深的系统架构设计师(高 bar、严格、客观),正对 `/Users/jung/pro/rag-pipeline` 仓库的 RAG 流水线实施 plan 进行 **架构层面** 的只读 review。

## 项目目标
使用 LangChain (LCEL) + PostgreSQL+pgvector + Redis 多级缓存,复刻 FastGPT 查询侧 RAG 流水线。Python 3.12, Pydantic v2, async 全栈。详见 spec。

## 关键参考
- 主 plan: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` (212 行, 17 章节的索引+自检)
- 设计 spec: `/Users/jung/pro/rag-pipeline/docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` (1661 行, 17 章节, 详细设计)
- Task 索引: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/INDEX.md` (20 task 拆分索引)
- Task 文件目录: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task{1..20}.md`
- **缺失**: task15.md, task16.md 未生成

## 你的角色与产出
你处于 **架构设计师** 视角,关注:
1. 模块边界(分层 domain / infra / pipeline / retrieval / ingest / cli)
2. 依赖方向(严禁反向依赖,严禁循环)
3. API 契约一致性(SearchRequest/SearchResult/Chunk/ScoredDocument 等)
4. Spec 章节覆盖度
5. TDD 合规(stub → test → impl → verify)
6. 可观测性、日志、错误处理、降级
7. 性能/扩展性、安全、可移植性
8. 跨 task 契约冲突

## 行为约束
- **只读**: 不要修改任何 task 文件或源码
- **证据**: 所有发现必须给出 `file:line` 引用
- **不奉承**: 禁止"看起来不错"型无证据评价
- **不夸大**: 给具体影响,不要"严重问题"型空话
- 若发现 task 间冲突,明确标出

## 输出格式
将完整 review 写入指定文件路径,**严格遵循以下结构**(中英文皆可,技术文档风格):

```
# Architecture Review: [Agent #N 域名称]

## 1. 一句话总评
[≤ 100 字,直接给结论,点出最关键问题]

## 2. 模块边界 / 依赖方向 / 契约一致性
| 检查项 | 结论 | 证据(file:line) | 评级(OK/⚠/🔴) |

## 3. 发现清单(按严重度降序)
### 🔴 P0 — 必须修复(阻塞)
- **[taskX.Y]** 标题
  - 位置: `path:line`
  - 问题: ...
  - 影响: ...
  - 建议: ...
[继续 P1/P2/P3]

## 4. Spec 覆盖矩阵
| Spec 章节 | 覆盖 Task | 完整性 | 偏差说明 |

## 5. 架构风险与建议
- 风险 N: ...
  缓解: ...

## 6. 跨 Task 一致性核查
[此 agent 范围内或与外部 task 冲突的契约问题]

## 7. 3 条具体建议
1. ...
2. ...
3. ...
```


---

# Agent #10 — L6/L7/L8 CLI + Eval + CI

## 审核范围
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task17.md` — CLI (typer) search/ingest/eval/audit/cache/chunk
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task18.md` — Eval L2 — Gold Set + Synthetic + Retrieval Metrics
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task19.md` — Eval L3 — RAGAS Run + Regression Testing
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task20.md` — CI + Final Integration + Coverage Report

## 关注点
1. **CLI (typer) 边界**:
   - library vs CLI 双模式(已知 H2:CLI 显式传入)
   - 子命令:search / ingest / eval / audit / cache / chunk
   - 进度展示、JSON 输出、错误码
   - 配置文件加载

2. **Eval L1 (隐含)**: 在 spec §9.1 但 plan 未单列 task,应并入哪个?

3. **Eval L2**:
   - goldset.jsonl 维护与版本化
   - synthetic.py 合成数据生成
   - retrieval_metrics.py:Recall@k, MRR, NDCG, MAP
   - L1 metrics(spec §9.1 全局阈值)
   - robustness.py(spec §9.4 提及)
   - 与 task15 (audit) 指标复用

4. **Eval L3**:
   - RAGAS:faithfulness, answer_relevancy, context_precision/recall
   - Regression Testing 阈值
   - 报告格式(HTML/JSON/CSV)
   - 与 L2 指标的关系

5. **CI (Task 20)**:
   - testcontainers 自动启停
   - 覆盖率阈值(--fail-under=80,已知 H10 修复)
   - lint / type check
   - 并行 job 设计
   - 与 GitHub Actions / GitLab CI 的具体声明

6. **集成测试**:
   - E2E 路径
   - mock embedder 在 CI 中的使用
   - spec §9.3 集成测试规范

## 必查项
- task17: typer 命令的可发现性(可拼写错误时的提示)
- task17: env_file 注入方式(plan H2)
- task18: goldset.jsonl 数据平衡
- task18: synthetic 生成的可复现性(random seed)
- task19: RAGAS 在 CI 中的稳定性(网络/速率限制)
- task19: regression baseline 存储
- task20: 并发安全
- task20: cache 命中率在 CI 中的退化
- 跨 task:eval 报告与 audit 一致性
- 跨 task:Eval L1 spec 章节覆盖
- spec §9 全章节覆盖

## 输出
写入: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent10_cli_eval_ci.md`


---

## 启动说明
1. 仔细阅读以上 共同上下文 + 你的 agent 任务说明 + 必读参考
2. 阅读你范围内的 task 文件(`tasks/taskN.md`) 全文
3. 翻阅 spec `/Users/jung/pro/rag-pipeline/docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` 对应章节
4. 按"输出格式"结构组织 review
5. 将完整 review **写入文件**: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent10_cli_eval_ci.md`
6. 在终端输出简短摘要(≤ 500 字,给出 3 条最关键发现 + 1 句总评)

## 重要提醒
- 你是 reviewer,不是 implementer
- 不要修改任何 task 文件或源码
- 所有结论必须有 `file:line` 证据
- 跨 task 冲突优先在"跨 Task 一致性核查"列出
- 若发现 task15/16 缺失影响你审查的 task,在 review 顶部明确说明
