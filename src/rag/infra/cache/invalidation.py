import uuid

from rag.infra.cache.connection import cache
from rag.infra.cache.keys import (
    NAMESPACE,
    dataset_version_key,
    search_key_pattern_for_dataset,
)


async def _incr_and_unlink_pattern(
    incr_key: str,
    pattern: str,
) -> int:
    """单次 round-trip: scan 收集 pattern → pipeline(INCR + UNLINK*) 原子下发。

    原子语义: incr 和所有 unlink 在同一 pipeline 中发送，server 顺序处理。
    比 ``await incr(); await delete_pattern()`` 少一次 RTT，且保证 incr 不会先于 unlink 完成。
    """
    keys: list[str] = [
        key async for key in cache.client.scan_iter(match=pattern, count=100)
    ]
    pipe = cache.client.pipeline(transaction=False)
    pipe.incr(incr_key)
    if keys:
        pipe.unlink(*keys)
    await pipe.execute()
    return len(keys)


async def on_chunks_changed(dataset_id: uuid.UUID | str) -> None:
    """Dataset chunks 变更: bump version + 清该 dataset 的 search 缓存。

    单 pipeline 发送 ``INCR version`` + ``UNLINK search:*``，避免
    ``incr() / delete_pattern()`` 两次 RTT 之间的窗口期（其它 reader 可能命中旧缓存）。
    """
    await _incr_and_unlink_pattern(
        dataset_version_key(dataset_id),
        search_key_pattern_for_dataset(dataset_id),
    )


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
