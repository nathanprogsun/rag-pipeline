from __future__ import annotations

import asyncio
import uuid
from asyncio import CancelledError
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest import fixture

from ingest_helpers import run_ingest
from rag.ingest import pipeline as pipeline_mod
from rag.ingest.chunker import Chunker, ChunkSettings
from rag.ingest.normalizer import NoOpNormalizer, StructureMode, StructureNormalizer
from rag.ingest.persist import persist as persist_chunks
from rag.ingest.pipeline import IngestPipeline
from rag.ingest.types import Chunk as IngestChunk
from rag.ingest.types import ChunkMetadata as IngestChunkMetadata
from rag.ingest.types import DocMeta, IngestResult, PersistConfig


def test_pipeline_txt_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("段落一。\n\n段落二。\n\n段落三。", encoding="utf-8")

    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=50)))
    result = run_ingest(pipeline, str(path))

    assert len(result.chunks) >= 1
    assert all(c.text.strip() for c in result.chunks)
    assert result.title == "a.txt"
    assert result.doc_meta.filename == "a.txt"


def test_pipeline_ingest_directory_expands(tmp_path: Path) -> None:
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text("# A\n\nbody a", encoding="utf-8")
    (d / "b.md").write_text("# B\n\nbody b", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=50)))
    outcome = asyncio.run(pipeline.ingest_many([str(d)]))
    assert len(outcome.items) == 2
    titles = {item.title for item in outcome.items}
    assert titles == {"A", "B"}


def test_pipeline_returns_chunk_objects(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text("# 标题\n\n内容。", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    result = run_ingest(pipeline, str(path))
    assert len(result.chunks) >= 1
    assert result.chunks[0].metadata.chunk_index == 0
    assert result.title == "标题"


def test_pipeline_populates_markdown_structure_metadata(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text(
        "# H1\n\n## H2\n\n正文含```python\nx=1\n``` 代码块。\n\n正文继续。",
        encoding="utf-8",
    )
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=200)))
    result = run_ingest(pipeline, str(path))
    assert len(result.chunks) >= 1
    chunks = result.chunks
    assert any(
        any("# H1" in h for h in c.metadata.heading_stack)
        and any("## H2" in h for h in c.metadata.heading_stack)
        for c in chunks
    )
    assert result.title == "H1"


def test_pipeline_txt_has_no_structure_metadata(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("plain text content", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    result = run_ingest(pipeline, str(path))
    assert len(result.chunks) >= 1
    for c in result.chunks:
        assert c.metadata.heading_stack == []
    assert result.title == "a.txt"


def _assert_doc_meta_injected(chunks: list, filename: str) -> None:
    for c in chunks:
        assert c.metadata.heading_stack is not None


def test_pipeline_injects_doc_meta_into_chunks(
    pipeline_e2e: IngestPipeline, sample_md: Path
) -> None:
    result = run_ingest(pipeline_e2e, str(sample_md))
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "sample.md")
    assert any(
        any("Sample Markdown Document" in h for h in c.metadata.heading_stack)
        for c in chunks
    )
    assert result.title == "Sample Markdown Document"
    assert result.doc_meta.filename == "sample.md"


def test_pipeline_txt_no_structure_metadata(
    pipeline_e2e: IngestPipeline, sample_txt: Path
) -> None:
    result = run_ingest(pipeline_e2e, str(sample_txt))
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "sample.txt")
    for c in chunks:
        assert c.metadata.heading_stack == []
    assert result.title == "sample.txt"


def test_pipeline_pdf_injects_page_count(
    pipeline_e2e: IngestPipeline, sample_pdf: Path
) -> None:
    result = run_ingest(pipeline_e2e, str(sample_pdf))
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "sample.pdf")
    assert result.doc_meta.page_count == 3


def test_pipeline_html_extracts_headings(
    pipeline_e2e: IngestPipeline, sample_html: Path
) -> None:
    result = run_ingest(pipeline_e2e, str(sample_html))
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "sample.html")
    assert result.title in ("Sample HTML Document", "sample.html")
    full = "\n".join(c.text for c in chunks)
    assert "Sample HTML Document" in full


def test_pipeline_without_normalizer_uses_noop() -> None:
    p = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=200)))
    assert isinstance(p.normalizer, NoOpNormalizer)


def test_pipeline_with_forbid_normalizer_does_not_call_llm(tmp_path: Path) -> None:
    from langchain_core.runnables import Runnable

    fake_model = MagicMock(spec=Runnable)
    p = IngestPipeline(
        chunker=Chunker(ChunkSettings(chunk_size=200)),
        normalizer=StructureNormalizer(
            chat_model=fake_model, mode=StructureMode.FORBID
        ),
    )
    f = tmp_path / "doc.txt"
    f.write_text(
        "hello content for testing pipeline normalizer integration", encoding="utf-8"
    )
    result = run_ingest(p, str(f))
    assert result.chunks
    fake_model.ainvoke.assert_not_called()
    fake_model.invoke.assert_not_called()


def test_pipeline_ingest_result_doc_meta_passthrough(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text("# Title\n\nbody", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    result = run_ingest(pipeline, str(path))
    assert result.doc_meta.filename == "a.md"


def test_pipeline_ingest_result_title_fallback_filename(tmp_path: Path) -> None:
    path = tmp_path / "no_heading.txt"
    path.write_text("plain content", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    result = run_ingest(pipeline, str(path))
    assert result.title == "no_heading.txt"


@fixture
def pipeline_e2e() -> IngestPipeline:
    return IngestPipeline(
        chunker=Chunker(
            ChunkSettings(chunk_size=200, max_chunk_size=800, min_chunk_size=50)
        ),
        normalizer=NoOpNormalizer(),
    )


@pytest.mark.asyncio
async def test_ingest_many_propagates_cancelled_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CancelledError 是 BaseException, 不应被吞。"""
    f = tmp_path / "a.txt"
    f.write_text("hello")

    async def cancel(_: Path, *, dataset_id: uuid.UUID | None = None) -> IngestResult:
        raise CancelledError()

    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    monkeypatch.setattr(pipeline, "_process", cancel)
    with pytest.raises(CancelledError):
        await pipeline.ingest_many([str(f)])


@pytest.mark.asyncio
async def test_ingest_many_resolves_dataset_once_for_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并发 ingest + create_dataset=True 必须只创建 1 个 dataset (消除 cfg.adopt race)。"""
    f1, f2, f3 = tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "c.txt"
    f1.write_text("a", encoding="utf-8")
    f2.write_text("b", encoding="utf-8")
    f3.write_text("c", encoding="utf-8")

    create_calls = 0
    resolved_id = uuid.uuid4()

    async def fake_create(session: object, name: str | None) -> uuid.UUID:
        nonlocal create_calls
        create_calls += 1
        return resolved_id

    monkeypatch.setattr(pipeline_mod, "_create_dataset_once", fake_create)

    # 短路 _process / _maybe_persist: 不真跑 PG 落库, 只验 dataset_id 透传
    async def fake_process(
        self: IngestPipeline,
        file: Path,
        *,
        dataset_id: uuid.UUID | None = None,
    ) -> IngestResult:
        from rag.ingest.types import DocMeta

        return IngestResult(
            chunks=[],
            title=file.name,
            doc_meta=DocMeta(filename=file.name),
        )

    monkeypatch.setattr(IngestPipeline, "_process", fake_process)

    pipeline = IngestPipeline(
        chunker=Chunker(ChunkSettings()),
        persist_config=PersistConfig(
            create_dataset=True, dataset_name="x", enabled=True
        ),
    )
    outcome = await pipeline.ingest_many([str(f1), str(f2), str(f3)])

    assert create_calls == 1
    # 三个文件都成功 (没走真实 PG, 但走通了分支)
    assert len(outcome.items) == 3
    assert outcome.errors == []


@pytest.mark.asyncio
async def test_max_concurrent_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = [(tmp_path / f"f{i}.txt") for i in range(20)]
    for f in files:
        f.write_text("x")

    in_flight = 0
    max_seen = 0

    async def fake_process(
        self: IngestPipeline,
        file: Path,
        *,
        dataset_id: uuid.UUID | None = None,
    ) -> IngestResult:
        nonlocal in_flight, max_seen
        in_flight += 1
        max_seen = max(max_seen, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return IngestResult(chunks=[], title=None, doc_meta=DocMeta(filename=str(file)))

    monkeypatch.setattr(IngestPipeline, "_process", fake_process)
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()), max_concurrent=4)
    await pipeline.ingest_many([str(f) for f in files])
    assert max_seen <= 4


# ----------------------------------------------------------------------
# persist() embed batches + resume behavior (短 session, insert 在 embed 循环外)
# ----------------------------------------------------------------------


class _FakeAsyncSession:
    """模拟 ``AsyncSessionLocal`` 上下文, 供单元测试 patch。"""

    def __init__(self) -> None:
        self.commit = AsyncMock()

    async def __aenter__(self) -> _FakeAsyncSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def _patch_persist_repos(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dataset_repo: MagicMock,
    doc_repo: _FakeDocumentRepo,
    chunk_repo: _FakeChunkRepo,
) -> None:
    import rag.ingest.persist as persist_mod

    monkeypatch.setattr(persist_mod, "AsyncSessionLocal", _FakeAsyncSession)
    monkeypatch.setattr(persist_mod, "DatasetRepository", lambda _s: dataset_repo)
    monkeypatch.setattr(persist_mod, "DocumentRepository", lambda _s: doc_repo)
    monkeypatch.setattr(persist_mod, "ChunkRepository", lambda _s: chunk_repo)


def _make_chunks(n: int) -> list[IngestChunk]:
    return [
        IngestChunk(
            id=uuid.uuid4(),
            text=f"chunk-{i}",
            metadata=IngestChunkMetadata(chunk_index=i, heading_stack=[]),
        )
        for i in range(n)
    ]


def _make_dataset() -> MagicMock:
    ds = MagicMock()
    ds.id = uuid.uuid4()
    ds.name = "ds-test"
    return ds


class _FakeEmbedder:
    """记录每批调用的 text 列表与对应 batch_size。"""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.batches: list[list[str]] = []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[0.0] * self.dim for _ in texts]


class _FakeDocumentRepo:
    def __init__(self, *, status: str = "pending") -> None:
        self.status = status
        self.marked: list[tuple[str, str | None]] = []
        self.document_id = uuid.uuid4()
        self.upsert_calls = 0

    async def upsert(self, **_kwargs: object) -> MagicMock:
        self.upsert_calls += 1
        doc = MagicMock()
        doc.id = self.document_id
        doc.status = "running"
        return doc

    async def mark_status(
        self, document_id: uuid.UUID, status: str, *, error_code: str | None = None
    ) -> None:
        self.status = status
        self.marked.append((status, error_code))

    async def get_active(
        self, dataset_id: uuid.UUID, filename: str
    ) -> MagicMock | None:
        if self.status == "running":
            doc = MagicMock()
            doc.id = self.document_id
            doc.status = "running"
            return doc
        return None


class _FakeChunkRepo:
    def __init__(self, existing_indexes: set[int] | None = None) -> None:
        self.existing = set(existing_indexes or ())
        self.bulk_inserts: list[list[object]] = []
        self.soft_delete_calls: list[uuid.UUID] = []

    async def get_existing_indexes(self, document_id: uuid.UUID) -> set[int]:
        return set(self.existing)

    async def soft_delete_by_document(self, document_id: uuid.UUID) -> int:
        self.soft_delete_calls.append(document_id)
        n = len(self.existing)
        self.existing.clear()
        return n

    async def bulk_insert(self, chunks: list[object]) -> None:
        self.bulk_inserts.append(list(chunks))


@pytest.mark.asyncio
async def test_persist_splits_embedding_into_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """embed_batch_size=4, 6 chunks -> embedder 调用 2 次 (4+2); insert 一次 bulk。"""
    ds = _make_dataset()
    dataset_repo = MagicMock()
    dataset_repo.get_by_id = AsyncMock(return_value=ds)
    doc_repo = _FakeDocumentRepo(status="pending")
    chunk_repo = _FakeChunkRepo()
    embedder = _FakeEmbedder()
    _patch_persist_repos(
        monkeypatch,
        dataset_repo=dataset_repo,
        doc_repo=doc_repo,
        chunk_repo=chunk_repo,
    )

    result_obj = IngestResult(
        chunks=_make_chunks(6),
        title="t",
        doc_meta=DocMeta(filename="f.txt"),
    )
    pr = await persist_chunks(
        result_obj,
        dataset_id=ds.id,
        embedder=embedder,  # type: ignore[arg-type]
        embed_batch_size=4,
    )

    assert len(embedder.batches) == 2
    assert [len(b) for b in embedder.batches] == [4, 2]
    # embed 循环外单次 bulk_insert
    assert len(chunk_repo.bulk_inserts) == 1
    assert sum(len(b) for b in chunk_repo.bulk_inserts) == 6
    assert pr.new_chunk_count == 6
    assert pr.old_chunk_count == 0


@pytest.mark.asyncio
async def test_persist_resume_skips_existing_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """status='running' + 已存在 chunk_index=0,1,2 -> 只 embed 3,4,5 (剩余 3 个)。"""
    ds = _make_dataset()
    dataset_repo = MagicMock()
    dataset_repo.get_by_id = AsyncMock(return_value=ds)
    doc_repo = _FakeDocumentRepo(status="running")
    chunk_repo = _FakeChunkRepo(existing_indexes={0, 1, 2})
    embedder = _FakeEmbedder()
    _patch_persist_repos(
        monkeypatch,
        dataset_repo=dataset_repo,
        doc_repo=doc_repo,
        chunk_repo=chunk_repo,
    )

    result_obj = IngestResult(
        chunks=_make_chunks(6),
        title="t",
        doc_meta=DocMeta(filename="f.txt"),
    )
    pr = await persist_chunks(
        result_obj,
        dataset_id=ds.id,
        embedder=embedder,  # type: ignore[arg-type]
        embed_batch_size=10,
    )

    assert chunk_repo.soft_delete_calls == []
    assert sum(len(b) for b in embedder.batches) == 3
    assert len(chunk_repo.bulk_inserts) == 1
    assert sum(len(b) for b in chunk_repo.bulk_inserts) == 3
    assert pr.old_chunk_count == 3
    assert pr.new_chunk_count == 3
    assert doc_repo.status == "completed"


@pytest.mark.asyncio
async def test_persist_fresh_ingest_soft_deletes_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """status='completed' (非 running) -> 软删旧 chunk, 全量重写。"""
    ds = _make_dataset()
    dataset_repo = MagicMock()
    dataset_repo.get_by_id = AsyncMock(return_value=ds)
    doc_repo = _FakeDocumentRepo(status="completed")
    chunk_repo = _FakeChunkRepo(existing_indexes={0, 1, 2, 3, 4})
    embedder = _FakeEmbedder()
    _patch_persist_repos(
        monkeypatch,
        dataset_repo=dataset_repo,
        doc_repo=doc_repo,
        chunk_repo=chunk_repo,
    )

    result_obj = IngestResult(
        chunks=_make_chunks(5),
        title="t",
        doc_meta=DocMeta(filename="f.txt"),
    )
    pr = await persist_chunks(
        result_obj,
        dataset_id=ds.id,
        embedder=embedder,  # type: ignore[arg-type]
        embed_batch_size=10,
    )

    assert chunk_repo.soft_delete_calls == [doc_repo.document_id]
    assert sum(len(b) for b in embedder.batches) == 5
    assert len(chunk_repo.bulk_inserts) == 1
    assert sum(len(b) for b in chunk_repo.bulk_inserts) == 5
    assert pr.old_chunk_count == 5
    assert pr.new_chunk_count == 5


@pytest.mark.asyncio
async def test_persist_accepts_embed_batch_size_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最小冒烟: persist 接受 embed_batch_size kwarg, 不报 TypeError。"""
    ds = _make_dataset()
    dataset_repo = MagicMock()
    dataset_repo.get_by_id = AsyncMock(return_value=ds)
    doc_repo = _FakeDocumentRepo(status="pending")
    chunk_repo = _FakeChunkRepo()
    embedder = _FakeEmbedder()
    _patch_persist_repos(
        monkeypatch,
        dataset_repo=dataset_repo,
        doc_repo=doc_repo,
        chunk_repo=chunk_repo,
    )

    result_obj = IngestResult(
        chunks=_make_chunks(2),
        title="t",
        doc_meta=DocMeta(filename="f.txt"),
    )
    await persist_chunks(
        result_obj,
        dataset_id=ds.id,
        embedder=embedder,  # type: ignore[arg-type]
        embed_batch_size=8,
    )
    assert embedder.batches
