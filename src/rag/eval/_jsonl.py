"""JSONL 数据集加载工具。

``runner.py`` 与 ``ragas_runner.py`` 各自复制了一份相同的 ``_load_jsonl`` 实现,
抽到此模块统一维护。
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


def load_jsonl[T: BaseModel](path: Path, record_cls: type[T]) -> list[T]:
    """加载 JSONL 文件, 跳过格式错误行并记录 warning。

    Args:
        path: JSONL 文件路径。
        record_cls: Pydantic 记录模型 (如 ``EvalRecord`` / ``RagasRecord``)。

    Returns:
        解析成功的 ``T`` 实例列表。
    """
    records: list[T] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(record_cls.model_validate_json(line))
            except Exception as e:
                logger.warning(
                    "Skipping malformed %s record: %r", record_cls.__name__, e
                )
    return records
