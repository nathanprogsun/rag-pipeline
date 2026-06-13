import logging
from unittest.mock import AsyncMock

import pytest

from rag.infra.cache.connection import Cache


@pytest.mark.asyncio
class TestCacheMetrics:
    async def test_hit_miss_counters(self) -> None:
        cache = Cache(url="redis://127.0.0.1:6379")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)
        cache._client = mock_client
        await cache.get("k", layer="L1")
        assert cache.metrics["L1"]["miss"] == 1
        await cache.close()

    async def test_unavailable_counter(self) -> None:
        cache = Cache(url="redis://127.0.0.1:1")
        await cache.connect()
        await cache.get("k", layer="L1")
        assert cache.metrics["L1"]["unavailable"] == 1
        assert cache.metrics["L1"]["miss"] == 0
        await cache.close()

    async def test_log_extra_fields(self) -> None:
        captured: list[logging.LogRecord] = []

        class CaptureHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        cache = Cache(url="redis://127.0.0.1:1")
        await cache.connect()
        handler = CaptureHandler()
        logger = logging.getLogger("rag.infra.cache.connection")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        await cache.get("k", layer="L1")

        cache_hit_records = [
            record for record in captured if hasattr(record, "cache_hit")
        ]
        assert len(cache_hit_records) >= 1
        assert cache_hit_records[0].cache_layer == "L1"  # type: ignore[attr-defined]
        await cache.close()
        logger.removeHandler(handler)
