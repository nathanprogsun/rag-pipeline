"""``RagasRunner``: 读取 JSONL, 计算 RAGAS stub 指标并聚合。

JSONL 每行一条记录, 包含 query、answer、contexts、retrieved_chunk_ids、
ground_truth_chunk_ids、metadata; 默认计算 ``faithfulness``、
``answer_relevance``、``context_precision`` 三项, 并用
``aggregate_metric`` 跨记录聚合。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rag.eval.metrics import aggregate_metric
from rag.eval.ragas_metrics import (
    answer_relevance_stub,
    context_precision_stub,
    faithfulness_stub,
)

logger = logging.getLogger(__name__)


# ---------- Records ----------


class RagasRecord(BaseModel):
    """RAGAS eval JSONL 数据集中的一条记录。"""

    model_config = ConfigDict(extra="allow")

    query: str
    answer: str = ""
    contexts: list[str] = Field(default_factory=list)
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    ground_truth_chunk_ids: list[uuid.UUID] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RagasSampleResult(BaseModel):
    """per-record RAGAS 结果 (三项 stub 指标)。"""

    model_config = ConfigDict(frozen=True)

    query: str
    metrics: dict[str, float]


class RagasSummary(BaseModel):
    """跨 eval 集合的 RAGAS 聚合结果。"""

    model_config = ConfigDict(frozen=True)

    sample_count: int
    metric_aggregates: dict[str, dict[str, float]]
    warnings: list[str] = Field(default_factory=list)


# ---------- RagasRunner ----------


@dataclass
class RagasRunner:
    """读取 JSONL, 计算 RAGAS stub 指标并聚合。

    Args:
        metrics: 待计算的指标名列表, 默认全部三项。
            合法名: ``faithfulness``、``answer_relevance``、``context_precision``。
    """

    metrics: list[str] = field(
        default_factory=lambda: [
            "faithfulness",
            "answer_relevance",
            "context_precision",
        ]
    )

    def run(
        self,
        dataset_path: Path,
        *,
        output_path: Path | None = None,
    ) -> RagasSummary:
        """加载 JSONL, 计算 per-record 指标, 聚合, 可选写入 JSON。"""
        records = _load_jsonl(dataset_path)
        if not records:
            return RagasSummary(
                sample_count=0, metric_aggregates={}, warnings=["empty dataset"]
            )

        results: list[RagasSampleResult] = []
        warnings: list[str] = []

        for record in records:
            try:
                sample_metrics = _compute_ragas_metrics(
                    answer=record.answer,
                    query=record.query,
                    contexts=record.contexts,
                    retrieved_chunk_ids=[str(c) for c in record.retrieved_chunk_ids],
                    ground_truth_chunk_ids=[
                        str(c) for c in record.ground_truth_chunk_ids
                    ],
                    metrics=self.metrics,
                )
                results.append(
                    RagasSampleResult(query=record.query, metrics=sample_metrics)
                )
            except Exception as e:
                msg = f"ragas metric failed for query={record.query!r}: {e!r}"
                logger.warning(msg)
                warnings.append(msg)

        # 逐指标聚合
        aggregates: dict[str, dict[str, float]] = {}
        for metric_name in self.metrics:
            values = [r.metrics.get(metric_name, 0.0) for r in results]
            aggregates[metric_name] = aggregate_metric(values)

        summary = RagasSummary(
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


def _load_jsonl(path: Path) -> list[RagasRecord]:
    records: list[RagasRecord] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(RagasRecord.model_validate_json(line))
            except Exception as e:
                logger.warning("Skipping malformed ragas record: %r", e)
    return records


def _compute_ragas_metrics(
    *,
    answer: str,
    query: str,
    contexts: Iterable[str],
    retrieved_chunk_ids: list[str],
    ground_truth_chunk_ids: list[str],
    metrics: list[str],
) -> dict[str, float]:
    out: dict[str, float] = {}
    for name in metrics:
        if name == "faithfulness":
            out[name] = faithfulness_stub(answer, contexts)
        elif name == "answer_relevance":
            out[name] = answer_relevance_stub(query, answer)
        elif name == "context_precision":
            out[name] = context_precision_stub(
                retrieved_chunk_ids, ground_truth_chunk_ids
            )
        else:
            logger.warning("Unknown RAGAS metric name: %s", name)
    return out
