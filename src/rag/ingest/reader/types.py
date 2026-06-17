"""Reader 内部类型: FormatReaderResult + MIME → 文件后缀映射。

`FormatReaderResult` 是 format adapter 的返回类型 (不含 filename/size_bytes,
这些由 dispatch 补)。`DocMeta` / `TextDoc` 复用 `rag.ingest.types`。
"""

from __future__ import annotations

from dataclasses import dataclass

from rag.ingest.types import DocMeta


@dataclass(frozen=True)
class FormatReaderResult:
    """单个 format adapter 的输出。

    adapter 只负责把 bytes 解成文本; ``filename`` / ``size_bytes``
    由 dispatch 层补全。

    字段:
    - raw_text: 必填, 主要文本
    - meta: 必填, 必含 mime / encoding / page_count / paragraph_count
    - format_text: 可选, csv/xlsx 的 markdown table 视图
    """

    raw_text: str
    meta: DocMeta
    format_text: str | None = None


# ---------- MIME → 文件后缀映射 ----------
# 原来 html2md.py 与 extensions/docx.py 各自维护一份, 合并到此处。

MIME_EXTENSION: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/bmp": "bmp",
}


def mime_to_extension(mime: str, *, default: str = "bin") -> str:
    """MIME → 文件后缀, 未识别的返回 ``default`` (默认 ``"bin"``)。"""
    return MIME_EXTENSION.get(mime.lower(), default)
