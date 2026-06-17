"""Rerank 模型工厂与协议定义。"""

from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError

from rag.config import settings

_RERANK_PATH_DASHSCOPE = "/reranks"


def _rerank_endpoint(base_url: str) -> str:
    """DashScope: ``{base}/reranks``; OpenRouter: base 已含 ``/rerank`` 则不再拼接。"""
    base = base_url.rstrip("/")
    if base.endswith("/rerank"):
        return base
    return f"{base}{_RERANK_PATH_DASHSCOPE}"


class Reranker(Protocol):
    """Rerank 协议。"""

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int,
    ) -> list[tuple[int, float]]:
        """对 `documents` 重新排序, 返回按 score 降序的 `(doc_idx, score)` 列表。"""
        ...


class _RerankResultItem(BaseModel):
    index: int
    relevance_score: float


class _RerankResponse(BaseModel):
    results: list[_RerankResultItem]


class _RerankErrorResponse(BaseModel):
    code: str
    message: str


class QwenRerank:
    """qwen3-rerank (DashScope compatible-api /reranks) 客户端。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-api/v1",
        model: str = "qwen3-rerank",
        instruct: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """初始化 QwenRerank 客户端。

        Args:
            api_key: DashScope API Key。
            base_url: 兼容 API 根地址。
            model: rerank 模型名。
            instruct: 可选 instruct 指令。
            client: 注入的 `httpx.AsyncClient`; 为 None 时每次调用自建。
        """
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._instruct = instruct
        self._client = client

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int,
    ) -> list[tuple[int, float]]:
        """调用 rerank 接口并解析结果。

        Args:
            query: 查询文本。
            documents: 候选文档原文列表。
            top_k: 返回的最大条目数。

        Returns:
            按 score 降序的 `(原始下标, 相关性分数)` 列表。
        """
        if not documents:
            return []

        payload: dict[str, object] = {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": top_k,
        }
        if self._instruct is not None:
            payload["instruct"] = self._instruct

        if self._client is not None:
            return await self._post_rerank(self._client, payload)

        async with httpx.AsyncClient(timeout=30.0) as client:
            return await self._post_rerank(client, payload)

    async def _post_rerank(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
    ) -> list[tuple[int, float]]:
        resp = await client.post(
            _rerank_endpoint(self._base_url),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        body: object = resp.json()
        if not isinstance(body, dict):
            msg = "rerank response must be a JSON object"
            raise TypeError(msg)
        if body.get("code"):
            err = _RerankErrorResponse.model_validate(body)
            msg = f"{err.code}: {err.message}"
            raise RuntimeError(msg)
        try:
            parsed = _RerankResponse.model_validate(body)
        except ValidationError as exc:
            msg = "invalid rerank response shape"
            raise RuntimeError(msg) from exc
        return [(item.index, item.relevance_score) for item in parsed.results]


class NoOpRerank:
    """Rerank 不可用时的兜底实现, 按输入顺序返回。"""

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int,
    ) -> list[tuple[int, float]]:
        """按输入顺序生成伪分数 (1.0 - i*0.01), 不调用任何上游。"""
        _ = query
        return [(i, 1.0 - i * 0.01) for i in range(min(top_k, len(documents)))]


def get_rerank_model(model: str | None = None) -> QwenRerank:
    """从 settings 构造 `QwenRerank` 客户端。

    Args:
        model: 模型名, 为 None 时使用 `settings.openai_rerank_model`。

    Returns:
        配置好的 `QwenRerank` 实例。
    """
    return QwenRerank(
        api_key=settings.openai_rerank_api_key.get_secret_value(),
        base_url=settings.openai_rerank_base_url,
        model=model or settings.openai_rerank_model,
    )


def get_reranker(model: str | None = None) -> Reranker:
    """根据 rerank API Key 是否配置, 返回真实客户端或 `NoOpRerank` 兜底。

    Args:
        model: 模型名, 仅在有 Key 时生效。

    Returns:
        满足 `Reranker` 协议的实现。
    """
    if not settings.openai_rerank_api_key.get_secret_value():
        return NoOpRerank()
    return get_rerank_model(model=model)
