from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import MarkingStatus
from app.models.types import CreatedAt, str_enum


class MarkingReport(Base):
    """Отчёт о выпуске для сервиса маркировки. Один на задание.

    idempotency_key не меняется между повторами — сервис по нему отбрасывает дубли.
    """

    __tablename__ = "marking_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("production_jobs.id"), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[MarkingStatus] = mapped_column(
        str_enum(MarkingStatus, "marking_status"), default=MarkingStatus.PENDING
    )
    attempts_count: Mapped[int] = mapped_column(default=0, server_default="0")
    # Последняя попытка текущего круга (None — marking_max_attempts). Ручной повтор
    # открывает новый круг: attempts_count + marking_max_attempts
    attempts_limit: Mapped[int | None]
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]

    attempts: Mapped[list["MarkingAttempt"]] = relationship(order_by="MarkingAttempt.id")


class MarkingAttempt(Base):
    """Журнал попыток отправки отчёта."""

    __tablename__ = "marking_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("marking_reports.id"), index=True)
    attempt_no: Mapped[int]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None]
    http_status: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]
