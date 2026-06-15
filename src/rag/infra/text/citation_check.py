"""引用标记 ``[id](CITE)`` 的解析与校验。

- LLM 响应中的 ``[id](CITE)`` 标记, ``id`` 为 ``SearchResult.citations`` 的 1-based 下标。
- ``CitationChecker`` 校验所有标记都能映射到实际引用, 并报告孤立引用
  （存在于 citations 列表但未在响应中引用）。
- 同时提供 cite 阶段使用的解析辅助函数: ``parse_inline_citations`` 抽取 id,
  ``resolve_citation_positions`` 回填 ``Citation.position``。

本模块只依赖 ``rag.domain.search.Citation`` 类型与本地正则, 放在
``infra/text/`` 下避免反向依赖业务包。

注: 仅做正则层校验, 不检测 LLM 幻觉。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from rag.domain.search import Citation

# 正则: [N](CITE), N 为 1 位或多位数字, 捕获组为编号。
_INLINE_CITE_RE: re.Pattern[str] = re.compile(r"\[(\d+)\]\(CITE\)")


# ---------- Marker parsing utilities ----------


def parse_inline_citations(response: str) -> list[int]:
    """从响应文本中解析 ``[id](CITE)`` 标记。

    返回按出现顺序排列的 1-based 引用 id 列表; 同一 id 多次出现都会被
    保留（调用方可用 ``sorted(set(...))`` 去重）。
    """
    if not response:
        return []
    return [int(m.group(1)) for m in _INLINE_CITE_RE.finditer(response)]


def resolve_citation_positions(
    response: str,
    citations: list[Citation],
) -> list[Citation]:
    """基于 ``[id](CITE)`` 标记填充 ``Citation.position``。

    对每个 ``citations[i]``（0-based, 对应 id ``i+1``）找到响应中
    ``[i+1](CITE)`` 的首次出现位置, 写入 ``citation.position`` 字符偏移。

    未被引用的 ``Citation`` 位置为 ``None``; id 越界的引用也会得到 ``None``
    （正则匹配不到）。

    Args:
        response: LLM 生成的响应文本（可为空）。
        citations: 0-based 引用列表; ``position[i]`` 对应 id ``i+1``。

    Returns:
        新的 ``Citation`` 列表, 已被引用的条目 ``position`` 已填充。
    """
    if not response or not citations:
        return list(citations)

    first_offset: dict[int, int] = {}
    for m in _INLINE_CITE_RE.finditer(response):
        cid = int(m.group(1))
        if cid not in first_offset:
            first_offset[cid] = m.start()

    result: list[Citation] = []
    for idx, c in enumerate(citations):
        cid = idx + 1
        offset = first_offset.get(cid)
        if offset is not None:
            result.append(c.model_copy(update={"position": offset}))
        else:
            result.append(c)
    return result


# ---------- Validation ----------


class CitationCheckResult(BaseModel):
    """响应中 ``[id](CITE)`` 标记的校验结果。

    Attributes:
        valid: 所有被引用的 id 都在 ``[1, len(citations)]`` 范围内时为 ``True``。
        referenced_ids: 按出现顺序排列的 id 列表（保留重复项）。
        referenced_unique: 去重并升序排列的 id 列表。
        out_of_range_ids: 响应中引用但超出 ``[1, len(citations)]`` 范围的 id。
        orphan_citation_indices: ``citations`` 中未被引用的条目下标（0-based,
            不是 1-based id）。
        unused_orphan_marker_ids: 解析不到对应引用的 1-based id
            （与 ``out_of_range_ids`` 等价, 保留为调用方对称便利）。
    """

    model_config = ConfigDict(frozen=True)

    valid: bool
    referenced_ids: list[int] = Field(default_factory=list)
    referenced_unique: list[int] = Field(default_factory=list)
    out_of_range_ids: list[int] = Field(default_factory=list)
    orphan_citation_indices: list[int] = Field(default_factory=list)
    unused_orphan_marker_ids: list[int] = Field(default_factory=list)


class CitationChecker:
    """校验 LLM 响应中的 ``[id](CITE)`` 标记是否对应 citations 列表。

    无状态: 把 ``response`` 与 ``citations`` 传给 ``check()``。可在管线
    生成后阶段（``SearchResult.response`` 落定后）或 eval / 调试工具中使用。
    """

    def check(self, response: str, citations: list[Citation]) -> CitationCheckResult:
        """执行完整校验。

        Returns:
            ``CitationCheckResult``, 包含全部差异; 仅当不存在越界标记时
            ``valid`` 为 ``True``。
        """
        ids = parse_inline_citations(response)
        unique_ids = sorted(set(ids))
        n = len(citations)

        out_of_range = [i for i in ids if i < 1 or i > n]
        referenced_set = set(ids)
        orphan = [idx for idx in range(n) if (idx + 1) not in referenced_set]

        return CitationCheckResult(
            valid=not out_of_range,
            referenced_ids=ids,
            referenced_unique=unique_ids,
            out_of_range_ids=out_of_range,
            orphan_citation_indices=orphan,
            unused_orphan_marker_ids=out_of_range,  # 与 out_of_range_ids 等价
        )
