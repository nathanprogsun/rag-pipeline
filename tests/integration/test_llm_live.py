"""LLM 集成测试 — 调用真实第三方 API（需 .env 配置对应 Key）。

未配置 Key 的用例自动 skip，避免无凭证环境失败。
"""

import pytest
from pydantic import BaseModel, Field, SecretStr

from rag.config import settings
from rag.infra.llm.chat import get_chat_model, get_structured_chat_model
from rag.infra.llm.embed import get_embed_model
from rag.infra.llm.rerank import NoOpRerank, QwenRerank, get_reranker
from rag.infra.llm.semaphore import llm_sem

pytestmark = pytest.mark.live_llm


def _require_secret(key: SecretStr, env_name: str) -> None:
    if not key.get_secret_value().strip():
        pytest.skip(f"{env_name} not configured")


class _LiveQueryExpansion(BaseModel):
    queries: list[str] = Field(min_length=1, description="expanded search queries")


@pytest.mark.asyncio(loop_scope="class")
class TestChatLive:
    async def test_chat_model_returns_content(self) -> None:
        _require_secret(settings.openai_api_key, "OPENAI_API_KEY")
        model = get_chat_model(temperature=0.0)
        response = await llm_sem.run(
            "chat",
            model.ainvoke("Reply with exactly: pong"),
        )
        text = str(response.content).strip().lower()
        assert "pong" in text

    async def test_structured_chat_model_returns_parsed_schema(self) -> None:
        _require_secret(settings.openai_api_key, "OPENAI_API_KEY")
        model = get_structured_chat_model(_LiveQueryExpansion, temperature=0.0)
        expansion = await llm_sem.run(
            "chat",
            model.ainvoke(
                "Expand the search query 'RAG pipeline' into 2 alternative queries."
            ),
        )
        if expansion is None:
            pytest.skip(
                "chat provider did not return tool_calls; "
                "structured output may be unsupported on this backend"
            )

        assert isinstance(expansion, _LiveQueryExpansion)
        assert len(expansion.queries) >= 1
        assert all(query.strip() for query in expansion.queries)


@pytest.mark.asyncio
class TestEmbeddingLive:
    async def test_embed_query_returns_expected_dimension(self) -> None:
        _require_secret(settings.openai_embedding_api_key, "OPENAI_EMBEDDING_API_KEY")
        model = get_embed_model()
        vector = await llm_sem.run(
            "embedding",
            model.aembed_query("rag pipeline smoke test"),
        )
        assert len(vector) == settings.openai_embedding_dim
        assert any(value != 0.0 for value in vector)

    async def test_embed_documents_batch(self) -> None:
        _require_secret(settings.openai_embedding_api_key, "OPENAI_EMBEDDING_API_KEY")
        model = get_embed_model()
        vectors = await llm_sem.run(
            "embedding",
            model.aembed_documents(["hello", "world"]),
        )
        assert len(vectors) == 2
        assert all(len(row) == settings.openai_embedding_dim for row in vectors)


@pytest.mark.asyncio
class TestRerankLive:
    async def test_reranker_ranks_relevant_document_first(self) -> None:
        _require_secret(settings.openai_rerank_api_key, "OPENAI_RERANK_API_KEY")
        reranker = get_reranker()
        assert isinstance(reranker, QwenRerank)

        ranked = await llm_sem.run(
            "rerank",
            reranker.rerank(
                query="什么是 RAG",
                documents=[
                    "今天北京天气晴朗。",
                    "RAG 是检索增强生成，结合向量检索与大模型回答。",
                ],
                top_k=2,
            ),
        )

        assert len(ranked) == 2
        assert ranked[0][0] == 1
        assert ranked[0][1] > ranked[1][1]

    async def test_get_reranker_uses_noop_without_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        if not settings.openai_rerank_api_key.get_secret_value().strip():
            pytest.skip("OPENAI_RERANK_API_KEY not configured")
        monkeypatch.setattr(
            "rag.infra.llm.rerank.settings.openai_rerank_api_key",
            SecretStr(""),
        )
        assert isinstance(get_reranker(), NoOpRerank)
