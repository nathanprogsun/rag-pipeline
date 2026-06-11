import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ChunkMetadata(BaseModel):
    """Chunk 的元数据载荷: 定位与溯源信息。"""

    dataset_id: uuid.UUID
    datasource: Literal["file", "manual", "api"]
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
    - q / a: 触发该 chunk 的 query 变体与该变体下的 top-1 答案片段,
             用于 ``remove_duplicates`` 按 (q, a) 元组做去重。
    - rerank_score: rerank 模型返回的独立相关性分数 (0~1),
             在 ``filter_by_score`` 切换 rerank 模式时取代 score 进行阈值过滤。
    """

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
    q: str | None = None  # remove_duplicates 用: 触发该 chunk 的 query 变体
    a: str | None = None  # remove_duplicates 用: 该变体下 chunk 的 top-1 答案片段
    rerank_score: float | None = None  # filter_by_score 切换 rerank 时用的相关性分数
