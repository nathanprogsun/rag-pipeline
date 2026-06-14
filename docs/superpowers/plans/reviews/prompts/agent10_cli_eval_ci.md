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
