from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.types import CreatedAt


class UserMemory(Base):
    """Факт о пользователе, который ассистент учитывает в новых диалогах
    («считать в евро», «мой цех — второй»). Принадлежит одному пользователю."""

    __tablename__ = "user_memories"
    __table_args__ = (UniqueConstraint("user_id", "normalized"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(String(300))
    normalized: Mapped[str] = mapped_column(String(300))  # для поиска дублей
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]
