# Agent #9 — 缺失任务审计 + 依赖闭环 (Missing Tasks & Dependency Audit)

## 审核范围
- 重点:task15.md, task16.md **未生成**,审计影响
- 主 plan: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md`
- INDEX: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/INDEX.md`
- 关联 task: task14 (subgraph+orchestrator), task17 (CLI), task18 (eval), task20 (CI)

## 关注点
1. **task15 缺失影响**: Retrieval Audit + Citation Checker
   - retrieval/audit.py + retrieval/citation_check.py 应在哪个 task 落盘
   - 与 task14 (orchestrator) 的接口(res.audit_metadata?)
   - 与 task18 (eval L2) 的指标(是否复用 audit)
   - 与 task19 (RAGAS) 的输入
   - SearchRequest.audit 字段如何被消费

2. **task16 缺失影响**: Build Full Pipeline + JSON Logging
   - pipeline/full.py(已知:SearchRequest.query_decomposition 引用 H3 修正)
   - pipeline/cite.py(已知:ScoredDocument.image_path 引用 H2 修正)
   - JSON Logging schema 字段完整性
   - itemgetter("query") → RunnableLambda 修正(已知 C3)
   - 与 task14 (subgraph) 集成入口
   - 与 task20 (CI) 集成

3. **依赖闭环**: L5/L6 链路完整性
   - task14 → task15 → task16 → task17/task20 的依赖是否所有 task 文件都已就位
   - 缺失 task15/16 阻塞多少下游 task?
   - 是否在主 plan 中有补救路径?

4. **跨 task 契约一致性核查**
   - SearchRequest.audit 字段
   - SearchRequest.use_global_rerank 字段
   - SearchRequest.parent_doc_window 字段
   - SearchRequest.query_decomposition 字段
   - SearchResult.failed_dataset_ids 字段
   - SearchResult.warnings 字段
   - ScoredDocument.image_path 字段
   - ChunkMetadata.dataset_id 字段

5. **已知修复落地核查**(基于 plan 末尾 trade-off 表)
   - H2: env_file 移除、CLI 显式传入
   - H3: with_structured_output method="function_calling"
   - H5: LLMSettings 单一定义
   - H6: CitationChecker regex
   - H7: ef_search SET LOCAL
   - C3: itemgetter → RunnableLambda

## 必查项
- 给出 **task15 应包含什么**(基于 spec §0.1, §0.2, §10)
- 给出 **task16 应包含什么**(基于 spec §6.5, §7)
- 给出 **修复建议**:在主 plan 中明确"task15, task16 待补"是否影响发布门槛
- **新发现**: 是否还有未标出的"应存在但缺失"模块?

## 输出
写入: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent9_missing_tasks_audit.md`
