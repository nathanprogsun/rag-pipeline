# Agent #8 — L5 Subgraph + Orchestrator + Rerank + Cite + Parent Doc

## 审核范围
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task14.md` — Subgraph + Orchestrator + Rerank + Cite + Parent Doc (706 行,核心编排)

## 关注点
1. Subgraph-per-dataset 并行模式(LCEL RunnableParallel)
2. Orchestrator 整体编排(sequential + parallel 混合)
3. Rerank 集成(Cohere 完整,BGE/Jina stub)
4. Citation 组装与编号 [1], [1,2,3] 格式(已知 H6 修复 regex)
5. Parent Doc 窗口(parent_doc_window 字段)
6. 失败隔离(单 dataset 失败不影响其他)
7. SearchResult.failed_dataset_ids 字段填充
8. SearchResult.warnings 字段填充
9. Rerank 缓存(rerank 键与 spec §6)
10. use_global_rerank vs per-dataset rerank

## 必查项
- task14: RunnableParallel 与 asyncio.gather 边界
- task14: Subgraph 输入输出 schema 严格性
- task14: Rerank 调用与 LLMSemaphore 集成
- task14: Citation 编号在多 dataset 下的全局一致性
- task14: Cite 异常时的 fallback(无引用)
- task14: Parent Doc 拉取是同步还是异步
- task14: rerank 跳过条件(use_global_rerank=false)
- task14: 整体超时控制
- task14: JSON Logging 字段完整性
- 跨 task:与 task15 (audit + citation_check) 的接口预留
- 跨 task:与 task16 (build_full_pipeline) 的接口预留
- 跨 task:CitationChecker regex `\[([\d,\s]+)\]` 兼容性(已知 H6)

## 输出
写入: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent8_subgraph_orchestrator.md`
