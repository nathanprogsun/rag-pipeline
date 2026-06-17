"""ingest 单元测试共享 helper。"""

from __future__ import annotations

import asyncio

from rag.ingest.pipeline import IngestPipeline
from rag.ingest.types import IngestOutcome, IngestResult


def first_result(outcome: IngestOutcome) -> IngestResult:
    """单条目 ``IngestOutcome`` 取 ``IngestResult``。"""
    assert len(outcome.items) == 1, outcome
    return outcome.items[0]


async def ingest_targets(pipeline: IngestPipeline, targets: list[str]) -> IngestOutcome:
    return await pipeline.ingest_many(targets)


def run_ingest(pipeline: IngestPipeline, target: str | list[str]) -> IngestResult:
    """同步运行 ``ingest_many``; 单文件时返回唯一 ``IngestResult``。"""
    targets = [target] if isinstance(target, str) else target
    outcome = asyncio.run(ingest_targets(pipeline, targets))
    return first_result(outcome)
