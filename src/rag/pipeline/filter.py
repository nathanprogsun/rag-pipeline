"""Filter pipeline: dedup + 阈值过滤 + token 预算。

Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 2:

签名::

    remove_duplicates(docs, traces)              # re-export from rag.retrieval.trace
    filter_by_score(docs, threshold, search_mode) -> tuple[list[ScoredDocument], bool]
    filter_by_token_budget(docs, max_tokens, tokenizer=None) -> list[ScoredDocument]

阈值过滤的语义:
- 读 ``score_breakdown[source]`` (per-source raw), 不是 ``.score`` (RRF sum)
- ``search_mode != embedding`` 时是 no-op (fulltext 没向量相似度)
- 返回 ``(filtered_docs, using_similarity_filter)`` 元组, 调用方可知是否真过滤

token 预算: 默认 960K (MiniMax-M3 1M context 留 40K headroom);
tokenizer 用 ``rag.infra.llm.tokenizer`` 模块(MiniMax-M3 BPE, lazy 下载)。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from tokenizers import Tokenizer

from rag.domain.document import ScoredDocument
from rag.infra.llm.tokenizer import (
    MINIMAX_M3_CONTEXT_WINDOW,
    count_tokens,
    load_minimax_m3_tokenizer,
)
from rag.retrieval.trace import remove_duplicates  # noqa: F401  re-export

SearchMode = Literal["embedding", "fulltext", "mixed"]

# 1M context minus 40K headroom (system prompt + max_tokens output + safety)
DEFAULT_TOKEN_BUDGET: int = MINIMAX_M3_CONTEXT_WINDOW - 40_000  # 960_000


@lru_cache(maxsize=1)
def _default_tokenizer() -> Tokenizer:
    return load_minimax_m3_tokenizer()


def filter_by_score(
    docs: list[ScoredDocument],
    *,
    threshold: float,
    search_mode: SearchMode = "embedding",
) -> tuple[list[ScoredDocument], bool]:
    """Per-source raw similarity threshold filter.

    Contract 2 invariants:
    - Reads ``score_breakdown[source]`` (per-source raw), NOT ``.score`` (RRF sum).
      RRF sums are ~0.01-0.1, comparing them to a raw threshold (0.3-0.9) would
      drop every hit. Per-source max merge is what callers want.
    - ``search_mode == "fulltext"`` -> no-op (no embedding score available);
      returns ``(docs, False)`` so caller knows the filter was a no-op.
    - ``search_mode == "mixed"`` -> uses ``max(vector, fulltext)`` per chunk.
    - ``search_mode == "embedding"`` -> uses ``score_breakdown["vector"]``.

    Args:
        docs: ScoredDocument list (assumed RRF-ranked, but order doesn't matter).
        threshold: Raw similarity threshold (typically 0.3-0.9).
        search_mode: Which sources are present in the hits.

    Returns:
        ``(filtered_docs, using_similarity_filter)``:
            filtered_docs: docs with per-source score >= threshold.
            using_similarity_filter: True if filter actually ran, False if no-op.
    """
    if search_mode == "fulltext":
        return list(docs), False

    if search_mode == "embedding":
        kept = [d for d in docs if d.score_breakdown.get("vector", 0.0) >= threshold]
        return kept, True

    kept = [
        d
        for d in docs
        if max(
            d.score_breakdown.get("vector", 0.0),
            d.score_breakdown.get("fulltext", 0.0),
        )
        >= threshold
    ]
    return kept, True


def filter_by_token_budget(
    docs: list[ScoredDocument],
    *,
    max_tokens: int = DEFAULT_TOKEN_BUDGET,
    tokenizer: Tokenizer | None = None,
) -> list[ScoredDocument]:
    """Greedy token-budget filter using MiniMax-M3 tokenizer (BPE).

    Iterates docs in current order (assumed RRF-ranked), keeps adding
    until the token budget would be exceeded. Returns the prefix that
    fits within the budget.

    Default tokenizer is MiniMax-M3 BPE (vocab=200,060). Pass an
    alternative Tokenizer to use a different model's tokenizer.

    Args:
        docs: ScoredDocument list (assumed score-sorted).
        max_tokens: Total token budget (default 960K for M3 1M context).
        tokenizer: Optional tokenizer override (default: M3 cached singleton).

    Returns:
        Prefix of ``docs`` whose total tokens fit within ``max_tokens``.
    """
    tok = tokenizer if tokenizer is not None else _default_tokenizer()
    used = 0
    kept: list[ScoredDocument] = []
    for d in docs:
        cost = count_tokens(d.text, tokenizer=tok)
        if used + cost > max_tokens:
            continue
        kept.append(d)
        used += cost
    return kept
