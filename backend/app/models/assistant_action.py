from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import ActionStatus
from app.models.types import CreatedAt, str_enum


class AssistantAction(Base):
    """Действие, предложенное ассистентом. Данные меняются только после того, как
    пользователь нажмёт «Подтвердить» в интерфейсе."""

    __tablename__ = "assistant_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(64))  # create_job
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)  # проверенные аргументы
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB)  # что будет сделано — для показа
    status: Mapped[ActionStatus] = mapped_column(
        str_enum(ActionStatus, "action_status"), default=ActionStatus.PENDING
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
