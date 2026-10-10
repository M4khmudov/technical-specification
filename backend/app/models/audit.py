from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.types import CreatedAt


class AuditLog(Base):
    """Журнал изменений: кто (ФИО), когда, что было, что стало.

    ФИО сохраняется снимком, чтобы запись не менялась при переименовании пользователя.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    user_full_name: Mapped[str] = mapped_column(String(255))
    entity_type: Mapped[str] = mapped_column(String(64))  # batch, job, ...
    entity_id: Mapped[int]
    action: Mapped[str] = mapped_column(String(64))  # create, accept, reserve, complete, ...
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[CreatedAt]
