import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from rag.infra.pg.base import Base, SoftDeleteMixin, TimestampMixin


class ChunkModel(Base, TimestampMixin, SoftDeleteMixin):
    """``chunks`` 表的 ORM 模型。"""

    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("modality IN ('text', 'image_caption')", name="modality_chk"),
        CheckConstraint(
            "(modality = 'image_caption' AND image_path IS NOT NULL) OR (modality = 'text')",
            name="image_path_required",
        ),
        Index("chunks_dataset_id_idx", "dataset_id"),
        Index("chunks_modality_idx", "modality"),
        Index(
            "chunks_document_chunk_idx_uniq",
            "document_id",
            "chunk_index",
            postgresql_where=text("deleted_at IS NULL"),
            unique=True,
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
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    modality: Mapped[str] = mapped_column(Text, default="text")
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_title: Mapped[str] = mapped_column(Text, default="")
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding = mapped_column(Vector(1536), nullable=False)
    ts_tokens = mapped_column(TSVECTOR, nullable=True)
