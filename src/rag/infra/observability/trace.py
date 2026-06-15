"""召回层溯源数据: 与 ``ScoredDocument`` 解耦。

为什么 ``q`` / ``a`` 不放在 ``ScoredDocument``:

- ``ScoredDocument`` 是落库 / 渲染通用的"召回结果"形状, 任何 store (vector /
  fulltext / caption) 都产它。如果塞 ``q`` / ``a``, 等于让所有 store 都得
  关心"我当前在跑哪个 query 变体", 而这本身属于召回链路的运行时上下文,
  与 chunk 本身的属性无关。
- ``q`` / ``a`` 只在 ``remove_duplicates`` 去重 (按 query 变体下 top-1 答案)
  等链路阶段使用, 用一个独立 ``RetrievalTrace`` 平行数组传给 ``remove_duplicates``
  更清晰, 也方便未来加 query_decomposition / 多 query 召回等场景扩展
  (``list[RetrievalTrace]`` 与 ``list[ScoredDocument]`` 等长对齐)。

``RetrievalTrace`` 是 dataclass 而非 Pydantic: 它是进程内一次性数据, 不落库、
不序列化、不跨边界, dataclass 比 BaseModel 更轻。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ScoredDocumentLike(Protocol):
    """``ScoredDocument`` 的 duck-type 协议: ``remove_duplicates`` 不依赖具体类。

    Protocol 而非具体类型: 避免 ``remove_duplicates`` 跟 ``rag.domain.document``
    形成循环依赖 (trace.py 是 observability 包入口, 不应反向依赖 domain)。
    """

    chunk_id: object


@dataclass(frozen=True)
class RetrievalTrace:
    """召回阶段运行时溯源: 与对应的 ``ScoredDocument`` 等长对齐。

    Attributes:
        q: 触发该 chunk 召回的 query 变体 (query_decomposition 场景下为子 query)。
            普通单 query 场景下与外部 ``SearchRequest.query`` 一致。
        a: 该 query 变体下该 chunk 的 top-1 答案片段, ``remove_duplicates`` 按
            ``(q, a)`` 元组做去重。``None`` 表示该 chunk 未参与答案生成 (例:
            仅 rerank 输入, 未挂答案)。
    """

    q: str | None = None
    a: str | None = None


def remove_duplicates(
    docs: list[ScoredDocumentLike],
    traces: list[RetrievalTrace],
) -> list[ScoredDocumentLike]:
    """按 ``(q, a)`` 元组去重, 保留同 ``(q, a)`` 下最先出现的项。

    Args:
        docs: ``ScoredDocument`` 列表 (按召回顺序, ``rank`` 越靠前越优先保留)。
        traces: 与 ``docs`` 等长的 ``RetrievalTrace`` 列表; 长度必须与 ``docs``
            相同, 否则 ``ValueError``。

    Returns:
        去重后的 ``docs`` 子集, 顺序与输入一致。

    Raises:
        ValueError: ``len(docs) != len(traces)``。
    """
    if len(docs) != len(traces):
        msg = f"docs/traces length mismatch: {len(docs)} != {len(traces)}"
        raise ValueError(msg)

    seen: set[tuple[str | None, str | None]] = set()
    out: list[ScoredDocumentLike] = []
    for doc, trace in zip(docs, traces, strict=True):
        key = (trace.q, trace.a)
        if key in seen:
            continue
        seen.add(key)
        out.append(doc)
    return out
