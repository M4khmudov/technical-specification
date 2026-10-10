"""Схемы поступлений и партий. Используются и HTTP API, и инструментами ассистента:
аргументы от модели валидируются теми же правилами, что и ввод из формы."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import BatchStatus, Currency, MovementKind

PositiveQty = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
NonNegativePrice = Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=6)]


class ReceiptLineIn(BaseModel):
    material_id: int
    quantity: PositiveQty
    price: NonNegativePrice  # за единицу, в валюте поставки
    currency: Currency
    comment: Annotated[str, Field(max_length=2000)] | None = None


class ReceiptIn(BaseModel):
    supplier_id: int
    received_on: date
    lines: Annotated[list[ReceiptLineIn], Field(min_length=1, max_length=200)]


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: int
    received_on: date
    material_id: int
    material_code: str
    material_name: str
    unit: str
    supplier_id: int
    supplier_name: str
    quantity: Decimal
    remaining_qty: Decimal
    reserved_qty: Decimal
    free_qty: Decimal
    price: Decimal
    currency: Currency
    exchange_rate: Decimal
    rate_date: date
    unit_cost_rub: Decimal
    status: BatchStatus
    comment: str | None


class ReceiptOut(BaseModel):
    id: int
    supplier_id: int
    received_on: date
    batches: list[BatchOut]


class BatchSort(StrEnum):
    NUMBER = "number"
    RECEIVED_ON = "received_on"
    MATERIAL = "material"
    SUPPLIER = "supplier"
    QUANTITY = "quantity"
    FREE_QTY = "free_qty"
    UNIT_COST_RUB = "unit_cost_rub"
    STATUS = "status"
    CURRENCY = "currency"


class BatchFilter(BaseModel):
    """Фильтры реестра партий (по колонкам)."""

    number: int | None = None
    material: Annotated[str, Field(max_length=100)] | None = None  # код или название
    material_id: int | None = None
    supplier: Annotated[str, Field(max_length=100)] | None = None
    status: BatchStatus | None = None
    currency: Currency | None = None
    received_from: date | None = None
    received_to: date | None = None


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: MovementKind
    quantity: Decimal
    job_id: int | None
    job_number: int | None = None
    note: str | None
    user_full_name: str | None = None
    created_at: datetime


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action: str
    user_full_name: str
    before: dict | None
    after: dict | None
    created_at: datetime


class BatchDetailOut(BatchOut):
    movements: list[MovementOut]
    audit: list[AuditOut]
