"""Normalizer 协议: TextDoc → TextDoc (text + structure)。"""

from __future__ import annotations

from rag.ingest.types import TextDoc


class Normalizer:
    """Normalizer 异步基类, 子类覆盖 ``async def normalize()``。

    Args:
        doc: 输入文档。
    """

    async def normalize(self, doc: TextDoc) -> TextDoc:
        raise NotImplementedError
