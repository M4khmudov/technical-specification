"""Мок сервиса курсов валют (по мотивам ЦБ РФ).

Курс публикуется только в рабочие дни: в выходные, праздники и будущие даты — 404.
Правило «в выходной берётся последний предыдущий курс» реализует клиент на бэкенде.
Курсы детерминированы (зависят только от даты), чтобы демо и тесты были воспроизводимы.
Значение отдаётся строкой, чтобы не терять точность на float.
"""

import math
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/rates", tags=["rates"])

BASE = {"USD": Decimal("92.5"), "EUR": Decimal("100.3")}
# Нерабочие праздничные дни (месяц, день)
HOLIDAYS = {(1, d) for d in range(1, 9)} | {(2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)}


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and (d.month, d.day) not in HOLIDAYS


def rate_for(currency: str, d: date) -> Decimal:
    # Плавное колебание ±3% вокруг базового курса
    wave = Decimal(str(round(math.sin(d.toordinal() / 9) * 0.03, 6)))
    return (BASE[currency] * (1 + wave)).quantize(Decimal("0.0001"))


@router.get("/{currency}")
async def get_rate(currency: str, on: date = Query(alias="date")) -> dict[str, str]:
    currency = currency.upper()
    if currency not in BASE:
        raise HTTPException(404, f"Неизвестная валюта {currency}")
    if on > date.today() or not is_business_day(on):
        raise HTTPException(404, f"Курс на {on.isoformat()} не опубликован")
    return {"currency": currency, "date": on.isoformat(), "rate": str(rate_for(currency, on))}
