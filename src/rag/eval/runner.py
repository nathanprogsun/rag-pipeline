"""EvalRunner — load (query, ground_truth) JSONL, run pipeline, compute metrics.

Per `.agents/design/2026-06-14-rag-pipeline-delivery.md` task 18:

JSONL schema (one record per line):
    {
      "query": "Python 列表推导式",
      "dataset_ids": ["uuid1", "uuid2"],
      "ground_truth_chunk_ids": ["uuid3", "uuid4"],
      "k": 10,
      "metadata": {...}  # optional, for downstream analysis
    }

EvalRunner reads JSONL, calls pipeline.ainvoke for each, extracts
``chunk_ids`` from ``_intermediate_hits``, computes per-query metrics,
aggregates. Results dumped as JSON.
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
    """One entry in the eval JSONL dataset."""

    model_config = ConfigDict(extra="allow")

    query: str
    dataset_ids: list[uuid.UUID] = Field(default_factory=list)
    ground_truth_chunk_ids: list[uuid.UUID] = Field(default_factory=list)
    k: int = 10
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalSampleResult(BaseModel):
    """Per-query eval result."""

    model_config = ConfigDict(frozen=True)

    query: str
    retrieved_chunk_ids: list[uuid.UUID]
    ground_truth_chunk_ids: list[uuid.UUID]
    k: int
    metrics: dict[str, float]


class EvalSummary(BaseModel):
    """Aggregate result over the eval set."""

    model_config = ConfigDict(frozen=True)

    sample_count: int
    metric_aggregates: dict[str, dict[str, float]]
    warnings: list[str] = Field(default_factory=list)


# ---------- EvalRunner ----------


@dataclass
class EvalRunner:
    """Run pipeline against JSONL eval set, compute retrieval metrics.

    Args:
        pipeline: callable ``(SearchRequest) -> Awaitable[SearchResult]``
            (typically ``build_full_pipeline(deps).ainvoke``).
        metrics: list of metric "templates" to compute. Default: ``recall``,
            ``precision``, ``hit_rate``, ``mrr``, ``ndcg`` (without ``@k``
            suffix → k is filled in per-record).
        default_k: default K for k-based metrics if not specified per record.
        concurrency: max parallel pipeline.ainvoke calls.
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
        """Load JSONL, run pipeline per record, compute metrics, optionally write JSON.

        Returns EvalSummary with per-metric aggregates (mean/std/min/max/median/count).
        Metric names in aggregates use per-record k (e.g. ``recall@5`` if
        record.k=5). This avoids cross-record aggregation across different
        k values.
        """
        records = _load_jsonl(dataset_path)
        if not records:
            return EvalSummary(
                sample_count=0, metric_aggregates={}, warnings=["empty dataset"]
            )

        # Concurrency-limited pipeline execution
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
                    # Build per-record metric list using that record's k
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

        # Aggregate per actual metric name (records may have different k,
        # leading to different metric name keys)
        all_metric_names = sorted(
            {name for r in results for name in r.metrics.keys()}
        )
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
                json.dumps(summary.model_dump(mode="json"), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        return summary


# ---------- Helpers ----------


def _load_jsonl(path: Path) -> list[EvalRecord]:
    """Load JSONL file, skipping malformed lines with warning."""
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
    """Compute requested metrics for a single query."""
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