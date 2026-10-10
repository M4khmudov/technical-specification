"""Производственные задания, потребность в материалах и резервы."""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Sequence,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.catalog import Material, Recipe
from app.models.enums import JobStatus
from app.models.stock import Batch
from app.models.types import CreatedAt, Qty, str_enum

job_number_seq = Sequence("job_number_seq")


class ProductionJob(Base):
    __tablename__ = "production_jobs"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(
        job_number_seq, server_default=job_number_seq.next_value(), unique=True
    )
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    quantity: Mapped[int]  # штук изделий
    planned_on: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "job_status"), default=JobStatus.DRAFT, index=True
    )
    comment: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[CreatedAt]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recipe: Mapped[Recipe] = relationship()
    materials: Mapped[list["JobMaterial"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobMaterial.id"
    )
    reservations: Mapped[list["Reservation"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Reservation.id"
    )


class JobMaterial(Base):
    """Потребность задания в материале: по норме и фактически (вводится при завершении)."""

    __tablename__ = "job_materials"
    __table_args__ = (
        UniqueConstraint("job_id", "material_id"),
        CheckConstraint("required_qty > 0", name="required_positive"),
        CheckConstraint("actual_qty IS NULL OR actual_qty >= 0", name="actual_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("production_jobs.id", ondelete="CASCADE"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    required_qty: Mapped[Qty]
    actual_qty: Mapped[Qty | None]

    job: Mapped[ProductionJob] = relationship(back_populates="materials")
    material: Mapped[Material] = relationship()


class Reservation(Base):
    """Резерв конкретной партии под задание. Сумма резервов партии = batches.reserved_qty."""

    __tablename__ = "reservations"
    __table_args__ = (
        UniqueConstraint("job_id", "batch_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("production_jobs.id", ondelete="CASCADE"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    quantity: Mapped[Qty]
    created_at: Mapped[CreatedAt]

    job: Mapped[ProductionJob] = relationship(back_populates="reservations")
    batch: Mapped[Batch] = relationship()
