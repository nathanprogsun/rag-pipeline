"""``ArtifactWriter``: per-query trace 落盘。

供 UnifiedEvalRunner 在 ``artifact_dir`` 非空时记录每条 query 的完整 trace:
检索结果 (chunk_ids + scores + rerank) + 生成结果 (answer + contexts) + 失败栈。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ArtifactWriter:
    """per-query trace 落盘器。

    输出目录结构::

        artifact_dir/
        ├── per_query/
        │   ├── 0001.json
        │   ├── 0002.json
        │   └── ...
        └── summary.json     # 调用方写入, 本类不负责
    """

    def __init__(self, base_dir: Path) -> None:
        self.base = Path(base_dir)
        self.per_query_dir = self.base / "per_query"
        self.per_query_dir.mkdir(parents=True, exist_ok=True)

    def write_query(self, idx: int, trace: dict[str, Any]) -> Path:
        """写入单条 query trace。

        Args:
            idx: 0-based 序号, 文件名用 4 位 zero-pad。
            trace: 可序列化 dict (Pydantic model_json / dict 都可)。

        Returns:
            实际写入的文件路径。
        """
        path = self.per_query_dir / f"{idx:04d}.json"
        path.write_text(
            json.dumps(trace, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def write_summary(self, summary: dict[str, Any]) -> Path:
        """写入顶层 summary.json (由调用方传入 model_dump 后的 dict)。"""
        path = self.base / "summary.json"
        path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
