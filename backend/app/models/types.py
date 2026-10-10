"""Общие типы колонок. Деньги и количества — только Numeric/Decimal, никакого float."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from sqlalchemy import DateTime, Enum, Numeric, func
from sqlalchemy.orm import mapped_column

# Количество материала: до 3 знаков (граммы в кг, мл в л)
Qty = Annotated[Decimal, mapped_column(Numeric(14, 3))]
# Сумма в валюте / рублях
Money = Annotated[Decimal, mapped_column(Numeric(14, 2))]
# Цена/себестоимость за единицу: нужна точность выше копейки (цена за грамм)
UnitPrice = Annotated[Decimal, mapped_column(Numeric(16, 6))]
# Курс валюты к рублю
Rate = Annotated[Decimal, mapped_column(Numeric(12, 6))]

CreatedAt = Annotated[
    datetime, mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
]


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Enum, хранимый строкой с CHECK-ограничением на допустимые значения."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )
