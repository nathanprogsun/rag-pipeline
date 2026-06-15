"""``EvalRunner``: 加载 (query, ground_truth) JSONL, 跑 pipeline 并计算指标。

JSONL 每行一条记录, 含 query、dataset_ids、ground_truth_chunk_ids、k、
可选 metadata。运行器对每条记录调用 ``pipeline.ainvoke``, 从
``_intermediate_hits`` 抽取 ``chunk_ids``, 计算 per-query 指标并聚合。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rag.domain.search import SearchRequest
from rag.eval.metrics import (
    aggregate_metric,
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

logger = logging.getLogger(__name__)


# ---------- Eval record ----------


class EvalRecord(BaseModel):
    """eval JSONL 数据集中的一条记录。"""

    model_config = ConfigDict(extra="allow")

    query: str
    dataset_ids: list[uuid.UUID] = Field(default_factory=list)
    ground_truth_chunk_ids: list[uuid.UUID] = Field(default_factory=list)
    k: int = 10
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalSampleResult(BaseModel):
    """per-query 评估结果。"""

    model_config = ConfigDict(frozen=True)

    query: str
    retrieved_chunk_ids: list[uuid.UUID]
    ground_truth_chunk_ids: list[uuid.UUID]
    k: int
    metrics: dict[str, float]


class EvalSummary(BaseModel):
    """跨 eval 集合的聚合结果。"""

    model_config = ConfigDict(frozen=True)

    sample_count: int
    metric_aggregates: dict[str, dict[str, float]]
    warnings: list[str] = Field(default_factory=list)


# ---------- EvalRunner ----------


@dataclass
class EvalRunner:
    """对 JSONL eval 集合跑 pipeline, 计算检索指标。

    Args:
        pipeline: ``(SearchRequest) -> Awaitable[SearchResult]`` 可调用对象
            (通常为 ``build_search_pipeline(deps).ainvoke``)。
        metrics: 指标 "模板" 列表, 默认 ``recall``、``precision``、
            ``hit_rate``、``mrr``、``ndcg`` (无 ``@k`` 后缀, k 由记录填充)。
        default_k: 记录未指定时 k-based 指标的默认 k。
        concurrency: 最大并行 ``pipeline.ainvoke`` 数。
    """

    pipeline: Callable[[SearchRequest], Awaitable[Any]]
    metrics: list[str] = field(
        default_factory=lambda: [
            "recall",
            "precision",
            "hit_rate",
            "mrr",
            "ndcg",
        ]
    )
    default_k: int = 10
    concurrency: int = 4

    async def run(
        self,
        dataset_path: Path,
        *,
        output_path: Path | None = None,
    ) -> EvalSummary:
        """加载 JSONL, 跑 per-record pipeline, 计算指标, 可选写入 JSON。

        Returns:
            ``EvalSummary`` 含 per-metric 聚合 (mean/std/min/max/median/count)。
            聚合中的指标名带 per-record k (如 ``recall@5``), 避免跨不同
            k 值聚合。
        """
        records = _load_jsonl(dataset_path)
        if not records:
            return EvalSummary(
                sample_count=0, metric_aggregates={}, warnings=["empty dataset"]
            )

        # 并发受限的 pipeline 执行
        semaphore = asyncio.Semaphore(self.concurrency)
        results: list[EvalSampleResult] = []
        warnings: list[str] = []

        async def _run_one(record: EvalRecord) -> None:
            async with semaphore:
                try:
                    request = SearchRequest(
                        query=record.query,
                        dataset_ids=record.dataset_ids,
                    )
                    response = await self.pipeline(request)
                    retrieved_ids = [
                        hit.chunk_id for hit in response._intermediate_hits
                    ]
                    k = record.k or self.default_k
                    # 按当前记录的 k 拼出指标名
                    metric_names = [
                        f"{name}@{k}" if name != "mrr" else name
                        for name in self.metrics
                    ]
                    sample_metrics = _compute_metrics(
                        retrieved_ids=retrieved_ids,
                        ground_truth_ids=record.ground_truth_chunk_ids,
                        k=k,
                        metrics=metric_names,
                    )
                    results.append(
                        EvalSampleResult(
                            query=record.query,
                            retrieved_chunk_ids=retrieved_ids,
                            ground_truth_chunk_ids=record.ground_truth_chunk_ids,
                            k=k,
                            metrics=sample_metrics,
                        )
                    )
                except Exception as e:
                    msg = f"pipeline failed for query={record.query!r}: {e!r}"
                    logger.warning(msg)
                    warnings.append(msg)

        await asyncio.gather(*(_run_one(r) for r in records))

        # 按实际指标名聚合 (不同 k 对应不同指标名键)
        all_metric_names = sorted({name for r in results for name in r.metrics.keys()})
        aggregates: dict[str, dict[str, float]] = {}
        for metric_name in all_metric_names:
            values = [r.metrics.get(metric_name, 0.0) for r in results]
            aggregates[metric_name] = aggregate_metric(values)

        summary = EvalSummary(
            sample_count=len(results),
            metric_aggregates=aggregates,
            warnings=warnings,
        )

        if output_path is not None:
            output_path.write_text(
                json.dumps(
                    summary.model_dump(mode="json"), indent=2, ensure_ascii=False
                ),
                encoding="utf-8",
            )

        return summary


# ---------- Helpers ----------


def _load_jsonl(path: Path) -> list[EvalRecord]:
    """加载 JSONL 文件, 跳过格式错误行并记录 warning。"""
    records: list[EvalRecord] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(EvalRecord.model_validate_json(line))
            except Exception as e:
                logger.warning("Skipping malformed eval record: %r", e)
    return records


def _compute_metrics(
    *,
    retrieved_ids: list[uuid.UUID],
    ground_truth_ids: list[uuid.UUID],
    k: int,
    metrics: list[str],
) -> dict[str, float]:
    """为单个 query 计算请求的指标。"""
    retrieved_str = [str(cid) for cid in retrieved_ids]
    gt_str = [str(cid) for cid in ground_truth_ids]
    out: dict[str, float] = {}
    for name in metrics:
        if name == f"recall@{k}":
            out[name] = recall_at_k(retrieved_str, gt_str, k)
        elif name == f"precision@{k}":
            out[name] = precision_at_k(retrieved_str, gt_str, k)
        elif name == f"hit_rate@{k}":
            out[name] = hit_rate_at_k(retrieved_str, gt_str, k)
        elif name == "mrr":
            out[name] = mrr(retrieved_str, gt_str)
        elif name == f"ndcg@{k}":
            out[name] = ndcg_at_k(retrieved_str, gt_str, k)
        else:
            logger.warning("Unknown metric name: %s", name)
    return out
