"""跨 unit/integration 共享的 fake 对象。

`FakeEmbeddings(Embeddings)` 在 4 处独立定义 (test_subgraph_live /
test_vector_retrieval / test_vector_store / test_pipeline_full), 行为都是
"返一个固定向量", 抽到本模块统一为 ``ConstantEmbeddings``。
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.embeddings import Embeddings


class ConstantEmbeddings(Embeddings):
    """返固定向量的 Embeddings stub。

    Args:
        vector: ``aembed_query`` 与 ``aembed_documents`` 都返这个向量。
            若需要每文本不同, 用 ``factory`` 接受 ``text -> vector`` 回调。
        factory: 可选 ``Callable[[str], list[float]]``; 给定时优先于 ``vector``。
    """

    def __init__(
        self,
        vector: list[float] | None = None,
        *,
        factory: Callable[[str], list[float]] | None = None,
    ) -> None:
        self._vector = vector
        self._factory = factory

    async def aembed_query(self, text: str) -> list[float]:
        if self._factory is not None:
            return self._factory(text)
        assert self._vector is not None, "ConstantEmbeddings: 须给 vector 或 factory"
        return self._vector

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._factory is not None:
            return [self._factory(t) for t in texts]
        assert self._vector is not None, "ConstantEmbeddings: 须给 vector 或 factory"
        return [self._vector for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        if self._factory is not None:
            return self._factory(text)
        assert self._vector is not None
        return self._vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._factory is not None:
            return [self._factory(t) for t in texts]
        assert self._vector is not None
        return [self._vector for _ in texts]
