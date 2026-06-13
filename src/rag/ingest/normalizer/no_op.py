"""NoOpNormalizer: 恒等实现, FORBID 模式的显式替代。"""

from __future__ import annotations

from rag.ingest.normalizer.base import Normalizer
from rag.ingest.types import TextDoc


class NoOpNormalizer(Normalizer):
    """透传 TextDoc (text / format_text / images / meta), 不调 LLM, 不打日志。

    等价于 `StructureNormalizer(mode=FORBID)`, 但无需持有 chat_model。

    继承自异步基类 Normalizer, ``async def normalize`` 与基类契约一致。
    """

    async def normalize(self, doc: TextDoc) -> TextDoc:
        return doc.model_copy()  # frozen, model_copy 仍保持不可变语义
