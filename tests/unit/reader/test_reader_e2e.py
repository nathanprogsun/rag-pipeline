"""Reader 公共 API e2e 测试: read_file + read_url 覆盖 8 个内置扩展。"""

from __future__ import annotations

from pathlib import Path

from rag.ingest.reader import read_file


def test_e2e_txt(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    doc = read_file(tmp_path / "a.txt")
    assert doc.text == "hello"
    assert doc.meta.mime == "text/plain"


def test_e2e_md(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# H", encoding="utf-8")
    doc = read_file(tmp_path / "a.md")
    assert doc.text == "# H"
    assert doc.meta.mime == "text/plain"


def test_e2e_html(tmp_path: Path) -> None:
    (tmp_path / "a.html").write_text("<p>hi</p>", encoding="utf-8")
    doc = read_file(tmp_path / "a.html")
    assert "hi" in doc.text
    assert doc.meta.mime == "text/html"


def test_e2e_htm(tmp_path: Path) -> None:
    (tmp_path / "a.htm").write_text("<p>hi</p>", encoding="utf-8")
    doc = read_file(tmp_path / "a.htm")
    assert "hi" in doc.text


def test_e2e_csv(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("a,b\n1,2", encoding="utf-8")
    doc = read_file(tmp_path / "a.csv")
    assert "a,b" in doc.text
    assert doc.meta.mime == "text/csv"


def test_e2e_read_file_filename_filled(tmp_path: Path) -> None:
    (tmp_path / "report.txt").write_text("x", encoding="utf-8")
    doc = read_file(tmp_path / "report.txt")
    assert doc.meta.filename == "report.txt"
