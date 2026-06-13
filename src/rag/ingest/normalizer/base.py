"""Normalizer 协议: TextDoc → TextDoc (text + structure + images)。"""

from __future__ import annotations

from rag.ingest.types import TextDoc


class Normalizer:
    """Normalizer 异步基类, 子类覆盖 ``async def normalize()``。

    异步契约: Normalizer 可能在内部触发 I/O (LLM 调用 / HTTP 上传)，
    因此 ``normalize`` 必须是 coroutine, 让上游 ``IngestPipeline._process``
    用 ``await`` 透传避免阻塞 event loop。
    """

    async def normalize(self, doc: TextDoc) -> TextDoc:
        raise NotImplementedError
