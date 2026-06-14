# Agent #2 — L1/L2 PG 基础设施 + 向量检索

## 审核范围
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task3.md` — PG database.py + base.py + Models + Repositories
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task4.md` — Vector Retriever (HNSW cosine)

## 关注点
1. SQLAlchemy 2.0 async + Repository 模式的可行性
2. AsyncSessionLocal 即用即弃模式与 FastAPI 最佳实践对齐
3. DeclarativeBase + TimestampMixin 复用
4. chunk_repo.py 接口(CRUD + 检索 + 批量)是否与 task4/task5 检索需求对齐
5. pgvector HNSW 索引参数(m=16, ef_construction=64 与 spec 对齐)
6. cosine distance 操作符选择(`<=>` vs `<->`)
7. ef_search 动态设置(已知 H7 修复:`SET LOCAL hnsw.ef_search`)
8. top_k > 40 时的静默截断问题
9. schema.sql 字符级与 Pydantic 模型对齐
10. Alembic 策略(create_all vs migration),M1 标记的产线方案

## 必查项
- task3 是否定义完整索引? HNSW + GIN 全文 + B-tree 关联?
- task4 query embedding 调用是否经过 LLMSemaphore?
- to_tsquery 注入风险(已知 M4 修复:`'simple'`)
- chunk_repo.find_by_embedding 是否批量化
- 缺少的审计/日志字段(created_at, updated_at)
- Spec §4 PG schema 与 task3 schema.sql 一致性
- dataset_id / chunk_id 索引策略

## 输出
写入: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent2_pg_vector.md`
