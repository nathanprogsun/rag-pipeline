"""Reader 内部类型: FormatReaderResult + 跨模块回调。

`FormatReaderResult` 是 format adapter 的返回类型 (不含 source/size/datasource,
这些由 dispatch 补)。`DocMeta` / `TextDoc` 复用 `rag.ingest.types`。

`UploadFileHandler` 是 html2md / 后续 reader 共享的异步上传回调签名,接收 (filename, mime, bytes)
返回 `UploadedFileResult` TypedDict, 其中 ``key`` 必填, ``previewUrl`` 可选。
TypedDict 与 ``dict[str, str]`` 在运行时结构兼容,
mypy 静态类型与 Pydantic 校验均容许 (Section 11 验收对齐)。

``FormatReaderResult`` / ``UploadedFileResult`` / ``UploadFileHandler`` 为本模块
single source; ``extensions/base.py`` 通过 re-export 共享, 避免双份定义飘移。
``FormatReaderResult.structure`` 字段已删除: doc-level structure 不再由 reader 抽取。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import NotRequired, TypedDict

from rag.ingest.types import DocMeta


class UploadedFileResult(TypedDict):
    """上传回调返回: ``key`` 必填, ``previewUrl`` 可选 (本模块只读 ``key``)。"""

    key: str
    previewUrl: NotRequired[str]  # noqa: N815 — 与 类型命名保持一致


# 上传回调签名: (filename, mime, bytes) -> UploadedFileResult
# 单一 canonical 定义: ``raw_text`` / ``html2md`` / 全部 extensions 都从此处导入。
UploadFileHandler = Callable[[str, str, bytes], Awaitable[UploadedFileResult]]


@dataclass(frozen=True)
class FormatReaderResult:
    """单个 format adapter 的输出。

    adapter 只负责把 bytes 解成文本; datasource / filename / size_bytes
    由 dispatch 层根据 source (path or url) 补全。

    字段:
    - raw_text: 必填, 主要文本
    - meta: 必填, 必含 mime / encoding / page_count / paragraph_count
    - format_text: 可选, csv/xlsx 的 markdown table 视图
    - images: 可选, adapter 内已上传的图片 URL/key 列表 (DOCX 等)
    - extras: 兜底, 适配器需要塞其他临时字段时使用
    """

    raw_text: str
    meta: DocMeta
    format_text: str | None = None
    images: list[str] = field(default_factory=list)
    extras: dict[str, object] = field(default_factory=dict)
