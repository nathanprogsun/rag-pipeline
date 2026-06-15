"""Integration test for real MiniMax-M3 tokenizer.

Downloads the tokenizer on first run (cached to ~/.cache/huggingface/),
then verifies real token counts against expected values. Skipped if
HF network is unavailable (e.g., in offline CI).
"""

from __future__ import annotations

import uuid

import pytest
from tokenizers import Tokenizer

from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.infra.llm.tokenizer import (
    MINIMAX_M3_CONTEXT_WINDOW,
    MINIMAX_M3_TOKENIZER_ID,
    count_tokens,
    load_minimax_m3_tokenizer,
)
from rag.search.post.filter import DEFAULT_TOKEN_BUDGET, filter_by_token_budget


def _doc(text: str) -> ScoredDocument:
    return ScoredDocument(
        chunk_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        text=text,
        score=0.5,
        rank=0,
        source="vector",
        metadata=ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file"),
    )


@pytest.fixture(scope="module")
def m3_tok() -> Tokenizer:
    """Load real M3 tokenizer. Skip on network failure."""
    try:
        return load_minimax_m3_tokenizer()
    except RuntimeError:
        pytest.skip(f"HuggingFace unavailable; cannot load {MINIMAX_M3_TOKENIZER_ID}")


def test_m3_tokenizer_id_is_correct() -> None:
    assert MINIMAX_M3_TOKENIZER_ID == "MiniMaxAI/MiniMax-M3"
    assert MINIMAX_M3_CONTEXT_WINDOW == 1_000_000


def test_m3_default_budget_is_960k(m3_tok: Tokenizer) -> None:
    assert DEFAULT_TOKEN_BUDGET == 960_000


def test_m3_count_short_text(m3_tok: Tokenizer) -> None:
    """Short text token count > 0 and sane."""
    n = count_tokens("hello world", tokenizer=m3_tok)
    assert 1 <= n <= 5  # "hello world" should be 1-5 tokens in BPE


def test_m3_count_mixed_cn_en(m3_tok: Tokenizer) -> None:
    """M3 is optimized for Chinese; mixed text should be efficient."""
    text = "hello world 中文测试 mixed"
    n = count_tokens(text, tokenizer=m3_tok)
    # Sanity: not zero, not absurdly large for ~30 chars
    assert 0 < n < 50


def test_m3_filter_default_budget_holds_lots(m3_tok: Tokenizer) -> None:
    """Default 960K budget holds 1000 short chunks easily."""
    docs = [_doc(f"chunk {i} content") for i in range(1000)]
    kept = filter_by_token_budget(docs, tokenizer=m3_tok)
    # 1000 * ~5 tokens = 5000 tokens, well within 960K
    assert len(kept) == 1000


def test_m3_filter_realistic_chunk_size(m3_tok: Tokenizer) -> None:
    """1000 chunks of ~500 tokens each = ~500K tokens, fits in 960K."""
    chunk_text = ("This is a realistic RAG chunk. " * 20).strip()  # ~500 tokens
    docs = [_doc(chunk_text) for _ in range(1000)]
    kept = filter_by_token_budget(docs, tokenizer=m3_tok)
    # Should fit ~1900 such chunks; 1000 is well within budget
    assert len(kept) == 1000


def test_m3_filter_truncation_at_budget_boundary(m3_tok: Tokenizer) -> None:
    """Verify greedy truncation when budget exceeded."""
    # Each chunk ~100 tokens; 50 chunks = ~5000 tokens
    chunk_text = "x" * 200  # M3 BPE may produce ~50-80 tokens for 200 chars
    docs = [_doc(chunk_text) for _ in range(100)]
    budget = 1000  # ~10-20 chunks
    kept = filter_by_token_budget(docs, max_tokens=budget, tokenizer=m3_tok)
    # Should keep some prefix, not all 100
    assert 0 < len(kept) < 100
