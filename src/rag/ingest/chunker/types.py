"""Chunker 公开类型: ``ChunkContext`` 元数据快照, 避免 Chunker 反向依赖 Reader/Normalizer。"""

from __future__ import annotations

from dataclasses import dataclass

from rag.ingest.types import DocMeta


@dataclass(frozen=True)
class ChunkContext:
    """Chunker 入参上下文: 来自 ``DocMeta`` 的元数据快照, 保证单向数据流 (Reader/Normalizer → Chunker)。

    仅透传 ``source / file_type / page_count / encoding``; 标题/代码/表格等结构判定
    下放到 per-chunk 现场, 不在 doc 级预计算。

    Args:
        source: 文档来源标识 (URI 或文件名)。
        file_type: 推断的文件类型 (扩展名或 mime 子类型)。
        page_count: PDF 等分页文档的页数。
        encoding: 文本编码, 默认 ``utf-8``。
    """

    source: str = ""
    file_type: str = ""
    page_count: int | None = None
    encoding: str = "utf-8"

    @classmethod
    def empty(cls) -> ChunkContext:
        """构造空 ``ChunkContext`` (全部字段取默认值)。"""
        return cls()

    @classmethod
    def from_meta(cls, meta: DocMeta) -> ChunkContext:
        """从 ``DocMeta`` 构造 ``ChunkContext``, 仅依赖 ``DocMeta``。

        Args:
            meta: 上游 ``DocMeta`` 实例。

        Returns:
            字段填好的 ``ChunkContext``。
        """
        return cls(
            # 优先用 meta.source (file:///abs/path 或 https://...),
            # 兜底用 filename (适配老调用方 / 测试 fixture)。
            source=meta.source or meta.filename or "",
            file_type=_guess_file_type(meta),
            page_count=meta.page_count,
            encoding=meta.encoding,
        )


def _guess_file_type(meta: DocMeta) -> str:
    """从 ``meta`` 推断 ``file_type``: 优先取文件名后缀, 否则取 ``mime`` 子类型。"""

    if meta.filename and "." in meta.filename:
        return meta.filename.rsplit(".", 1)[-1].lower()
    if meta.mime:
        return meta.mime.split("/")[-1].lower()
    return ""
