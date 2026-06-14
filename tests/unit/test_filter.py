"""Unit tests for ``rag.pipeline.filter`` per Contract 2 of
``.agents/design/2026-06-14-cross-task-contracts.md``.

Uses a FakeTokenizer to keep tests deterministic and offline.
Real MiniMax-M3 tokenizer integration is exercised by the slow
``tests/integration/test_m3_tokenizer.py`` test.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.pipeline.filter import (
    DEFAULT_TOKEN_BUDGET,
    filter_by_score,
    filter_by_token_budget,
    remove_duplicates,  # re-export
)
from rag.retrieval.trace import RetrievalTrace

# ---------- FakeTokenizer (deterministic, offline) ----------


@dataclass
class _Enc:
    ids: list[int]


class FakeTokenizer:
    """Returns ``len(text) // tokens_per_char`` tokens, minimum 1.

    ``text_per_token`` controls how many chars produce 1 token.
    e.g. tokens_per_char=0.25 means 4 chars -> 1 token (the old heuristic).
    """

    def __init__(self, tokens_per_char: float = 0.25) -> None:
        self.tokens_per_char = tokens_per_char

    def encode(self, text: str) -> _Enc:  # noqa: D401 - match real API
        n = max(1, int(len(text) * self.tokens_per_char))
        return _Enc(ids=list(range(n)))


# 1 char -> 1 token (predictable per-char counting)
_FAKE_1C1T = FakeTokenizer(tokens_per_char=1.0)
# 4 chars -> 1 token (matches old heuristic for comparison tests)
_FAKE_4C1T = FakeTokenizer(tokens_per_char=0.25)


# ---------- Fixtures ----------


def _meta() -> ChunkMetadata:
    return ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file")


def _doc(
    chunk_id_str: str,
    *,
    score: float = 0.0,
    source: str = "vector",
    text: str = "x",
    score_breakdown: dict[str, float] | None = None,
) -> ScoredDocument:
    return ScoredDocument(
        chunk_id=uuid.UUID(chunk_id_str),
        dataset_id=uuid.uuid4(),
        text=text,
        score=score,
        rank=0,
        source=source,  # type: ignore[arg-type]
        metadata=_meta(),
        score_breakdown=score_breakdown or {},
    )


A = "00000000-0000-0000-0000-000000000001"
B = "00000000-0000-0000-0000-000000000002"
C = "00000000-0000-0000-0000-000000000003"
D = "00000000-0000-0000-0000-000000000004"


# ---------- remove_duplicates re-export ----------


def test_remove_duplicates_reexport_works() -> None:
    """Re-export from rag.retrieval.trace is callable with the same signature."""
    a = _doc(A)
    b = _doc(B)
    t1 = RetrievalTrace(q="q1", a="a1")
    t2 = RetrievalTrace(q="q2", a="a2")
    t1_dup = RetrievalTrace(q="q1", a="a1")
    result = remove_duplicates([a, b, a], [t1, t2, t1_dup])
    assert len(result) == 2
    assert str(result[0].chunk_id) == A
    assert str(result[1].chunk_id) == B


# ---------- filter_by_score: embedding mode ----------


def test_filter_by_score_embedding_keeps_above_threshold() -> None:
    a = _doc(A, score_breakdown={"vector": 0.8})
    b = _doc(B, score_breakdown={"vector": 0.5})
    c = _doc(C, score_breakdown={"vector": 0.95})
    kept, ran = filter_by_score([a, b, c], threshold=0.7, search_mode="embedding")
    assert ran is True
    assert [str(d.chunk_id) for d in kept] == [A, C]


def test_filter_by_score_embedding_excludes_below() -> None:
    a = _doc(A, score_breakdown={"vector": 0.2})
    b = _doc(B, score_breakdown={"vector": 0.3})
    kept, ran = filter_by_score([a, b], threshold=0.7, search_mode="embedding")
    assert ran is True
    assert kept == []


def test_filter_by_score_embedding_does_not_read_score_field() -> None:
    """P0-2: filter reads score_breakdown, NOT .score (which is RRF sum)."""
    a = _doc(A, score=0.005, score_breakdown={"vector": 0.9})
    b = _doc(B, score=0.5, score_breakdown={"vector": 0.1})
    kept, _ = filter_by_score([a, b], threshold=0.5, search_mode="embedding")
    assert [str(d.chunk_id) for d in kept] == [A]


# ---------- filter_by_score: fulltext mode no-op ----------


def test_filter_by_score_fulltext_mode_is_noop() -> None:
    a = _doc(A, score_breakdown={"fulltext": 0.99})
    b = _doc(B, score_breakdown={"fulltext": 0.01})
    kept, ran = filter_by_score([a, b], threshold=0.5, search_mode="fulltext")
    assert ran is False
    assert [str(d.chunk_id) for d in kept] == [A, B]


# ---------- filter_by_score: mixed mode max ----------


def test_filter_by_score_mixed_uses_max_per_source() -> None:
    a = _doc(A, score_breakdown={"vector": 0.2, "fulltext": 0.8})
    b = _doc(B, score_breakdown={"vector": 0.6, "fulltext": 0.3})
    c = _doc(C, score_breakdown={"vector": 0.1, "fulltext": 0.2})
    d = _doc(D, score_breakdown={"vector": 0.5, "fulltext": 0.5})
    kept, ran = filter_by_score([a, b, c, d], threshold=0.5, search_mode="mixed")
    assert ran is True
    assert {str(x.chunk_id) for x in kept} == {A, B, D}


# ---------- filter_by_score: edge cases ----------


def test_filter_by_score_empty_input() -> None:
    kept, ran = filter_by_score([], threshold=0.5, search_mode="embedding")
    assert kept == []
    assert ran is True


def test_filter_by_score_using_similarity_filter_flag() -> None:
    docs_emb = [_doc(A, score_breakdown={"vector": 0.9})]
    docs_ft = [_doc(A, score_breakdown={"fulltext": 0.9})]
    docs_mix = [_doc(A, score_breakdown={"vector": 0.9, "fulltext": 0.9})]

    _, ran_e = filter_by_score(docs_emb, threshold=0.5, search_mode="embedding")
    _, ran_f = filter_by_score(docs_ft, threshold=0.5, search_mode="fulltext")
    _, ran_m = filter_by_score(docs_mix, threshold=0.5, search_mode="mixed")
    assert ran_e is True
    assert ran_f is False
    assert ran_m is True


# ---------- filter_by_token_budget (with FakeTokenizer) ----------


def test_filter_by_token_budget_default_is_960k_for_m3() -> None:
    """DEFAULT_TOKEN_BUDGET = 1M - 40K headroom for M3 1M context."""
    assert DEFAULT_TOKEN_BUDGET == 1_000_000 - 40_000
    assert DEFAULT_TOKEN_BUDGET == 960_000


def test_filter_by_token_budget_uses_passed_tokenizer() -> None:
    """Explicit tokenizer override (1 char -> 1 token)."""
    docs = [
        _doc(A, text="abcd"),  # 4 tokens
        _doc(B, text="efgh"),  # 4 tokens, total 8
        _doc(C, text="ij"),  # 2 tokens, total 10
    ]
    kept = filter_by_token_budget(docs, max_tokens=10, tokenizer=_FAKE_1C1T)
    assert [str(d.chunk_id) for d in kept] == [A, B, C]


def test_filter_by_token_budget_truncates_when_over() -> None:
    """max_tokens=8: 4+4 fits, +2 would push over, skip C."""
    docs = [
        _doc(A, text="abcd"),  # 4 tokens
        _doc(B, text="efgh"),  # 4 tokens, total 8
        _doc(C, text="ij"),  # 2 tokens, would push to 10, skip
    ]
    kept = filter_by_token_budget(docs, max_tokens=8, tokenizer=_FAKE_1C1T)
    assert [str(d.chunk_id) for d in kept] == [A, B]


def test_filter_by_token_budget_zero_max_returns_empty() -> None:
    docs = [_doc(A, text="hello")]
    kept = filter_by_token_budget(docs, max_tokens=0, tokenizer=_FAKE_1C1T)
    assert kept == []


def test_filter_by_token_budget_default_budget_handles_large_text() -> None:
    """Default 960K budget easily holds 100 short chunks (1 token each)."""
    docs = [_doc(uuid.uuid4().__str__(), text=f"chunk{i}") for i in range(100)]
    kept = filter_by_token_budget(docs, tokenizer=_FAKE_1C1T)
    # 1 token per chunk * 100 chunks = 100 tokens, well within 960K
    assert len(kept) == 100


def test_filter_by_token_budget_empty_input() -> None:
    kept = filter_by_token_budget([], tokenizer=_FAKE_1C1T)
    assert kept == []


def test_filter_by_token_budget_tokenizer_independence() -> None:
    """Same text, different tokenizers -> different token counts."""
    docs = [_doc(A, text="x" * 20)]  # 20 chars
    # 1 char = 1 token
    kept_1c1t = filter_by_token_budget(docs, max_tokens=20, tokenizer=_FAKE_1C1T)
    # 4 chars = 1 token -> 5 tokens
    kept_4c1t = filter_by_token_budget(docs, max_tokens=5, tokenizer=_FAKE_4C1T)
    assert len(kept_1c1t) == 1  # 20 tokens, fits
    assert len(kept_4c1t) == 1  # 5 tokens, fits
    # max=4 with 4c1t -> doc is 5 tokens, doesn't fit
    kept_4c1t_over = filter_by_token_budget(docs, max_tokens=4, tokenizer=_FAKE_4C1T)
    assert kept_4c1t_over == []


def test_filter_by_token_budget_default_constants() -> None:
    """DEFAULT_TOKEN_BUDGET exposed and reasonable."""
    assert DEFAULT_TOKEN_BUDGET == 960_000
    assert DEFAULT_TOKEN_BUDGET % 1000 == 0  # round number
