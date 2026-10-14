"""S5: фоновый воркер маркировки — отложенный повтор и периодическая страховка."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.core import clock
from app.models import MarkingReport, ProductionJob
from app.models.enums import JobStatus, MarkingStatus
from app.worker import resend_due_marking_reports, send_marking_report
from tests.factories import make_batch, make_material, make_recipe, make_user
from tests.test_marking import FakeMarkingService


class FakeRedis:
    """Очередь arq: запоминаем, что и когда поставлено."""

    def __init__(self):
        self.jobs: list[tuple[str, tuple, dict]] = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.jobs.append((name, args, kwargs))


async def committed_report(session_factory, *, due_in: timedelta = timedelta(0)) -> int:
    async with session_factory() as s:
        tag = uuid4().hex[:6]
        user = await make_user(s, login=f"worker-{tag}")
        material = await make_material(s, code=f"WRK-{tag}")
        recipe = await make_recipe(s, {material: "1"}, name=f"Воркер {tag}")
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
            next_attempt_at=datetime.now(UTC) + due_in,
        )
        s.add(report)
        await s.commit()
        return report.id


async def test_failed_attempt_schedules_next_one_after_the_pause(session_factory):
    report_id = await committed_report(session_factory)
    redis = FakeRedis()
    ctx = {"marking_client": FakeMarkingService("500").client(), "redis": redis}

    assert await send_marking_report(ctx, report_id) == "retry"

    async with session_factory() as s:
        report = await s.get(MarkingReport, report_id)
    ((name, args, kwargs),) = redis.jobs
    assert (name, args) == ("send_marking_report", (report_id,))
    assert kwargs["_defer_until"] == report.next_attempt_at > datetime.now(UTC)
    assert kwargs["_job_id"] == f"marking:{report_id}:1"  # один отложенный повтор на попытку


async def test_early_wakeup_does_not_send(session_factory):
    """Отложенная задача сработала раньше паузы (или её подхватил cron) — не отправляем."""
    report_id = await committed_report(session_factory, due_in=timedelta(minutes=5))
    service = FakeMarkingService("ok")
    ctx = {"marking_client": service.client(), "redis": FakeRedis()}
    assert await send_marking_report(ctx, report_id) == "not_due"
    assert service.keys_seen == []


async def test_cron_sends_only_due_reports(session_factory):
    due = await committed_report(session_factory)
    later = await committed_report(session_factory, due_in=timedelta(minutes=5))
    service = FakeMarkingService()
    ctx = {"marking_client": service.client(), "redis": FakeRedis()}

    await resend_due_marking_reports(ctx)

    async with session_factory() as s:
        assert (await s.get(MarkingReport, due)).status == MarkingStatus.SENT
        assert (await s.get(MarkingReport, later)).status == MarkingStatus.PENDING
