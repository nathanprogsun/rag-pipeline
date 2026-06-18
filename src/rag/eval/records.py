"""``EvalRecord``: UnifiedEvalRunner 单条 JSONL 输入。

一份记录覆盖"检索"和"生成"两侧的 ground truth, UnifiedEvalRunner 据此
一次跑出 recall@k + faithfulness 全套指标, 不再分裂为 EvalRecord + RagasRecord。
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvalRecord(BaseModel):
    """Eval JSONL 数据集中的一条记录。

    Args:
        query: 用户查询。
        dataset_ids: 限定检索范围的 dataset UUID 列表 (允许跨 dataset 评估)。
        ground_truth_chunk_ids: 检索 ground truth, 用于 recall@k / precision@k / mrr / ndcg。
        k: per-record k, 缺省时回退到 ``EvalConfig.default_k``。
        reference_answer: 生成 ground truth, 用于 answer_relevance / context_precision。
        reference_contexts: 喂给 LLM 的 reference 上下文 (用于 faithfulness)。
        metadata: 透传字段, 写到 artifact 里便于溯源。
    """

    model_config = ConfigDict(extra="allow")

    query: str
    dataset_ids: list[uuid.UUID] = Field(default_factory=list)

    # === 检索 ground truth ===
    ground_truth_chunk_ids: list[uuid.UUID] = Field(default_factory=list)
    k: int = 10

    # === 生成 ground truth (可选, 缺则不计算生成指标) ===
    reference_answer: str = ""
    reference_contexts: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)
