import json

import httpx
import pytest

from rag.infra.llm.rerank import QwenRerank

_BASE_URL = "https://dashscope.aliyuncs.com/compatible-api/v1"


@pytest.mark.asyncio
async def test_qwen_rerank_parses_compatible_api_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{_BASE_URL}/reranks"
        assert request.headers["Authorization"] == "Bearer sk-test"
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3-rerank"
        assert payload["query"] == "what is rag"
        assert payload["top_n"] == 2
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.5},
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        reranker = QwenRerank(
            api_key="sk-test",
            base_url=_BASE_URL,
            client=client,
        )
        ranked = await reranker.rerank(
            query="what is rag",
            documents=["irrelevant", "relevant"],
            top_k=2,
        )

    assert ranked == [(1, 0.9), (0, 0.5)]
