# Agent #5 — L3/L4 Chunker + Ingest Pipeline

## 审核范围
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task9.md` — Chunker (12-level recursive split)
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task10.md` — Ingest Pipeline (atomic + async batch)

## 关注点
1. 17 级 recursive split(plan 中有 "12-level" 与 "17 级" 矛盾,需要确认;plan 标记 C2)与 FastGPT 对齐
2. 分隔符表(spec §15 详细)与实现一致性
3. 块大小(1000)与重叠(metadata overlap)策略
4. 父子块(parent_doc_window)关系维护
5. Ingest atomic 性(事务边界)
6. Async batch embedding 调用与 LLMSemaphore 集成
7. 失败重试与部分提交
8. 文件 → Reader → Chunker → Embedder → PG 的数据流
9. 重复检测(hash / simhash)
10. ingest 进度回调 / 状态

## 必查项
- task9: 17 级分隔符完整列表与 spec §15 对齐
- task9: 中英文混合切分逻辑
- task9: 块重叠实现(token-level vs char-level)
- task9: parent_doc 关联(metadata vs 单独表)
- task9: 性能(单文档 1MB 处理时间)
- task10: 事务回滚边界(embedding 成功但 PG 失败)
- task10: batch size 与 LLMSemaphore 协调
- task10: 重试时是否重复计算 embedding(去重)
- task10: dataset_id 校验
- task10: 软删除 vs 硬删除
- task10: 重复 ingest 同源文件处理
- spec §0.1 与 spec §3 ingest 流程的差异

## 输出
写入: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent5_chunker_ingest.md`
