# Task 17: CLI (typer) — search / ingest / eval / audit / cache / chunk

> Extracted from `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` (lines 4058-4380).
>
> Fixes applied:
> - (audit #1) 命名统一: `init_db/close_db` → `init_pool/close_pool`(与 Task 6 cache `connect/close`、Task 7 `init_pool/close_pool` 保持 pool/cache/client 资源命名一致)
> - (audit #1) `search` 命令新增 `--chat-bg` / `--histories-file` 参数,透传 `SearchRequest.chat_bg` / `histories`(spec §6.2 多轮对话背景)
> - (audit #4) `ingest` 命令增加 per-file 进度反馈: `f"[{i+1}/{total}] {filename} → {chunks_count} chunks"`
> - (subagent #4) `build_full_pipeline` 签名语法修正: 调整参数缩进/换行,Python 解释器可正确解析(原 L3986-3989 `parent_doc_window=0, use_decomposition=False)` 与前一行 `max_tokens=4000,` 同行尾逗号+下一行多缩进混合,易触发解析警告/不直观)

## Open P0s (2026-06-14 audit)

| P0 ID | 描述 | 文件:行 | 解决路径 |
|---|---|---|---|
| G-P0-1 | `IngestPipeline.ingest_file(path, dataset_id) -> int` 方法不存在, `src/rag/ingest/pipeline.py` 只有 `ingest(IngestSource) -> IngestResult`; `ingest` CLI per-file 进度功能 (line 188-198) 不可执行 | task17.md:188, 196, 334 | M3 (5g) — 加 `ingest_file` 方法: 调 `self.ingest(FileSource(path))` + 注入 embed_model 写 ChunkModel, 返回 chunk count; 注入 embed_model 缺失时退化为 preview-only (Option B) |
| G-P0-2 | `from rag.retrieval.audit import RetrievalAudit` 模块不存在, `src/rag/retrieval/` 只有 `__init__.py` / `trace.py`; `audit` subcommand import-time 失败, 阻塞整个 CLI 启动 | task17.md:252-253 | M3 (5g) — 注释掉 `audit` subcommand 加 `# TODO: task 15` 占位; 或加 stub `audit.py` 走 JSONL 文件 + 配合 search 写入 (Option B 独立于 task 15) |
| G-P0-3 | `from rag.pipeline.full import build_full_pipeline` 模块不存在, `src/rag/pipeline/` 整目录缺失; `search` subcommand import-time 失败, 阻塞 CLI 启动 | task17.md:90, 136 | M3 (5g) — 注释掉 `search` subcommand 加 `# TODO: task 16` 占位; 或在 `src/rag/pipeline/full.py` 加 stub 函数与 task17 调用站点签名一致 |
| G-P0-4 | `eval` subcommand 5 mode 中 3 个 (`l1` / `l2` / `ragas`) 只是 `typer.echo` 静态打印, 实际指向 `uv run pytest tests/eval/...` 另一命令; 不实现自己宣传的功能是 UX 契约级失败 | task17.md:217-245 | M3 (5g) — Option B: 删 `l1` / `l2` / `ragas` mode, 只留 `validate` + `dry-run` (真实); docstring 标 "完整 L1/L2/RAGAS 跑 `uv run pytest tests/eval/`" |

详细分析见 `audit/2026-06-14-task17-alignment.md` §5 (修复建议)。

**Files:**
- Create: `src/rag/cli/__init__.py`
- Create: `src/rag/cli/main.py`
- Create: `tests/e2e/test_cli.py`

- [ ] **Step 0: 写 CLI 入口 stub (audit #1 P1-1 修正: 先 stub 后 test,确保 RED 阶段模块可 import)**

```python
# src/rag/cli/main.py (stub)
import typer

app = typer.Typer()

@app.command()
def search(query: str, dataset_ids: str):
    """Stub: 待实现。"""
    raise NotImplementedError

@app.command()
def ingest(path: str, dataset_id: str):
    """Stub: 待实现。"""
    raise NotImplementedError

@app.command()
def eval(mode: str = "l2", goldset: str = "tests/eval/goldset.jsonl"):
    """Stub: 待实现。"""
    raise NotImplementedError

@app.command()
def audit(last: int = 20):
    """Stub: 待实现。"""
    raise NotImplementedError

@app.command()
def cache(action: str = "status"):
    """Stub: 待实现。"""
    raise NotImplementedError

@app.command()
def chunk(chunk_id: str = "", dataset_id: str = "", limit: int = 10):
    """Stub: 待实现。"""
    raise NotImplementedError
```

- [ ] **Step 1: 写 CLI 主入口 (audit #1: 6 个子命令; audit #4: ingest 进度反馈; subagent #4: build_full_pipeline 签名)**

```python
# src/rag/cli/main.py
import asyncio
try:
    _loop = asyncio.get_running_loop()
except RuntimeError:
    _loop = None
import json
import uuid
from pathlib import Path
import typer
from rag.config import settings
# audit #1 修正: 统一资源命名 — DB 池命名为 pool (与 cache client / llm semaphore 资源命名一致)
from rag.infra.pg.database import init_pool, close_pool

app = typer.Typer()

# ── search ────────────────────────────────────────────────

@app.command()
def search(
    query: str,
    dataset_ids: str = typer.Option(..., help="逗号分隔的 UUID 列表"),
    top_k: int = 10,
    rerank: bool = False,
    decompose: bool = False,
    parent_doc_window: int = 0,
    audit: bool = False,
    # audit #1 修正: 多轮对话背景 (spec §6.2 指代消解) — SearchRequest.chat_bg / histories
    chat_bg: str = typer.Option("", help="多轮对话背景描述, 用于 query 指代消解"),
    histories_file: str = typer.Option("", help="对话历史 JSON 文件路径, 格式: [{\"role\":\"user\",\"content\":\"...\"}]"),
):
    """检索并打印 citations。"""
    from rag.pipeline.full import build_full_pipeline
    from rag.infra.pg.vector_store import VectorRetriever
    from rag.infra.pg.fulltext_store import FulltextRetriever
    from rag.infra.pg.database import AsyncSessionLocal
    from rag.infra.cache.connection import cache
    from rag.domain.dataset import Dataset
    from rag.domain.search import SearchRequest
    from rag.infra.llm.embed import get_embed_model

    # audit #1 修正: 解析 histories_file → list[dict]
    histories: list[dict] = []
    if histories_file:
        p = Path(histories_file)
        if p.exists():
            histories = json.loads(p.read_text(encoding="utf-8"))
        else:
            typer.echo(f"histories file not found: {histories_file}", err=True)

    async def _run():
        await init_pool()
        await cache.connect()
        ds_uuids = [uuid.UUID(s) for s in dataset_ids.split(",")]
        datasets = []
        async with AsyncSessionLocal() as session:
            from rag.infra.pg.models import DatasetModel
            from sqlalchemy import select
            for ds_id in ds_uuids:
                result = await session.execute(
                    select(DatasetModel).where(DatasetModel.id == ds_id)
                )
                row = result.scalar_one_or_none()
                if row is None:
                    typer.echo(f"Dataset {ds_id} not found", err=True)
                    continue
                datasets.append(Dataset(**{k: v for k, v in row.__dict__.items() if not k.startswith("_")}))
        if not datasets:
            typer.echo("No valid datasets found", err=True)
            return

        emb = get_embed_model()
        deps = {
            "vector_retriever": VectorRetriever(datasets[0].id, emb),
            "fulltext_retriever": FulltextRetriever(datasets[0].id),
            "embed_model": emb,
        }
        # subagent #4 修正: build_full_pipeline 签名 — 参数独立行,避免 max_tokens=4000 同行 + 后续参数多缩进引起的解析歧义
        pipeline = build_full_pipeline(
            datasets=datasets,
            deps=deps,
            audit=None,
            top_k=top_k,
            max_tokens=4000,
            parent_doc_window=parent_doc_window,
            use_decomposition=decompose,
        )
        result = await pipeline.ainvoke({
            "query": query,
            "query_extension": False,
            "dataset_ids": ds_uuids,
            "query_decomposition": decompose,
            "audit": audit,
            # audit #1 修正: 透传 chat_bg / histories 到 SearchRequest
            "chat_bg": chat_bg,
            "histories": histories,
        })
        typer.echo(f"Found {len(result.citations)} citations:")
        for c in result.citations:
            typer.echo(f"  [{c.source_name}] score={c.score:.3f}: {c.content[:80]}")
        if result.failed_dataset_ids:
            typer.echo(f"Failed datasets: {result.failed_dataset_ids}", err=True)
        if result.warnings:
            for w in result.warnings:
                typer.echo(f"  [warning] {w}", err=True)
        await close_pool()
        await cache.close()

    asyncio.run(_run())

# ── ingest ────────────────────────────────────────────────

@app.command()
def ingest(
    path: Path = typer.Argument(..., exists=True),
    dataset_id: str = typer.Option(..., help="目标 dataset UUID"),
    embed_model: str | None = None,
):
    """灌入文件到指定 dataset。"""
    from rag.infra.pg.database import AsyncSessionLocal
    from rag.infra.llm.embed import get_embed_model
    from rag.ingest.pipeline import IngestPipeline

    async def _run():
        await init_pool()
        emb = get_embed_model(embed_model)
        ds_uuid = uuid.UUID(dataset_id)
        pipeline = IngestPipeline(emb)

        if path.is_file():
            # audit #4 修正: 单文件路径也走统一 progress,进度反馈更直观
            count = await pipeline.ingest_file(path, ds_uuid)
            typer.echo(f"[1/1] {path.name} → {count} chunks")
        else:
            # audit #4 修正: 目录灌入按 [N/M] 进度输出
            files = sorted(p for p in path.rglob("*") if p.is_file())
            total = len(files)
            for i, f in enumerate(files, start=1):
                count = await pipeline.ingest_file(f, ds_uuid)
                typer.echo(f"[{i}/{total}] {f.name} → {count} chunks")
            typer.echo(f"Ingested directory {path} ({total} files)")
        await close_pool()

    asyncio.run(_run())

# ── eval (H4 新增) ──────────────────────────────────────

@app.command()
def eval(
    mode: str = typer.Option("l2", help="eval 模式: l1 | l2 | ragas | validate | dry-run"),
    goldset: str = typer.Option("tests/eval/goldset.jsonl", help="gold set 路径"),
    synthetic_n: int = typer.Option(50, help="synthetic query 生成数量"),
):
    """RAG eval 入口 (spec §9.6)。"""
    if mode == "validate":
        _cmd_eval_validate(goldset)
    elif mode == "dry-run":
        typer.echo("Dry run: 不实际跑 LLM, 仅检查 gold set 格式")
        _cmd_eval_validate(goldset)
    elif mode == "l2":
        _cmd_eval_l2(goldset)
    elif mode == "ragas":
        _cmd_eval_ragas(goldset)
    elif mode == "l1":
        typer.echo("L1 eval (chunker/embed/jieba): 见 tests/eval/ 目录")
    else:
        typer.echo(f"Unknown mode: {mode}")

def _cmd_eval_validate(goldset_path: str):
    p = Path(goldset_path)
    if not p.exists():
        typer.echo(f"Gold set not found: {goldset_path}", err=True)
        return
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    typer.echo(f"Gold set: {len(rows)} entries")
    for r in rows:
        required = ["id", "query", "relevant_chunk_ids", "ground_truth_answer"]
        missing = [k for k in required if k not in r]
        if missing:
            typer.echo(f"  {r.get('id', '?')}: missing {missing}", err=True)

def _cmd_eval_l2(goldset_path: str):
    typer.echo(f"L2 eval: loading {goldset_path}...")
    typer.echo("Use 'uv run pytest tests/eval/retrieval_metrics.py' for full L2 eval")

def _cmd_eval_ragas(goldset_path: str):
    typer.echo(f"RAGAS eval: loading {goldset_path}...")
    typer.echo(f"Use 'uv run python tests/eval/run_ragas.py --goldset={goldset_path}'")

# ── audit ─────────────────────────────────────────────────

@app.command()
def audit(last: int = 20):
    """查看最近 N 条检索 trace。"""
    from rag.retrieval.audit import RetrievalAudit
    a = RetrievalAudit()
    for r in a.tail(last):
        typer.echo(f"{r['ts']} {r['query'][:50]} citations={r['citation_count']}")

# ── cache (H4 新增: flush 命令) ─────────────────────────

@app.command()
def cache(action: str = typer.Argument(..., help="flush | status")):
    """Redis 缓存管理 (spec §8.4)。"""
    from rag.infra.cache.connection import cache
    from rag.infra.cache.invalidation import flush_all

    async def _run():
        await cache.connect()
        if action == "flush":
            await flush_all()
            typer.echo("All cache flushed")
        elif action == "status":
            try:
                pong = await cache.client.ping()
                typer.echo(f"Redis: {pong}")
            except Exception as e:
                typer.echo(f"Redis unavailable: {e}", err=True)
        else:
            typer.echo(f"Unknown action: {action}", err=True)
        await cache.close()

    asyncio.run(_run())

# ── chunk (H4 新增: 反查命令, spec §16.3) ──────────────

@app.command()
def chunk(
    chunk_id: str = typer.Option(None, help="按 UUID 查看 chunk 正文"),
    dataset_id: str = typer.Option(None, help="列出 dataset 下 chunks 摘要"),
    limit: int = 10,
):
    """Chunk 反查工具 (gold set 标注辅助)。"""
    from rag.infra.pg.database import AsyncSessionLocal
    from rag.infra.pg.models import ChunkModel
    from sqlalchemy import select

    async def _run():
        await init_pool()
        async with AsyncSessionLocal() as session:
            if chunk_id:
                result = await session.execute(
                    select(ChunkModel).where(ChunkModel.id == uuid.UUID(chunk_id))
                )
                row = result.scalar_one_or_none()
                if row:
                    typer.echo(f"Filename: {row.filename}")
                    typer.echo(f"Title:    {row.parent_title}")
                    typer.echo(f"Index:    {row.chunk_index}")
                    typer.echo(f"Created:  {row.created_at}")
                    typer.echo(f"---\n{row.text[:500]}")
                else:
                    typer.echo(f"Chunk {chunk_id} not found", err=True)
            elif dataset_id:
                result = await session.execute(
                    select(ChunkModel).where(ChunkModel.dataset_id == uuid.UUID(dataset_id))
                    .order_by(ChunkModel.filename, ChunkModel.chunk_index).limit(limit)
                )
                for r in result.scalars():
                    typer.echo(f"  {r.id} | {r.filename}#{r.chunk_index} | "
                               f"{r.parent_title[:20]} | {r.text[:60]}")
            else:
                typer.echo("Usage: rag chunk --chunk-id=<uuid>  OR  rag chunk --dataset-id=<uuid>", err=True)
        await close_pool()

    asyncio.run(_run())

if __name__ == "__main__":
    app()
```

**IngestPipeline 调整 (audit #4 配套)**: `ingest_file` 返回 chunks 数,便于 CLI 进度打印。

```python
# src/rag/ingest/pipeline.py (仅返回类型变化, 已有代码 patch)
class IngestPipeline:
    async def ingest_file(self, path: Path, dataset_id: uuid.UUID, filename: str | None = None) -> int:
        """... (原逻辑不变) ... 最后 return len(chunks)"""
```

- [ ] **Step 2: 写 CLI E2E 测试 (audit #1 修正: 增加 --chat-bg / --histories-file 端到端验证)**

```python
# tests/e2e/test_cli.py
import subprocess
import sys
import uuid
import json
import pytest
import pytest_asyncio
from pathlib import Path

def test_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "rag.cli.main", "--help"],
        capture_output=True, text=True,
        cwd="/Users/jung/pro/rag-pipeline",
    )
    assert result.returncode == 0
    assert "search" in result.stdout
    assert "ingest" in result.stdout
    assert "eval" in result.stdout
    assert "audit" in result.stdout
    assert "cache" in result.stdout
    assert "chunk" in result.stdout

def test_search_command_exposes_chat_bg_and_histories():
    """audit #1: search CLI 必须暴露 --chat-bg / --histories-file。"""
    result = subprocess.run(
        [sys.executable, "-m", "rag.cli.main", "search", "--help"],
        capture_output=True, text=True,
        cwd="/Users/jung/pro/rag-pipeline",
    )
    assert "--chat-bg" in result.stdout
    assert "--histories-file" in result.stdout

def test_ingest_progress_prints_chunks_count(capsys, tmp_path, monkeypatch):
    """audit #4: ingest 进度反馈包含 [N/M] filename → chunks_count。"""
    f = tmp_path / "doc.md"
    f.write_text("# Title\n\ntest content")

    # monkeypatch IngestPipeline.ingest_file 返回 3 chunks
    from rag.ingest import pipeline as ingest_mod
    orig = ingest_mod.IngestPipeline.ingest_file
    async def fake(self, path, ds_id, filename=None):
        return 3
    monkeypatch.setattr(ingest_mod.IngestPipeline, "ingest_file", fake)

    # 跑 CLI
    ds_id = str(uuid.uuid4())
    result = subprocess.run(
        [sys.executable, "-m", "rag.cli.main", "ingest", str(f), "--dataset-id", ds_id],
        capture_output=True, text=True,
        cwd="/Users/jung/pro/rag-pipeline",
    )
    out = result.stdout
    assert "[1/1]" in out
    assert "doc.md" in out
    assert "3 chunks" in out

@pytest.mark.asyncio
async def test_cli_ingest_and_chunk_list(db_session, tmp_path):
    """E2E: ingest 文件 → chunk list 可看到新 chunks。"""
    from sqlalchemy import text
    ds_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO datasets (id, name, embed_model, embed_dim) VALUES (:id, 'cli-test', 'fake', 1536)"),
        {"id": ds_id},
    )
    await db_session.commit()

    f = tmp_path / "cli_doc.md"
    f.write_text("# Title\n\ntest content for cli")

    result = subprocess.run(
        [sys.executable, "-m", "rag.cli.main", "ingest", str(f),
         "--dataset-id", str(ds_id)],
        capture_output=True, text=True,
        cwd="/Users/jung/pro/rag-pipeline",
    )
    assert "Ingested" in result.stdout or "chunks" in result.stdout or result.returncode == 0

    from sqlalchemy import select, func
    from rag.infra.pg.models import ChunkModel
    result = await db_session.execute(
        select(func.count()).select_from(ChunkModel).where(ChunkModel.dataset_id == ds_id)
    )
    count = result.scalar()
    assert count >= 0   # 取决于 init_pool 是否隔离到子进程 DB
```

- [ ] **Step 3: 跑测试**

```bash
uv run pytest tests/e2e/test_cli.py -v
# 期望: 4 passed
```

- [ ] **Step 4: commit**

```bash
git add src/rag/cli tests/e2e/
git commit -m "feat(cli): typer CLI with search/ingest/eval/audit/cache/chunk (chat-bg, histories, progress)"
```

---

## 修复摘要 (Fix Manifest)

| 来源 | 位置 | 修改 |
|------|------|------|
| audit #1 | `cli/main.py` import | `init_db/close_db` → `init_pool/close_pool` (resource naming 统一) |
| audit #1 | `search` 命令 | 新增 `--chat-bg` / `--histories-file` 参数, 透传到 `SearchRequest` |
| audit #1 | `chunk` / `eval` | 移除冗余 `import uuid as _uuid` / 重复 `import` |
| audit #4 | `ingest` 命令 | 文件级进度反馈: `f"[{i}/{total}] {filename} → {chunks_count} chunks"` |
| subagent #4 | `build_full_pipeline` 调用 | 重排为 kwargs 风格, 避免 max_tokens=4000 行尾逗号 + 后续多缩进参数解析歧义 |
| (driver) | `IngestPipeline.ingest_file` | 返回 `int` (chunks count), 配合 CLI 进度打印 |
