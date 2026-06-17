from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class ChunkMetadata(BaseModel):
    """Chunk 元数据: 定位与溯源信息。

    ``datasource`` 固定为 ``"file"``: 当前只有 file ingest 路径。
    PG schema 不持久化此字段, mapper 读路径取默认。
    """

    datasource: Literal["file"] = "file"
    filename: str | None = None
    parent_title: str = ""
    chunk_index: int = 0


class Chunk(BaseModel):
    """入库前的原始 Chunk: reader + chunker 出来的内容块。

    Args:
        id: chunk 唯一标识。
        dataset_id: 所属 dataset 的 UUID。
        document_id: 所属 document 的 UUID (T2 起为必填, 取代 filename 维度的归属)。
        text: 块文本内容。
        modality: 模态, `text` 或图片描述 `image_caption`。
        image_path: 当 `modality=image_caption` 时有值, 指向图片。
        metadata: 定位与溯源元数据。
        embedding: 预计算的向量; 缺失时由 embedding 阶段补齐。
    """

    id: uuid.UUID
    dataset_id: uuid.UUID
    document_id: uuid.UUID
    text: str
    modality: Literal["text", "image_caption"] = "text"
    image_path: str | None = None
    metadata: ChunkMetadata
    embedding: list[float] | None = None


class DocumentDto(BaseModel):
    """单 document persist 工单: document 已落库后的可跨 session 传递上下文。

    不含 ORM / SQLAlchemy 对象; ``pending`` 的 chunk 正文由 ``IngestResult.chunks`` 提供,
    本 DTO 只携带 identity + resume 元数据。
    """

    document_id: uuid.UUID
    dataset_id: uuid.UUID
    dataset_name: str
    filename: str | None
    existing_chunk_indexes: set[int] = Field(default_factory=set)
    is_resume: bool = False


class ScoredDocument(BaseModel):
    """召回结果。RRF 公式需要 `score` + `rank` 同时存在。

    扩展字段:
    - `rerank_score`: rerank 模型返回的独立相关性分数 (0~1),
      在 `filter_by_score` 切换 rerank 模式时取代 `score` 进行阈值过滤。
    - `score_breakdown`: 各源原始分数, fusion 阶段保留。
      空 dict 表示单源路径未经过 fusion。
      键取值: `vector` / `fulltext` / `caption` / `rerank`。
      多组重复出现时, fusion 按源取 `max`, 因此各源原始相似度
      在 RRF 累积后仍可追溯。
    - (q, a) 溯源字段已迁出, 见 `rag.infra.observability.trace.RetrievalTrace`,
      与 `ScoredDocument` 解耦, 只在去重 / 链路阶段按平行数组传入。
    """

    model_config = {"frozen": False}

    chunk_id: uuid.UUID
    dataset_id: uuid.UUID
    text: str
    score: float
    rank: int
    source: Literal["vector", "fulltext", "caption", "rerank"]
    modality: Literal["text", "image_caption"] = "text"
    image_path: str | None = None  # `modality=image_caption` 时有值, cite 组装引用用
    metadata: ChunkMetadata
    embedding: list[float] | None = None
    rerank_score: float | None = None  # `filter_by_score` 切换 rerank 时用的相关性分数
    score_breakdown: dict[str, float] = Field(default_factory=dict)
