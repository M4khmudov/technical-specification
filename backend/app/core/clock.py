"""Время мастерской.

Сервер живёт по UTC, а мастерская — по своему часовому поясу (по умолчанию Москва).
«Сегодня» для дат поступлений, заданий и слов ассистента «на этой неделе», «в пятницу»
считается здесь, а не по системным часам: иначе с 00:00 до 03:00 МСК сегодняшнее
поступление было бы «в будущем», а «пятница» считалась бы от вчерашнего дня.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def _utcnow() -> datetime:  # подменяется в тестах
    return datetime.now(UTC)


def now() -> datetime:
    return _utcnow().astimezone(ZoneInfo(get_settings().timezone))


def today() -> date:
    return now().date()
