from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError

from rag.config import settings

_RERANK_PATH = "/reranks"


class Reranker(Protocol):
    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int,
    ) -> list[tuple[int, float]]:
        """返回 (doc_idx, score) 列表, 按 score 降序。"""
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
    """DashScope compatible-api qwen3-rerank (POST .../compatible-api/v1/reranks)。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-api/v1",
        model: str = "qwen3-rerank",
        instruct: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
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
            f"{self._base_url}{_RERANK_PATH}",
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
    """Rerank 不可用时的兜底, 按输入顺序返回。"""

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int,
    ) -> list[tuple[int, float]]:
        _ = query
        return [(i, 1.0 - i * 0.01) for i in range(min(top_k, len(documents)))]


def get_rerank_model(model: str | None = None) -> QwenRerank:
    """获取 qwen3-rerank 客户端。调用方通过 llm_sem.run(\"rerank\", ...) 进入限流。"""
    return QwenRerank(
        api_key=settings.openai_rerank_api_key.get_secret_value(),
        base_url=settings.openai_rerank_base_url,
        model=model or settings.openai_rerank_model,
    )


def get_reranker(model: str | None = None) -> Reranker:
    """无 rerank API Key 时返回 NoOpRerank，否则返回 QwenRerank。"""
    if not settings.openai_rerank_api_key.get_secret_value():
        return NoOpRerank()
    return get_rerank_model(model=model)
