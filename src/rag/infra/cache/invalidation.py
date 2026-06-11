import uuid

from rag.infra.cache.connection import cache
from rag.infra.cache.keys import (
    NAMESPACE,
    dataset_version_key,
    search_key_pattern_for_dataset,
)


async def on_chunks_changed(dataset_id: uuid.UUID | str) -> None:
    await cache.client.incr(dataset_version_key(dataset_id))
    await cache.delete_pattern(search_key_pattern_for_dataset(dataset_id))


async def on_dataset_deleted(dataset_id: uuid.UUID | str) -> None:
    await cache.client.delete(dataset_version_key(dataset_id))
    await cache.delete_pattern(search_key_pattern_for_dataset(dataset_id))
    await cache.delete_pattern(f"{NAMESPACE}:rk:*{dataset_id}*")


async def on_model_changed(_dataset_id: uuid.UUID | str) -> None:
    await cache.delete_pattern(f"{NAMESPACE}:emb:*")
    await cache.delete_pattern(f"{NAMESPACE}:qext:*")
    await cache.delete_pattern(f"{NAMESPACE}:search:*")


async def flush_all() -> None:
    await cache.delete_pattern(f"{NAMESPACE}:*")
