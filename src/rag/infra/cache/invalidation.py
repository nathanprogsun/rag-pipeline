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
    """单次 round-trip 完成版本号递增 + 模式匹配清理。

    将 `INCR incr_key` 与所有匹配的 `UNLINK` 放入同一 pipeline, server 顺序执行。
    比 ``await incr(); await delete_pattern()`` 少一次 RTT, 且保证 incr 不会先于 unlink 完成。

    Returns:
        实际 unlink 的 key 数量。
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
    """Dataset chunks 变更: 递增版本号 + 清该 dataset 的 search 缓存。

    合并到单 pipeline 避免两次 RTT 之间的窗口期 (其他 reader 可能命中旧缓存)。
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
