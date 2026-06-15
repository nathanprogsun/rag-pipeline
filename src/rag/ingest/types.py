"""Ingest 层数据契约: 不可变 Pydantic 模型, 跨函数传值无 mutation 风险。"""

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
    source: str = ""
    datasource: IngestDatasource = "file"
    mime: str | None = None
    encoding: str = "utf-8"
    size_bytes: int = 0
    page_count: int | None = None
    paragraph_count: int | None = None
    created_at: str | None = None
    extras: dict[str, object] = Field(default_factory=dict)

    def stored_datasource(self) -> StoredDatasource:
        """把 ingest 阶段 datasource 转持久化语义。

        Returns:
            ``StoredDatasource`` 枚举: ``file`` / ``manual`` / ``api``。

        Note:
            唯一允许的转换入口, pipeline 边界 + mapper 层统一调用。
        """
        return ingest_to_stored_datasource(self.datasource, self.source)


class TextDoc(BaseModel):
    """Reader 与 Normalizer 共同的产物: 文本 + 元数据 + (可选) 图片引用。"""

    model_config = ConfigDict(frozen=True)

    text: str
    format_text: str | None = None
    meta: DocMeta
    images: list[str] = Field(default_factory=list)


class ChunkMetadata(BaseModel):
    """Chunk 附加元数据, 供下游审计 + 缓存 + 检索使用。"""

    model_config = ConfigDict(frozen=True)

    chunk_index: int = 0
    total_chunks: int = 0
    valid_len: int = 0

    source: str = ""
    file_type: str = ""
    page_count: int | None = None
    encoding: str = "utf-8"

    heading_stack: list[str] = Field(default_factory=list)
    has_code: bool = False
    has_table: bool = False
    image_refs: list[str] = Field(default_factory=list)


class Chunk(BaseModel):
    """Chunker 最终输出单元。"""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    text: str
    raw_text: str = ""
    format_text: str | None = None
    metadata: ChunkMetadata = Field(default_factory=ChunkMetadata)


class IngestResult(BaseModel):
    """Pipeline.ingest 的统一返回: chunks + 文档级元信息 + 降级信号。"""

    model_config = ConfigDict(frozen=True)

    chunks: list[Chunk]
    title: str | None = None
    doc_meta: DocMeta
    warnings: list[str] = Field(default_factory=list)
