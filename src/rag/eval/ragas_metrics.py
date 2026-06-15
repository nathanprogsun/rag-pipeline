"""RAGAS 指标的纯函数 stub 版本 (无 LLM judge、无重依赖)。

提供 ``faithfulness_stub``、``answer_relevance_stub``、``context_precision_stub``。
这些是启发式实现, 非真实幻觉检测 / 语义相关性, 用于在 v2 引入真实
RAGAS 之前先跑通评估流程。
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
    # 拉丁/词级 tokens, 跳过整段为 CJK 的 run
    for m in _TOKEN_RE.finditer(text):
        if not _CJK_RUN_RE.fullmatch(m.group(0)):
            out.add(m.group(0).lower())
    # 逐字 CJK tokens
    out.update(_CJK_CHAR_RE.findall(text))
    return out


def _split_into_claims(answer: str) -> list[set[str]]:
    """将 answer 拆分为 claim 片段 (按句末标点)。返回各 claim 的 token 集。"""
    if not answer:
        return []
    raw = re.split(r"[.!?。！？\n]+", answer)
    return [_tokenize(part) for part in raw if part.strip()]


# ---------- Faithfulness ----------


def faithfulness_stub(answer: str, contexts: Iterable[str]) -> float:
    """faithfulness stub: answer 中能被 context 覆盖的 claim 比例。

    返回 ``|claims_in_context| / |claims|``, 范围 ``[0, 1]``。

    启发式实现, 非真实幻觉检测; 真实 RAGAS 使用 LLM judge。
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
    """answer relevance stub: query 与 answer tokens 的 Jaccard 相似度。

    返回 ``|q ∩ a| / |q ∪ a|``, 范围 ``[0, 1]``。

    启发式实现; 真实 RAGAS 使用 query / answer embedding 的余弦相似度。
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
    """context precision stub: retrieved 中属于 ground truth 的比例。

    返回 ``|retrieved ∩ gt| / |retrieved|``, 范围 ``[0, 1]``。
    """
    if not retrieved_chunk_ids:
        return 1.0
    gt = set(ground_truth_chunk_ids)
    if not gt:
        return 0.0
    hits = sum(1 for cid in retrieved_chunk_ids if cid in gt)
    return hits / len(retrieved_chunk_ids)


# ---------- 聚合 (与 metrics.py 对称地再导出) ----------
