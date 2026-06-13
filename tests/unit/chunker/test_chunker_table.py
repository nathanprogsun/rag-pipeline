from rag.ingest.chunker.table import markdown_table_split, str_is_md_table


def test_str_is_md_table_valid() -> None:
    text = "| col1 | col2 |\n|------|------|\n| a | b |"
    assert str_is_md_table(text) is True


def test_str_is_md_table_missing_separator() -> None:
    text = "| col1 | col2 |\n| a | b |"
    assert str_is_md_table(text) is False


def test_str_is_md_table_no_pipe() -> None:
    text = "no table here"
    assert str_is_md_table(text) is False


def test_markdown_table_split_repeats_header() -> None:
    """构造 5 行表格, chunk_size=20 强制分块, 验证每块都有表头。"""
    lines = ["| col1 | col2 |", "|------|------|"]
    rows = [f"| r{i:02d} | v{i:02d} |" for i in range(20)]
    text = "\n".join(lines + rows)
    chunks = markdown_table_split(text, chunk_size=80)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert "col1" in chunk
        assert "col2" in chunk


def test_markdown_table_split_non_table_returns_singleton() -> None:
    text = "not a table"
    chunks = markdown_table_split(text, chunk_size=100)
    assert chunks == [text]
