"""Персональные данные: псевдонимы до отправки в LLM и обратная замена для пользователя."""

import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.assistant.pii import Pseudonymizer
from app.llm.fake import ScriptedProvider
from app.llm.registry import get_llm
from app.llm.router import LLMRouter, Route
from app.llm.types import (
    AssistantMessage,
    Completed,
    TextDelta,
    ToolCall,
    ToolCallRequested,
    ToolResult,
    UserMessage,
)
from app.main import app
from app.models import ChatMessage, Supplier, User
from app.models.enums import Currency, Role
from app.schemas.stock import ReceiptIn, ReceiptLineIn
from app.services import batches
from tests.factories import make_material
from tests.fakes import FakeRates
from tests.test_chat import parse_sse

PII = Pseudonymizer(
    {1: "Иванова Мария Петровна", 2: "Петров Иван Сергеевич", 3: "Кузнецова Анна Игоревна"},
    {
        1: "ООО «Восковой двор»",
        3: "ИП Смирнова Ольга Викторовна",
        4: "Global Wicks Ltd",
        5: "Aroma Trade GmbH",
    },
)


# ---------------------------------------------------------------- один субъект — один псевдоним


@pytest.mark.parametrize(
    ("text", "pseudonym"),
    [
        # из ТЗ
        ("ООО Восковой двор", "Поставщик-1"),
        ("Восковой двор, ООО", "Поставщик-1"),
        ("Петров Иван", "Сотрудник-2"),
        ("Петрову И.", "Сотрудник-2"),
        # другие написания
        ("ООО «Восковой двор»", "Поставщик-1"),
        ("«Восковой двор»", "Поставщик-1"),
        ("Воскового двора", "Поставщик-1"),
        ("Петров Иван Сергеевич", "Сотрудник-2"),
        ("Петров И. С.", "Сотрудник-2"),
        ("Петров И.С.", "Сотрудник-2"),
        ("И. С. Петров", "Сотрудник-2"),
        ("Иван Петров", "Сотрудник-2"),
        ("Петровым", "Сотрудник-2"),
        ("Иванова Мария Петровна", "Сотрудник-1"),
        ("Марии Петровны", "Сотрудник-1"),
        ("Кузнецовой Анне", "Сотрудник-3"),
        ("ИП Смирнова О. В.", "Поставщик-3"),
        ("Смирновой О. В.", "Поставщик-3"),
        ("Global Wicks Ltd", "Поставщик-4"),
        ("Global Wicks", "Поставщик-4"),
        ("aroma trade gmbh", "Поставщик-5"),
    ],
)
def test_every_spelling_gets_the_same_pseudonym(text, pseudonym):
    assert PII.mask(text) == pseudonym


@pytest.mark.parametrize(
    "text",
    [
        "Заказ для магазина на Петровке",  # улица, а не Петров
        "воск соевый лежит у двора склада",
        "Иван — частое имя",
        "Сергеевич",
        "Партия №12, 25 кг",
        "Поставщик-1 уже псевдоним",
    ],
)
def test_ordinary_text_is_not_masked(text):
    assert PII.mask(text) == text


def test_sentence_with_several_people():
    text = "Партию от Воскового двора принял Петров И. С., счёт на Смирнову О. В."
    assert PII.mask(text) == "Партию от Поставщик-1 принял Сотрудник-2, счёт на Поставщик-3"


def test_unmask_restores_names_and_keeps_unknown_pseudonyms():
    text = "Сотрудник-2 принял партию от Поставщик-1; Поставщик-99 неизвестен"
    assert PII.unmask(text) == (
        "Петров Иван Сергеевич принял партию от ООО «Восковой двор»; Поставщик-99 неизвестен"
    )


def test_json_tool_output_is_masked_and_stays_valid():
    output = json.dumps(
        {"supplier": "ООО «Восковой двор»", "by": "Петров Иван Сергеевич"}, ensure_ascii=False
    )
    masked = json.loads(PII.mask(output))
    assert masked == {"supplier": "Поставщик-1", "by": "Сотрудник-2"}


# ---------------------------------------------------------------- обратная замена в потоке


ANSWER = "Принял Сотрудник-2, поставщик Поставщик-1. Поставщик-3 тоже. Итого 12 кг."
EXPECTED = (
    "Принял Петров Иван Сергеевич, поставщик ООО «Восковой двор». "
    "ИП Смирнова Ольга Викторовна тоже. Итого 12 кг."
)


@pytest.mark.parametrize("cut", range(1, len(ANSWER)))
def test_stream_unmask_with_pseudonym_split_anywhere(cut):
    unmasker = PII.stream_unmasker()
    shown = unmasker.feed(ANSWER[:cut]) + unmasker.feed(ANSWER[cut:]) + unmasker.flush()
    assert shown == EXPECTED


def test_stream_unmask_char_by_char():
    unmasker = PII.stream_unmasker()
    shown = "".join(unmasker.feed(ch) for ch in ANSWER) + unmasker.flush()
    assert shown == EXPECTED


def test_pseudonym_is_never_shown_half_replaced():
    unmasker = PII.stream_unmasker()
    first = unmasker.feed("Принял Сотрудн")
    assert "Сотрудн" not in first  # придержано
    assert unmasker.feed("ик-2.") == "Петров Иван Сергеевич."
    assert first == "Принял "


# ---------------------------------------------------------------- сквозной сценарий через чат


class Recorder(ScriptedProvider):
    """Запоминает всё, что ушло в модель, — чтобы проверить отсутствие имён."""

    def sent_texts(self) -> str:
        parts = []
        for _, request in self.requests:
            parts.append(request.instructions)
            for item in request.items:
                if isinstance(item, UserMessage | AssistantMessage):
                    parts.append(item.text)
                elif isinstance(item, ToolResult):
                    parts.append(item.output)
                elif isinstance(item, ToolCall):
                    parts.append(item.arguments)
        return "\n".join(parts)


@pytest.fixture
async def people(session_factory):
    """Сотрудник и поставщик с уникальными в тестовой базе именами + партия с комментарием."""
    async with session_factory() as s:
        employee = User(
            login=f"pii-{uuid4().hex[:6]}",
            full_name="Лебедев Олег Ильич",
            password_hash="x",
            role=Role.MANAGER,
        )
        # Имя поставщика уникально в БД: если тест уже создавал его в этом прогоне — берём его
        supplier = await s.scalar(select(Supplier).where(Supplier.name == "ООО «Медовый луг»"))
        if supplier is None:
            supplier = Supplier(name="ООО «Медовый луг»")
        s.add_all([employee, supplier])
        await s.commit()
        material = await make_material(s, code=f"PII-{uuid4().hex[:6]}")
        await s.commit()
        receipt = await batches.create_receipt(
            s,
            employee,
            ReceiptIn(
                supplier_id=supplier.id,
                received_on=date(2026, 10, 1),
                lines=[
                    ReceiptLineIn(
                        material_id=material.id,
                        quantity=Decimal("5"),
                        price=Decimal("100"),
                        currency=Currency.RUB,
                        comment="Принял Лебедев О. И., Медовый луг, ООО обещал скидку",
                    )
                ],
            ),
            FakeRates(),
        )
        return employee, supplier, receipt.batches[0]


async def test_llm_never_sees_names_and_user_sees_them(api, people, session_factory):
    client, *_ = api
    employee, supplier, batch = people
    recorder = Recorder()
    recorder.push(
        ToolCallRequested(
            ToolCall("c1", "get_batch_history", json.dumps({"batch_number": batch.number}))
        ),
        Completed(model="m", finish="tool_calls"),
    )
    # Модель отвечает псевдонимами, причём разрезает их на куски
    recorder.push(
        TextDelta("Партию принял Сотруд"),
        TextDelta(f"ник-{employee.id}, поставщик Поставщ"),
        TextDelta(f"ик-{supplier.id}."),
        Completed(model="m"),
    )
    app.dependency_overrides[get_llm] = lambda: LLMRouter(
        [Route(recorder, "m")], event_timeout=2, total_timeout=10
    )
    try:
        conversation_id = (await client.post("/api/chat/conversations", json={})).json()["id"]
        question = f"Кто принял партию №{batch.number}? Это был Лебедеву О. И. от Медового луга?"
        response = await client.post(
            f"/api/chat/conversations/{conversation_id}/messages", json={"text": question}
        )
    finally:
        app.dependency_overrides.pop(get_llm, None)

    sent = recorder.sent_texts()
    for fragment in ("Лебедев", "Олег", "Медов", "луг"):
        assert fragment not in sent, f"в модель ушло «{fragment}»"
    assert f"Сотрудник-{employee.id}" in sent  # из вопроса, комментария и истории партии
    assert f"Поставщик-{supplier.id}" in sent

    events = parse_sse(response.text)
    shown = "".join(d["text"] for e, d in events if e == "delta")
    expected = f"Партию принял Лебедев Олег Ильич, поставщик {supplier.name}."
    assert shown == expected
    async with session_factory() as s:
        saved = await s.get(ChatMessage, events[-1][1]["message_id"])
        assert saved.content == expected


async def test_tool_arguments_from_model_are_unmasked_before_saving(api, people, session_factory):
    """Модель пишет «ответственный Сотрудник-N» — в задание попадает настоящее имя."""
    from app.models import Recipe, RecipeItem

    client, material, _ = api
    employee, *_ = people
    recipe_name = f"Кедр {uuid4().hex[:6]}"
    async with session_factory() as s:
        s.add(Recipe(name=recipe_name, items=[RecipeItem(material_id=material.id, qty_per_unit=1)]))
        # фикстура api по умолчанию — кладовщик; для действия нужен технолог
        user = await s.get(User, (await client.get("/api/auth/me")).json()["id"])
        user.role = Role.TECHNOLOGIST
        await s.commit()

    recorder = Recorder()
    arguments = {
        "recipe": recipe_name,
        "quantity": 3,
        "planned_on": "2030-01-10",
        "comment": f"ответственный Сотрудник-{employee.id}",
    }
    recorder.push(
        ToolCallRequested(ToolCall("c1", "create_job", json.dumps(arguments, ensure_ascii=False))),
        Completed(model="m", finish="tool_calls"),
    )
    recorder.push(TextDelta("Подтвердите"), Completed(model="m"))
    app.dependency_overrides[get_llm] = lambda: LLMRouter(
        [Route(recorder, "m")], event_timeout=2, total_timeout=10
    )
    try:
        conversation_id = (await client.post("/api/chat/conversations", json={})).json()["id"]
        response = await client.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"text": "Запланируй 3 штуки"},
        )
    finally:
        app.dependency_overrides.pop(get_llm, None)

    card = [d for e, d in parse_sse(response.text) if e == "tool" and d["status"] == "finished"][0]
    assert card["action"]["preview"]["comment"] == "ответственный Лебедев Олег Ильич"
    # а в модель предпросмотр вернулся с псевдонимом
    assert "Лебедев" not in recorder.sent_texts()


async def test_database_errors_do_not_leak_values_into_logs(session, caplog):
    """Значения строк (ФИО, поставщики) не попадают в логи при ошибках БД."""
    from sqlalchemy.exc import IntegrityError

    from app.db.session import engine

    assert engine.sync_engine.hide_parameters
    from tests.factories import make_supplier

    await make_supplier(session, name="ООО «Секретный поставщик»")
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            session.add(Supplier(name="ООО «Секретный поставщик»"))
            await session.flush()
    # Обработчик API пишет в лог только имя ограничения
    import logging

    from app.api import errors as api_errors

    caplog.set_level(logging.WARNING)
    handler = next(h for exc_type, h in _handlers(api_errors).items() if exc_type is IntegrityError)
    await handler(None, exc.value)
    assert "Секретный" not in caplog.text
    assert "uq_suppliers_name" in caplog.text


def _handlers(module):
    from fastapi import FastAPI

    probe = FastAPI()
    module.install(probe)
    return probe.exception_handlers


# Найдено прогоном на настоящей модели (gpt-oss): она склоняет псевдоним и ставит
# неразрывный дефис — обратная замена не срабатывала, пользователь видел «Поставщиком‑1».
@pytest.mark.parametrize(
    ("model_text", "shown"),
    [
        ("привезена **Поставщиком\u20111**.", "привезена **ООО «Восковой двор»**."),
        ("у Поставщика\u20131", "у ООО «Восковой двор»"),
        ("передал Сотруднику – 2", "передал Петров Иван Сергеевич"),
        ("сотрудник-2 принял", "Петров Иван Сергеевич принял"),
    ],
)
def test_declined_pseudonyms_and_other_hyphens_are_unmasked(model_text, shown):
    assert PII.unmask(model_text) == shown
    unmasker = PII.stream_unmasker()  # и в потоке по одному символу
    assert "".join(unmasker.feed(ch) for ch in model_text) + unmasker.flush() == shown
