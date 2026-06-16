"""使用真实 fixture 文件 (tests/data/) 验证 reader 全链路。"""

from __future__ import annotations

from pathlib import Path

from rag.ingest.reader import read_file


def test_sample_txt_readable(sample_txt: Path) -> None:
    doc = read_file(sample_txt)
    assert "Plain text fixture" in doc.text
    assert "中文" in doc.text
    assert "🎉" in doc.text
    assert doc.meta.mime == "text/plain"


def test_sample_md_readable(sample_md: Path) -> None:
    doc = read_file(sample_md)
    assert "# Sample Markdown Document" in doc.text
    assert "## Section A" in doc.text
    assert "```python" in doc.text  # code block preserved
    assert "| Column 1 |" in doc.text  # table preserved
    assert doc.meta.mime == "text/plain"


def test_sample_html_strips_script(sample_html: Path) -> None:
    doc = read_file(sample_html)
    assert "Sample HTML Document" in doc.text
    assert "段落" in doc.text
    assert "alert" not in doc.text  # script stripped
    assert "font-family" not in doc.text  # style stripped
    assert doc.meta.mime == "text/html"


def test_sample_htm_same_as_html(sample_htm: Path) -> None:
    """htm 扩展名走 html adapter。"""
    doc = read_file(sample_htm)
    assert "Sample .htm File" in doc.text
    assert doc.meta.mime == "text/html"


def test_sample_csv_with_format_text(sample_csv: Path) -> None:
    doc = read_file(sample_csv)
    assert "Alice" in doc.text
    assert "Beijing" in doc.text
    assert doc.text.startswith("id,name,age,city")


def test_sample_pdf_reads_pages(sample_pdf: Path) -> None:
    doc = read_file(sample_pdf)
    assert doc.meta.mime == "application/pdf"
    assert doc.meta.page_count == 3
    # reportlab STSong 字体让中文可抽
    assert "Sample PDF Document" in doc.text
    assert "中文" in doc.text


def test_sample_docx_reads_paragraphs(sample_docx: Path) -> None:
    doc = read_file(sample_docx)
    assert "Sample DOCX Document" in doc.text
    assert "Section A" in doc.text
    assert "python-docx" in doc.text


def test_all_fixtures_round_trip(all_sample_files: dict[str, Path]) -> None:
    """8 个 fixture 全部能 read_file 成功。"""
    for ext, path in all_sample_files.items():
        doc = read_file(path)
        assert doc.text, f"{ext} produced empty text"
        assert doc.meta.filename == path.name


def test_filename_propagated_from_path(all_sample_files: dict[str, Path]) -> None:
    """filename 来自 path.name, 不是 path 全路径。"""
    for ext, path in all_sample_files.items():
        doc = read_file(path)
        assert doc.meta.filename == f"sample.{ext}"
