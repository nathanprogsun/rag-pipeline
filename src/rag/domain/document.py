import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from rag.domain.enums import StoredDatasource


class ChunkMetadata(BaseModel):
    """Chunk 的元数据载荷: 定位与溯源信息。

    ``datasource`` 用 ``StoredDatasource`` 而非 ``Datasource``: 这是落库语义
    ('file' / 'manual' / 'api'), ingest 阶段的 'url' 已通过
    ``ingest_to_stored_datasource`` 在 pipeline 边界映射。
    """

    dataset_id: uuid.UUID
    datasource: StoredDatasource
    filename: str | None = None
    parent_title: str = ""
    chunk_index: int = 0
    custom_separator: str | None = None
    created_at: datetime | None = None


class Chunk(BaseModel):
    """入库前的原始 Chunk: reader + chunker 出来的内容块。"""

    id: uuid.UUID
    dataset_id: uuid.UUID
    text: str
    modality: Literal["text", "image_caption"] = "text"
    image_path: str | None = None
    metadata: ChunkMetadata
    embedding: list[float] | None = None


class ScoredDocument(BaseModel):
    """召回结果: RRF 公式需要 score + rank 同时存。

    扩展字段:
    - rerank_score: rerank 模型返回的独立相关性分数 (0~1),
             在 ``filter_by_score`` 切换 rerank 模式时取代 score 进行阈值过滤。
    - score_breakdown: per-source raw scores preserved by fusion.
             Empty dict means single-source path that didn't go through fusion.
             Keys: 'vector' / 'fulltext' / 'caption' / 'rerank'.
             On duplicate sightings across groups, fusion takes ``max`` per source
             (对齐 FastGPT ``concatScore.find(type).value = max(...)`` 语义),
             so the original raw similarity per source survives RRF accumulation.
    - (q, a) 溯源字段已迁出: 见 ``rag.infra.observability.trace.RetrievalTrace``,
             与 ``ScoredDocument`` 解耦, 只在去重 / 链路阶段按平行数组传入。
    """

    model_config = {"frozen": False}

    chunk_id: uuid.UUID
    dataset_id: uuid.UUID
    text: str
    score: float
    rank: int
    source: Literal["vector", "fulltext", "caption", "rerank"]
    modality: Literal["text", "image_caption"] = "text"
    image_path: str | None = None  # modality=image_caption 时有值, cite 组装引用用
    metadata: ChunkMetadata
    embedding: list[float] | None = None
    rerank_score: float | None = None  # filter_by_score 切换 rerank 时用的相关性分数
    score_breakdown: dict[str, float] = Field(default_factory=dict)
