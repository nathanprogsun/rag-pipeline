"""``documents`` 表的 ORM 模型。"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import BYTEA, UUID
from sqlalchemy.orm import Mapped, mapped_column

from rag.infra.pg.base import Base, SoftDeleteMixin, TimestampMixin


class DocumentModel(Base, TimestampMixin, SoftDeleteMixin):
    """``documents`` 表。

    一个 ``(dataset_id, filename)`` 对应一个 active row (active 部分唯一索引保证)。
    ``generation`` 在 re-ingest 时自增; 读路径取 ``MAX(generation)``。
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="documents_status_chk",
        ),
        Index("documents_dataset_id_idx", "dataset_id"),
        Index(
            "documents_status_idx",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    modality: Mapped[str] = mapped_column(Text, default="text")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(Text, default="pending")
    generation: Mapped[int] = mapped_column(Integer, default=1)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
