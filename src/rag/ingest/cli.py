"""``rag-ingest`` Typer CLI：本地文件 / URL → IngestPipeline → stdout。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated, Final, Literal, cast

import typer

from rag.config import settings
from rag.error_codes import ConfigErrorCode
from rag.exception import RAGError
from rag.infra.llm.chat import get_structured_chat_model
from rag.ingest.chunker import Chunker
from rag.ingest.chunker.quality import format_chunk_stats, measure_chunks
from rag.ingest.chunker.settings import ChunkSettings
from rag.ingest.normalizer import StructureMode, StructureNormalizer
from rag.ingest.normalizer.structure import StructuredText
from rag.ingest.pipeline import IngestPipeline
from rag.ingest.source import FileSource, IngestSource, UrlSource
from rag.ingest.types import ChunkMetadata, IngestResult

NormalizeMode = Literal["auto", "force", "off"]
IngestMode = Literal["file", "url"]

logger = logging.getLogger(__name__)

_YELLOW: Final[str] = "\033[33m"
_RESET: Final[str] = "\033[0m"
_PREVIEW_LEN: Final[int] = 80
_SEPARATOR: Final[str] = "=" * 60
_MAX_FILE_BYTES: Final[int] = 100 * 1024 * 1024
_SKIP_DIR_NAMES: Final[frozenset[str]] = frozenset({"__pycache__"})

_FORMAT_TEXT_HELP: Final[str] = (
    "csv/xlsx: --format-text 用 markdown table (默认); --no-format-text 用 raw_text。"
)
_CHUNK_STATS_HELP: Final[str] = "输出 chunk 质量统计。"
_NORMALIZE_HELP: Final[str] = (
    "LLM 段落重整: off (默认) | auto | force；auto/force 需 OPENAI_API_KEY。"
)

_CLI_HELP: Final[str] = """\
读取本地文件或 URL，解析、切块并打印到 stdout。

\b
Options:
  --mode [file|url]                  file=本地路径 (默认); url=HTTP(S) URL
  --format-text | --no-format-text
  --chunk-stats
  --normalize [off|auto|force]
  -r, --recursive                    仅 file 模式；>100MB 文件跳过

\b
Examples:
  rag-ingest report.pdf
  rag-ingest -r ./docs/ --chunk-stats
  rag-ingest --mode url https://example.com/page.html
"""

app = typer.Typer(name="rag-ingest", add_completion=False)


def _structure_mode_for_cli(mode: NormalizeMode) -> StructureMode | None:
    if mode == "off":
        return None
    if mode == "auto":
        return StructureMode.AUTO
    return StructureMode.FORCE


def _configure_cli_logging(normalize: NormalizeMode) -> None:
    if normalize == "off":
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logger.info("normalize=%s", normalize)


def _ensure_normalize_api_key(mode: NormalizeMode) -> None:
    if mode == "off" or settings.openai_api_key.get_secret_value().strip():
        return
    typer.echo(
        f"ingest failed: [{ConfigErrorCode.MISSING_ENV}] "
        "OPENAI_API_KEY required for --normalize auto|force",
        err=True,
    )
    raise typer.Exit(code=1)


def _build_pipeline(normalize: NormalizeMode = "off") -> IngestPipeline:
    structure_mode = _structure_mode_for_cli(normalize)
    if structure_mode is None:
        return IngestPipeline(chunker=Chunker(ChunkSettings()))
    chat_model = get_structured_chat_model(StructuredText, temperature=0.1)
    normalizer = StructureNormalizer(chat_model=chat_model, mode=structure_mode)
    return IngestPipeline(
        chunker=Chunker(ChunkSettings()),
        normalizer=normalizer,
    )


def _format_heading(metadata: ChunkMetadata) -> str:
    headings = metadata.heading_stack
    if not headings:
        return "(root)"
    return " > ".join(headings)


def _format_preview(text: str) -> str:
    flat = text.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
    if len(flat) <= _PREVIEW_LEN:
        return flat
    return flat[:_PREVIEW_LEN] + "..."


def _render_result(result: IngestResult, *, chunk_stats: bool = False) -> None:
    chunks = result.chunks
    total = len(chunks)
    typer.echo(f"title: {result.title}")
    typer.echo(f"source: {result.doc_meta.source}")
    typer.echo(f"datasource: {result.doc_meta.datasource}")
    typer.echo(f"page_count: {result.doc_meta.page_count}")
    typer.echo(f"chunks: {total}")
    if chunk_stats and chunks:
        chunk_settings = ChunkSettings()
        metrics = measure_chunks(chunks, chunk_settings.chunk_size)
        typer.echo(format_chunk_stats(metrics, chunk_settings.chunk_size))
    typer.echo("---")
    for idx, chunk in enumerate(chunks):
        heading = _format_heading(chunk.metadata)
        preview = _format_preview(chunk.text)
        typer.echo(f"[{idx}/{total}] {result.doc_meta.source}: {heading} | {preview}")
    if result.warnings:
        typer.echo("---")
        typer.echo("warnings:")
        for w in result.warnings:
            typer.echo(f"{_YELLOW}{w}{_RESET}", err=True)


def _render_error(exc: Exception) -> None:
    if isinstance(exc, RAGError):
        typer.echo(f"ingest failed: [{exc.code}] {exc.message}", err=True)
    else:
        typer.echo(f"ingest failed: [{type(exc).__name__}] {exc}", err=True)


def _run_ingest(
    source: IngestSource,
    *,
    get_format_text: bool = True,
    normalize: NormalizeMode = "off",
    chunk_stats: bool = False,
) -> None:
    _configure_cli_logging(normalize)
    _ensure_normalize_api_key(normalize)
    pipeline = _build_pipeline(normalize=normalize)
    try:
        result = asyncio.run(pipeline.ingest(source, get_format_text=get_format_text))
    except Exception as exc:  # noqa: BLE001
        _render_error(exc)
        raise typer.Exit(code=1) from exc
    _render_result(result, chunk_stats=chunk_stats)


async def _run_batch_async(
    file_paths: list[Path],
    *,
    format_text: bool,
    normalize: NormalizeMode,
) -> list[IngestResult | BaseException]:
    pipeline = _build_pipeline(normalize=normalize)
    sources: list[IngestSource] = [FileSource(path=p) for p in file_paths]

    async def _one(src: IngestSource) -> IngestResult:
        return await pipeline.ingest(src, get_format_text=format_text)

    return await asyncio.gather(*[_one(s) for s in sources], return_exceptions=True)


def _run_batch(
    *,
    file_paths: list[Path],
    normalize: NormalizeMode,
    format_text: bool,
    chunk_stats: bool,
) -> None:
    _configure_cli_logging(normalize)
    _ensure_normalize_api_key(normalize)
    try:
        results = asyncio.run(
            _run_batch_async(
                file_paths=file_paths,
                format_text=format_text,
                normalize=normalize,
            )
        )
    except Exception as exc:  # noqa: BLE001
        _render_error(exc)
        raise typer.Exit(code=1) from exc

    had_failure = False
    total = len(file_paths)
    for idx, (path, result) in enumerate(
        zip(file_paths, results, strict=True), start=1
    ):
        typer.echo(f"[{idx}/{total}] {path}")
        if isinstance(result, BaseException):
            had_failure = True
            _render_error(
                result if isinstance(result, Exception) else Exception(result)
            )
        else:
            _render_result(result, chunk_stats=chunk_stats)
        if idx < total:
            typer.echo(_SEPARATOR)

    if had_failure:
        raise typer.Exit(code=1)


def _expand_paths(
    file_paths: list[Path],
    *,
    recursive: bool,
) -> tuple[list[Path], list[str]]:
    expanded: list[Path] = []
    warnings: list[str] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        if p in seen:
            return
        seen.add(p)
        expanded.append(p)

    for raw in file_paths:
        if not raw.exists():
            warnings.append(f"path not found: {raw}")
            continue
        if raw.is_file():
            _add(raw)
            continue
        if raw.is_dir():
            if not recursive:
                warnings.append(f"skip directory (pass --recursive to descend): {raw}")
                continue
            for p in sorted(raw.rglob("*")):
                if not p.is_file() or p.name.startswith("."):
                    continue
                if any(part in _SKIP_DIR_NAMES for part in p.parts):
                    continue
                try:
                    if p.stat().st_size > _MAX_FILE_BYTES:
                        warnings.append(
                            f"skip oversized file (>{_MAX_FILE_BYTES} bytes): {p}"
                        )
                        continue
                except OSError as exc:
                    warnings.append(f"stat failed for {p}: {exc}")
                    continue
                _add(p)
            continue
        warnings.append(f"skip non-file/non-dir path: {raw}")

    expanded.sort()
    return expanded, warnings


def _render_batch_prelude(file_paths: list[Path], warnings: list[str]) -> None:
    typer.echo(f"batch: {len(file_paths)} file(s)")
    for w in warnings:
        typer.echo(f"{_YELLOW}{w}{_RESET}", err=True)
    typer.echo(_SEPARATOR)


@app.command(help=_CLI_HELP)
def ingest_cmd(
    targets: Annotated[
        list[str],
        typer.Argument(help="file: 本地路径 (可多); url: 单个 HTTP(S) URL。"),
    ],
    mode: Annotated[
        IngestMode,
        typer.Option("--mode", help="file (默认) | url。", case_sensitive=False),
    ] = "file",
    recursive: Annotated[
        bool,
        typer.Option("-r", "--recursive", help="file 模式: 递归展开目录。"),
    ] = False,
    format_text: Annotated[
        bool,
        typer.Option("--format-text/--no-format-text", help=_FORMAT_TEXT_HELP),
    ] = True,
    chunk_stats: Annotated[
        bool,
        typer.Option("--chunk-stats", help=_CHUNK_STATS_HELP),
    ] = False,
    normalize: Annotated[
        NormalizeMode,
        typer.Option("--normalize", help=_NORMALIZE_HELP, case_sensitive=False),
    ] = "off",
) -> None:
    ingest_mode = cast(IngestMode, mode.lower())
    normalize_mode = cast(NormalizeMode, normalize.lower())

    if ingest_mode == "url":
        if len(targets) != 1:
            typer.echo("url mode requires exactly one URL", err=True)
            raise typer.Exit(code=1)
        _run_ingest(
            UrlSource(url=targets[0]),
            get_format_text=format_text,
            normalize=normalize_mode,
            chunk_stats=chunk_stats,
        )
        return

    if ingest_mode != "file":
        typer.echo(f"unsupported --mode: {ingest_mode!r} (use file or url)", err=True)
        raise typer.Exit(code=1)

    file_paths = [Path(t) for t in targets]
    expanded, warnings = _expand_paths(file_paths, recursive=recursive)
    if not expanded and warnings:
        for w in warnings:
            typer.echo(f"{_YELLOW}{w}{_RESET}", err=True)
        raise typer.Exit(code=1)

    is_batch = len(expanded) > 1 or bool(warnings)
    if is_batch:
        _render_batch_prelude(expanded, warnings)
        _run_batch(
            file_paths=expanded,
            normalize=normalize_mode,
            format_text=format_text,
            chunk_stats=chunk_stats,
        )
    else:
        _run_ingest(
            FileSource(path=expanded[0]),
            get_format_text=format_text,
            normalize=normalize_mode,
            chunk_stats=chunk_stats,
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
