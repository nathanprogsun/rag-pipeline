"""tests/unit conftest — 共享 fixtures (sample fixture 文件 + pipeline helper)。"""

from __future__ import annotations

from pathlib import Path

import pytest

# tests/data 目录 (含 10 个内置 sample.* fixture)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="session")
def sample_data_dir() -> Path:
    """tests/data 目录: 10 个内置 sample.* fixture。"""
    return DATA_DIR


@pytest.fixture(scope="session")
def sample_txt() -> Path:
    """tests/data/sample.txt fixture 路径。"""
    return DATA_DIR / "sample.txt"


@pytest.fixture(scope="session")
def sample_md() -> Path:
    """tests/data/sample.md fixture 路径。"""
    return DATA_DIR / "sample.md"


@pytest.fixture(scope="session")
def sample_html() -> Path:
    """tests/data/sample.html fixture 路径。"""
    return DATA_DIR / "sample.html"


@pytest.fixture(scope="session")
def sample_htm() -> Path:
    """tests/data/sample.htm fixture 路径。"""
    return DATA_DIR / "sample.htm"


@pytest.fixture(scope="session")
def sample_csv() -> Path:
    """tests/data/sample.csv fixture 路径。"""
    return DATA_DIR / "sample.csv"


@pytest.fixture(scope="session")
def sample_pdf() -> Path:
    """tests/data/sample.pdf fixture 路径。"""
    return DATA_DIR / "sample.pdf"


@pytest.fixture(scope="session")
def sample_docx() -> Path:
    """tests/data/sample.docx fixture 路径。"""
    return DATA_DIR / "sample.docx"


@pytest.fixture(scope="session")
def sample_xlsx() -> Path:
    """tests/data/sample.xlsx fixture 路径。"""
    return DATA_DIR / "sample.xlsx"


@pytest.fixture(scope="session")
def sample_pptx() -> Path:
    """tests/data/sample.pptx fixture 路径。"""
    return DATA_DIR / "sample.pptx"


@pytest.fixture(scope="session")
def all_sample_files() -> dict[str, Path]:
    """全部 fixture {ext: path} 字典 (用于 round-trip 测试)。"""
    return {
        "txt": DATA_DIR / "sample.txt",
        "md": DATA_DIR / "sample.md",
        "html": DATA_DIR / "sample.html",
        "htm": DATA_DIR / "sample.htm",
        "csv": DATA_DIR / "sample.csv",
        "pdf": DATA_DIR / "sample.pdf",
        "docx": DATA_DIR / "sample.docx",
        "xlsx": DATA_DIR / "sample.xlsx",
        "pptx": DATA_DIR / "sample.pptx",
    }
