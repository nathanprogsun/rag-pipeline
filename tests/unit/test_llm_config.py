import pytest
from pydantic import SecretStr

from rag.config import Settings
from rag.infra.llm.rerank import NoOpRerank, QwenRerank, get_reranker


class TestLLMConcurrencySettings:
    def test_chat_and_embedding_limits_from_env_fields(self) -> None:
        app = Settings(
            openai_max_concurrent=3,
            openai_embedding_max_concurrent=9,
            openai_rerank_api_key=SecretStr(""),
        )
        cfg = app.llm_concurrency
        assert cfg.chat.max_concurrent == 3
        assert cfg.embedding.max_concurrent == 9
        assert cfg.rerank is None

    def test_rerank_lane_present_when_api_key_configured(self) -> None:
        app = Settings(
            openai_rerank_api_key=SecretStr("sk-test"),
            openai_rerank_max_concurrent=6,
        )
        cfg = app.llm_concurrency
        assert cfg.rerank is not None
        assert cfg.rerank.max_concurrent == 6


class TestGetReranker:
    def test_returns_noop_without_api_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "rag.infra.llm.rerank.settings",
            Settings(openai_rerank_api_key=SecretStr("")),
        )
        assert isinstance(get_reranker(), NoOpRerank)

    def test_returns_qwen_when_api_key_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "rag.infra.llm.rerank.settings",
            Settings(openai_rerank_api_key=SecretStr("sk-test")),
        )
        reranker = get_reranker()
        assert isinstance(reranker, QwenRerank)
