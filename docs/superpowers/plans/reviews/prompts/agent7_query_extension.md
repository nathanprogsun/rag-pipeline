# Agent #7 — L5 Query Extension / Image Caption / Decomposition

## 审核范围
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task13.md` — Query Extension + Image Caption + Decomposition (901 行,最复杂)

## 关注点
1. Query Extension(MultiQuery 思路)的 LLM 改写 prompt
2. Submodular Query Selection(spec §0.1 提及,lazy_greedy.py)
3. Image Caption 多模态 LLM 集成
4. Query Decomposer(复杂查询拆分)
5. 与 LLM Semaphore 的并发集成
6. 缓存策略(query_ext / decomposition 缓存键)
7. prompt 注入与 system 隔离
8. 简单查询短路
9. 子查询合并 vs 独立检索
10. 已知 B1-B9 blocker 落地情况(plan 标记 task13 独占 5 个 🔴)

## 必查项
- MultiQuery 改写后是否与原 query 一同检索
- Lazy Greedy 子模选择的目标函数(diversity vs relevance)
- Image caption 与 text query 的检索融合
- Decomposition 失败时降级
- 缓存键与 spec §6 对齐
- DecomposedQueries Pydantic schema 与 pipeline 输入对齐
- LLM call 失败重试
- token 限制
- 与 SearchRequest.query_decomposition 开关字段对齐
- task13 内部模块划分是否合理(query_ext / image_caption / decomposition 三个子模块)

## 输出
写入: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent7_query_extension.md`
