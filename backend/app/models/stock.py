"""Поступления, партии и движения по партиям."""

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Sequence, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.catalog import Material, Supplier
from app.models.enums import BatchStatus, Currency, MovementKind
from app.models.types import CreatedAt, Qty, Rate, UnitPrice, str_enum

# Автономер партии: отдельная последовательность, не зависящая от id
batch_number_seq = Sequence("batch_number_seq")


class Receipt(Base):
    """Поступление от поставщика. Каждая строка поступления — отдельная партия."""

    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    received_on: Mapped[date] = mapped_column(Date)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[CreatedAt]

    supplier: Mapped[Supplier] = relationship()
    batches: Mapped[list["Batch"]] = relationship(back_populates="receipt", order_by="Batch.id")


class Batch(Base):
    """Партия материала.

    quantity      — сколько пришло;
    remaining_qty — физический остаток (уменьшается при списании в производство);
    reserved_qty  — сколько из остатка зарезервировано под задания.
    Свободный остаток = remaining_qty - reserved_qty. Инварианты закреплены CHECK-ами,
    чтобы даже ошибка в коде не могла увести остаток в минус.
    """

    __tablename__ = "batches"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("remaining_qty >= 0", name="remaining_non_negative"),
        CheckConstraint("reserved_qty >= 0", name="reserved_non_negative"),
        CheckConstraint("reserved_qty <= remaining_qty", name="reserved_within_remaining"),
        CheckConstraint("exchange_rate > 0", name="rate_positive"),
        # FIFO: выборка принятых партий материала в порядке поступления
        Index("ix_batches_fifo", "material_id", "status", "received_on", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(
        batch_number_seq, server_default=batch_number_seq.next_value(), unique=True
    )
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    received_on: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Qty]
    remaining_qty: Mapped[Qty]
    reserved_qty: Mapped[Qty] = mapped_column(default=Decimal("0"), server_default="0")
    price: Mapped[UnitPrice]  # цена за единицу в валюте поставки
    currency: Mapped[Currency] = mapped_column(str_enum(Currency, "currency"))
    exchange_rate: Mapped[Rate]  # курс к рублю на дату поступления (для RUB = 1)
    unit_cost_rub: Mapped[UnitPrice]  # себестоимость единицы в рублях
    status: Mapped[BatchStatus] = mapped_column(
        str_enum(BatchStatus, "batch_status"), default=BatchStatus.PENDING
    )
    # Свободный текст — может содержать что угодно, в т.ч. «инструкции» для LLM
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]

    receipt: Mapped[Receipt] = relationship(back_populates="batches")
    material: Mapped[Material] = relationship()

    @property
    def free_qty(self) -> Decimal:
        return self.remaining_qty - self.reserved_qty


class BatchMovement(Base):
    """Журнал движений по партии (поступление, резерв, снятие резерва, списание)."""

    __tablename__ = "batch_movements"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("production_jobs.id"), index=True)
    kind: Mapped[MovementKind] = mapped_column(str_enum(MovementKind, "movement_kind"))
    quantity: Mapped[Qty]
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[CreatedAt]
