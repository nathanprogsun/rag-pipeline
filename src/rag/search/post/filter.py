"""过滤管道: 去重 + 阈值过滤 + token 预算。

签名::
    filter_by_score(docs, threshold, search_mode) -> tuple[list[ScoredDocument], bool]
    filter_by_token_budget(docs, max_tokens, tokenizer=None) -> list[ScoredDocument]

阈值过滤读取 ``score_breakdown[source]`` (per-source raw), 而非 ``.score`` (RRF sum);
``search_mode == "fulltext"`` 时为 no-op。token 预算默认 960K (留 40K headroom);
tokenizer 来自 ``rag.infra.llm.tokenizer`` (MiniMax-M3 BPE, lazy 下载)。
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
from rag.infra.observability.trace import remove_duplicates  # noqa: F401  re-export

SearchMode = Literal["embedding", "fulltext", "mixed"]

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
    """按 per-source raw similarity 阈值过滤。

    - 读 ``score_breakdown[source]`` (per-source raw), 而非 ``.score`` (RRF sum)。
    - ``search_mode == "fulltext"``: no-op, 返回 ``(docs, False)``。
    - ``search_mode == "mixed"``: 取每 chunk 的 ``max(vector, fulltext)``。
    - ``search_mode == "embedding"``: 使用 ``score_breakdown["vector"]``。
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
    """贪心式 token 预算过滤 (使用 MiniMax-M3 BPE tokenizer)。"""
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
