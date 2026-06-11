from rag.ingest.structure import extract_markdown_structure


def test_extract_h1_h2() -> None:
    text = "# A\n\n## B\n\nbody\n\n## C\n\nbody2"
    structure = extract_markdown_structure(text)
    assert len(structure.heading_tree) == 1
    assert structure.heading_tree[0].level == 1
    assert structure.heading_tree[0].text == "A"
    assert len(structure.heading_tree[0].children) == 2
    assert structure.heading_tree[0].children[0].level == 2
    assert structure.heading_tree[0].children[0].text == "B"
    assert structure.heading_tree[0].children[1].text == "C"


def test_detect_code_block() -> None:
    structure = extract_markdown_structure("text\n```python\nx=1\n```\nmore")
    assert structure.has_code_blocks


def test_detect_table() -> None:
    structure = extract_markdown_structure(
        "| col1 | col2 |\n|------|------|\n| a | b |"
    )
    assert structure.has_tables
