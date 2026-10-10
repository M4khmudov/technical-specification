"""Форматирование чисел для сообщений пользователю: «2,5 кг», «1 200 шт», «1 250,00 ₽»."""

from decimal import Decimal

NBSP = " "


def _format(text: str) -> str:
    integer, _, fraction = text.partition(".")
    sign, digits = ("-", integer[1:]) if integer.startswith("-") else ("", integer)
    groups = []
    while digits:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    return sign + NBSP.join(groups or ["0"]) + ("," + fraction if fraction else "")


def fmt_qty(value: Decimal, unit: str | None = None) -> str:
    # normalize() убирает хвостовые нули, формат «f» — экспоненту (1E+1 → 10)
    result = _format(format(value.normalize(), "f"))
    return f"{result}{NBSP}{unit}" if unit else result


def fmt_money(value: Decimal) -> str:
    return f"{_format(format(value.quantize(Decimal('0.01')), 'f'))}{NBSP}₽"
