"""ChunkModel <-> domain.document.Chunk 双向 mapper。

PG row 与业务层 domain 对象之间的字段映射全部集中在这里。新增字段只需改一处,
retriever / repository 直接调 mapper, 不再散落隐式映射。

字段对应:
  ChunkModel                       DomainChunk / DomainChunkMetadata
  ───────────                      ─────────────────────────────────
  id                               Chunk.id
  dataset_id                       Chunk.dataset_id
  text                             Chunk.text
  embedding                        Chunk.embedding
  modality                         Chunk.modality
  image_path                       Chunk.image_path
  parent_title                     Chunk.metadata.parent_title
  chunk_index                      Chunk.metadata.chunk_index
  filename                         Chunk.metadata.filename
  created_at (TimestampMixin)      Chunk.metadata.created_at
  —                                Chunk.metadata.datasource (默认 "file", PG 不存)
  —                                Chunk.metadata.custom_separator (默认 None, PG 不存)
"""

from __future__ import annotations

from typing import Literal, cast

from rag.domain.document import Chunk as DomainChunk
from rag.domain.document import ChunkMetadata as DomainChunkMetadata
from rag.infra.pg.models.chunk import ChunkModel


def chunk_model_to_domain(model: ChunkModel) -> DomainChunk:
    """ChunkModel (PG row) -> domain.document.Chunk (业务层)。"""
    metadata = DomainChunkMetadata(
        dataset_id=model.dataset_id,
        datasource="file",  # PG schema 当前不持久化 datasource, 读路径取默认
        filename=model.filename,
        parent_title=model.parent_title,
        chunk_index=model.chunk_index,
        custom_separator=None,  # PG schema 当前不持久化 custom_separator
        created_at=model.created_at,
    )
    return DomainChunk(
        id=model.id,
        dataset_id=model.dataset_id,
        text=model.text,
        modality=cast(Literal["text", "image_caption"], model.modality),
        image_path=model.image_path,
        metadata=metadata,
        embedding=model.embedding,
    )


def chunk_model_list_to_domain(models: list[ChunkModel]) -> list[DomainChunk]:
    """批量映射。"""
    return [chunk_model_to_domain(m) for m in models]


def domain_chunk_to_model(chunk: DomainChunk) -> ChunkModel:
    """domain.document.Chunk (业务层) -> ChunkModel (PG row)。

    写库前调用 (例如 ingest 写库入口)。
    PG schema 不存的字段 (datasource, custom_separator) 仅留业务侧, 不下推。
    """
    return ChunkModel(
        id=chunk.id,
        dataset_id=chunk.dataset_id,
        text=chunk.text,
        modality=chunk.modality,
        image_path=chunk.image_path,
        embedding=chunk.embedding if chunk.embedding is not None else _zero_embedding(),
        parent_title=chunk.metadata.parent_title,
        chunk_index=chunk.metadata.chunk_index,
        filename=chunk.metadata.filename,
    )


def _zero_embedding() -> list[float]:
    """PG embedding 列 NOT NULL; 业务层允许 None, 写库前用零向量兜底。

    默认维度 1536 与 schema.sql / ChunkModel Vector(1536) 对齐。
    """
    return [0.0] * 1536
