# Agent #6 — L4 融合 + 过滤

## 审核范围
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task11.md` — Fusion (intra + inter WRRF)
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task12.md` — Filter Pipeline (去重 / 阈值 / token 预算)

## 关注点
1. WRRF (Weighted Reciprocal Rank Fusion) 算法实现
2. intra-dataset 融合(vector × fulltext)与 inter-dataset 融合的边界
3. rrf_k=60 默认值与 spec §0.1 可配性(per-dataset)
4. rank 起始值(plan 标记 M3:rank 从 1 开始,标准 RRF)
5. weight 配置(各 dataset 可调)
6. Filter 维度:去重 / 阈值 / token 预算 三种过滤的组合
7. 阈值(score < cutoff)与 spec §5 对齐
8. token 预算估算(tiktoken? heuristic?)
9. MMR 多样性(spec 是否提?)
10. Citation 边界与 Filter 后 ID 重映射

## 必查项
- task11: WRRF 公式正确性 `1 / (k + rank)`
- task11: 同 chunk 在两路召回中加权 vs 去重
- task11: 空结果退化
- task11: dataset 失败处理(partial failure)
- task12: 去重键选择(chunk_id vs content_hash)
- task12: token 计数与 LLM 真实计数的偏差
- task12: 过滤顺序(去重 → 阈值 → 预算 vs 反之)
- task12: 过滤后召回不足的兜底
- task12: 与 parent_doc_window 交互
- 跨 task:SearchResult.citations 与 cite.py 字段一致性

## 输出
写入: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent6_fusion_filter.md`
