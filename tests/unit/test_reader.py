from pathlib import Path

import pytest

from rag.ingest.reader import read_file


def test_read_txt(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("hello world")
    text, base_meta = read_file(path)
    assert text == "hello world"
    assert base_meta.datasource == "file"


def test_read_markdown(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text("# Title\n\ncontent")
    text, base_meta = read_file(path)
    assert "Title" in text
    assert path.name == base_meta.filename


def test_unsupported_extension_raises(tmp_path: Path) -> None:
    path = tmp_path / "a.xyz"
    path.write_text("x")
    with pytest.raises(ValueError):
        read_file(path)
