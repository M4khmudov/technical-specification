"""Схемы производственных заданий (общие для API и инструментов ассистента)."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

from app.models.enums import JobStatus

NonNegativeQty = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=3)]


class JobCreateIn(BaseModel):
    recipe_id: int
    quantity: Annotated[int, Field(gt=0, le=100_000)]  # штук изделий
    planned_on: date
    comment: Annotated[str, Field(max_length=2000)] | None = None


class ActualIn(BaseModel):
    material_id: int
    actual_qty: NonNegativeQty


class JobCompleteIn(BaseModel):
    """Фактический расход. Материал, не указанный в списке, считается израсходованным по норме."""

    actuals: Annotated[list[ActualIn], Field(max_length=100)] = []


class Light(StrEnum):
    """«Светофор» компонента: всё в резерве / часть / ничего."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class JobMaterialOut(BaseModel):
    material_id: int
    material_code: str
    material_name: str
    unit: str
    required_qty: Decimal  # по норме
    reserved_qty: Decimal
    deficit_qty: Decimal
    actual_qty: Decimal | None  # факт (после завершения)
    light: Light | None  # только пока задание не завершено и не отменено


class ReservationOut(BaseModel):
    batch_id: int
    batch_number: int
    material_id: int
    received_on: date
    quantity: Decimal
    unit_cost_rub: Decimal


class JobOut(BaseModel):
    id: int
    number: int
    recipe_id: int
    recipe_name: str
    quantity: int
    planned_on: date | None
    status: JobStatus
    status_label: str
    comment: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    has_deficit: bool
    materials: list[JobMaterialOut]
    reservations: list[ReservationOut]
    # Себестоимость: до завершения — оценка по зарезервированным партиям (без дефицита),
    # после — по фактически списанным партиям
    cost_rub: Decimal
    cost_is_final: bool
    cost_covers_all: bool  # false, если часть материалов не зарезервирована


class JobListItem(BaseModel):
    id: int
    number: int
    recipe_name: str
    quantity: int
    planned_on: date | None
    status: JobStatus
    status_label: str
    has_deficit: bool
