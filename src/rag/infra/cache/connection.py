import json
import logging

import redis.asyncio as aioredis
from pydantic import BaseModel
from redis.exceptions import RedisError

from rag.config import settings

logger = logging.getLogger(__name__)

_LAYER_TTL_ATTR: dict[str, str] = {
    "L1": "l1_ttl",
    "L2": "l2_ttl",
    "L3": "l3_ttl",
    "L4": "l4_ttl",
}


async def _create_client(url: str) -> aioredis.Redis:
    return aioredis.from_url(
        url,
        decode_responses=True,
        socket_timeout=1.0,
        socket_connect_timeout=1.0,
        max_connections=20,
        health_check_interval=30,
    )


class Cache:
    def __init__(self, url: str | None = None) -> None:
        """初始化缓存实例。

        Args:
            url: Redis 连接 URL；为 None 时从 `settings.redis_url` 读取。
        """
        self.url = url or str(settings.redis_url)
        # client 在 `init_cache()` 阶段创建, 不在 import 时同步连接, 避免
        # 进程启动期阻塞 / 端口未就绪时抛错。
        self._client: aioredis.Redis | None = None
        self.metrics: dict[str, dict[str, int]] = {
            "L1": {"hit": 0, "miss": 0, "unavailable": 0},
            "L2": {"hit": 0, "miss": 0, "unavailable": 0},
            "L3": {"hit": 0, "miss": 0, "unavailable": 0},
            "L4": {"hit": 0, "miss": 0, "unavailable": 0},
        }

    async def connect(self) -> None:
        """`close()` 之后重建 client；正常 import 流程无需显式调用。"""
        if self._client is None:
            self._client = await _create_client(self.url)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError(
                "Cache closed. Call cache.connect() or init_cache() first."
            )
        return self._client

    def _ttl_for_layer(self, layer: str) -> int:
        attr = _LAYER_TTL_ATTR.get(layer)
        if attr is None:
            msg = f"Unknown cache layer: {layer}"
            raise ValueError(msg)
        return int(getattr(settings.cache, attr))

    def _emit_cache_event(self, layer: str, hit: bool, key: str | None = None) -> None:
        logger.info(
            f"cache {'hit' if hit else 'miss'} layer={layer}",
            extra={"cache_hit": hit, "cache_layer": layer, "cache_key": key},
        )

    def _record_unavailable(
        self,
        layer: str,
        key: str | None,
        warnings: list[str] | None,
        operation: str,
    ) -> None:
        logger.warning(
            f"Redis {operation} 失败, 降级: layer={layer}",
            extra={
                "cache_unavailable": True,
                "cache_layer": layer,
                "cache_key": key,
            },
        )
        if warnings is not None:
            warnings.append(f"redis_unavailable: layer={layer}")
        self.metrics[layer]["unavailable"] += 1

    async def get(
        self,
        key: str,
        layer: str = "L1",
        warnings: list[str] | None = None,
    ) -> str | None:
        """按 `key` 读取字符串值并更新命中指标。

        Args:
            key: Redis key。
            layer: 缓存层 (L1-L4), 仅用于指标统计与 TTL 校验。
            warnings: 若提供, Redis 不可用时追加降级原因。

        Returns:
            取到的字符串；不存在或 Redis 不可用时返回 None。
        """
        # layer 须对应 settings.cache 中的 TTL 配置；过期由 Redis 在 set(ex=...) 时写入
        self._ttl_for_layer(layer)
        try:
            result = await self.client.get(key)
            if result is not None:
                self.metrics[layer]["hit"] += 1
                self._emit_cache_event(layer, hit=True, key=key)
            else:
                self.metrics[layer]["miss"] += 1
                self._emit_cache_event(layer, hit=False, key=key)
            if isinstance(result, bytes):
                return result.decode()
            return result
        except RedisError:
            self._record_unavailable(layer, key, warnings, "get")
            self._emit_cache_event(layer, hit=False, key=key)
            return None

    async def set(
        self,
        key: str,
        value: object,
        ex: int | None = None,
        layer: str = "L1",
        warnings: list[str] | None = None,
    ) -> bool:
        """序列化 `value` 并写入 Redis。

        Args:
            key: Redis key。
            value: 待写入值, 支持 `BaseModel` / `str` / 其他 JSON 可序列化对象。
            ex: 显式 TTL 秒数；为 None 时使用 layer 默认 TTL。
            layer: 缓存层 (L1-L4)。
            warnings: 若提供, Redis 不可用时追加降级原因。

        Returns:
            写入成功返回 True, Redis 不可用返回 False。
        """
        self._ttl_for_layer(layer)
        try:
            if isinstance(value, BaseModel):
                serialized = value.model_dump_json()
            elif isinstance(value, str):
                serialized = value
            else:
                serialized = json.dumps(value, default=str)
            ttl = ex if ex is not None else self._ttl_for_layer(layer)
            await self.client.set(key, serialized, ex=ttl)
            return True
        except RedisError:
            self._record_unavailable(layer, key, warnings, "set")
            return False

    async def delete_pattern(
        self,
        pattern: str,
        warnings: list[str] | None = None,
    ) -> int:
        """使用 `scan_iter` 分批删除匹配 `pattern` 的 keys。

        Args:
            pattern: Redis `SCAN` MATCH 模式, 支持通配符。
            warnings: 若提供, Redis 不可用时追加降级原因。

        Returns:
            实际删除的 key 数量；Redis 不可用时返回 0。
        """
        try:
            count = 0
            async for key in self.client.scan_iter(match=pattern, count=100):
                await self.client.unlink(key)
                count += 1
            return count
        except RedisError:
            logger.warning(
                "Redis delete_pattern 失败",
                extra={"cache_unavailable": True, "cache_layer": "delete_pattern"},
            )
            if warnings is not None:
                warnings.append("redis_unavailable: layer=delete_pattern")
            self.metrics["L3"]["unavailable"] += 1
            return 0


cache = Cache()


async def init_cache() -> None:
    """创建 Redis client 并 `ping` 验证连通性。"""
    await cache.connect()
    await cache.client.ping()


async def close_cache() -> None:
    """关闭 client, 释放连接池。"""
    await cache.close()
