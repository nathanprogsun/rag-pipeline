"""RAGAS metric stubs per task 19 G-P0-2 (real RAGAS deferred to v2).

Per `.agents/design/2026-06-14-cross-task-contracts.md` task 19:

> Real RAGAS `faithfulness` with custom LLM judge (task 19 ships a stub
> for v2; see G-P0-2)

This module ships pure-function STUBS that approximate RAGAS metrics
without the LLM judge or heavy RAGAS dependency. Replace with real
RAGAS in v2 once the dep is added (currently ``ragas>=0.3,<0.4`` is
in pyproject but heavy).

Stub implementations:

- ``faithfulness_stub(answer, contexts) -> float`` (0-1, higher = less hallucination)
  Tokenize answer into word claims; each claim token-set must be a
  subset of the union of all context token-sets. Fraction of in-context
  claims = faithfulness. Heuristic, NOT a real hallucination detector.

- ``answer_relevance_stub(query, answer) -> float`` (0-1, higher = more on-topic)
  Jaccard similarity between query tokens and answer tokens.
  Heuristic, NOT a true semantic relevance.

- ``context_precision_stub(retrieved_chunk_ids, ground_truth_chunk_ids) -> float``
  Fraction of retrieved chunks that are in ground truth (precision-like).
  Same as ``precision_at_k`` from ``rag.eval.metrics`` but exposed here
  under the RAGAS metric name for API symmetry.

Real RAGAS interface (v2 replacement, NOT shipped):
- ``from ragas.metrics import faithfulness, answer_relevancy, context_precision``
- ``from ragas import evaluate``
- Uses LLM-as-judge for faithfulness (e.g. GPT-4 scores 1-5, normalized).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# ---------- Tokenization ----------


_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
# CJK Unified Ideographs + Extension A: tokenize each character individually
# so "教程很好" → {"教", "程", "很", "好"} (matches RAGAS's per-character
# Chinese tokenization default).
_CJK_CHAR_RE = re.compile(r"[一-鿿㐀-䶿]")
# Match runs that contain at least one CJK character (used to filter out
# the combined-CJK-run that ``\w+`` would otherwise produce, since it
# mismatches against per-character contexts).
_CJK_RUN_RE = re.compile(r"[\w]*[一-鿿ꀀ-꓏][\w]*", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens + per-character CJK tokens.

    For ASCII/Latin: ``\\w+`` matches whole words.
    For CJK (Chinese/Japanese kanji/Korean hanja): each character is its
    own token, matching RAGAS-style per-character tokenization.

    Pure-CJK runs from ``\\w+`` are dropped to avoid subset mismatch
    (e.g. claim "教程" would not be subset of context tokenized into
    individual chars).

    This makes ``教程很好`` tokenize as ``{"教", "程", "很", "好"}`` so
    subset presence checks behave intuitively.
    """
    if not text:
        return set()
    out: set[str] = set()
    # Latin/word tokens: skip runs that are entirely CJK
    for m in _TOKEN_RE.finditer(text):
        if not _CJK_RUN_RE.fullmatch(m.group(0)):
            out.add(m.group(0).lower())
    # Per-character CJK tokens
    out.update(_CJK_CHAR_RE.findall(text))
    return out


def _split_into_claims(answer: str) -> list[set[str]]:
    """Split answer into claim-like chunks (sentences/segments) for faithfulness.

    Heuristic: split on sentence-ending punctuation (. ! ? 。 ！ ？ \n).
    Returns a list of token-sets; each = one claim's tokens.
    """
    if not answer:
        return []
    raw = re.split(r"[.!?。！？\n]+", answer)
    return [_tokenize(part) for part in raw if part.strip()]


# ---------- Faithfulness ----------


def faithfulness_stub(answer: str, contexts: Iterable[str]) -> float:
    """Faithfulness stub: fraction of answer claims whose tokens are in context.

    Returns ``|claims_in_context| / |claims|``. Range [0, 1].

    Edge cases:
    - empty answer → 1.0 (no claims to verify)
    - empty contexts → 0.0 (no evidence)
    - single-token answer → checks token presence

    Heuristic, NOT a real hallucination detector. Real RAGAS uses an
    LLM judge to verify each claim against the context.
    """
    claims = _split_into_claims(answer)
    if not claims:
        return 1.0
    context_tokens = set()
    for ctx in contexts:
        context_tokens |= _tokenize(ctx)
    if not context_tokens:
        return 0.0
    in_context = sum(
        1 for claim_tokens in claims if claim_tokens and claim_tokens <= context_tokens
    )
    return in_context / len(claims)


# ---------- Answer relevance ----------


def answer_relevance_stub(query: str, answer: str) -> float:
    """Answer relevance stub: Jaccard similarity between query and answer tokens.

    Returns ``|q ∩ a| / |q ∪ a|``. Range [0, 1].

    Edge cases:
    - empty query or empty answer → 0.0
    - both empty → 0.0

    Heuristic, NOT a true semantic relevance. Real RAGAS computes cosine
    similarity between query and answer embeddings.
    """
    q = _tokenize(query)
    a = _tokenize(answer)
    if not q or not a:
        return 0.0
    return len(q & a) / len(q | a)


# ---------- Context precision ----------


def context_precision_stub(
    retrieved_chunk_ids: list[str],
    ground_truth_chunk_ids: list[str] | set[str],
) -> float:
    """Context precision stub: fraction of retrieved chunks that are in ground truth.

    Returns ``|retrieved ∩ gt| / |retrieved|``. Range [0, 1].

    Edge cases:
    - empty retrieved → 1.0 (nothing to evaluate, vacuous)
    - empty ground_truth → 0.0 (no relevant signal in retrieval)

    Real RAGAS context precision uses a more nuanced formula that
    considers the order of retrieved chunks.
    """
    if not retrieved_chunk_ids:
        return 1.0
    gt = set(ground_truth_chunk_ids)
    if not gt:
        return 0.0
    hits = sum(1 for cid in retrieved_chunk_ids if cid in gt)
    return hits / len(retrieved_chunk_ids)


# ---------- Aggregations (re-export for symmetry with metrics.py) ----------


