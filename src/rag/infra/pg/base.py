from datetime import datetime
from typing import ClassVar

from sqlalchemy import DateTime, event, func
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    ORMExecuteState,
    Session,
    mapped_column,
    with_loader_criteria,
)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class SoftDeleteMixin:
    """Provides deleted_at column; NULL means active row."""

    __mapper_args__: ClassVar[dict[str, object]] = {"confirm_deleted_rows": False}

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )


@event.listens_for(Session, "do_orm_execute")
def _apply_soft_delete_filter(execute_state: ORMExecuteState) -> None:
    """Exclude soft-deleted rows from SELECT queries.

    Opt out per query with `.execution_options(include_deleted=True)`.
    """
    if (
        execute_state.is_select
        and not execute_state.is_column_load
        and not execute_state.is_relationship_load
        and not execute_state.execution_options.get("include_deleted", False)
    ):
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                SoftDeleteMixin,
                lambda cls: cls.deleted_at.is_(None),
                include_aliases=True,
            )
        )
