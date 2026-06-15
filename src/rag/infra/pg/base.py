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
    """提供 ``deleted_at`` 列; ``NULL`` 表示有效行。"""

    __mapper_args__: ClassVar[dict[str, object]] = {"confirm_deleted_rows": False}

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )


@event.listens_for(Session, "do_orm_execute")
def _apply_soft_delete_filter(execute_state: ORMExecuteState) -> None:
    """从 SELECT 查询中过滤掉软删行。

    单条查询可通过 ``.execution_options(include_deleted=True)`` 关闭该过滤。
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
