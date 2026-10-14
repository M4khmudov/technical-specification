"""Сервис маркировки: outbox, повторы с растущей паузой, идемпотентность, журнал попыток."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update

from app.core import clock
from app.core.config import get_settings
from app.core.errors import PermissionDeniedError
from app.models import AuditLog, MarkingReport, ProductionJob
from app.models.enums import JobStatus, MarkingStatus, Role
from app.schemas.production import JobCompleteIn
from app.services import marking
from app.services import production as jobs
from tests.factories import make_batch, make_material, make_recipe, make_user
from tests.test_production import batch, make_shop, started_job


class FakeMarkingService:
    """Ведёт себя как сервис маркировки: хранит отчёты по Idempotency-Key.

    behaviours — что делать на очередной вызов: "ok", "500", "400", "timeout"
    ("timeout" = сервис отчёт сохранил, но ответ до нас не дошёл).
    """

    def __init__(self, *behaviours: str, delay: float = 0):
        self.behaviours = list(behaviours)
        self.delay = delay
        self.reports: dict[str, dict] = {}
        self.keys_seen: list[str] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if self.delay:
            await asyncio.sleep(self.delay)
        key = request.headers["Idempotency-Key"]
        self.keys_seen.append(key)
        behaviour = self.behaviours.pop(0) if self.behaviours else "ok"
        if behaviour == "500":
            return httpx.Response(500, text="temporary failure")
        if behaviour == "400":
            return httpx.Response(400, text="bad report")
        duplicate = key in self.reports
        self.reports.setdefault(key, {"payload": request.content})
        if behaviour == "timeout":
            raise httpx.ReadTimeout("no response", request=request)
        return httpx.Response(200, json={"duplicate": duplicate})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def no_jitter(monkeypatch):
    monkeypatch.setattr(marking._random, "uniform", lambda a, b: 1.0)


async def pause_passed(session, report_id: int) -> None:
    """Время следующей попытки наступило (вместо реального ожидания паузы)."""
    await session.execute(
        update(MarkingReport)
        .where(MarkingReport.id == report_id)
        .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()


async def completed_job(session):
    shop = await make_shop(session)
    shop.user.full_name = "Петров Иван Сергеевич"
    await batch(session, shop.wax, "10", day=1)
    await batch(session, shop.wick, "100", day=1)
    job = await started_job(session, shop, quantity=10)
    job = await jobs.complete_job(session, shop.user, job.id, JobCompleteIn())
    report = await marking.get_report(session, job.id)
    return shop, job, report


async def test_completion_creates_pending_report_without_waiting_for_service(session):
    _, job, report = await completed_job(session)

    assert job.status == JobStatus.COMPLETED  # завершено сразу, сервис не вызывался
    assert report.status == MarkingStatus.PENDING
    assert report.attempts_count == 0
    assert report.next_attempt_at is not None
    assert len(report.idempotency_key) == 32
    assert report.payload["job_number"] == job.number
    assert report.payload["quantity"] == 10
    assert "Петров" not in str(report.payload)  # персональных данных в отчёте нет


async def test_successful_send_moves_job_to_marking_status(session):
    _, job, report = await completed_job(session)
    service = FakeMarkingService("ok")

    outcome = await marking.send_report(session, report.id, service.client())

    assert outcome == "sent"
    assert (await jobs.get_job(session, job.id)).status_label == "Сервис маркировки"
    report = await marking.get_report(session, job.id)
    assert report.status == MarkingStatus.SENT
    attempts = await marking.list_attempts(session, report.id)
    assert [(a.attempt_no, a.http_status, a.error) for a in attempts] == [(1, 200, None)]
    log = await session.scalar(
        select(AuditLog).where(AuditLog.entity_id == job.id, AuditLog.action == "marked")
    )
    assert log.user_full_name == "Система (сервис маркировки)"


async def test_retry_after_lost_response_does_not_create_duplicate(session):
    """Сервис принял отчёт, но ответ не дошёл → повтор с тем же ключом → один отчёт."""
    _, job, report = await completed_job(session)
    service = FakeMarkingService("timeout", "ok")

    assert await marking.send_report(session, report.id, service.client()) == "retry"
    await pause_passed(session, report.id)
    assert await marking.send_report(session, report.id, service.client()) == "sent"

    assert len(service.keys_seen) == 2
    assert len(set(service.keys_seen)) == 1  # ключ идемпотентности не менялся
    assert len(service.reports) == 1  # дубля нет
    attempts = await marking.list_attempts(session, report.id)
    assert attempts[0].error.startswith("Нет ответа за")
    assert attempts[1].http_status == 200
    assert (await jobs.get_job(session, job.id)).status == JobStatus.MARKED


async def test_server_errors_are_retried_with_growing_pause(session):
    _, job, report = await completed_job(session)
    service = FakeMarkingService("500", "500", "500")
    pauses = []
    for _ in range(3):
        await pause_passed(session, report.id)
        before = datetime.now(UTC)
        assert await marking.send_report(session, report.id, service.client()) == "retry"
        report = await marking.get_report(session, job.id)
        pauses.append(round((report.next_attempt_at - before).total_seconds()))

    assert pauses == [5, 10, 20]  # база 5 с, удвоение
    assert report.status == MarkingStatus.PENDING
    assert [a.http_status for a in await marking.list_attempts(session, report.id)] == [500] * 3


def test_backoff_is_capped():
    assert marking.backoff_delay(1) == timedelta(seconds=5)
    assert marking.backoff_delay(20) == timedelta(seconds=300)


async def test_client_error_is_not_retried(session):
    _, job, report = await completed_job(session)
    service = FakeMarkingService("400")

    assert await marking.send_report(session, report.id, service.client()) == "failed"
    # повторный вызов ничего не отправляет
    assert await marking.send_report(session, report.id, service.client()) == "failed"
    assert len(service.keys_seen) == 1
    assert (await jobs.get_job(session, job.id)).status == JobStatus.COMPLETED


async def test_gives_up_after_max_attempts_then_manager_can_retry(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "marking_max_attempts", 2)
    shop, job, report = await completed_job(session)
    service = FakeMarkingService("500", "500", "ok")

    assert await marking.send_report(session, report.id, service.client()) == "retry"
    await pause_passed(session, report.id)
    assert await marking.send_report(session, report.id, service.client()) == "failed"

    storekeeper = await make_user(session, role=Role.STOREKEEPER)
    with pytest.raises(PermissionDeniedError):
        await marking.retry_failed(session, storekeeper, job.id)

    report = await marking.retry_failed(session, shop.user, job.id)  # руководитель
    assert report.status == MarkingStatus.PENDING
    assert await marking.send_report(session, report.id, service.client()) == "sent"
    assert len(set(service.keys_seen)) == 1


async def test_retry_waits_for_its_pause(session):
    """S5: повтор не уходит раньше времени, кто бы его ни запустил — отложенная задача
    или периодическая."""
    _, job, report = await completed_job(session)
    report_id = report.id
    service = FakeMarkingService("500", "ok")
    assert await marking.send_report(session, report_id, service.client()) == "retry"
    assert await marking.send_report(session, report_id, service.client()) == "not_due"
    assert len(service.keys_seen) == 1
    assert len(await marking.list_attempts(session, report_id)) == 1


async def test_manual_retry_starts_a_full_round(session, monkeypatch):
    """S5: после ручного повтора — снова marking_max_attempts попыток, а не одна."""
    monkeypatch.setattr(get_settings(), "marking_max_attempts", 2)
    shop, job, report = await completed_job(session)
    service = FakeMarkingService("500", "500", "500", "500")
    assert await marking.send_report(session, report.id, service.client()) == "retry"
    await pause_passed(session, report.id)
    assert await marking.send_report(session, report.id, service.client()) == "failed"

    await marking.retry_failed(session, shop.user, job.id)
    assert await marking.send_report(session, report.id, service.client()) == "retry"
    await pause_passed(session, report.id)
    assert await marking.send_report(session, report.id, service.client()) == "failed"
    assert [a.attempt_no for a in await marking.list_attempts(session, report.id)] == [1, 2, 3, 4]


async def test_enqueue_does_not_hang_when_redis_is_down(monkeypatch):
    """S5: «Завершить» не ждёт недоступный Redis."""
    import time

    settings = get_settings()
    monkeypatch.setattr(settings, "marking_enqueue_enabled", True)
    monkeypatch.setattr(settings, "redis_url", "redis://10.255.255.1:6379/0")  # не отвечает
    started = time.monotonic()
    await marking.enqueue(1)
    assert time.monotonic() - started < 2


async def test_due_reports_are_picked_up(session):
    _, job, report = await completed_job(session)
    assert report.id in await marking.due_report_ids(session)

    await marking.send_report(session, report.id, FakeMarkingService("500").client())
    assert report.id not in await marking.due_report_ids(session)  # пауза ещё не прошла


async def test_two_workers_do_not_send_same_report_twice(session_factory):
    async with session_factory() as s:
        tag = uuid4().hex[:6]
        user = await make_user(s, login=f"mark-{tag}")
        material = await make_material(s, code=f"MARK-{tag}")
        recipe = await make_recipe(s, {material: "1"}, name=f"Маркировка {tag}")
        await make_batch(s, material, "1", received_on=date(2026, 9, 1))
        job = ProductionJob(
            recipe_id=recipe.id,
            quantity=1,
            planned_on=clock.today(),
            status=JobStatus.COMPLETED,
            created_by_id=user.id,
        )
        s.add(job)
        await s.flush()
        report = MarkingReport(
            job_id=job.id,
            idempotency_key=uuid4().hex,
            payload={"job_number": job.number, "quantity": 1, "materials": []},
            status=MarkingStatus.PENDING,
            next_attempt_at=datetime.now(UTC),
        )
        s.add(report)
        await s.commit()

    service = FakeMarkingService(delay=0.3)

    async def worker():
        async with session_factory() as s:
            return await marking.send_report(s, report.id, service.client())

    outcomes = sorted(await asyncio.gather(worker(), worker()))
    assert outcomes == ["locked", "sent"]
    assert len(service.keys_seen) == 1


async def test_marking_status_over_http(api, session_factory):
    client, *_ = api
    response = await client.get("/api/jobs/999999/marking")
    assert response.status_code == 404
    assert "создаётся при завершении" in response.json()["detail"]


def test_report_quantities_are_strings_not_floats():
    job = SimpleNamespace(
        id=1,
        number=7,
        quantity=3,
        recipe=SimpleNamespace(name="Лаванда"),
        completed_at=datetime(2026, 10, 10, tzinfo=UTC),
        materials=[
            SimpleNamespace(material=SimpleNamespace(code="WAX"), actual_qty=Decimal("0.6"))
        ],
    )
    report = marking.build_report(job)
    assert report.payload["materials"] == [{"code": "WAX", "actual_qty": "0.6"}]
