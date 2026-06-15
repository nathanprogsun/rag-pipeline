"""引用 (cite) 阶段。

LLM 的 ``response`` 文本中含 ``[id](CITE)`` 标记, ``id`` 为
``SearchResult.citations`` 列表的 1-based 索引 (索引顺序即 1-based 编号)。

提供:
- ``SimpleCite``: 默认 cite 阶段, 给 docs 编 1-based 序号并构造 Citation DTO。

标记解析工具 (``parse_inline_citations``, ``resolve_citation_positions``)
位于 ``rag.infra.text.citation_check``; ``SimpleCite`` 仍在此模块, 因为
它依赖 search 专属的 Citation DTO 形状。
"""

from __future__ import annotations

from collections.abc import Callable

from rag.domain.document import ScoredDocument
from rag.domain.search import Citation, SearchRequest


class CiteStageProtocol:
    """cite 阶段回调, 将最终 ScoredDocument 列表映射为 Citation DTO。

    注: 本类型是 Callable 而非严格 Protocol, 因此编排器可接受类实例
    或普通函数。
    """

    def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[Citation]:
        raise NotImplementedError


class SimpleCite:
    """给 docs 编 1-based 序号并按顺序构造 Citation DTO。

    每个 ``ScoredDocument`` 对应一个 ``Citation``:
    - ``chunk_id`` / ``dataset_id``: 来自 ScoredDocument
    - ``source_name``: 由 ``source_name_fn`` 生成 (默认 ``"src-{i}"``)
    - ``content``: ScoredDocument.text
    - ``image_path``: ScoredDocument.image_path (文本 chunk 为 None)
    - ``score``: ScoredDocument.score (融合后 RRF 值; 原始 per-source
      分需读取 ``score_breakdown``)

    Args:
        source_name_fn: 可选命名覆盖函数, 接收 (ScoredDocument, 1-based idx)
            返回字符串, 默认 ``"src-{i}"``。
    """

    DEFAULT_SOURCE_NAME: str = "src-{i}"

    def __init__(
        self,
        *,
        source_name_fn: Callable[[ScoredDocument, int], str] | None = None,
    ) -> None:
        self.source_name_fn = source_name_fn or self._default_source_name

    @staticmethod
    def _default_source_name(doc: ScoredDocument, idx: int) -> str:
        """默认 1-based 命名。"""
        return SimpleCite.DEFAULT_SOURCE_NAME.format(i=idx)

    def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[Citation]:
        return [
            Citation(
                chunk_id=d.chunk_id,
                dataset_id=d.dataset_id,
                source_name=self.source_name_fn(d, i),
                content=d.text,
                image_path=d.image_path,
                score=d.score,
            )
            for i, d in enumerate(docs, start=1)
        ]
