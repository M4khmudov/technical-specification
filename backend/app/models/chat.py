"""Диалоги с ассистентом."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import FileOrigin, MessageRole, MessageStatus
from app.models.types import CreatedAt, str_enum


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )


class ChatMessage(Base):
    """Реплика диалога. У ответа ассистента — модель, вызванные инструменты и статус:
    отвечает / готово / остановлено пользователем / оборван / ошибка."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_conversation", "conversation_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[MessageRole] = mapped_column(str_enum(MessageRole, "message_role"))
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[MessageStatus] = mapped_column(
        str_enum(MessageStatus, "message_status"), default=MessageStatus.COMPLETE
    )
    model: Mapped[str | None] = mapped_column(String(100))
    fallback_from: Mapped[str | None] = mapped_column(String(100))
    # [{"name", "arguments", "ok", "summary"}] — какие инструменты вызваны и с чем
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    # [{"code", "logs", "error"}] — код, выполненный в Code Interpreter (раскрывается под ответом)
    code_runs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    created_at: Mapped[CreatedAt]
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ChatFile(Base):
    """Файл диалога: загруженный пользователем (Excel/CSV для анализа) или созданный
    кодом модели в Code Interpreter (график, таблица). Хранится у нас: контейнер
    провайдера временный."""

    __tablename__ = "chat_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    origin: Mapped[FileOrigin] = mapped_column(str_enum(FileOrigin, "file_origin"))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int]
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[CreatedAt]
