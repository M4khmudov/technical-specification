"""Курсы валют к рублю из внешнего сервиса курсов.

Сервис публикует курс только на рабочие дни. Для выходного/праздника берётся
последний предыдущий опубликованный курс.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from app.core.config import get_settings
from app.core.errors import ExternalServiceError
from app.models.enums import Currency

# Самые длинные новогодние каникулы — 10 дней подряд без курса; берём с запасом
MAX_LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class RateQuote:
    currency: Currency
    rate: Decimal
    rate_date: date  # дата, на которую курс опубликован (может быть раньше запрошенной)


class RatesProvider(Protocol):
    async def get_rate(self, currency: Currency, on: date) -> RateQuote: ...


class HttpRatesProvider:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(5.0))

    async def get_rate(self, currency: Currency, on: date) -> RateQuote:
        if currency == Currency.RUB:
            return RateQuote(currency, Decimal("1"), on)

        for back in range(MAX_LOOKBACK_DAYS + 1):
            day = on - timedelta(days=back)
            try:
                response = await self._client.get(
                    f"{self._base_url}/{currency.value}", params={"date": day.isoformat()}
                )
            except httpx.HTTPError as exc:
                raise ExternalServiceError(
                    "Сервис курсов валют недоступен, попробуйте позже"
                ) from exc
            if response.status_code == 404:
                continue  # выходной или праздник — смотрим предыдущий день
            if response.status_code != 200:
                raise ExternalServiceError(
                    f"Сервис курсов валют ответил ошибкой {response.status_code}, попробуйте позже"
                )
            try:
                rate = Decimal(str(response.json()["rate"]))
            except (KeyError, ValueError, InvalidOperation) as exc:
                raise ExternalServiceError("Сервис курсов валют вернул некорректный ответ") from exc
            if rate <= 0:
                raise ExternalServiceError("Сервис курсов валют вернул некорректный курс")
            return RateQuote(currency, rate, day)

        raise ExternalServiceError(
            f"Не найден курс {currency.value} на {on:%d.%m.%Y} и {MAX_LOOKBACK_DAYS} дней до неё"
        )


_provider: RatesProvider | None = None


def get_rates_provider() -> RatesProvider:
    """FastAPI-зависимость; в тестах подменяется через dependency_overrides."""
    global _provider
    if _provider is None:
        _provider = HttpRatesProvider(get_settings().rates_service_url)
    return _provider
