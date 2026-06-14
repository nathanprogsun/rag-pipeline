# Agent #3 — L2 全文检索 + Redis 缓存

## 审核范围
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task5.md` — Fulltext Retriever (jieba + tsvector)
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task6.md` — Cache Layer (Redis + keys + invalidation)

## 关注点
1. jieba 分词在检索阶段的同步开销(应预计算)
2. tsvector 列 vs tsquery 生成(plan 标记 M4:`func.to_tsquery('simple', ts_query)`)
3. 中文简单配置 vs english/zhparser 选择
4. Redis 多级缓存层级(embedding / query_ext / search / rerank)
5. Cache key 命名空间与 invalidation 粒度(dataset_id / version / TTL)
6. 1s timeout + degradation(circuit breaker v1)
7. Cache stampede 防护(批量并发)
8. cache_decorator.py 与 pipeline/full.py 集成
9. JSON 序列化(Pydantic model → str)
10. await 链路上 Redis 阻塞风险

## 必查项
- task5: jieba 词典与 spec 词表是否一致
- task5: simple 配置下中文检索质量
- task5: 高亮(snippet)生成
- task6: TTL 配置默认值是否合理(embedding 永久?query_ext 1h?)
- task6: invalidation 在 ingest 完成时的触发点
- task6: SCAN vs KEYS 的 O(N) 风险
- task6: Redis 连接池(redis.asyncio)配置
- task6: 缓存键与 spec §6 对齐
- task6: 跨实例一致性(本地缓存 L1 缺失)
- task6: 大对象缓存(>1MB chunk list)与 maxmemory-policy 交互

## 输出
写入: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent3_fulltext_cache.md`
