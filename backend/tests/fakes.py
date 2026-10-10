from datetime import date
from decimal import Decimal

from app.core.errors import ExternalServiceError
from app.models.enums import Currency
from app.services.rates import RateQuote


class FakeRates:
    """Подмена сервиса курсов: фиксированные курсы, без сети."""

    def __init__(self, rates: dict[Currency, str] | None = None, *, fail: bool = False):
        self.rates = {Currency.RUB: Decimal("1")} | {
            c: Decimal(v) for c, v in (rates or {Currency.USD: "90", Currency.EUR: "100"}).items()
        }
        self.fail = fail
        self.calls: list[tuple[Currency, date]] = []

    async def get_rate(self, currency: Currency, on: date) -> RateQuote:
        self.calls.append((currency, on))
        if self.fail:
            raise ExternalServiceError("Сервис курсов валют недоступен, попробуйте позже")
        return RateQuote(currency, self.rates[currency], on)
