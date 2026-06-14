"""Audit tap for per-request JSONL audit logging.

Per `.agents/design/2026-06-14-cross-task-contracts.md` task 15:
- ``AuditRecord``: per-request structured record (Pydantic).
- ``AuditTap``: append records as JSONL (one object per line).

Used by ``PipelineOrchestrator`` when ``req.audit=True``:
- After ``ainvoke`` returns, build ``AuditRecord.from_search_result(req, result)``
- Pass to ``AuditTap.record(...)`` for JSONL append.
- EvalRunner / debug channels read the JSONL stream offline.

NDJSON schema:
    {"ts":"...","request_id":"...","query":"...","dataset_ids":[...],"hit_count":N,
     "stage_hit_counts":{...},"citation_count":N,"warnings":[...],"failed_dataset_ids":[...]}
"""

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
    """Per-request audit record. Serialized to NDJSON via ``model_dump_json``.

    Captures provenance for offline analysis: which query was asked,
    how many hits each stage produced, whether the LLM response contained
    citations, what warnings accumulated. Does NOT include the full hit
    contents (use ``_intermediate_hits`` dump separately if needed).
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    query: str
    dataset_ids: list[uuid.UUID]
    image_urls: list[str] = Field(default_factory=list)
    retrieval_top_k: int
    retrieval_use_rerank: bool
    parent_doc_window: int
    response: str
    citation_count: int
    hit_count: int  # post-filter, post-cite
    intermediate_hits_count: int  # post-rerank, post-filter (full chain end)
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
        """Build record from SearchRequest + SearchResult.

        Reads ``_intermediate_hits`` from result (Contract 6: PrivateAttr,
        programmatic access). Captures retrieval / generation flags from
        request sub-configs.
        """
        intermediate_count = len(result._intermediate_hits)
        return cls(
            request_id=request_id or cls.model_fields["request_id"].default_factory(),
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
    """Append-only NDJSON writer for AuditRecord.

    Args:
        file_path: Path to JSONL file (created if missing, appended otherwise).
        sample_rate: 0.0-1.0 probability of recording each request
            (1.0 = record all; useful for high-volume dev).
        sync: If True, write synchronously (suitable for tests).
            If False (default), writes are buffered and flushed on close.
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
        """Append a single record. Returns True if recorded, False if sampled out.

        Never raises on write errors (logs warning instead). Audit must
        never break the orchestrator's response flow.
        """
        if self._closed:
            logger.warning("AuditTap closed, skipping record for %s", record.request_id)
            return False
        if not self._should_record():
            return False
        try:
            line = record.model_dump_json()
            # Ensure parent directory exists (idempotent)
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.file_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                if self.sync:
                    f.flush()
        except OSError as e:
            logger.warning(
                "AuditTap write failed for %s: %r", record.request_id, e
            )
            return False
        return True

    def close(self) -> None:
        self._closed = True


# ---------- JSONL utilities ----------


def read_jsonl_records(file_path: Path) -> list[dict[str, Any]]:
    """Read all JSONL records from a file (offline analysis).

    Skips malformed lines with a warning. Returns list of dicts.
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