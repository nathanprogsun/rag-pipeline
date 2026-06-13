"""Chunk mapper 单元测试 — PG row 与 domain.document.Chunk 双向映射。

不依赖真实 PG: 直接构造 ChunkModel 内存对象, 验证 mapper 字段一致性。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from rag.domain.document import Chunk as DomainChunk
from rag.domain.document import ChunkMetadata as DomainChunkMetadata
from rag.infra.pg.mappers import (
    chunk_model_list_to_domain,
    chunk_model_to_domain,
    domain_chunk_to_model,
)
from rag.infra.pg.models.chunk import ChunkModel


def _make_model(**overrides: object) -> ChunkModel:
    """构造 ChunkModel 内存对象, 不走 PG。"""
    base: dict[str, object] = {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "dataset_id": uuid.UUID("00000000-0000-0000-0000-000000000002"),
        "text": "hello world",
        "modality": "text",
        "image_path": None,
        "parent_title": "Chapter 1",
        "chunk_index": 3,
        "filename": "doc.md",
        "embedding": [0.1] * 1536,
    }
    base.update(overrides)
    return cast(ChunkModel, ChunkModel(**cast(dict[str, Any], base)))


def test_chunk_model_to_domain_maps_all_fields() -> None:
    """读路径: ChunkModel -> DomainChunk 字段一致。"""
    model = _make_model()

    chunk = chunk_model_to_domain(model)

    assert chunk.id == model.id
    assert chunk.dataset_id == model.dataset_id
    assert chunk.text == "hello world"
    assert chunk.modality == "text"
    assert chunk.image_path is None
    assert chunk.embedding == [0.1] * 1536
    assert chunk.metadata.parent_title == "Chapter 1"
    assert chunk.metadata.chunk_index == 3
    assert chunk.metadata.filename == "doc.md"
    assert chunk.metadata.dataset_id == model.dataset_id
    # PG schema 不持久化: datasource / custom_separator 取默认
    assert chunk.metadata.datasource == "file"
    assert chunk.metadata.custom_separator is None


def test_chunk_model_to_domain_preserves_created_at() -> None:
    """TimestampMixin.created_at 应流入 ChunkMetadata.created_at。"""
    ts = datetime(2026, 6, 13, 12, 0, 0)
    model = _make_model()
    model.created_at = ts

    chunk = chunk_model_to_domain(model)

    assert chunk.metadata.created_at == ts


def test_chunk_model_to_domain_handles_image_modality() -> None:
    """modality='image_caption' 必须保留, image_path 不为 None。"""
    model = _make_model(
        modality="image_caption",
        image_path="/tmp/page-1.png",
    )

    chunk = chunk_model_to_domain(model)

    assert chunk.modality == "image_caption"
    assert chunk.image_path == "/tmp/page-1.png"


def test_chunk_model_list_to_domain_bulk() -> None:
    """批量映射: 顺序与长度一致。"""
    models = [_make_model(text=f"t-{i}") for i in range(5)]

    chunks = chunk_model_list_to_domain(models)

    assert [c.text for c in chunks] == [f"t-{i}" for i in range(5)]


def test_domain_chunk_to_model_maps_all_fields() -> None:
    """写路径: DomainChunk -> ChunkModel 字段一致。"""
    chunk = DomainChunk(
        id=uuid.UUID("00000000-0000-0000-0000-0000000000aa"),
        dataset_id=uuid.UUID("00000000-0000-0000-0000-0000000000bb"),
        text="write me",
        modality="text",
        image_path=None,
        metadata=DomainChunkMetadata(
            dataset_id=uuid.UUID("00000000-0000-0000-0000-0000000000bb"),
            datasource="file",
            filename="out.md",
            parent_title="Intro",
            chunk_index=7,
        ),
        embedding=[0.5] * 1536,
    )

    model = domain_chunk_to_model(chunk)

    assert model.id == chunk.id
    assert model.dataset_id == chunk.dataset_id
    assert model.text == "write me"
    assert model.modality == "text"
    assert model.image_path is None
    assert model.embedding == [0.5] * 1536
    assert model.filename == "out.md"
    assert model.parent_title == "Intro"
    assert model.chunk_index == 7


def test_domain_chunk_to_model_defaults_embedding_when_none() -> None:
    """DomainChunk.embedding=None 时, mapper 用 1536 维零向量兜底 (NOT NULL 列)。"""
    chunk = DomainChunk(
        id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        text="no embedding yet",
        modality="text",
        metadata=DomainChunkMetadata(
            dataset_id=uuid.uuid4(),
            datasource="file",
        ),
        embedding=None,
    )

    model = domain_chunk_to_model(chunk)

    assert model.embedding is not None
    assert len(model.embedding) == 1536
    assert all(v == 0.0 for v in model.embedding)


def test_roundtrip_preserves_persisted_fields() -> None:
    """DomainChunk -> model -> DomainChunk 后, PG 持久化字段一致。"""
    original = DomainChunk(
        id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        text="roundtrip",
        modality="text",
        metadata=DomainChunkMetadata(
            dataset_id=uuid.uuid4(),
            datasource="file",
            filename="x.md",
            parent_title="P",
            chunk_index=2,
        ),
        embedding=[0.2] * 1536,
    )

    model = domain_chunk_to_model(original)
    restored = chunk_model_to_domain(model)

    assert restored.id == original.id
    assert restored.dataset_id == original.dataset_id
    assert restored.text == original.text
    assert restored.modality == original.modality
    assert restored.metadata.filename == original.metadata.filename
    assert restored.metadata.parent_title == original.metadata.parent_title
    assert restored.metadata.chunk_index == original.metadata.chunk_index
    assert restored.embedding == original.embedding
