"""Chunker 入口 E2E 测试: 验证 split(text) -> list[Chunk] 公开 API。

覆盖:
  - 基础段落切分 / Markdown 标题 / 代码块保留 / max_chunk_size / 空输入
  - 自定义分隔符 / min_chunk_size 合并 / 中文标点切分
  - 旧 API split_str() 返回 list[str] (向后兼容)
"""

from __future__ import annotations

from rag.ingest.chunker import Chunker, ChunkSettings


def test_basic_paragraph_split() -> None:
    s = ChunkSettings(chunk_size=50, max_chunk_size=200, min_chunk_size=10)
    # 每段 valid_len 须超过 chunk_size, 才会触发段落级切分 (短文档会 merge 成单块)。
    text = "段落一。" * 12 + "\n\n" + "段落二。" * 12 + "\n\n" + "段落三。" * 12
    chunks = Chunker(s).split_str(text)
    assert len(chunks) >= 2
    assert all(c.strip() for c in chunks)


def test_markdown_heading_creates_chunks() -> None:
    s = ChunkSettings(chunk_size=100, max_chunk_size=500)
    text = "# 标题A\n\n内容A。\n\n# 标题B\n\n内容B。"
    chunks = Chunker(s).split_str(text)
    assert len(chunks) >= 1


def test_code_block_preserved_intact() -> None:
    s = ChunkSettings(chunk_size=1000, max_chunk_size=8000)
    text = "前文\n```python\ndef f():\n    return 1\n```\n后文"
    chunks = Chunker(s).split_str(text)
    code_chunk = next(c for c in chunks if "def f():" in c)
    assert "```python" in code_chunk
    assert "return 1" in code_chunk


def test_max_chunk_size_enforced_on_huge_text() -> None:
    s = ChunkSettings(chunk_size=1000, max_chunk_size=200)
    text = "字" * 1000
    chunks = Chunker(s).split_str(text)
    assert all(len(c) <= s.max_chunk_size for c in chunks)


def test_empty_input_returns_empty() -> None:
    chunks = Chunker(ChunkSettings()).split_str("")
    assert chunks == []


def test_whitespace_only_returns_empty() -> None:
    chunks = Chunker(ChunkSettings()).split_str("   \n\n  ")
    assert chunks == []


def test_custom_separator_splits() -> None:
    s = ChunkSettings(chunk_size=100, max_chunk_size=500, custom_separator=r"---")
    text = "part1\n---\npart2\n---\npart3"
    chunks = Chunker(s).split_str(text)
    assert len(chunks) >= 2


def test_min_chunk_size_merge() -> None:
    s = ChunkSettings(chunk_size=200, min_chunk_size=64, max_chunk_size=500)
    text = "短。" * 5 + "\n\n" + "长内容。" * 20
    chunks = Chunker(s).split_str(text)
    for c in chunks:
        if c != chunks[-1]:
            assert len(c) >= s.min_chunk_size or len(c) == 0


def test_chinese_punctuation_split() -> None:
    s = ChunkSettings(chunk_size=20, max_chunk_size=200)
    text = "第一句。第二句!还有问句?还有分号;还有逗号,继续。"
    chunks = Chunker(s).split_str(text)
    assert len(chunks) >= 2


# ── split() 新主签名: 返回 list[Chunk] ─────────────────────────
def test_split_returns_chunk_objects() -> None:
    s = ChunkSettings(chunk_size=50, max_chunk_size=200, min_chunk_size=10)
    text = "段落一。\n\n段落二。\n\n段落三。"
    chunks = Chunker(s).split(text)
    assert all(hasattr(c, "text") and hasattr(c, "metadata") for c in chunks)
    assert all(c.metadata.chunk_index == i for i, c in enumerate(chunks))
    assert all(c.metadata.total_chunks == len(chunks) for c in chunks)
    assert all(c.text.strip() for c in chunks)


def test_split_injects_chunk_context() -> None:
    """split() 接受 ChunkContext, 注入 source / file_type / encoding。"""
    from rag.ingest.chunker.types import ChunkContext

    s = ChunkSettings(chunk_size=50, max_chunk_size=200, min_chunk_size=10)
    ctx = ChunkContext(source="file:///x.md", file_type="md", encoding="utf-8")
    chunks = Chunker(s).split("段落一。\n\n段落二。", ctx=ctx)
    for c in chunks:
        assert c.metadata.source == "file:///x.md"
        assert c.metadata.file_type == "md"
        assert c.metadata.encoding == "utf-8"


def test_split_empty_returns_empty_list() -> None:
    chunks = Chunker(ChunkSettings()).split("")
    assert chunks == []
    chunks2 = Chunker(ChunkSettings()).split("   \n\n  ")
    assert chunks2 == []
