"""Cache 集成测试 — class 形式；配合 pytest-xdist -n auto 并发执行。"""

import os
import uuid
from collections.abc import AsyncGenerator

import pytest

import rag.infra.cache.connection as connection_module
import rag.infra.cache.invalidation as invalidation_module
from rag.config import settings
from rag.infra.cache.connection import Cache
from rag.infra.cache.connection import cache as global_cache
from rag.infra.cache.invalidation import on_chunks_changed, on_model_changed
from rag.infra.cache.keys import dataset_version_key, search_key

TEST_REDIS_URL = str(settings.redis_url)
XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")


def _bind_global_cache(test_cache: Cache) -> None:
    connection_module.cache = test_cache
    invalidation_module.cache = test_cache


@pytest.fixture
async def redis_cache() -> AsyncGenerator[Cache]:
    test_cache = Cache(url=TEST_REDIS_URL)
    yield test_cache
    await test_cache.close()


@pytest.fixture
async def bound_redis_cache(redis_cache: Cache) -> AsyncGenerator[Cache]:
    _bind_global_cache(redis_cache)
    yield redis_cache
    _bind_global_cache(global_cache)


@pytest.mark.asyncio
class TestCacheConnection:
    async def test_get_set_roundtrip(self, redis_cache: Cache) -> None:
        key = f"rag-test:{XDIST_WORKER}:get-set"
        assert await redis_cache.set(key, "v", ex=60)
        assert await redis_cache.get(key) == "v"
        await redis_cache.client.delete(key)

    async def test_unavailable_returns_none(self) -> None:
        cache = Cache(url="redis://127.0.0.1:1")
        result = await cache.get("any")
        assert result is None
        await cache.close()

    async def test_unavailable_appends_warning(self) -> None:
        cache = Cache(url="redis://127.0.0.1:1")
        warnings: list[str] = []
        result = await cache.get("any", layer="L1", warnings=warnings)
        assert result is None
        assert any(
            "redis_unavailable" in warning and "L1" in warning for warning in warnings
        )
        await cache.close()


@pytest.mark.asyncio
class TestCacheInvalidation:
    async def test_on_chunks_changed_per_dataset_isolation(
        self, bound_redis_cache: Cache
    ) -> None:
        d1 = str(uuid.uuid4())
        d2 = str(uuid.uuid4())
        key_d1 = search_key(
            {"query": "q", "dataset_ids": [d1], "dataset_versions": [1]}
        )
        key_d2 = search_key(
            {"query": "q", "dataset_ids": [d2], "dataset_versions": [1]}
        )
        await bound_redis_cache.set(key_d1, "result-d1", ex=60)
        await bound_redis_cache.set(key_d2, "result-d2", ex=60)

        await on_chunks_changed(d1)

        new_version = await bound_redis_cache.client.get(dataset_version_key(d1))
        assert new_version is not None
        assert int(new_version) >= 1

        await bound_redis_cache.client.delete(key_d1, key_d2, dataset_version_key(d1))

    async def test_on_model_changed_clears_l3(self, bound_redis_cache: Cache) -> None:
        dataset_id = str(uuid.uuid4())
        key = search_key(
            {"query": "q", "dataset_ids": [dataset_id], "dataset_versions": [1]}
        )
        await bound_redis_cache.set(key, "old-model-result", ex=60)
        assert await bound_redis_cache.get(key) == "old-model-result"

        await on_model_changed(dataset_id)

        assert await bound_redis_cache.get(key) is None
