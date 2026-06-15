"""``rag-eval`` Typer CLI: JSONL 数据集 → ``EvalRunner`` 聚合指标。"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated, Final

import typer

from rag.config import settings
from rag.eval.runner import EvalRunner, EvalSummary
from rag.infra.llm.chat import get_chat_model
from rag.infra.llm.embed import get_embed_model
from rag.infra.llm.rerank import get_rerank_model
from rag.search.factory import SearchPipelineDeps, build_search_pipeline

logger = logging.getLogger(__name__)

_YELLOW: Final[str] = "\033[33m"
_RESET: Final[str] = "\033[0m"


app = typer.Typer(name="rag-eval", add_completion=False)


def _err_exit(msg: str, code: int = 1) -> typer.Exit:
    typer.echo(f"{_YELLOW}{msg}{_RESET}", err=True)
    raise typer.Exit(code=code)


def _emit_text(summary: EvalSummary) -> None:
    """以可读文本格式输出聚合指标。"""
    typer.echo(f"Samples: {summary.sample_count}")
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


def _emit_json(summary: EvalSummary) -> None:
    """将完整 summary 以 JSON 输出到 stdout。"""
    typer.echo(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2)
    )


@app.command()
def main(
    dataset: Annotated[
        Path,
        typer.Option(
            "-d",
            "--dataset",
            help="Eval JSONL dataset path。",
            exists=True,
        ),
    ],
    output: Annotated[
        str,
        typer.Option(
            "--output",
            help="输出格式: text (默认) | json。",
            case_sensitive=False,
        ),
    ] = "text",
    output_path: Annotated[
        Path | None,
        typer.Option(
            "--output-path",
            help="把 summary 写到 JSON 文件 (可选, 与 --output 独立)。",
        ),
    ] = None,
    k: Annotated[
        int,
        typer.Option("-k", "--k", help="默认 k (per-record JSONL 不指定时用)。"),
    ] = 10,
    concurrency: Annotated[
        int,
        typer.Option(
            "--concurrency",
            help="并发 pipeline 调用数 (默认 4)。",
        ),
    ] = 4,
) -> None:
    """跑 eval dataset, 输出聚合指标。

    Args:
        dataset: eval JSONL 数据集路径。
        output: 输出格式, text 或 json。
        output_path: 可选 summary JSON 输出文件。
        k: 默认 k (per-record JSONL 未指定时使用)。
        concurrency: 并发 pipeline 调用数。
    """
    if output.lower() not in ("text", "json"):
        _err_exit(f"--output 必须是 text 或 json, got {output!r}")

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

    deps = SearchPipelineDeps(embedder=embedder, llm=llm, rerank_client=rerank, top_k=k)
    pipeline = build_search_pipeline(deps)

    runner = EvalRunner(pipeline=pipeline.ainvoke, default_k=k, concurrency=concurrency)
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


if __name__ == "__main__":
    app()
