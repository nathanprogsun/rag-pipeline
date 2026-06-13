"""Ingest 层数据契约: DocMeta / TextDoc / Chunk / ChunkMetadata。

设计原则:
- 不可变 (frozen=True), 跨函数传值无 mutation 风险。
- TextDoc 是 reader 与 normalizer 共同使用的唯一类型 (旧 RawDoc 已合并进来)。
- Chunk.id 用 uuid4 工厂; 未来切换到 uuid5(source_hash) 需先引入 source_hash。
- images 字段: 由支持图片抽取的 adapter (DOCX, 未来 PDF OCR) 填充, 上层做引用溯源。

当前契约 (无 Heading / DocumentStructure / TextDoc.structure / heading_path):
- heading_stack / has_code / has_table / image_refs 由 chunker 内部 per-chunk
  regex 现场重算, 不再依赖 doc-level DFS 透传。
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from rag.domain.enums import (
    IngestDatasource,
    StoredDatasource,
    ingest_to_stored_datasource,
)


class DocMeta(BaseModel):
    """文档元数据, 各 reader 按能力填充对应字段。"""

    model_config = ConfigDict(frozen=True)

    filename: str | None = None
    source: str = ""  # 完整来源 URI: file:///abs/path 或 https://...
    datasource: IngestDatasource = "file"
    mime: str | None = None
    encoding: str = "utf-8"
    size_bytes: int = 0
    page_count: int | None = None
    paragraph_count: int | None = None
    created_at: str | None = None  # ISO-8601, str 而非 datetime 避免 tz 复杂度
    extras: dict[str, object] = Field(default_factory=dict)

    def stored_datasource(self) -> StoredDatasource:
        """把 ingest 阶段 datasource 转持久化语义 ('file' / 'manual' / 'api')。

        唯一允许的转换入口: pipeline 边界 + mapper 层。
        """
        return ingest_to_stored_datasource(self.datasource, self.source)


class TextDoc(BaseModel):
    """Reader 与 Normalizer 共同的产物: 文本 + 元数据 + (可选) 图片引用。

    旧 RawDoc (reader 裸输出) 已合并进来; reader 现在直接产 TextDoc (images=[]),
    normalizer 链路也只传递 TextDoc, 不再区分两阶段类型。

    ``format_text``: csv / xlsx adapter 的 markdown table 视图; 仅这两种扩展
    会填充。chunker 用它实现 ``get_format_text`` 切流。

    ``structure`` 字段已删除; chunker 内部 per-chunk regex 现场重算
    heading_stack / has_code / has_table / image_refs。
    """

    model_config = ConfigDict(frozen=True)

    text: str
    format_text: str | None = None
    meta: DocMeta
    images: list[str] = Field(default_factory=list)


class ChunkMetadata(BaseModel):
    """Chunk 附加元数据, 供下游审计 + 缓存 + 检索使用。

    字段来源:
      - 来自 Chunker (chunk_index / total_chunks / valid_len)
      - 来自 DocMeta (source / file_type / page_count / encoding)
      - 来自 chunker per-chunk regex:
        * heading_stack: 当前 chunk 所在的 heading 栈 (检索上下文)
        * has_code / has_table / image_refs: 当前 chunk 内含的代码块/表格/图片引用
    """

    model_config = ConfigDict(frozen=True)

    # ── Chunk 位置 ──
    chunk_index: int = 0
    total_chunks: int = 0
    # valid_len 取代了旧的 char_count: 去空白后字符数, 与 embedding 实际输入更接近。
    valid_len: int = 0

    # ── 来自 DocMeta (Step 3 新增) ──
    source: str = ""
    file_type: str = ""
    page_count: int | None = None
    encoding: str = "utf-8"

    # ── per-chunk 现场重算 ──
    heading_stack: list[str] = Field(default_factory=list)
    has_code: bool = False
    has_table: bool = False
    image_refs: list[str] = Field(default_factory=list)


class Chunk(BaseModel):
    """Chunker 最终输出单元。

    新增字段:
      - ``raw_text``: 始终来自 reader 的 raw_text 对应切片。
      - ``format_text``: 来自 reader 的 format_text 对应切片 (csv/xlsx 等) 或 None。
      - ``text``: 对外暴露字段, 按 ``get_format_text`` 选 ``format_text or raw_text``。
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    text: str
    raw_text: str = ""
    format_text: str | None = None
    metadata: ChunkMetadata = Field(default_factory=ChunkMetadata)


class IngestResult(BaseModel):
    """Pipeline.ingest 的统一返回: chunks + 文档级 identifier + 降级信号。

    设计意图:
    - 取代旧的 ``list[Chunk]`` 直接返回, 把 doc-level 元信息 (title / page_count /
      paragraph_count) 提到外层, 避免下游通过 ``chunks[0].metadata`` 反推。
    - ``warnings`` 收集 ingest 全程的非致命降级信号, 供上层审计 / UI 展示。
    - ``frozen=True`` 保证跨函数透传无 mutation 风险。
    """

    model_config = ConfigDict(frozen=True)

    chunks: list[Chunk]
    title: str | None = None
    doc_meta: DocMeta
    warnings: list[str] = Field(default_factory=list)
