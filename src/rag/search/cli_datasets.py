"""`rag-search list-datasets` 子命令。

挂在 `rag-search` 下, 让用户在执行检索前先发现可用的 dataset
(避免盲目猜测 UUID)。
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from rag.infra.pg.database import AsyncSessionLocal
from rag.infra.pg.repositories.dataset_repo import DatasetRepository

datasets_app = typer.Typer(
    name="list-datasets",
    add_completion=False,
    help="发现当前 PG 中可搜索的 dataset 列表。",
    no_args_is_help=True,
)


def _format_text_table(items: list) -> str:
    """渲染为对齐的纯文本表格。"""
    if not items:
        return "(no datasets found)"

    id_w = max(len("DATASET ID"), 8)
    name_w = max(len("NAME"), max(len(i.name) for i in items))
    embed_w = max(len("EMBED MODEL"), max(len(i.embed_model) for i in items))
    chunks_w = max(len("CHUNKS"), max(len(str(i.chunk_count)) for i in items))

    header = (
        f"{'DATASET ID':<{id_w}}  {'NAME':<{name_w}}  "
        f"{'EMBED MODEL':<{embed_w}}  {'CHUNKS':>{chunks_w}}  CREATED"
    )
    sep = "-" * len(header)
    rows = [
        (
            f"{str(i.id):<{id_w}}  "
            f"{i.name:<{name_w}}  "
            f"{i.embed_model:<{embed_w}}  "
            f"{i.chunk_count:>{chunks_w}}  "
            f"{i.created_at.strftime('%Y-%m-%d')}"
        )
        for i in items
    ]
    return "\n".join([header, sep, *rows])


@datasets_app.command(name="list")
def list_datasets(
    output: Annotated[
        str,
        typer.Option(
            "--output",
            help="输出格式: text (默认, 对齐表格) | json (含 total)。",
            case_sensitive=False,
        ),
    ] = "text",
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            help="最多返回条数 (默认 100)。",
        ),
    ] = 100,
    offset: Annotated[
        int,
        typer.Option(
            "--offset",
            help="分页偏移 (默认 0)。",
        ),
    ] = 0,
    name_contains: Annotated[
        str | None,
        typer.Option(
            "--name-contains",
            help="按 name 模糊过滤 (LIKE '%...%')。",
        ),
    ] = None,
    include_deleted: Annotated[
        bool,
        typer.Option(
            "--include-deleted/--no-include-deleted",
            help="是否包含软删除的 dataset (默认排除)。",
        ),
    ] = False,
) -> None:
    """列出 PG 中可搜索的 dataset 及其 chunk 数量。"""
    if output.lower() not in ("text", "json"):
        typer.echo(f"--output 必须是 text 或 json, got {output!r}", err=True)
        raise typer.Exit(1)
    if limit <= 0 or limit > 10000:
        typer.echo(f"--limit 必须在 (0, 10000], got {limit}", err=True)
        raise typer.Exit(1)
    if offset < 0:
        typer.echo(f"--offset 必须 >= 0, got {offset}", err=True)
        raise typer.Exit(1)

    async def _run() -> list:
        async with AsyncSessionLocal() as session:
            repo = DatasetRepository(session)
            return await repo.list(
                limit=limit,
                offset=offset,
                name_contains=name_contains,
                include_deleted=include_deleted,
            )

    try:
        items = asyncio.run(_run())
    except Exception as e:
        typer.echo(f"query failed: {e!r}", err=True)
        raise typer.Exit(1) from None

    if output.lower() == "json":
        payload = {
            "datasets": [i.model_dump(mode="json") for i in items],
            "total": len(items),
            "limit": limit,
            "offset": offset,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(_format_text_table(items))
