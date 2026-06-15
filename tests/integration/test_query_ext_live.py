"""Query Extension 集成测试 — 调真实 LLM (MiniMax-M3) + 真实 embedder。

未配置 Key 的用例自动 skip，避免无凭证环境失败。
需要 ``OPENAI_API_KEY`` + ``OPENAI_EMBEDDING_API_KEY``。
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from rag.config import settings
from rag.infra.llm.chat import get_structured_chat_model
from rag.infra.llm.embed import get_embed_model
from rag.search.extension.query_ext import (
    QueryExtensionRunnable,
    QueryExtensionVariants,
)

pytestmark = pytest.mark.live_llm


def _require_secret(key: SecretStr, env_name: str) -> None:
    if not key.get_secret_value().strip():
        pytest.skip(f"{env_name} not configured")


@pytest.mark.asyncio(loop_scope="class")
class TestQueryExtensionLive:
    async def test_rewrite_returns_variants_for_simple_query(self) -> None:
        """Real LLM rewrite: should return 1+ variants for a real query."""
        _require_secret(settings.openai_api_key, "OPENAI_API_KEY")
        ext = QueryExtensionRunnable(model="MiniMax-M3", generate_count=5, k=3)
        variants = ext.rewrite("什么是 RAG")
        assert len(variants) >= 1
        assert all(v.strip() for v in variants)

    async def test_rewrite_incorporates_chat_bg(self) -> None:
        """When chat_bg is provided, LLM should use it in variants (per FastGPT)."""
        _require_secret(settings.openai_api_key, "OPENAI_API_KEY")
        ext = QueryExtensionRunnable(model="MiniMax-M3", generate_count=3, k=3)
        # Query refers to "他" (pronoun) — should be resolved to 张三 via chat_bg
        variants = ext.rewrite(
            "他的核心观点是什么", chat_bg="张三在三月做了关于 AI 的演讲"
        )
        # At least one variant should reference 张三 or 三月 (chat_bg content)
        assert any("张三" in v or "三月" in v for v in variants), (
            f"Expected chat_bg content in variants, got: {variants}"
        )

    async def test_rewrite_caps_at_generate_count(self) -> None:
        """LLM respects generate_count upper bound."""
        _require_secret(settings.openai_api_key, "OPENAI_API_KEY")
        ext = QueryExtensionRunnable(model="MiniMax-M3", generate_count=3, k=3)
        variants = ext.rewrite("Python tutorial")
        assert len(variants) <= 3
        assert len(variants) >= 1  # at least 1

    async def test_rewrite_default_generate_count_is_10(self) -> None:
        """Default generate_count=10 — LLM returns up to 10 variants."""
        _require_secret(settings.openai_api_key, "OPENAI_API_KEY")
        ext = QueryExtensionRunnable()  # defaults
        assert ext.generate_count == 10
        variants = ext.rewrite("machine learning")
        assert len(variants) >= 1
        # No more than 10 (per default)
        assert len(variants) <= 10

    async def test_rewrite_falls_back_on_unusual_query(self) -> None:
        """Even an unusual query (very long, special chars) returns [query] or variants."""
        _require_secret(settings.openai_api_key, "OPENAI_API_KEY")
        ext = QueryExtensionRunnable(model="MiniMax-M3", generate_count=3, k=3)
        # Very long query — should still return either variants or fallback
        unusual = "x" * 500 + " random chars 你好世界 😊"
        variants = ext.rewrite(unusual)
        assert isinstance(variants, list)
        assert len(variants) >= 1  # either variants or [query] fallback

    async def test_structured_output_returns_pydantic(self) -> None:
        """Direct structured LLM call returns QueryExtensionVariants instance."""
        _require_secret(settings.openai_api_key, "OPENAI_API_KEY")
        structured_llm = get_structured_chat_model(
            QueryExtensionVariants, model="MiniMax-M3"
        )
        # Call with the same prompt QueryExtensionRunnable uses
        result = structured_llm.invoke(
            [
                {
                    "role": "user",
                    "content": 'Generate 3 search variants for: "FastGPT RAG"',
                }
            ]
        )
        # Should be a QueryExtensionVariants (per Pydantic schema)
        assert isinstance(result, QueryExtensionVariants)
        assert len(result.variants) >= 1
        assert len(result.variants) <= 50  # max_length bound


@pytest.mark.asyncio(loop_scope="class")
class TestQueryExtensionEndToEndLive:
    """Full pipeline: LLM rewrite + lazy-greedy select + string dedup with real services."""

    async def test_end_to_end_full_flow(self) -> None:
        """Complete 3-stage pipeline with real LLM + real embedder."""
        _require_secret(settings.openai_api_key, "OPENAI_API_KEY")
        _require_secret(settings.openai_embedding_api_key, "OPENAI_EMBEDDING_API_KEY")

        embedder = get_embed_model()
        ext = QueryExtensionRunnable(
            model="MiniMax-M3",
            generate_count=10,
            k=3,
            alpha=0.3,
            embedder=embedder,
        )
        result = ext("什么是 RAG pipeline")

        # Original always at index 0
        assert result.original == "什么是 RAG pipeline"
        assert result.deduped_variants[0] == "什么是 RAG pipeline"

        # LLM returned at least 1 variant
        assert len(result.variants) >= 1
        assert len(result.variants) <= 10  # cap

        # deduped_variants is <= variants (after lazy-greedy + string dedup)
        assert (
            len(result.deduped_variants) <= len(result.variants) + 1
        )  # +1 for original

    async def test_end_to_end_with_chinese_context(self) -> None:
        """Chinese query with chat_bg: end-to-end flow with real services."""
        _require_secret(settings.openai_api_key, "OPENAI_API_KEY")
        _require_secret(settings.openai_embedding_api_key, "OPENAI_EMBEDDING_API_KEY")

        embedder = get_embed_model()
        ext = QueryExtensionRunnable(
            model="MiniMax-M3",
            generate_count=5,
            k=3,
            embedder=embedder,
        )
        result = ext(
            "他的核心观点",
            chat_bg="李四在四月发表了关于向量数据库的演讲",
        )

        # Original preserved
        assert result.deduped_variants[0] == "他的核心观点"
        # At least one variant should reflect chat_bg (per FastGPT contract)
        assert any(
            "李四" in v or "四月" in v or "向量" in v for v in result.variants
        ), f"Expected chat_bg content in variants, got: {result.variants}"

    async def test_end_to_end_dedup_removes_near_duplicates(self) -> None:
        """If LLM returns punctuation-only-different variants, string dedup drops them."""
        _require_secret(settings.openai_api_key, "OPENAI_API_KEY")
        _require_secret(settings.openai_embedding_api_key, "OPENAI_EMBEDDING_API_KEY")

        embedder = get_embed_model()
        ext = QueryExtensionRunnable(
            model="MiniMax-M3", generate_count=5, k=3, embedder=embedder
        )
        result = ext("FastGPT RAG implementation")

        # deduped_variants is deduplicated (string normalize + original at index 0)
        # No two consecutive entries should normalize to the same value
        from rag.search.extension.query_ext import _normalize_for_dedup

        seen: set[str] = set()
        for v in result.deduped_variants:
            norm = _normalize_for_dedup(v)
            assert norm not in seen, (
                f"Duplicate after normalize in deduped_variants: {v!r}"
            )
            seen.add(norm)
