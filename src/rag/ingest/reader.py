import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BaseMeta:
    datasource: str = "file"
    filename: str | None = None


def read_txt(p: Path) -> tuple[str, BaseMeta]:
    return p.read_text(encoding="utf-8"), BaseMeta(filename=p.name)


def read_md(p: Path) -> tuple[str, BaseMeta]:
    """MD 直接读, 标题结构在 structure.py 解析。"""
    return p.read_text(encoding="utf-8"), BaseMeta(filename=p.name)


def read_pdf(p: Path) -> tuple[str, BaseMeta]:
    from pypdf import PdfReader

    reader = PdfReader(str(p))
    return (
        "\n\n".join(page.extract_text() or "" for page in reader.pages),
        BaseMeta(filename=p.name),
    )


def read_docx(p: Path) -> tuple[str, BaseMeta]:
    from docx import Document

    document = Document(str(p))
    return (
        "\n\n".join(paragraph.text for paragraph in document.paragraphs),
        BaseMeta(filename=p.name),
    )


def read_html(p: Path) -> tuple[str, BaseMeta]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser")
    body = soup.body or soup
    return body.get_text("\n", strip=True), BaseMeta(filename=p.name)


def read_json(p: Path) -> tuple[str, BaseMeta]:
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        text = "\n\n".join(
            item.get("content", str(item)) if isinstance(item, dict) else str(item)
            for item in data
        )
    elif isinstance(data, dict):
        text = data.get("content", json.dumps(data, ensure_ascii=False))
    else:
        text = str(data)
    return text, BaseMeta(filename=p.name)


_READERS: dict[str, Callable[[Path], tuple[str, BaseMeta]]] = {
    ".txt": read_txt,
    ".md": read_md,
    ".pdf": read_pdf,
    ".docx": read_docx,
    ".html": read_html,
    ".htm": read_html,
    ".json": read_json,
}


def read_file(p: Path) -> tuple[str, BaseMeta]:
    """按后缀分发, 返回 (text, base_metadata)。"""
    path = Path(p)
    reader = _READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(f"Unsupported file type: {path.suffix}")
    return reader(path)
