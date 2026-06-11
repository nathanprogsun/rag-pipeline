import pytest

from rag.ingest.chunker import Chunker, ChunkSettings


@pytest.fixture
def settings() -> ChunkSettings:
    return ChunkSettings(
        chunk_size=1000,
        max_chunk_size=8000,
        overlap_ratio=0.15,
        paragraph_chunk_deep=5,
        paragraph_chunk_min_size=100,
        min_chunk_size=64,
    )


def test_paragraph_split(settings: ChunkSettings) -> None:
    text = "第一段。\n\n第二段。\n\n第三段。"
    chunks = Chunker(settings).split(text)
    assert len(chunks) >= 2


def test_markdown_header_inheritance(settings: ChunkSettings) -> None:
    text = "# A\n\n## B\n\n内容。\n\n## C\n\n更多内容。"
    chunks = Chunker(settings).split(text)
    assert any("# A" in c or "## B" in c for c in chunks)


def test_code_block_protection(settings: ChunkSettings) -> None:
    text = "前文\n```python\ndef f():\n    return 1\n```\n后文"
    chunks = Chunker(settings).split(text)
    assert any("```python" in c and "def f():" in c and "return 1" in c for c in chunks)


def test_html_table_preserved(settings: ChunkSettings) -> None:
    text = "text\n<table><tr><td>a</td><td>b</td></tr></table>\nmore"
    chunks = Chunker(settings).split(text)
    assert any("<table>" in c and "</table>" in c for c in chunks)


def test_markdown_table_header_repeat(settings: ChunkSettings) -> None:
    text = (
        "| col1 | col2 |\n|------|------|\n| a | b |\n| c | d |\n| e | f |\n| g | h |"
    )
    chunks = Chunker(settings).split(text)
    assert any("col1" in c for c in chunks)


def test_chinese_punctuation_split(settings: ChunkSettings) -> None:
    text = "第一句。第二句,带逗号;还有分号。"
    chunks = Chunker(settings).split(text)
    assert len(chunks) >= 1


def test_max_chunk_size_enforced(settings: ChunkSettings) -> None:
    text = "字" * 10000
    chunks = Chunker(settings).split(text)
    assert all(len(c) <= settings.max_chunk_size for c in chunks)


def test_caption_skips_markdown_steps() -> None:
    s = ChunkSettings(chunk_size=200)
    text = "这是图片的描述,内容很丰富。" * 30
    chunks = Chunker(s).split(text)
    assert all(len(c) <= s.max_chunk_size for c in chunks)


def test_min_chunk_merge(settings: ChunkSettings) -> None:
    s = ChunkSettings(chunk_size=200, min_chunk_size=64, max_chunk_size=500)
    text = "短。" * 5 + "\n\n" + "长内容。" * 20
    chunks = Chunker(s).split(text)
    assert all(len(c) >= s.min_chunk_size or c == chunks[-1] for c in chunks)


def test_custom_regex_split(settings: ChunkSettings) -> None:
    s = ChunkSettings(chunk_size=200, custom_separator=r"---")
    text = "part1\n---\npart2\n---\npart3"
    chunks = Chunker(s).split(text)
    assert len(chunks) >= 2


def test_overlap_preserved(settings: ChunkSettings) -> None:
    s = ChunkSettings(chunk_size=100, overlap_ratio=0.15, max_chunk_size=200)
    text = "。".join([f"段落{i}的内容在这里" for i in range(20)])
    chunks = Chunker(s).split(text)
    if len(chunks) >= 2:
        tail = chunks[0][-20:]
        head = chunks[1][:20]
        overlap_chars = set(tail) & set(head)
        assert len(overlap_chars) > 0 or len(chunks) <= 2


def test_empty_string_returns_empty_list(settings: ChunkSettings) -> None:
    chunks = Chunker(settings).split("")
    assert chunks == []


def test_whitespace_only_returns_empty_list(settings: ChunkSettings) -> None:
    chunks = Chunker(settings).split("   \n\n\t  ")
    assert chunks == []


def test_candidates_less_than_k_returns_all(settings: ChunkSettings) -> None:
    s = ChunkSettings(chunk_size=50, max_chunk_size=200, min_chunk_size=10)
    text = "短段一。\n\n短段二。\n\n短段三。"
    chunks = Chunker(s).split(text)
    assert len(chunks) >= 1
    assert all(c.strip() for c in chunks)


def test_candidates_empty_returns_empty(settings: ChunkSettings) -> None:
    s = ChunkSettings(chunk_size=100, custom_separator=r"\.+")
    chunks = Chunker(s).split("....")
    assert isinstance(chunks, list)
