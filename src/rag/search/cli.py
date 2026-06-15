"""``rag-search`` Typer CLI: query → SearchResult。

通过 ``build_search_pipeline`` 装配 pipeline 并执行检索 + 生成。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Annotated, Final

import typer
from langchain_core.embeddings import Embeddings

from rag.config import settings
from rag.domain.search import SearchRequest
from rag.infra.llm.chat import get_chat_model
from rag.infra.llm.embed import get_embed_model
from rag.infra.llm.rerank import get_rerank_model
from rag.infra.observability.audit import AuditTap
from rag.search.factory import SearchPipelineDeps, build_search_pipeline

logger = logging.getLogger(__name__)

_RESET: Final[str] = "\033[0m"
_YELLOW: Final[str] = "\033[33m"
_SEPARATOR: Final[str] = "=" * 60


def _build_embedder() -> Embeddings:
    """从 settings 构造真实 embedding 模型。"""
    return get_embed_model()


def _build_llm() -> object:
    """从 settings 构造真实 LLM。"""
    return get_chat_model()


def _build_rerank_or_none() -> object:
    """若 API key 已设置则构造 rerank 模型, 否则返回 None。"""
    if not settings.openai_rerank_api_key.get_secret_value().strip():
        return None
    return get_rerank_model()


app = typer.Typer(name="rag-search", add_completion=False)


def _err_exit(msg: str, code: int = 1) -> typer.Exit:
    typer.echo(f"{_YELLOW}{msg}{_RESET}", err=True)
    raise typer.Exit(code=code)


def _emit_text(query: str, response: str, citations: list, hits: list) -> None:
    """以可读文本格式输出 response 和 citations。"""
    typer.echo(_SEPARATOR)
    typer.echo(f"Query: {query}")
    typer.echo(_SEPARATOR)
    typer.echo(f"Response:\n{response}")
    typer.echo(_SEPARATOR)
    typer.echo(f"Citations ({len(citations)}):")
    for i, c in enumerate(citations, start=1):
        typer.echo(
            f"  [{i}] {c.source_name} (chunk_id={c.chunk_id}, score={c.score:.3f})"
        )
        preview = c.content[:80].replace("\n", " ")
        typer.echo(f"      {preview}{'...' if len(c.content) > 80 else ''}")
    typer.echo(_SEPARATOR)
    typer.echo(f"Intermediate hits: {len(hits)}")


def _emit_json(
    query: str, request: SearchRequest, response_text: str, citations: list, hits: list
) -> None:
    """以 JSON 格式输出结果到 stdout。"""
    out = {
        "query": query,
        "dataset_ids": [str(d) for d in request.dataset_ids],
        "response": response_text,
        "citations": [
            {
                "chunk_id": str(c.chunk_id),
                "dataset_id": str(c.dataset_id),
                "source_name": c.source_name,
                "content": c.content,
                "score": c.score,
                "position": c.position,
                "image_path": c.image_path,
            }
            for c in citations
        ],
        "intermediate_hits_count": len(hits),
        "intermediate_hits": [
            {
                "chunk_id": str(h.chunk_id),
                "dataset_id": str(h.dataset_id),
                "score": h.score,
                "text_preview": h.text[:120],
            }
            for h in hits
        ],
    }
    typer.echo(json.dumps(out, ensure_ascii=False, indent=2))


@app.command()
def main(
    query: Annotated[
        str,
        typer.Option("-q", "--query", help="搜索 query (用户提问)。"),
    ],
    dataset_id: Annotated[
        list[uuid.UUID] | None,
        typer.Option(
            "--dataset-id",
            help="目标 dataset UUID (可多次指定)。至少 1 个。",
        ),
    ] = None,
    top_k: Annotated[
        int,
        typer.Option("-k", "--top-k", help="每 dataset 召回 top-k (默认 10)。"),
    ] = 10,
    output: Annotated[
        str,
        typer.Option(
            "--output",
            help="输出格式: text (默认) | json。",
            case_sensitive=False,
        ),
    ] = "text",
    audit: Annotated[
        bool,
        typer.Option(
            "--audit",
            help="写入 audit NDJSON 到 settings.cache.audit_path (如果配置)。",
        ),
    ] = False,
    audit_path: Annotated[
        Path | None,
        typer.Option(
            "--audit-path",
            help="指定 audit NDJSON 写入路径 (默认 settings 配置)。",
        ),
    ] = None,
    rerank_weight: Annotated[
        float,
        typer.Option("--rerank-weight", help="Rerank 权重 (0-1, 默认 0.7)。"),
    ] = 0.7,
) -> None:
    """查询 rag-pipeline 并输出 SearchResult。

    Args:
        query: 用户搜索 query。
        dataset_id: 目标 dataset UUID 列表, 至少 1 个。
        top_k: 每 dataset 召回 top-k。
        output: 输出格式, text 或 json。
        audit: 是否启用 audit 写入。
        audit_path: audit NDJSON 显式写入路径。
        rerank_weight: rerank 权重, 范围 0-1。
    """
    if not query or not query.strip():
        _err_exit("query 不能为空")
    if not dataset_id:
        _err_exit("至少指定一个 --dataset-id")
    if output.lower() not in ("text", "json"):
        _err_exit(f"--output 必须是 text 或 json, got {output!r}")

    try:
        embedder = _build_embedder()
        llm = _build_llm()
        rerank = _build_rerank_or_none()
    except Exception as e:
        _err_exit(f"构建依赖失败: {e!r}")

    audit_tap: AuditTap | None = None
    if audit:
        path = audit_path or _default_audit_path()
        if path is None:
            typer.echo(f"{_YELLOW}--audit 指定但未配置 audit_path{_RESET}", err=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            audit_tap = AuditTap(path, sample_rate=1.0, sync=True)

    deps = SearchPipelineDeps(
        embedder=embedder,
        llm=llm,
        rerank_client=rerank,
        audit_tap=audit_tap,
        top_k=top_k,
        rerank_weight=rerank_weight,
    )
    pipeline = build_search_pipeline(deps)
    assert dataset_id is not None
    req = SearchRequest(query=query, dataset_ids=list(dataset_id), audit=audit)

    try:
        result = asyncio.run(pipeline.ainvoke(req))
    except Exception as e:
        _err_exit(f"pipeline.ainvoke failed: {e!r}")

    if output.lower() == "json":
        _emit_json(
            query, req, result.response, result.citations, result._intermediate_hits
        )
    else:
        _emit_text(query, result.response, result.citations, result._intermediate_hits)

    if audit_tap is not None:
        typer.echo(f"(audit → {audit_tap.file_path})", err=True)


def _default_audit_path() -> Path | None:
    """返回 settings 中的 audit 路径, 未配置则返回 None。"""
    raw = getattr(settings, "cache_audit_path", None)
    if raw and str(raw).strip():
        return Path(str(raw))
    return None


if __name__ == "__main__":
    app()
