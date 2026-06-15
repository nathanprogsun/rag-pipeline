"""Unit tests for ``rag.search.extension.query_ext`` per Contract 9 of
``.agents/design/2026-06-14-cross-task-contracts.md`` + FastGPT alignment
(2026-06-14 audit).

Tests use injected structured-output mock LLM (no network, deterministic).
"""

from __future__ import annotations

import pytest

from rag.search.extension.query_ext import (
    QueryExtensionResult,
    QueryExtensionRunnable,
    QueryExtensionVariants,
    _cosine_similarity,
    _marginal_gain,
    _normalize_for_dedup,
)

# ---------- FakeStructuredLLM ----------


class FakeStructuredLLM:
    """Mock LangChain Runnable with structured output. Returns QueryExtensionVariants."""

    def __init__(
        self, variants_by_prompt_substr: dict[str, list[str]] | None = None
    ) -> None:
        self.variants_by_prompt_substr = variants_by_prompt_substr or {}
        self.calls: list[list[dict]] = []

    def invoke(self, prompt: object) -> object:
        if isinstance(prompt, list):
            self.calls.append(prompt)
            user_msg = next(
                (m["content"] for m in prompt if m.get("role") == "user"), ""
            )
        else:
            self.calls.append([{"role": "user", "content": str(prompt)}])
            user_msg = str(prompt)
        for key, val in self.variants_by_prompt_substr.items():
            if key in user_msg:
                return QueryExtensionVariants(variants=val)
        return QueryExtensionVariants(variants=[])


# ---------- FakeEmbedder ----------


class FakeEmbedder:
    """Deterministic 2-dim vector from first 2 chars' ordinals (mod 100)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [float(ord(t[i]) % 100) if i < len(t) else 0.0 for i in (0, 1)]
            norm = (v[0] ** 2 + v[1] ** 2) ** 0.5 or 1.0
            out.append([v[0] / norm, v[1] / norm])
        return out


class CustomEmbedder:
    """Embedder with caller-supplied vectors per text (for controlled tests)."""

    def __init__(self, vecs: dict[str, list[float]]) -> None:
        self.vecs = vecs

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.vecs[t] for t in texts]


# ---------- Structured-output schema ----------


def test_query_extension_variants_schema_default() -> None:
    """Default QueryExtensionVariants has empty variants list."""
    s = QueryExtensionVariants()
    assert s.variants == []


def test_query_extension_variants_schema_with_values() -> None:
    s = QueryExtensionVariants(variants=["a", "b", "c"])
    assert s.variants == ["a", "b", "c"]


# ---------- Normalize helper ----------


def test_normalize_strips_punctuation() -> None:
    assert _normalize_for_dedup("hello, world!") == "helloworld"


def test_normalize_lowercase() -> None:
    assert _normalize_for_dedup("Hello World") == "helloworld"


def test_normalize_unicode_letters_preserved() -> None:
    assert _normalize_for_dedup("你好，世界") == "你好世界"


def test_normalize_whitespace_stripped() -> None:
    assert _normalize_for_dedup("  hello  world  ") == "helloworld"


# ---------- Cosine + marginal gain helpers ----------


def test_cosine_similarity_identical() -> None:
    v = [1.0, 0.0]
    assert _cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal() -> None:
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_marginal_gain_no_selection_full_diversity() -> None:
    cand = [0.95, 0.31]
    orig = [1.0, 0.0]
    gain = _marginal_gain(cand, orig, selected_vecs=[], alpha=0.3)
    # relevance = 0.95, diversity = 1.0
    # gain = 0.3 * 0.95 + 0.7 * 1.0 = 0.985
    assert gain == pytest.approx(0.985, abs=0.01)


def test_marginal_gain_reduces_diversity_after_selection() -> None:
    cand = [0.95, 0.31]
    orig = [1.0, 0.0]
    selected = [cand]
    gain = _marginal_gain(cand, orig, selected_vecs=selected, alpha=0.3)
    # diversity = 0 (max sim = 1.0)
    # gain = 0.3 * 0.95 + 0.7 * 0 = 0.285
    assert gain == pytest.approx(0.285, abs=0.01)


# ---------- Stage 1: LLM rewrite (structured output) ----------


def test_rewrite_returns_structured_variants() -> None:
    """LLM returns QueryExtensionVariants -> variants extracted."""
    llm = FakeStructuredLLM(
        variants_by_prompt_substr={
            "原问题": ["variant one", "variant two", "variant three"]
        }
    )
    ext = QueryExtensionRunnable(model="test", generate_count=5, k=3, llm=llm)
    variants = ext.rewrite("original query")
    assert variants == ["variant one", "variant two", "variant three"]


def test_rewrite_caps_at_generate_count() -> None:
    llm = FakeStructuredLLM(
        variants_by_prompt_substr={
            "原问题": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        }
    )
    ext = QueryExtensionRunnable(model="test", generate_count=3, k=3, llm=llm)
    variants = ext.rewrite("query")
    assert len(variants) == 3
    assert variants == ["a", "b", "c"]


def test_rewrite_default_generate_count_is_10() -> None:
    """FastGPT default is 10 variants."""
    assert QueryExtensionRunnable.DEFAULT_GENERATE_COUNT == 10


def test_rewrite_falls_back_to_original_on_llm_error() -> None:
    class FailingLLM:
        def invoke(self, prompt: object) -> object:
            raise RuntimeError("LLM down")

    ext = QueryExtensionRunnable(model="test", llm=FailingLLM())
    assert ext.rewrite("hello") == ["hello"]


def test_rewrite_falls_back_on_empty_variants() -> None:
    """LLM returns QueryExtensionVariants(variants=[]) -> [query]."""
    llm = FakeStructuredLLM()  # no configured variants -> returns empty
    ext = QueryExtensionRunnable(model="test", llm=llm)
    assert ext.rewrite("hello") == ["hello"]


def test_rewrite_falls_back_on_unexpected_response_type() -> None:
    """LLM returns non-Pydantic object -> [query]."""

    class WeirdLLM:
        def invoke(self, prompt: object) -> object:
            return "not a pydantic model"  # string, not QueryExtensionVariants

    ext = QueryExtensionRunnable(model="test", llm=WeirdLLM())
    assert ext.rewrite("hello") == ["hello"]


def test_rewrite_includes_chat_bg_in_prompt() -> None:
    llm = FakeStructuredLLM(variants_by_prompt_substr={"原问题": ["a", "b"]})
    ext = QueryExtensionRunnable(model="test", llm=llm)
    ext.rewrite("query", chat_bg="user is in Shenyang")
    user_prompt = llm.calls[0][1]["content"]
    assert "Shenyang" in user_prompt


def test_rewrite_includes_histories_in_prompt() -> None:
    llm = FakeStructuredLLM(variants_by_prompt_substr={"原问题": ["a"]})
    ext = QueryExtensionRunnable(model="test", llm=llm)
    ext.rewrite("query", histories=["prev msg 1", "prev msg 2"])
    user_prompt = llm.calls[0][1]["content"]
    assert "prev msg 1" in user_prompt
    assert "prev msg 2" in user_prompt


def test_rewrite_uses_system_and_user_roles() -> None:
    """LLM called with 2 messages: system (rules) + user (template)."""
    llm = FakeStructuredLLM(variants_by_prompt_substr={"原问题": ["a"]})
    ext = QueryExtensionRunnable(model="test", llm=llm)
    ext.rewrite("query")
    msgs = llm.calls[0]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert "改写器" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    assert "query" in msgs[1]["content"]


# ---------- Stage 2: lazy-greedy select ----------


def test_lazy_greedy_default_k_is_3() -> None:
    assert QueryExtensionRunnable.DEFAULT_K == 3


def test_lazy_greedy_default_alpha_is_0_3() -> None:
    assert QueryExtensionRunnable.DEFAULT_ALPHA == 0.3


def test_lazy_greedy_skips_when_candidates_le_k() -> None:
    emb = FakeEmbedder()
    ext = QueryExtensionRunnable(
        model="test", k=3, llm=FakeStructuredLLM(), embedder=emb
    )
    assert ext.lazy_greedy_select("orig", ["a"]) == ["a"]
    assert ext.lazy_greedy_select("orig", ["a", "b"]) == ["a", "b"]


def test_lazy_greedy_picks_top_k_by_gain_with_controlled_vectors() -> None:
    """With controlled vectors, verify gain-based top-k selection.

    Setup:
      - original: [1, 0]
      - cand_a: [0.95, 0.31]  sim(orig)=0.95
      - cand_b: [0.5, 0.866]  sim(orig)=0.5
      - cand_c: [0, 1]        sim(orig)=0

    With k=2, alpha=0.3:
      First pick (no selection): gain = 0.3*sim + 0.7*1.0
        cand_a: 0.3*0.95 + 0.7 = 0.985  -> picked
      Second pick: gain = 0.3*sim + 0.7*(1-max_sim_to_a)
        cand_b: 0.3*0.5 + 0.7*(1-0.743) = 0.33
        cand_c: 0.3*0 + 0.7*1.0 = 0.7  -> picked
      Result: [cand_a, cand_c]
    """
    vecs = {
        "original": [1.0, 0.0],
        "cand_a": [0.95, 0.31],
        "cand_b": [0.5, 0.866],
        "cand_c": [0.0, 1.0],
    }
    ext = QueryExtensionRunnable(
        model="test",
        k=2,
        alpha=0.3,
        llm=FakeStructuredLLM(),
        embedder=CustomEmbedder(vecs),
    )
    selected = ext.lazy_greedy_select("original", ["cand_a", "cand_b", "cand_c"])
    assert selected == ["cand_a", "cand_c"]


def test_lazy_greedy_falls_back_on_embedder_error() -> None:
    class FailingEmbedder:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("embedder down")

    ext = QueryExtensionRunnable(
        model="test", k=2, llm=FakeStructuredLLM(), embedder=FailingEmbedder()
    )
    selected = ext.lazy_greedy_select("orig", ["a", "b", "c", "d", "e"])
    assert selected == ["a", "b"]  # first 2 by insertion order


# ---------- Stage 3: string normalize dedup ----------


def test_string_normalize_dedup_strips_punctuation() -> None:
    ext = QueryExtensionRunnable(model="test", llm=FakeStructuredLLM())
    queries = ["hello world", "hello, world!", "Hello World"]
    result = ext.string_normalize_dedup(queries)
    assert result == ["hello world"]


def test_string_normalize_dedup_keeps_distinct() -> None:
    ext = QueryExtensionRunnable(model="test", llm=FakeStructuredLLM())
    queries = ["hello world", "goodbye world", "Hello World"]
    result = ext.string_normalize_dedup(queries)
    assert result == ["hello world", "goodbye world"]


def test_string_normalize_dedup_preserves_order() -> None:
    ext = QueryExtensionRunnable(model="test", llm=FakeStructuredLLM())
    queries = ["z", "a", "z!", "a...", "m"]
    result = ext.string_normalize_dedup(queries)
    assert result == ["z", "a", "m"]


def test_string_normalize_dedup_unicode() -> None:
    ext = QueryExtensionRunnable(model="test", llm=FakeStructuredLLM())
    queries = ["你好世界", "你好，世界！", "你好世界?"]
    result = ext.string_normalize_dedup(queries)
    assert result == ["你好世界"]


# ---------- End-to-end: __call__ ----------


def test_call_pipeline_full_flow() -> None:
    """__call__ runs all 3 stages: rewrite + lazy-greedy + string dedup."""
    llm = FakeStructuredLLM(
        variants_by_prompt_substr={
            "原问题": ["alpha word", "alpha word!", "bravo word", "charlie word"]
        }
    )
    emb = FakeEmbedder()
    ext = QueryExtensionRunnable(
        model="test", generate_count=10, k=3, alpha=0.3, llm=llm, embedder=emb
    )
    result = ext("original query")
    assert isinstance(result, QueryExtensionResult)
    assert result.original == "original query"
    # original at index 0 of deduped_variants (always preserved)
    assert result.deduped_variants[0] == "original query"
    # variants are at most generate_count
    assert len(result.variants) <= 10


def test_call_pipeline_preserves_original() -> None:
    """Original query is always at index 0 of deduped_variants."""
    llm = FakeStructuredLLM(
        variants_by_prompt_substr={"原问题": ["variant 1", "variant 2"]}
    )
    ext = QueryExtensionRunnable(model="test", llm=llm)
    result = ext("hello")
    assert result.deduped_variants[0] == "hello"


# ---------- Constructor uses structured chat model ----------


def test_default_constructor_uses_get_structured_chat_model() -> None:
    """When llm=None, the runnable builds via get_structured_chat_model.

    Verify the constructed LLM is callable (has .invoke) and the
    QueryExtensionVariants schema is the structured output target.
    """
    ext = QueryExtensionRunnable(model="test", llm=FakeStructuredLLM())
    assert ext._llm is not None
    assert hasattr(ext._llm, "invoke")
    result = ext._llm.invoke([{"role": "user", "content": "no match"}])
    assert isinstance(result, QueryExtensionVariants)


# ---------- Constants ----------


def test_default_model_is_minimax_m3() -> None:
    assert QueryExtensionRunnable.DEFAULT_MODEL == "MiniMax-M3"
