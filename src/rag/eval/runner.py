"""``UnifiedEvalRunner``: 一份 JSONL -> 检索 + 生成 全套指标 + gate + artifact。

主入口 ``UnifiedEvalRunner.run(dataset_path)`` 一次性产出:

1. 检索侧: ``recall@k`` / ``precision@k`` / ``hit_rate@k`` / ``mrr`` / ``ndcg@k``
2. 生成侧: ``faithfulness`` / ``answer_relevance`` / ``context_precision`` (按 backend)
3. 跨 record 聚合: mean / std / min / max / median / count
4. 与 baseline diff (可选)
5. gate 判定 (阈值 + 回归)
6. per-query trace artifact (可选)

旧 ``EvalRunner`` (retrieval-only) 与 ``RagasRunner`` (generation-only) 已删除,
统一入口消除"跑两次 runner"的痛点。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from rag.domain.search import SearchRequest, SearchResult
from rag.eval._jsonl import load_jsonl
from rag.eval.artifacts import ArtifactWriter
from rag.eval.backends import GenMetricsBackend, get_backend
from rag.eval.config import EvalConfig
from rag.eval.gate import GateResult, compute_baseline_delta, evaluate_gate
from rag.eval.metrics import (
    aggregate_metric,
    hit_rate_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from rag.eval.metrics import (
    mrr as mrr_fn,
)
from rag.eval.records import EvalRecord

logger = logging.getLogger(__name__)


# ---------- 结果模型 ----------


class EvalSampleResult(BaseModel):
    """单条 query 的评估结果。"""

    model_config = ConfigDict(frozen=True)

    query: str
    retrieved_chunk_ids: list[uuid.UUID]
    ground_truth_chunk_ids: list[uuid.UUID]
    k: int
    retrieval_metrics: dict[str, float]
    generation_metrics: dict[str, float] = Field(default_factory=dict)
    error: str | None = None


class UnifiedEvalSummary(BaseModel):
    """UnifiedEvalRunner 输出。"""

    model_config = ConfigDict(frozen=True)

    sample_count: int
    metric_aggregates: dict[str, dict[str, float]]
    baseline_delta: dict[str, float] | None = None
    gate: GateResult
    artifact_dir: Path | None = None
    warnings: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.gate.passed

    def exit_code(self) -> int:
        """供 CLI: gate 通过 -> 0, 失败 -> 1。"""
        return 0 if self.gate.passed else 1

    def dump(self, path: Path) -> None:
        path.write_text(
            self.model_dump_json(indent=2, exclude={"artifact_dir"}),
            encoding="utf-8",
        )


# ---------- Runner ----------


@dataclass
class UnifiedEvalRunner:
    """统一 eval 入口。

    Args:
        pipeline: ``(SearchRequest) -> Awaitable[SearchResult]`` 可调用。
            接收 ``SearchRequest``, 返回带 ``_intermediate_hits`` 的 ``SearchResult``。
        config: EvalConfig 配置。
        gen_backend: 生成侧 backend, None 时按 ``config.gen_backend`` 装配。
    """

    pipeline: Callable[[SearchRequest], Awaitable[SearchResult]]
    config: EvalConfig
    gen_backend: GenMetricsBackend | None = None

    def __post_init__(self) -> None:
        # 兜底: 即使 caller 没显式传 gen_backend, 也按 config 装配。
        # mypy 仍然把 self.gen_backend 视为 Optional, 调用处用 ``_gen_backend()`` 收窄。
        if self.gen_backend is None:
            self.gen_backend = get_backend(self.config.gen_backend)

    def _gen_backend(self) -> GenMetricsBackend:
        """返回非空 backend, 收窄 Optional 类型供 mypy 通过。"""
        assert self.gen_backend is not None, "gen_backend 由 __post_init__ 兜底装配"
        return self.gen_backend

    async def run(
        self,
        dataset_path: Path,
        *,
        output_path: Path | None = None,
        artifact_dir: Path | None = None,
    ) -> UnifiedEvalSummary:
        """加载 JSONL, 并发跑 pipeline + 算指标, 聚合 + gate, 可选落盘。

        Args:
            dataset_path: eval JSONL 数据集路径。
            output_path: 写 summary JSON 的位置 (可选)。
            artifact_dir: 写 per-query trace 的目录 (可选, 覆盖 config.artifact_dir)。

        Returns:
            ``UnifiedEvalSummary``。
        """
        records = load_jsonl(dataset_path, EvalRecord)
        if not records:
            return self._empty_summary(warnings=["empty dataset"])

        artifact_root = artifact_dir or self.config.artifact_dir
        artifact_writer = ArtifactWriter(artifact_root) if artifact_root else None

        sample_results = await self._run_all(records, artifact_writer)
        aggregates = self._aggregate(sample_results)
        baseline_delta = (
            compute_baseline_delta(aggregates, self.config.baseline_path)
            if self.config.baseline_path
            else None
        )
        gate_result = evaluate_gate(
            aggregates,
            baseline_delta,
            self.config.gate,
            self.config.max_regression_pct,
        )

        warnings = [r.error for r in sample_results if r.error]  # 收集失败 query

        summary = UnifiedEvalSummary(
            sample_count=len(sample_results),
            metric_aggregates=aggregates,
            baseline_delta=baseline_delta,
            gate=gate_result,
            artifact_dir=artifact_root,
            warnings=warnings,
        )

        if output_path is not None:
            summary.dump(output_path)
        if artifact_writer is not None:
            artifact_writer.write_summary(
                summary.model_dump(mode="json", exclude={"artifact_dir"})
            )

        return summary

    # ---- 内部流程 ----

    async def _run_all(
        self,
        records: list[EvalRecord],
        artifact_writer: ArtifactWriter | None,
    ) -> list[EvalSampleResult]:
        """并发跑所有 record: pipeline + 检索指标 + 生成指标 + artifact。"""
        sem = asyncio.Semaphore(self.config.concurrency)

        async def _one(idx: int, record: EvalRecord) -> EvalSampleResult:
            async with sem:
                return await self._run_one(idx, record, artifact_writer)

        return await asyncio.gather(*(_one(i, r) for i, r in enumerate(records)))

    async def _run_one(
        self,
        idx: int,
        record: EvalRecord,
        artifact_writer: ArtifactWriter | None,
    ) -> EvalSampleResult:
        """单条 record: 跑 pipeline -> 算指标 -> 落 trace。"""
        k = record.k or self.config.default_k
        try:
            request = SearchRequest(
                query=record.query,
                dataset_ids=record.dataset_ids,
            )
            response = await self.pipeline(request)
            retrieved_ids = [hit.chunk_id for hit in response._intermediate_hits]

            retrieval_metrics = _compute_retrieval_metrics(
                retrieved_ids=retrieved_ids,
                ground_truth_ids=record.ground_truth_chunk_ids,
                k=k,
                metric_templates=self.config.retrieval_metrics,
            )
            generation_metrics = await self._compute_generation_metrics(
                record=record,
                response=response,
            )

            if artifact_writer is not None:
                artifact_writer.write_query(
                    idx,
                    {
                        "query": record.query,
                        "k": k,
                        "retrieval_metrics": retrieval_metrics,
                        "generation_metrics": generation_metrics,
                        "retrieved_chunk_ids": [str(c) for c in retrieved_ids],
                        "answer": response.response,
                        "ground_truth_chunk_ids": [
                            str(c) for c in record.ground_truth_chunk_ids
                        ],
                        "reference_answer": record.reference_answer,
                        "reference_contexts": record.reference_contexts,
                    },
                )

            return EvalSampleResult(
                query=record.query,
                retrieved_chunk_ids=retrieved_ids,
                ground_truth_chunk_ids=record.ground_truth_chunk_ids,
                k=k,
                retrieval_metrics=retrieval_metrics,
                generation_metrics=generation_metrics,
            )
        except Exception as e:
            msg = f"pipeline failed for query={record.query!r}: {e!r}"
            logger.warning(msg)
            return EvalSampleResult(
                query=record.query,
                retrieved_chunk_ids=[],
                ground_truth_chunk_ids=record.ground_truth_chunk_ids,
                k=k,
                retrieval_metrics={},
                generation_metrics={},
                error=msg,
            )

    async def _compute_generation_metrics(
        self,
        *,
        record: EvalRecord,
        response: SearchResult,
    ) -> dict[str, float]:
        """调用 backend 计算生成指标。无 reference_answer 时 backend 仍可算 faithfulness。"""
        backend = self._gen_backend()
        if backend.name == "skip":
            return {}
        contexts = [hit.text for hit in response._intermediate_hits]
        return await backend.compute(
            query=record.query,
            answer=response.response,
            contexts=contexts,
            reference=record.reference_answer,
            retrieved_chunk_ids=[str(c) for c in response._intermediate_hits],
            ground_truth_chunk_ids=[str(c) for c in record.ground_truth_chunk_ids],
        )

    def _aggregate(
        self,
        results: list[EvalSampleResult],
    ) -> dict[str, dict[str, float]]:
        """按指标名聚合 per-query 值, 返回 ``{metric_name: {mean, std, ...}}``。"""
        all_metric_names = sorted(
            {
                name
                for r in results
                for name in (
                    *r.retrieval_metrics.keys(),
                    *r.generation_metrics.keys(),
                )
            }
        )
        aggregates: dict[str, dict[str, float]] = {}
        for metric_name in all_metric_names:
            values = [
                r.retrieval_metrics.get(metric_name)
                or r.generation_metrics.get(metric_name)
                or 0.0
                for r in results
            ]
            aggregates[metric_name] = aggregate_metric(values)
        return aggregates

    def _empty_summary(self, warnings: list[str]) -> UnifiedEvalSummary:
        return UnifiedEvalSummary(
            sample_count=0,
            metric_aggregates={},
            gate=GateResult(passed=True),
            warnings=warnings,
        )


# ---------- 纯辅助函数 ----------


def _compute_retrieval_metrics(
    *,
    retrieved_ids: list[uuid.UUID],
    ground_truth_ids: list[uuid.UUID],
    k: int,
    metric_templates: list[str],
) -> dict[str, float]:
    """按 metric_templates (无 ``@k`` 后缀) + per-record k 计算检索指标。"""

    retrieved_str = [str(cid) for cid in retrieved_ids]
    gt_str = [str(cid) for cid in ground_truth_ids]
    out: dict[str, float] = {}
    for name in metric_templates:
        if name == "recall":
            out[f"recall@{k}"] = recall_at_k(retrieved_str, gt_str, k)
        elif name == "precision":
            out[f"precision@{k}"] = precision_at_k(retrieved_str, gt_str, k)
        elif name == "hit_rate":
            out[f"hit_rate@{k}"] = hit_rate_at_k(retrieved_str, gt_str, k)
        elif name == "mrr":
            out["mrr"] = mrr_fn(retrieved_str, gt_str)
        elif name == "ndcg":
            out[f"ndcg@{k}"] = ndcg_at_k(retrieved_str, gt_str, k)
        else:
            logger.warning("Unknown retrieval metric name: %s", name)
    return out
