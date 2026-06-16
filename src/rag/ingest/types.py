"""Ingest 层数据契约: 不可变 Pydantic 模型, 跨函数传值无 mutation 风险。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field


class DocMeta(BaseModel):
    """文档元数据, 各 reader 按能力填充。"""

    model_config = ConfigDict(frozen=True)

    filename: str | None = None
    mime: str | None = None
    page_count: int | None = None


class TextDoc(BaseModel):
    """Reader 与 Normalizer 共同的产物: 文本 + 元数据 + (可选) 图片引用。"""

    model_config = ConfigDict(frozen=True)

    text: str
    format_text: str | None = None
    meta: DocMeta
    images: list[str] = Field(default_factory=list)


class ChunkMetadata(BaseModel):
    """Chunk 附加元数据: 定位与 per-chunk 特征。"""

    model_config = ConfigDict(frozen=True)

    chunk_index: int = 0
    heading_stack: list[str] = Field(default_factory=list)


class Chunk(BaseModel):
    """Chunker 最终输出单元。"""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    text: str
    metadata: ChunkMetadata = Field(default_factory=ChunkMetadata)


class PersistOutcome(BaseModel):
    """``IngestPipeline`` 落库阶段结果 (可选)。"""

    model_config = ConfigDict(frozen=True)

    dataset_id: uuid.UUID
    dataset_name: str
    old_chunk_count: int
    new_chunk_count: int


class IngestResult(BaseModel):
    """Pipeline.ingest 的统一返回: chunks + 文档级元信息 + 可选落库结果。"""

    model_config = ConfigDict(frozen=True)

    chunks: list[Chunk]
    title: str | None = None
    doc_meta: DocMeta
    persist: PersistOutcome | None = None


@dataclass
class IngestOutcome:
    """``ingest_many`` 批量结果。

    Attributes:
        items: 成功处理的 ``IngestResult`` 列表 (按输入顺序)。
        warnings: 非致命的路径展开 / 跳过文件警告。
        errors: 每个失败输入的 ``(label, exc)`` 列表, ``label`` 为文件路径。
            空列表表示全部成功。
    """

    items: list[IngestResult]
    warnings: list[str]
    errors: list[tuple[str, BaseException]] = field(default_factory=list)


class PersistConfig(BaseModel):
    """持久化配置。

    ``dataset_id`` 与 ``create_dataset`` 二选一:
    - ``dataset_id``: 复用已存在 dataset。
    - ``create_dataset=True``: 与 ``dataset_name`` 一起新建 dataset。
        ``ingest_many`` 入口解析 dataset 后, 此标志不再改变。
    """

    dataset_id: uuid.UUID | None = Field(default=None)
    create_dataset: bool = Field(default=False)
    dataset_name: str | None = Field(default=None)
    enabled: bool = Field(default=False)
