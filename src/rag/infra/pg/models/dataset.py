import uuid

from sqlalchemy import Float, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from rag.infra.pg.base import Base, SoftDeleteMixin, TimestampMixin


class DatasetModel(Base, TimestampMixin, SoftDeleteMixin):
    """``datasets`` 表的 ORM 模型。"""

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    embed_model: Mapped[str] = mapped_column(Text, nullable=False)
    embed_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, default=1000)
    rerank_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    rrf_k: Mapped[int] = mapped_column(Integer, default=60)
    vector_weight: Mapped[float] = mapped_column(Float, default=0.7)
    fulltext_weight: Mapped[float] = mapped_column(Float, default=0.3)
    query_select_alpha: Mapped[float] = mapped_column(Float, default=0.3)
    prompt_template: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
