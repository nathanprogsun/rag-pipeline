"""``rag-ingest`` Typer CLI: 本地文件 → IngestPipeline → stdout。"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from typing import Annotated, Final

import typer

from rag.config import settings
from rag.error_codes import ConfigErrorCode
from rag.exception import RAGError
from rag.infra.llm.chat import get_structured_chat_model
from rag.ingest.chunker import Chunker
from rag.ingest.chunker.settings import ChunkSettings
from rag.ingest.normalizer import StructureMode, StructureNormalizer
from rag.ingest.normalizer.structure import _LLM_TIMEOUT_SEC, StructuredText
from rag.ingest.pipeline import IngestPipeline
from rag.ingest.types import IngestOutcome, IngestResult, PersistConfig

logger = logging.getLogger(__name__)

_YELLOW: Final[str] = "\033[33m"
_RESET: Final[str] = "\033[0m"
_PREVIEW_LEN: Final[int] = 80
_SEPARATOR: Final[str] = "=" * 60
_LOG_FORMAT: Final[str] = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT: Final[str] = "%H:%M:%S"

_CLI_HELP: Final[str] = """\
读取本地文件，解析、切块、(可选) 写入 PG 并打印到 stdout。

\b
Options:
  --dataset-name STR      按名称 get-or-create dataset 并落库 (需 OPENAI_EMBEDDING_API_KEY)
  --dataset-id UUID       向已有 dataset 追加文档 (与 --dataset-name 互斥)

\b
Examples:
  rag-ingest report.pdf
  rag-ingest ./docs/ report.pdf
  rag-ingest report.pdf --dataset-name "python-tutorial"
  rag-ingest report.pdf --dataset-id <UUID>
"""

app = typer.Typer(name="rag-ingest", add_completion=False)


def configure_ingest_logging(*, quiet: bool = False) -> None:
    """为 CLI 配置 stderr 日志; 默认 INFO, 便于 bulk ingest 时观察进度。

    各子模块已有 ``logger.info``, 此前未调用本函数时默认级别为 WARNING, 运行中几乎无输出。
    """
    level = logging.WARNING if quiet else logging.INFO
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt=_LOG_DATEFMT,
        stream=sys.stderr,
    )
    # 第三方库降噪 (HF Hub 未认证警告等)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)


def _render_result(result: IngestResult) -> None:
    chunks = result.chunks
    total = len(chunks)
    typer.echo(f"title: {result.title}")
    typer.echo(f"filename: {result.doc_meta.filename}")
    typer.echo(f"page_count: {result.doc_meta.page_count}")
    typer.echo(f"chunks: {total}")
    typer.echo("---")
    for idx, chunk in enumerate(chunks):
        headings = chunk.metadata.heading_stack
        heading = "(root)" if not headings else " > ".join(headings)
        flat = (
            chunk.text.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
        )
        preview = flat if len(flat) <= _PREVIEW_LEN else flat[:_PREVIEW_LEN] + "..."
        label = result.doc_meta.filename or ""
        typer.echo(f"[{idx}/{total}] {label}: {heading} | {preview}")
    if result.persist is not None:
        pr = result.persist
        typer.echo(
            f"persisted: dataset_id={pr.dataset_id} name={pr.dataset_name!r} "
            f"old_chunks={pr.old_chunk_count} new_chunks={pr.new_chunk_count}",
        )


def _render_outcome(outcome: IngestOutcome) -> bool:
    """渲染 ingest 结果; 返回是否有失败或空批次。"""
    for w in outcome.warnings:
        typer.echo(f"{_YELLOW}{w}{_RESET}", err=True)

    total = len(outcome.items)
    if total == 0:
        return True

    had_failure = bool(outcome.errors)

    batch = total > 1 or bool(outcome.warnings)
    if batch:
        typer.echo(f"batch: {total} file(s)")
        if outcome.warnings:
            typer.echo(_SEPARATOR)

    for idx, result in enumerate(outcome.items, start=1):
        if batch and total > 1:
            if idx > 1:
                typer.echo(_SEPARATOR)
            label = result.doc_meta.filename or ""
            typer.echo(f"[{idx}/{total}] {label}")
            _render_result(result)
        else:
            _render_result(result)
    return had_failure


def default_pipeline(persist_config: PersistConfig | None = None) -> IngestPipeline:
    chunker = Chunker(ChunkSettings())
    chat_model = get_structured_chat_model(
        StructuredText,
        temperature=0.1,
        timeout=_LLM_TIMEOUT_SEC,
        include_raw=True,
    )
    normalizer = StructureNormalizer(chat_model=chat_model, mode=StructureMode.AUTO)
    return IngestPipeline(
        chunker=chunker,
        normalizer=normalizer,
        persist_config=persist_config,
    )


async def _run_ingest_async(
    targets: list[str],
    *,
    persist_config: PersistConfig | None = None,
) -> bool:
    pipeline = default_pipeline(persist_config)
    outcome = await pipeline.ingest_many(targets)
    return _render_outcome(outcome)


def run_ingest(
    targets: list[str],
    *,
    persist_config: PersistConfig | None = None,
    quiet: bool = False,
) -> None:
    configure_ingest_logging(quiet=quiet)
    logger.info(
        "ingest.cli.start targets=%s persist=%s", targets, persist_config is not None
    )

    if not settings.openai_api_key.get_secret_value().strip():
        typer.echo(
            f"ingest failed: [{ConfigErrorCode.MISSING_ENV}] "
            "OPENAI_API_KEY required for default normalize=auto",
            err=True,
        )
        raise typer.Exit(code=1)
    if persist_config is not None and persist_config.enabled:
        if not settings.openai_embedding_api_key.get_secret_value().strip():
            typer.echo(
                f"ingest failed: [{ConfigErrorCode.MISSING_ENV}] "
                "OPENAI_EMBEDDING_API_KEY required for persist "
                "(--dataset-name / --dataset-id)",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        had_failure = asyncio.run(
            _run_ingest_async(targets, persist_config=persist_config)
        )
    except RAGError as exc:
        typer.echo(f"ingest failed: [{exc.code}] {exc.message}", err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        typer.echo(f"ingest failed: [{type(exc).__name__}] {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if had_failure:
        raise typer.Exit(code=1)


@app.command(help=_CLI_HELP)
def ingest_cmd(
    targets: Annotated[
        list[str],
        typer.Argument(help="本地路径 (可多, 目录递归展开)"),
    ],
    dataset_name: Annotated[
        str | None,
        typer.Option("--dataset-name", help="按名称 get-or-create dataset 并落库。"),
    ] = None,
    dataset_id: Annotated[
        uuid.UUID | None,
        typer.Option("--dataset-id", help="向已有 dataset 追加文档。"),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="仅输出 WARNING 及以上日志。"),
    ] = False,
) -> None:
    if dataset_name is not None and dataset_id is not None:
        typer.echo(
            "ingest failed: --dataset-name 与 --dataset-id 互斥",
            err=True,
        )
        raise typer.Exit(code=1)

    persist_config: PersistConfig | None = None
    if dataset_name is not None:
        persist_config = PersistConfig(
            create_dataset=True,
            dataset_name=dataset_name,
            enabled=True,
        )
    elif dataset_id is not None:
        persist_config = PersistConfig(dataset_id=dataset_id, enabled=True)

    run_ingest(targets, persist_config=persist_config, quiet=quiet)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
