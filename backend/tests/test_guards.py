"""S9: тесты на места, где ошибка в коде раньше не роняла ни одного теста.

Каждый тест здесь падает, если убрать соответствующую защиту: блокировку строки
задания, перечитывание партий после блокировки, слияние резерва в одной партии,
FIFO при перерасходе, блокировку партии при приёмке/браке, режим округления,
маскирование истории и имени файла, лимит шагов агента, изоляцию памяти.
"""

import asyncio
import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select

from app.assistant import runner
from app.assistant.agent import MAX_STEPS, STEP_LIMIT_NOTE
from app.assistant.chat import create_conversation, save_upload, start_turn
from app.assistant.tools import Tool, ToolOutcome, ToolRegistry
from app.core.errors import ConflictError, DomainError
from app.llm.fake import ScriptedProvider, text_reply, tool_call_reply
from app.llm.router import LLMRouter, Route
from app.models import Batch, Reservation
from app.models.enums import BatchStatus, Currency, JobStatus, Role
from app.schemas.production import ActualIn, JobCompleteIn
from app.schemas.stock import ReceiptIn, ReceiptLineIn
from app.services import batches, memory
from app.services import production as service
from tests.factories import make_batch, make_material, make_supplier, make_user
from tests.fakes import FakeRates
from tests.test_production import (
    batch,
    by_material,
    committed_shop,
    job_in,
    make_shop,
    reload,
    started_job,
)

D = Decimal


async def _outcome(coro):
    try:
        return (await coro).status
    except (ConflictError, DomainError):
        return "conflict"


# ---------------------------------------------------------------- блокировка строки задания


async def test_start_and_cancel_at_once_have_one_outcome(session_factory):
    shop, stock = await committed_shop(session_factory, "10")
    async with session_factory() as s:
        job = await service.create_job(s, shop.user, job_in(shop, 4), plan=True)

    async def act(operation):
        async with session_factory() as s:
            return await _outcome(operation(s, shop.user, job.id))

    results = await asyncio.gather(act(service.start_job), act(service.cancel_job))
    assert "conflict" in results  # второе действие увидело новый статус
    async with session_factory() as s:
        final = await service.get_job(s, job.id)
        reserved = (await s.get(Batch, stock.id)).reserved_qty
    expected = {JobStatus.IN_PROGRESS: D("4"), JobStatus.CANCELLED: D("0")}
    assert reserved == expected[final.status]


async def test_complete_and_cancel_at_once_have_one_outcome(session_factory):
    shop, stock = await committed_shop(session_factory, "10")
    async with session_factory() as s:
        job = await service.create_job(s, shop.user, job_in(shop, 4), plan=True)
        await service.start_job(s, shop.user, job.id)

    async def complete(s, user, job_id):
        return await service.complete_job(s, user, job_id, JobCompleteIn())

    async def act(operation):
        async with session_factory() as s:
            return await _outcome(operation(s, shop.user, job.id))

    results = await asyncio.gather(act(complete), act(service.cancel_job))
    assert results.count("conflict") == 1
    async with session_factory() as s:
        b = await s.get(Batch, stock.id)
    assert (b.remaining_qty, b.reserved_qty) == (D("6"), D("0"))


async def test_planning_waits_for_a_locked_batch(session_factory):
    """Детерминированная версия «двух резервов»: пока другая транзакция держит партию
    (резервирует её), планирование ждёт, а потом берёт только остаток."""
    shop, stock = await committed_shop(session_factory, "10")

    async def plan():
        async with session_factory() as s:
            return await service.create_job(s, shop.user, job_in(shop, 8), plan=True)

    async with session_factory() as holder:
        locked = await holder.scalar(select(Batch).where(Batch.id == stock.id).with_for_update())
        planning = asyncio.create_task(plan())
        await asyncio.sleep(0.3)
        assert not planning.done()  # ждёт блокировку партии
        locked.reserved_qty = D("8")  # «другой резерв» занял 8 кг
        await holder.commit()
    job = await planning
    assert by_material(job, shop.wax).reserved_qty == D("2")


# ---------------------------------------------------------------- свежие партии после блокировки


async def test_stale_batch_in_session_does_not_oversell(session_factory):
    """Сессия уже держит партию в identity map со старым резервом. Без populate_existing
    после блокировки она взяла бы устаревшее «свободно» и перепродала остаток."""
    shop, stock = await committed_shop(session_factory, "10")
    async with session_factory() as stale:
        # закэшировали: резерв 0. Ссылку держим — identity map хранит объекты слабо
        cached = await stale.get(Batch, stock.id)
        async with session_factory() as other:
            await service.create_job(other, shop.user, job_in(shop, 8), plan=True)
        job = await service.create_job(stale, shop.user, job_in(shop, 8), plan=True)
        assert by_material(job, shop.wax).reserved_qty == D("2")
        assert cached in stale
    async with session_factory() as s:
        reserved = await s.scalar(
            select(func.sum(Reservation.quantity)).where(Reservation.batch_id == stock.id)
        )
        b = await s.get(Batch, stock.id)
    assert reserved == b.reserved_qty == D("10")


# ---------------------------------------------------------------- дорезерв в ту же партию


async def test_rereserve_adds_to_existing_reservation_in_same_batch(session):
    shop = await make_shop(session)
    b1 = await batch(session, shop.wax, "2", day=1)
    await batch(session, shop.wick, "100", day=1)
    other = await service.create_job(session, shop.user, job_in(shop, 5), plan=True)  # 1 кг
    job = await service.create_job(session, shop.user, job_in(shop, 10), plan=True)  # 2 кг
    assert by_material(job, shop.wax).reserved_qty == D("1")

    await service.cancel_job(session, shop.user, other.id)  # в b1 освободился 1 кг
    job = await service.reserve_job(session, shop.user, job.id)

    wax = [r for r in job.reservations if r.material_id == shop.wax.id]
    assert [(r.batch_id, r.quantity) for r in wax] == [(b1.id, D("2"))]  # одна строка, 1 + 1
    assert (await reload(session, b1)).reserved_qty == D("2")


# ---------------------------------------------------------------- перерасход по FIFO


async def test_overrun_is_taken_from_oldest_free_batches_first(session):
    shop = await make_shop(session)
    await batch(session, shop.wax, "8", day=1)
    b2 = await batch(session, shop.wax, "1", day=2)
    b3 = await batch(session, shop.wax, "10", day=3)
    await batch(session, shop.wick, "100", day=1)
    job = await started_job(session, shop)  # 8 кг из b1

    await service.complete_job(
        session,
        shop.user,
        job.id,
        JobCompleteIn(actuals=[ActualIn(material_id=shop.wax.id, actual_qty=D("9.5"))]),
    )
    assert (await reload(session, b2)).remaining_qty == D("0")  # сначала более старая
    assert (await reload(session, b3)).remaining_qty == D("9.5")


# ---------------------------------------------------------------- приёмка и брак


async def test_accept_and_reject_at_once_have_one_outcome(session_factory):
    async with session_factory() as s:
        user = await make_user(s, login=f"review-{uuid4().hex[:6]}", role=Role.STOREKEEPER)
        material = await make_material(s, code=f"REV-{uuid4().hex[:6]}")
        pending = await make_batch(s, material, "5", status=BatchStatus.PENDING)
        await s.commit()

    async def act(operation, *args):
        async with session_factory() as s:
            return await _outcome(operation(s, user, pending.id, *args))

    # Детерминированно: «брак» уже держит строку партии, «принять» обязано дождаться
    # его коммита и увидеть новый статус, а не принять поверх
    async with session_factory() as holder:
        locked = await holder.scalar(select(Batch).where(Batch.id == pending.id).with_for_update())
        accept = asyncio.create_task(act(batches.accept_batch))
        await asyncio.sleep(0.3)
        assert not accept.done()  # ждёт блокировку
        locked.status = BatchStatus.REJECTED
        await holder.commit()
    assert await accept == "conflict"


async def test_unit_cost_is_rounded_half_up(session):
    """0,000001 $ × 2,5 = 0,0000025 ₽ → 0,000003 (не банковское 0,000002)."""
    user = await make_user(session)
    material = await make_material(session)
    supplier = await make_supplier(session)
    data = ReceiptIn(
        supplier_id=supplier.id,
        received_on=date(2026, 10, 1),
        lines=[
            ReceiptLineIn(
                material_id=material.id,
                quantity=1,
                price=D("0.000001"),
                currency=Currency.USD,
            )
        ],
    )
    receipt = await batches.create_receipt(session, user, data, FakeRates({Currency.USD: "2.5"}))
    assert receipt.batches[0].unit_cost_rub == D("0.000003")


# ---------------------------------------------------------------- маскирование истории и файла


async def test_history_and_uploaded_filename_reach_the_model_masked(session_factory):
    provider = ScriptedProvider(text_reply("ок"), text_reply("ок"))
    llm = LLMRouter([Route(provider, "m")], event_timeout=2, total_timeout=5)
    async with session_factory() as s:
        tag = uuid4().hex[:6]
        user = await make_user(s, login=f"mask-{tag}", full_name=f"Зайцев{tag} Пётр Ильич")
        conversation = await create_conversation(s, user, None)
        _, answer, items = await start_turn(s, user, conversation.id, f"Привет, я Зайцев{tag}")
    turn = runner.Turn(user.id, conversation.id, answer.id, items)
    async for _ in runner.stream_turn(turn, llm, ToolRegistry()):
        pass

    async with session_factory() as s:
        upload = await save_upload(
            s, user, conversation.id, f"Зайцев{tag}_отчёт.csv", b"a;b\n1;2\n"
        )
        _, answer, items = await start_turn(s, user, conversation.id, "Что в файле?", [upload.id])
    turn = runner.Turn(user.id, conversation.id, answer.id, items)
    async for _ in runner.stream_turn(turn, llm, ToolRegistry()):
        pass

    sent = json.dumps(
        [getattr(i, "text", "") for i in provider.requests[-1][1].items], ensure_ascii=False
    )
    assert f"Зайцев{tag}" not in sent  # ни в истории, ни в имени файла
    assert f"Сотрудник-{user.id}" in sent


# ---------------------------------------------------------------- лимит шагов агента


class _NoArgs(__import__("pydantic").BaseModel):
    pass


async def test_agent_stops_after_step_limit(session_factory):
    provider = ScriptedProvider(*[tool_call_reply("probe", "{}") for _ in range(MAX_STEPS + 3)])
    llm = LLMRouter([Route(provider, "m")], event_timeout=2, total_timeout=10)

    async def probe(ctx, args):
        return ToolOutcome({"ok": True}, "ок")

    async with session_factory() as s:
        user = await make_user(s, login=f"steps-{uuid4().hex[:6]}")
        conversation = await create_conversation(s, user, None)
        _, answer, items = await start_turn(s, user, conversation.id, "Вопрос")
    turn = runner.Turn(user.id, conversation.id, answer.id, items)
    registry = ToolRegistry([Tool("probe", "проверка", _NoArgs, probe)])
    async for _ in runner.stream_turn(turn, llm, registry):
        pass
    assert len(provider.requests) == MAX_STEPS
    async with session_factory() as s:
        from app.models import ChatMessage

        message = await s.get(ChatMessage, answer.id)
    assert STEP_LIMIT_NOTE.strip() in message.content


# ---------------------------------------------------------------- память: дубли у каждого свои


async def test_same_fact_of_two_users_is_stored_for_both(session):
    a = await make_user(session)
    b = await make_user(session)
    _, created_a = await memory.remember(session, a, "Считать в евро")
    fact_b, created_b = await memory.remember(session, b, "Считать в евро")
    assert created_a and created_b  # «уже помню» — только про свои факты
    assert [f.id for f in await memory.list_facts(session, b)] == [fact_b.id]
