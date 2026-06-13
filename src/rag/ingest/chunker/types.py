"""Chunker 公开类型: Chunk (重导出) + ChunkContext (新)。"""

from __future__ import annotations

from dataclasses import dataclass

from rag.ingest.types import DocMeta


@dataclass(frozen=True)
class ChunkContext:
    """Chunker 入参上下文: 来自 DocMeta 的元数据快照。

    设计意图: Chunker 不反向依赖 Reader/Normalizer, 通过 ChunkContext 数据类
    接收元数据, 保证单向数据流 (Reader/Normalizer -> Chunker)。

    ``heading_path`` / ``has_code`` / ``has_table`` 字段已删除:
    doc-level ``heading_path`` 不再有消费者 (改由 per-chunk ``heading_stack``
    现场重算取代), doc-level ``has_code`` / ``has_table`` 也改为 chunker per-chunk
    regex 现场判定。``DocMeta`` 透传 ``source / file_type / page_count / encoding``。
    """

    source: str = ""
    file_type: str = ""
    page_count: int | None = None
    encoding: str = "utf-8"

    @classmethod
    def empty(cls) -> ChunkContext:
        return cls()

    @classmethod
    def from_meta(cls, meta: DocMeta) -> ChunkContext:
        """从 DocMeta 构造 ChunkContext: 透传 source / file_type / page_count / encoding.

        ``from_meta_and_structure`` 已删除 (无 structure 入参),
        改名 ``from_meta`` 反映纯 DocMeta 依赖。
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
    """从 meta 推断 file_type (无后缀, 用 mime)。"""
    if meta.filename and "." in meta.filename:
        return meta.filename.rsplit(".", 1)[-1].lower()
    if meta.mime:
        return meta.mime.split("/")[-1].lower()
    return ""
