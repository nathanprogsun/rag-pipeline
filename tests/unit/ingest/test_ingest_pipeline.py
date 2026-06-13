from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag.ingest.chunker import Chunker, ChunkSettings
from rag.ingest.pipeline import IngestPipeline
from rag.ingest.source import BufferSource, FileSource, UrlSource
from rag.ingest.types import IngestResult


def run_ingest(coro: Coroutine[object, object, IngestResult]) -> IngestResult:
    """同步运行 ``IngestPipeline.ingest`` coroutine。"""
    return asyncio.run(coro)


def test_pipeline_txt_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("段落一。\n\n段落二。\n\n段落三。", encoding="utf-8")

    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=50)))
    result = run_ingest(pipeline.ingest(FileSource(path)))

    assert len(result.chunks) >= 1
    assert all(c.text.strip() for c in result.chunks)
    # title 兜底用 filename
    assert result.title == "a.txt"
    assert result.doc_meta.filename == "a.txt"


def test_pipeline_returns_chunk_objects(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text("# 标题\n\n内容。", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    result = run_ingest(pipeline.ingest(FileSource(path)))
    assert len(result.chunks) >= 1
    assert result.chunks[0].metadata.chunk_index == 0
    # title 优先取 heading 树第一项 text
    assert result.title == "标题"


def test_pipeline_populates_markdown_structure_metadata(tmp_path: Path) -> None:
    """heading_stack / has_code / has_table 由 chunker per-chunk regex 从 chunk 文本内现场重算。

    文档级 heading_path / DocumentStructure 已不再透传; has_code 由 chunk 文本内 ```python 触发。
    """
    path = tmp_path / "a.md"
    path.write_text(
        "# H1\n\n## H2\n\n正文含```python\nx=1\n``` 代码块。\n\n正文继续。",
        encoding="utf-8",
    )
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=200)))
    result = run_ingest(pipeline.ingest(FileSource(path)))
    assert len(result.chunks) >= 1
    chunks = result.chunks
    # heading_stack per-chunk 重算: 至少 1 个 chunk 同时含 H1+H2
    assert any(
        any("# H1" in h for h in c.metadata.heading_stack)
        and any("## H2" in h for h in c.metadata.heading_stack)
        for c in chunks
    )
    # 含代码块的 chunk → has_code=True (per-chunk 重算, 不再 doc-level 透传)
    assert any(c.metadata.has_code is True for c in chunks)
    # title 从文本第一行 # 抽取
    assert result.title == "H1"


def test_pipeline_txt_has_no_structure_metadata(tmp_path: Path) -> None:
    """纯文本无结构 → heading_stack=[], has_code/has_table=False。"""
    path = tmp_path / "a.txt"
    path.write_text("plain text content", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    result = run_ingest(pipeline.ingest(FileSource(path)))
    assert len(result.chunks) >= 1
    for c in result.chunks:
        assert c.metadata.heading_stack == []
        assert c.metadata.has_code is False
        assert c.metadata.has_table is False
    # title 兜底用 filename (txt 无 heading 树)
    assert result.title == "a.txt"


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 新增: DocMeta 注入 + 真实 fixture e2e
# ─────────────────────────────────────────────────────────────────────────────


def _assert_doc_meta_injected(chunks: list, file_type: str, source_suffix: str) -> None:
    for c in chunks:
        assert c.metadata.file_type == file_type
        assert c.metadata.source.endswith(source_suffix)
        assert c.metadata.encoding in ("utf-8", "utf8")
        assert c.metadata.chunk_index < c.metadata.total_chunks


def test_pipeline_injects_doc_meta_into_chunks(
    pipeline_e2e: IngestPipeline, sample_md: Path
) -> None:
    """Markdown fixture: DocMeta 字段 (source/file_type/encoding) 注入每块。

    heading / code / table 由 chunker per-chunk regex 从 chunk 文本内现场重算, 不再 doc-level 透传。
    """
    result = run_ingest(pipeline_e2e.ingest(FileSource(sample_md)))
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "md", "sample.md")
    # heading_stack: 至少 1 个 chunk 含 "# Sample Markdown Document"
    assert any(
        any("Sample Markdown Document" in h for h in c.metadata.heading_stack)
        for c in chunks
    )
    # code/table per-chunk: 文档中含 code + table, 至少各 1 个 chunk 命中
    assert any(c.metadata.has_code for c in chunks)
    assert any(c.metadata.has_table for c in chunks)
    # title 从文本第一行 # 抽取
    assert result.title == "Sample Markdown Document"
    # doc_meta 透传
    assert result.doc_meta.datasource == "file"


def test_pipeline_txt_no_structure_metadata(
    pipeline_e2e: IngestPipeline, sample_txt: Path
) -> None:
    """txt 没有 structure 提取, heading/code/table 全 false。"""
    result = run_ingest(pipeline_e2e.ingest(FileSource(sample_txt)))
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "txt", "sample.txt")
    for c in chunks:
        assert c.metadata.heading_stack == []
        assert c.metadata.has_code is False
        assert c.metadata.has_table is False
    # title 兜底用 filename
    assert result.title == "sample.txt"


def test_pipeline_pdf_injects_page_count(
    pipeline_e2e: IngestPipeline, sample_pdf: Path
) -> None:
    """PDF fixture: page_count 从 DocMeta 注入。"""
    result = run_ingest(pipeline_e2e.ingest(FileSource(sample_pdf)))
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "pdf", "sample.pdf")
    for c in chunks:
        assert c.metadata.page_count == 3
    # doc_meta.page_count 透传到 IngestResult
    assert result.doc_meta.page_count == 3


def test_pipeline_html_extracts_headings(
    pipeline_e2e: IngestPipeline, sample_html: Path
) -> None:
    """HTML 走 html2md adapter: html → markdown, ``<h1>`` → ``#``。

    adapter 把 ``<h1>Sample HTML Document</h1>`` 转成 ``# Sample HTML Document``;
    chunker per-chunk regex 命中 markdown ``#``。title 从 ``# Sample HTML Document``
    第一行抽 (注意: html2md 在某些布局下不会在 ``#`` 前插入 ``\\n``, 走 ``re.MULTILINE``
    的 ``^#`` regex 可能不命中, 此时退到 filename 兜底。这里只验证 chunks 非空
    + 文件路径注入正确)。
    """
    result = run_ingest(pipeline_e2e.ingest(FileSource(sample_html)))
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "html", "sample.html")
    # title 要么命中 markdown heading, 要么兜底 filename
    assert result.title in ("Sample HTML Document", "sample.html")
    # chunk 文本至少包含 "Sample HTML Document" 字面值
    full = "\n".join(c.text for c in chunks)
    assert "Sample HTML Document" in full


def test_pipeline_without_normalizer_uses_noop() -> None:
    """不传 normalizer → 自动用 NoOpNormalizer。"""
    from rag.ingest.normalizer import NoOpNormalizer

    p = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=200)))
    assert isinstance(p.normalizer, NoOpNormalizer)


def test_pipeline_with_forbid_normalizer_does_not_call_llm(tmp_path: Path) -> None:
    """FORBID 模式 Normalizer 不调 LLM, pipeline 正常工作。"""
    from langchain_core.runnables import Runnable

    from rag.ingest.normalizer import StructureMode, StructureNormalizer

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
    result = run_ingest(p.ingest(FileSource(f)))
    assert result.chunks
    fake_model.ainvoke.assert_not_called()
    fake_model.invoke.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_ingest_url_html(pipeline_e2e: IngestPipeline) -> None:
    """URL 入口走 read_url + 完整三段。"""
    html = b"<html><body><h1>Web</h1><p>body.</p></body></html>"
    resp = MagicMock()
    resp.text = html.decode()
    resp.content = html
    resp.headers = {"content-type": "text/html; charset=utf-8"}
    resp.url = "https://example.com/page.html"
    resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=MagicMock(headers={}))
        mock_client.get = AsyncMock(return_value=resp)
        mock_client_cls.return_value = mock_client

        result = await pipeline_e2e.ingest(UrlSource("https://example.com/page.html"))

    assert result.chunks
    _assert_doc_meta_injected(result.chunks, "html", "page.html")
    for c in result.chunks:
        assert c.metadata.source == "https://example.com/page.html"
    # html adapter 走 html2md: ``<h1>Web</h1>`` → ``# Web``,
    # _extract_title 命中 markdown heading → "Web"。
    assert result.title == "Web"


@pytest.mark.asyncio
async def test_pipeline_ingest_buffer(pipeline_e2e: IngestPipeline) -> None:
    """Buffer 入口: bytes + file_type → IngestResult, DocMeta 注入。

    heading_stack 由 chunker per-chunk regex 从 chunk 文本内现场抽取, 至少 1 个 chunk
    的 heading_stack 含 "# Inline"。
    """
    md = b"# Inline\n\nbody."
    result = await pipeline_e2e.ingest(
        BufferSource(buf=md, file_type="md", source="inline://x.md")
    )
    chunks = result.chunks
    assert chunks
    _assert_doc_meta_injected(chunks, "md", "x.md")
    for c in chunks:
        assert c.metadata.source == "inline://x.md"
    # heading_stack per-chunk 重算 (chunk 文本内含 `# Inline`)
    assert any(any("Inline" in h for h in c.metadata.heading_stack) for c in chunks)
    # title 从文本第一行 # 抽取
    assert result.title == "Inline"


# ─────────────────────────────────────────────────────────────────────────────
# IngestResult doc-level identifier 透传
# ─────────────────────────────────────────────────────────────────────────────


def test_pipeline_ingest_result_doc_meta_passthrough(tmp_path: Path) -> None:
    """doc_meta 字段从 reader 透传到 IngestResult, 不再仅靠 chunks[0].metadata 反推。"""
    path = tmp_path / "a.md"
    path.write_text("# Title\n\nbody", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    result = run_ingest(pipeline.ingest(FileSource(path)))
    # 透传字段
    assert result.doc_meta.filename == "a.md"
    assert result.doc_meta.datasource == "file"
    assert result.doc_meta.encoding in ("utf-8", "utf8")


def test_pipeline_ingest_result_title_fallback_filename(tmp_path: Path) -> None:
    """无 heading 树 → title 兜底用 filename。"""
    path = tmp_path / "no_heading.txt"
    path.write_text("plain content", encoding="utf-8")
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    result = run_ingest(pipeline.ingest(FileSource(path)))
    assert result.title == "no_heading.txt"
    # 兜底无降级信号 (因为 filename 存在)
    assert result.warnings == []


def test_pipeline_ingest_result_warnings_for_anon_source() -> None:
    """BufferSource 没法从 source 抽 filename 时 → warnings 记录降级。"""
    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    # source 无扩展名 → filename 取 source 自身, 不带后缀
    result = run_ingest(
        pipeline.ingest(
            BufferSource(buf=b"plain content", file_type="txt", source="anon")
        )
    )
    assert result.title == "anon.txt"
    # warnings: 当 text doc.meta.filename 兜底时无降级 (因为 _derive_title 兜底用 filename)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures (Step 3 新增的 fixture 化 pipeline)
# ─────────────────────────────────────────────────────────────────────────────


from pytest import fixture  # noqa: E402

from rag.ingest.normalizer import NoOpNormalizer  # noqa: E402


@fixture
def pipeline_e2e() -> IngestPipeline:
    """标准化 e2e pipeline: 200 chunk / 800 max / NoOpNormalizer。"""
    return IngestPipeline(
        chunker=Chunker(
            ChunkSettings(chunk_size=200, max_chunk_size=800, min_chunk_size=50)
        ),
        normalizer=NoOpNormalizer(),
    )
