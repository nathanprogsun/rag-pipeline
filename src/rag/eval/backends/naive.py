"""NaiveBackend: 无 LLM 启发式 backend, 供 CI smoke / 离线快速回归。

WARNING: 数值仅作 token 重合度 / Jaccard 启发, **不是**真实语义指标。
生产环境评估请使用 ``RagasBackend``。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# ---------- 分词 ----------

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
# CJK 表意文字 + 扩展 A 区: 逐字分词, 使 ``教程很好`` 切分为
# ``{"教", "程", "很", "好"}`` (对齐 RAGAS 的逐字中文分词默认)。
_CJK_CHAR_RE = re.compile(r"[一-鿿㐀-䶿]")
# 匹配包含至少一个 CJK 字符的整段, 用于过滤掉 ``\w+`` 切出的
# 整段 CJK, 避免与逐字上下文产生子集判断偏差。
_CJK_RUN_RE = re.compile(r"[\w]*[一-鿿ꀀ-꓏][\w]*", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    """返回小写词级 tokens 与逐字 CJK tokens 的并集。

    - 拉丁词: 整词 token。
    - CJK 字符: 逐字 token。
    - 纯 CJK 整段从 ``\\w+`` 结果中剔除, 避免子集判断误判。
    """
    if not text:
        return set()
    out: set[str] = set()
    for m in _TOKEN_RE.finditer(text):
        if not _CJK_RUN_RE.fullmatch(m.group(0)):
            out.add(m.group(0).lower())
    out.update(_CJK_CHAR_RE.findall(text))
    return out


def _split_into_claims(answer: str) -> list[set[str]]:
    """将 answer 拆分为 claim 片段 (按句末标点)。返回各 claim 的 token 集。"""
    if not answer:
        return []
    raw = re.split(r"[.!?。！？\n]+", answer)
    return [_tokenize(part) for part in raw if part.strip()]


# ---------- 启发式指标 (无 LLM) ----------


def _naive_faithfulness(answer: str, contexts: Iterable[str]) -> float:
    """启发式 faithfulness: answer 中能被 context 覆盖的 claim 比例。"""
    claims = _split_into_claims(answer)
    if not claims:
        return 1.0
    context_tokens: set[str] = set()
    for ctx in contexts:
        context_tokens |= _tokenize(ctx)
    if not context_tokens:
        return 0.0
    in_context = sum(
        1 for claim_tokens in claims if claim_tokens and claim_tokens <= context_tokens
    )
    return in_context / len(claims)


def _naive_answer_relevance(query: str, answer: str) -> float:
    """启发式 answer_relevance: query 与 answer tokens 的 Jaccard 相似度。"""
    q = _tokenize(query)
    a = _tokenize(answer)
    if not q or not a:
        return 0.0
    return len(q & a) / len(q | a)


def _naive_context_precision(
    retrieved_chunk_ids: list[str],
    ground_truth_chunk_ids: list[str] | set[str],
) -> float:
    """启发式 context_precision: retrieved 中属于 ground truth 的比例。

    注意: 此处不感知 rank, 等价于 precision@|retrieved|。如需 rank-aware
    请使用 ``metrics.recall_at_k`` / ``metrics.ndcg_at_k``。
    """
    if not retrieved_chunk_ids:
        return 1.0
    gt = set(ground_truth_chunk_ids)
    if not gt:
        return 0.0
    hits = sum(1 for cid in retrieved_chunk_ids if cid in gt)
    return hits / len(retrieved_chunk_ids)


# ---------- Backend ----------


class NaiveBackend:
    """启发式生成指标 backend。

    默认支撑三项指标: ``faithfulness`` / ``answer_relevance`` / ``context_precision``。
    通过 ``supported_metrics`` 可裁剪; 调用方传入的 metric 名若不在子集内则跳过。
    """

    name: str = "naive"

    DEFAULT_SUPPORTED: tuple[str, ...] = (
        "faithfulness",
        "answer_relevance",
        "context_precision",
    )

    def __init__(self, supported_metrics: Iterable[str] | None = None) -> None:
        self.supported = set(supported_metrics or self.DEFAULT_SUPPORTED)

    async def compute(
        self,
        *,
        query: str,
        answer: str,
        contexts: list[str],
        reference: str = "",  # noqa: ARG002  # 启发式不需要
        retrieved_chunk_ids: list[str] | None = None,
        ground_truth_chunk_ids: list[str] | None = None,
        **_: object,
    ) -> dict[str, float]:
        out: dict[str, float] = {}
        if "faithfulness" in self.supported:
            out["faithfulness"] = _naive_faithfulness(answer, contexts)
        if "answer_relevance" in self.supported:
            out["answer_relevance"] = _naive_answer_relevance(query, answer)
        if "context_precision" in self.supported and retrieved_chunk_ids:
            out["context_precision"] = _naive_context_precision(
                retrieved_chunk_ids, ground_truth_chunk_ids or []
            )
        return out
