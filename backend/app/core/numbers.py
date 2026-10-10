"""Числа из текста ответа: «1 250,5», «1250.50», «5,467», неразрывные пробелы."""

import re
from decimal import Decimal, InvalidOperation

NUMBER_RE = re.compile(r"(?<![\d.,])-?\d{1,3}(?:[\s  ]\d{3})+(?:[.,]\d+)?|-?\d+(?:[.,]\d+)?")


def numbers_in(text: str) -> list[Decimal]:
    """Все числа из текста: «1 250,5», «1250.50», «5,467», «29.124»."""
    result = []
    for raw in NUMBER_RE.findall(text):
        cleaned = re.sub(r"[\s  ]", "", raw).replace(",", ".")
        try:
            result.append(Decimal(cleaned))
        except InvalidOperation:
            continue
    return result


def close(a: Decimal, b: Decimal, rel: Decimal = Decimal("0.005"), abs_: Decimal = Decimal("0.01")):
    return abs(a - b) <= max(abs_, abs(b) * rel)
