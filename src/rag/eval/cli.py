"""``rag-eval`` Typer CLI: 单入口跑 UnifiedEvalRunner。

支持:
- ``--backend naive|ragas|skip`` 选择生成侧 backend。
- ``--baseline <summary.json>`` 启用回归 diff。
- ``--artifact-dir <dir>`` 落盘 per-query trace。
- ``--gate-min-<metric> <value>`` 设门禁阈值。
- ``--max-regression-pct <pct>`` 触发回归告警。
- ``--output text|json`` 切换输出格式。
- exit code: 0 (passed) / 1 (failed), 供 CI ``set -e`` 使用。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated, Final

import typer

from rag.config import settings
from rag.eval.config import EvalConfig, GateThresholds
from rag.eval.runner import UnifiedEvalRunner, UnifiedEvalSummary
from rag.infra.llm.chat import get_chat_model
from rag.infra.llm.embed import get_embed_model
from rag.infra.llm.rerank import get_rerank_model
from rag.search.orchestrator import SearchPipeline

logger = logging.getLogger(__name__)

_YELLOW: Final[str] = "\033[33m"
_RESET: Final[str] = "\033[0m"

app = typer.Typer(name="rag-eval", add_completion=False)


def _err_exit(msg: str, code: int = 1) -> typer.Exit:
    typer.echo(f"{_YELLOW}{msg}{_RESET}", err=True)
    raise typer.Exit(code=code)


def _emit_text(summary: UnifiedEvalSummary) -> None:
    """以可读文本格式输出聚合指标 + gate 结果。"""
    typer.echo(f"Samples: {summary.sample_count}")
    typer.echo(f"Gate passed: {summary.gate.passed}")
    if summary.gate.failed_metrics:
        typer.echo("Failed metrics:")
        for f in summary.gate.failed_metrics:
            typer.echo(f"  - {f}")
    if summary.gate.regressions:
        typer.echo("Regressions:")
        for r in summary.gate.regressions:
            typer.echo(f"  - {r}")
    typer.echo(f"Warnings: {len(summary.warnings)}")
    for w in summary.warnings[:5]:
        typer.echo(f"  - {w}")
    if len(summary.warnings) > 5:
        typer.echo(f"  ... ({len(summary.warnings) - 5} more)")
    typer.echo("")
    typer.echo("Metric aggregates:")
    for metric_name, agg in summary.metric_aggregates.items():
        typer.echo(
            f"  {metric_name:<20} mean={agg['mean']:.3f}  "
            f"std={agg['std']:.3f}  min={agg['min']:.3f}  max={agg['max']:.3f}  "
            f"count={agg['count']}"
        )
    if summary.baseline_delta:
        typer.echo("")
        typer.echo("Baseline delta (%):")
        for metric, pct in summary.baseline_delta.items():
            typer.echo(f"  {metric:<20} {pct:+.1f}%")


def _emit_json(summary: UnifiedEvalSummary) -> None:
    """将完整 summary 以 JSON 输出到 stdout。"""
    typer.echo(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2)
    )


@app.command()
def main(
    dataset: Annotated[
        Path,
        typer.Option("-d", "--dataset", help="Eval JSONL dataset path。", exists=True),
    ],
    output: Annotated[
        str,
        typer.Option(
            "--output", help="输出格式: text (默认) | json。", case_sensitive=False
        ),
    ] = "text",
    output_path: Annotated[
        Path | None,
        typer.Option(
            "--output-path",
            help="把 summary 写到 JSON 文件 (可选, 与 --output 独立)。",
        ),
    ] = None,
    backend: Annotated[
        str,
        typer.Option(
            "--backend",
            help="生成侧 backend: naive (默认) | ragas | skip。",
            case_sensitive=False,
        ),
    ] = "naive",
    baseline: Annotated[
        Path | None,
        typer.Option(
            "--baseline", help="上次跑的 summary JSON 路径, 启用 baseline diff。"
        ),
    ] = None,
    artifact_dir: Annotated[
        Path | None,
        typer.Option("--artifact-dir", help="per-query trace 落盘目录 (可选)。"),
    ] = None,
    max_regression_pct: Annotated[
        float | None,
        typer.Option(
            "--max-regression-pct",
            help="比 baseline 跌超此百分比视为回归 (需要 --baseline)。",
        ),
    ] = None,
    k: Annotated[
        int, typer.Option("-k", "--k", help="默认 k (per-record JSONL 不指定时用)。")
    ] = 10,
    concurrency: Annotated[
        int, typer.Option("--concurrency", help="pipeline 并发上限 (默认 4)。")
    ] = 4,
    min_recall_at_k: Annotated[
        float | None,
        typer.Option(
            "--min-recall-at-k",
            help="recall@k 门禁阈值, 低于此值 gate 失败。",
        ),
    ] = None,
    min_precision_at_k: Annotated[
        float | None,
        typer.Option("--min-precision-at-k", help="precision@k 门禁阈值。"),
    ] = None,
    min_hit_rate_at_k: Annotated[
        float | None, typer.Option("--min-hit-rate-at-k", help="hit_rate@k 门禁阈值。")
    ] = None,
    min_mrr: Annotated[
        float | None, typer.Option("--min-mrr", help="mrr 门禁阈值。")
    ] = None,
    min_ndcg_at_k: Annotated[
        float | None, typer.Option("--min-ndcg-at-k", help="ndcg@k 门禁阈值。")
    ] = None,
    min_faithfulness: Annotated[
        float | None,
        typer.Option("--min-faithfulness", help="faithfulness 门禁阈值。"),
    ] = None,
    min_answer_relevance: Annotated[
        float | None,
        typer.Option("--min-answer-relevance", help="answer_relevance 门禁阈值。"),
    ] = None,
    min_context_precision: Annotated[
        float | None,
        typer.Option("--min-context-precision", help="context_precision 门禁阈值。"),
    ] = None,
) -> None:
    """跑 unified eval: 检索 + 生成指标 + 可选 gate + 可选 baseline diff。"""
    if output.lower() not in ("text", "json"):
        _err_exit(f"--output 必须是 text 或 json, got {output!r}")
    if backend.lower() not in ("naive", "ragas", "skip"):
        _err_exit(f"--backend 必须是 naive|ragas|skip, got {backend!r}")

    try:
        embedder = get_embed_model()
        llm = get_chat_model()
        rerank = (
            get_rerank_model()
            if settings.openai_rerank_api_key.get_secret_value().strip()
            else None
        )
    except Exception as e:
        _err_exit(f"构建依赖失败: {e!r}")

    pipeline = SearchPipeline(embedder=embedder, llm=llm, rerank_client=rerank)

    gate = GateThresholds(
        min_recall_at_k=min_recall_at_k,
        min_precision_at_k=min_precision_at_k,
        min_hit_rate_at_k=min_hit_rate_at_k,
        min_mrr=min_mrr,
        min_ndcg_at_k=min_ndcg_at_k,
        min_faithfulness=min_faithfulness,
        min_answer_relevance=min_answer_relevance,
        min_context_precision=min_context_precision,
    )

    config = EvalConfig(
        gen_backend=backend.lower(),  # type: ignore[arg-type]
        baseline_path=baseline,
        max_regression_pct=max_regression_pct,
        artifact_dir=artifact_dir,
        concurrency=concurrency,
        default_k=k,
        gate=gate,
    )

    runner = UnifiedEvalRunner(pipeline=pipeline.ainvoke, config=config)
    summary = asyncio.run(runner.run(dataset, output_path=output_path))

    if output.lower() == "json":
        _emit_json(summary)
    else:
        _emit_text(summary)

    if summary.warnings:
        typer.echo(
            f"{_YELLOW}run completed with {len(summary.warnings)} warning(s){_RESET}",
            err=True,
        )

    raise typer.Exit(summary.exit_code())


if __name__ == "__main__":
    app()
