"""请求级 JSONL 审计日志的写入器。"""

from __future__ import annotations

import json
import logging
import random
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rag.domain.search import SearchRequest, SearchResult

logger = logging.getLogger(__name__)


# ---------- AuditRecord ----------


class AuditRecord(BaseModel):
    """单次请求的审计记录。通过 ``model_dump_json`` 序列化为 NDJSON。

    用于离线追溯：记录查询内容、各阶段命中数、LLM 响应是否含引用、
    累计的告警等。不包含完整命中内容。
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    query: str
    dataset_ids: list[uuid.UUID]
    image_urls: list[str] = Field(default_factory=list)
    retrieval_top_k: int
    retrieval_use_rerank: bool
    parent_doc_window: int
    response: str
    citation_count: int
    hit_count: int  # 过滤后、引用检查后的命中数
    intermediate_hits_count: int  # 重排后、过滤后的中间命中数（完整链路末端）
    warnings: list[str]
    failed_dataset_ids: list[uuid.UUID]
    errors: list[str] = Field(default_factory=list)

    @classmethod
    def from_search_result(
        cls,
        req: SearchRequest,
        result: SearchResult,
        *,
        request_id: str | None = None,
    ) -> AuditRecord:
        """从 ``SearchRequest`` 与 ``SearchResult`` 构造审计记录。

        读取 ``result._intermediate_hits`` 作为中间命中数; 从 ``SearchRequest``
        的子配置中提取检索 / 生成相关标志位。
        """
        intermediate_count = len(result._intermediate_hits)
        rid = request_id if request_id is not None else str(uuid.uuid4())
        return cls(
            request_id=rid,
            query=req.query,
            dataset_ids=list(req.dataset_ids),
            image_urls=list(req.image_urls),
            retrieval_top_k=req.retrieval.top_k,
            retrieval_use_rerank=req.retrieval.use_rerank,
            parent_doc_window=req.context.parent_doc_window,
            response=result.response,
            citation_count=len(result.citations),
            hit_count=len(result.citations),
            intermediate_hits_count=intermediate_count,
            warnings=list(result.warnings),
            failed_dataset_ids=list(result.failed_dataset_ids),
        )


# ---------- AuditTap ----------


class AuditTap:
    """``AuditRecord`` 的追加式 NDJSON 写入器。

    Args:
        file_path: JSONL 文件路径（不存在则创建, 存在则追加）。
        sample_rate: 每条请求被记录的概率, 取值范围 ``[0.0, 1.0]``
            （``1.0`` 表示全量记录, 适合高流量调试场景）。
        sync: ``True`` 时同步写入（适合测试）; ``False``（默认）时
            写入会被缓冲, 仅在 ``close()`` 时落盘。
    """

    def __init__(
        self,
        file_path: Path,
        *,
        sample_rate: float = 1.0,
        sync: bool = False,
    ) -> None:
        if not 0.0 <= sample_rate <= 1.0:
            msg = f"sample_rate must be in [0, 1], got {sample_rate}"
            raise ValueError(msg)
        self.file_path = Path(file_path)
        self.sample_rate = sample_rate
        self.sync = sync
        self._closed = False

    def _should_record(self) -> bool:
        return random.random() < self.sample_rate  # noqa: S311

    async def record(self, record: AuditRecord) -> bool:
        """追加单条记录。被采样命中时返回 ``True``, 未命中返回 ``False``。

        写入出错时仅记录告警日志而不抛出异常, 避免审计失败打断主流程。
        """
        if self._closed:
            logger.warning("AuditTap closed, skipping record for %s", record.request_id)
            return False
        if not self._should_record():
            return False
        try:
            line = record.model_dump_json()
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.file_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                if self.sync:
                    f.flush()
        except OSError as e:
            logger.warning("AuditTap write failed for %s: %r", record.request_id, e)
            return False
        return True

    def close(self) -> None:
        self._closed = True


# ---------- JSONL utilities ----------


def read_jsonl_records(file_path: Path) -> list[dict[str, Any]]:
    """从 JSONL 文件读取全部记录（用于离线分析）。

    跳过格式错误的行并记录告警。返回字典列表。
    """
    records: list[dict[str, Any]] = []
    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("Skipping malformed JSONL line: %r", e)
    return records
