"""Клиент сервиса курсов: правило «в выходной — последний предыдущий курс»."""

from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.core.errors import ExternalServiceError
from app.models.enums import Currency
from app.services.rates import HttpRatesProvider

# Как настоящий сервис: курс есть только в рабочие дни
PUBLISHED = {
    date(2025, 12, 31): "95.1234",  # среда, последний рабочий день года
    date(2026, 10, 9): "92.0001",  # пятница
    date(2026, 10, 12): "92.5000",  # понедельник
}


def provider(handler) -> HttpRatesProvider:
    return HttpRatesProvider(
        "http://rates", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def published_rates(request: httpx.Request) -> httpx.Response:
    on = date.fromisoformat(request.url.params["date"])
    if on not in PUBLISHED:
        return httpx.Response(404, json={"detail": "not published"})
    return httpx.Response(200, json={"rate": PUBLISHED[on]})


async def test_rub_rate_is_one_without_request():
    def fail(_):
        raise AssertionError("для рубля сервис курсов не вызывается")

    quote = await provider(fail).get_rate(Currency.RUB, date(2026, 10, 10))
    assert quote.rate == Decimal("1")


async def test_business_day_uses_own_rate():
    quote = await provider(published_rates).get_rate(Currency.USD, date(2026, 10, 12))
    assert quote.rate == Decimal("92.5000")
    assert quote.rate_date == date(2026, 10, 12)


@pytest.mark.parametrize("weekend", [date(2026, 10, 10), date(2026, 10, 11)])
async def test_weekend_uses_previous_friday_rate(weekend):
    quote = await provider(published_rates).get_rate(Currency.USD, weekend)
    assert quote.rate == Decimal("92.0001")
    assert quote.rate_date == date(2026, 10, 9)


async def test_new_year_holidays_fall_back_to_last_year_rate():
    quote = await provider(published_rates).get_rate(Currency.EUR, date(2026, 1, 8))
    assert quote.rate_date == date(2025, 12, 31)


async def test_rate_is_parsed_as_exact_decimal():
    quote = await provider(published_rates).get_rate(Currency.USD, date(2026, 10, 9))
    assert isinstance(quote.rate, Decimal)
    assert str(quote.rate) == "92.0001"


async def test_server_error_becomes_user_message():
    with pytest.raises(ExternalServiceError, match="ответил ошибкой 500"):
        await provider(lambda _: httpx.Response(500)).get_rate(Currency.USD, date(2026, 10, 9))


async def test_network_error_becomes_user_message():
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ExternalServiceError, match="недоступен"):
        await provider(down).get_rate(Currency.USD, date(2026, 10, 9))


async def test_no_rate_for_too_long_is_an_error():
    with pytest.raises(ExternalServiceError, match="Не найден курс"):
        await provider(lambda _: httpx.Response(404)).get_rate(Currency.USD, date(2026, 10, 9))
