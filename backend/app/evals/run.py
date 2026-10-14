"""Прогон эталонных вопросов (вручную перед релизом, не в CI).

    docker compose exec backend python -m app.evals.run              # все вопросы + судья
    docker compose exec backend python -m app.evals.run --no-judge   # только машинные проверки
    docker compose exec backend python -m app.evals.run --case stock --case pii
    docker compose exec backend python -m app.evals.run --dry-run    # только эталоны, без LLM

Каждый вопрос проходит через настоящий конвейер ассистента (агентный цикл, псевдонимы,
Code Interpreter, память) от имени служебного пользователя нужной роли. Машинные
проверки (инструмент, числа из базы, отсутствие побочных эффектов, отсутствие ФИО в
запросах к модели) отделены от оценки LLM-судьи. После вопроса его диалог и
неподтверждённые предложения удаляются. Отчёт — evals/reports/<время>.md и .json,
копия последнего — evals/reports/latest.md.
"""

import argparse
import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from app.assistant import chat, runner
from app.assistant.agent import ChatEvent
from app.assistant.pii import load_pseudonymizer
from app.assistant.tool_set import action_card
from app.assistant.tools import default_registry
from app.core import clock
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.evals.cases import CASES, Case
from app.evals.checks import CheckResult, Observation, answered, no_pii_sent
from app.llm.prompts import load_prompt
from app.llm.registry import get_llm
from app.llm.router import LLMRouter, Route
from app.llm.types import (
    AssistantMessage,
    LLMRequest,
    ToolCall,
    ToolResult,
    UserMessage,
)
from app.models import (
    AssistantAction,
    Batch,
    ChatMessage,
    Conversation,
    ProductionJob,
    Reservation,
    Supplier,
    User,
    UserMemory,
)
from app.models.enums import ActionStatus, Role

REPORTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "reports"
EVAL_USERS = {
    Role.MANAGER: ("eval-manager", "Проверяющий Руководитель"),
    Role.STOREKEEPER: ("eval-storekeeper", "Проверяющий Кладовщик"),
    Role.TECHNOLOGIST: ("eval-technologist", "Проверяющий Технолог"),
}


# ---------------------------------------------------------------- запись того, что ушло в модель


class Recording:
    """Обёртка провайдера: запоминает всё, что отправлено в модель."""

    def __init__(self, inner):
        self.inner = inner
        self.name = inner.name
        self.sent: list[str] = []

    def __getattr__(self, item):  # файловые операции Code Interpreter — как есть
        return getattr(self.inner, item)

    async def stream(self, request: LLMRequest, model: str) -> AsyncIterator:
        parts = [request.instructions]
        for item in request.items:
            if isinstance(item, UserMessage | AssistantMessage):
                parts.append(item.text)
            elif isinstance(item, ToolResult):
                parts.append(item.output)
            elif isinstance(item, ToolCall):
                parts.append(item.arguments)
        self.sent.append("\n".join(parts))
        async for event in self.inner.stream(request, model):
            yield event


def recording_router(base: LLMRouter) -> tuple[LLMRouter, list[Recording]]:
    wrappers: dict[int, Recording] = {}
    routes = []
    for route in base.routes:
        wrapper = wrappers.setdefault(id(route.provider), Recording(route.provider))
        routes.append(Route(wrapper, route.model))
    router = LLMRouter(routes, event_timeout=base.event_timeout, total_timeout=base.total_timeout)
    return router, list(wrappers.values())


# ---------------------------------------------------------------- побочные эффекты


async def snapshot(session) -> dict[str, Any]:
    """Состояние данных, которое ассистент без подтверждения менять не должен."""
    jobs = dict(
        (
            await session.execute(
                select(ProductionJob.status, func.count()).group_by(ProductionJob.status)
            )
        ).all()
    )
    return {
        "jobs": {k.value: v for k, v in jobs.items()},
        "reserved_total": str(
            await session.scalar(select(func.coalesce(func.sum(Reservation.quantity), 0)))
        ),
        "batches_remaining": str(
            await session.scalar(select(func.coalesce(func.sum(Batch.remaining_qty), 0)))
        ),
        "confirmed_actions": await session.scalar(
            select(func.count()).where(AssistantAction.status == ActionStatus.CONFIRMED)
        ),
    }


def diff(before: dict, after: dict) -> dict[str, Any]:
    return {k: {"было": before[k], "стало": after[k]} for k in before if before[k] != after[k]}


# ---------------------------------------------------------------- один вопрос


@dataclass
class CaseResult:
    id: str
    title: str
    tags: list[str]
    role: str
    question: str
    answer: str
    status: str
    model: str | None
    tools: list[str]
    seconds: float
    checks: list[CheckResult]
    judge: dict[str, Any] | None = None
    error: str | None = None
    reference: str = ""
    tokens: int | None = None

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


async def ensure_user(session, role: Role) -> User:
    login, full_name = EVAL_USERS[role]
    user = await session.scalar(select(User).where(User.login == login))
    if user is None:
        user = User(
            login=login,
            full_name=full_name,
            role=role,
            password_hash=hash_password(
                f"eval-{datetime.now(UTC).timestamp()}"
            ),  # входить не нужно
        )
        session.add(user)
        await session.commit()
    return user


async def run_case(
    case: Case, llm: LLMRouter, recorders: list[Recording], today: date
) -> tuple[CaseResult, dict[str, Any]]:
    async with SessionLocal() as session:
        user = await ensure_user(session, case.role)
        reference = await case.reference(session, today)
        question = case.question(reference)
        names = [n for (n,) in (await session.execute(select(User.full_name))).all()] + [
            n for (n,) in (await session.execute(select(Supplier.name))).all()
        ]
        before = await snapshot(session)
        conversation = await chat.create_conversation(session, user, f"[eval] {case.id}")
        _, answer, items = await chat.start_turn(session, user, conversation.id, question)
        conversation_id, message_id, user_id = conversation.id, answer.id, user.id

    for recorder in recorders:
        recorder.sent.clear()
    queue: asyncio.Queue[ChatEvent] = asyncio.Queue()
    started = time.monotonic()
    await runner.generate(
        runner.Turn(user_id, conversation_id, message_id, items), llm, default_registry(), queue
    )
    seconds = time.monotonic() - started
    done: dict[str, Any] = {}
    while not queue.empty():
        event = queue.get_nowait()
        if event.type == "done":
            done = event.data

    async with SessionLocal() as session:
        after = await snapshot(session)
        actions = (
            await session.scalars(
                select(AssistantAction).where(AssistantAction.message_id == message_id)
            )
        ).all()
        message = await session.get(ChatMessage, message_id)
        tokens = (message.input_tokens or 0) + (message.output_tokens or 0) or None
        observation = Observation(
            answer=done.get("content") or message.content,
            status=done.get("status", message.status.value),
            tools=done.get("tools", []),
            code_runs=done.get("code_runs", []),
            files=done.get("files", []),
            actions=[action_card(a) for a in actions],
            side_effects=diff(before, after),
            sent_to_llm="\n".join(s for r in recorders for s in r.sent),
            model=done.get("model"),
            error=done.get("error"),
        )
        results = [
            answered(observation),
            *case.checks(observation, reference),
            no_pii_sent(observation, names),
        ]
        pii = await load_pseudonymizer(session)
        judge_input = {
            "question": pii.mask(question),
            "reference": pii.mask(case.judge_notes(reference)),
            "answer": pii.mask(observation.answer),
            "tools_called": [t["name"] for t in observation.tools],
            "actions_proposed": [a["preview"].get("title") for a in observation.actions],
            "code_executed": bool(observation.code_runs),
        }
        # Уборка: диалог (с сообщениями, файлами и предложениями) и память служебного пользователя
        await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
        await session.execute(delete(UserMemory).where(UserMemory.user_id == user_id))
        await session.commit()

    return CaseResult(
        id=case.id,
        title=case.title,
        tags=list(case.tags),
        role=case.role.value,
        question=question,
        answer=observation.answer,
        status=observation.status,
        model=observation.model,
        tools=[t["name"] for t in observation.tools],
        seconds=round(seconds, 1),
        tokens=tokens,
        checks=results,
        error=observation.error,
        reference=case.judge_notes(reference),
    ), judge_input


# ---------------------------------------------------------------- LLM-судья


def parse_judgement(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {"score": None, "verdict": "судья не вернул JSON", "issues": [text[:200]]}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"score": None, "verdict": "судья вернул некорректный JSON", "issues": [text[:200]]}
    score = data.get("score")
    if not isinstance(score, int) or not 1 <= score <= 5:
        data["score"] = None
    return data


async def judge(llm: LLMRouter, judge_input: dict[str, Any]) -> dict[str, Any]:
    """Оценка LLM-судьи. Если судья не вернул JSON (у моделей с рассуждениями, например
    gpt-oss, ответ может не уместиться), — один повтор с напоминанием о формате."""
    payload = json.dumps(judge_input, ensure_ascii=False, indent=2)
    result: dict[str, Any] = {}
    for attempt in range(2):
        text = payload if attempt == 0 else payload + "\n\nОтветь ТОЛЬКО одним JSON-объектом."
        request = LLMRequest(
            instructions=load_prompt("eval_judge"),
            items=[UserMessage(text)],
            max_output_tokens=1500,  # с запасом на рассуждения модели
        )
        try:
            answer, started = await llm.complete(request)
        except Exception as exc:  # noqa: BLE001 — судья не должен ронять прогон
            return {
                "score": None,
                "verdict": f"судья недоступен: {type(exc).__name__}",
                "issues": [],
            }
        result = parse_judgement(answer)
        result["model"] = started.model if started else None
        if result.get("score") is not None:
            break
    return result


# ---------------------------------------------------------------- отчёт


def render_markdown(
    results: list[CaseResult],
    started_at: datetime,
    with_judge: bool,
    skipped: list[str] | None = None,
) -> str:
    passed = sum(r.passed for r in results)
    scores = [r.judge["score"] for r in results if r.judge and r.judge.get("score")]
    models = sorted({r.model for r in results if r.model})
    lines = [
        "# Отчёт прогона эталонных вопросов",
        "",
        f"- Дата: {started_at:%d.%m.%Y %H:%M} UTC",
        f"- Модели: {', '.join(models) or '—'}",
        f"- **Машинные проверки: {passed} из {len(results)} вопросов без замечаний**",
    ]
    if with_judge:
        avg = f"{sum(scores) / len(scores):.2f}" if scores else "—"
        lines.append(f"- **LLM-судья: средняя оценка {avg} из 5** ({len(scores)} оценок)")
    if skipped:
        lines.append(
            f"- Пропущено (у провайдера нет файлов Code Interpreter): {', '.join(skipped)}"
        )
    lines += [
        "",
        "Машинные проверки и оценка судьи независимы: «✅» — все машинные проверки вопроса прошли.",
        "",
        "| # | Вопрос | Теги | Машинные проверки | Судья | Инструменты | Время | Токены |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, r in enumerate(results, 1):
        ok = sum(c.passed for c in r.checks)
        mark = "✅" if r.passed else "❌"
        score = r.judge.get("score") if r.judge else None
        lines.append(
            f"| {i} | {r.title} | {', '.join(r.tags)} | {mark} {ok}/{len(r.checks)} | "
            f"{score if score else '—'} | {', '.join(r.tools) or '—'} | {r.seconds} с | {r.tokens or '—'} |"
        )
    for i, r in enumerate(results, 1):
        lines += [
            "",
            f"## {i}. {r.title} (`{r.id}`, роль {r.role})",
            "",
            f"**Вопрос:** {r.question}",
            "",
            f"**Эталон:** {r.reference or '—'}",
            "",
            f"**Ответ** ({r.status}, модель {r.model or '—'}):",
            "",
            "> " + (r.answer.strip().replace("\n", "\n> ") or "(пусто)"),
            "",
        ]
        if r.error:
            lines += [f"**Ошибка:** {r.error}", ""]
        lines.append("**Машинные проверки:**")
        lines.append("")
        for c in r.checks:
            lines.append(
                f"- {'✅' if c.passed else '❌'} {c.name}"
                + (f" — {c.details}" if c.details and not c.passed else "")
            )
        if r.judge:
            lines += [
                "",
                f"**Судья:** {r.judge.get('score') or '—'}/5 — {r.judge.get('verdict', '')}",
            ]
            for issue in r.judge.get("issues") or []:
                lines.append(f"- {issue}")
    return "\n".join(lines) + "\n"


async def main(argv: list[str] | None = None, llm: LLMRouter | None = None) -> list[CaseResult]:
    parser = argparse.ArgumentParser(description="Прогон эталонных вопросов ассистента")
    parser.add_argument("--case", action="append", help="id вопроса (можно несколько)")
    parser.add_argument("--no-judge", action="store_true", help="без LLM-судьи")
    parser.add_argument("--dry-run", action="store_true", help="только показать вопросы и эталоны")
    parser.add_argument("--out", type=Path, default=REPORTS_DIR)
    parser.add_argument(
        "--pause", type=float, default=0, help="пауза между вопросами, с (лимиты бесплатных API)"
    )
    parser.add_argument(
        "--retry-wait",
        type=float,
        default=65,
        help="если модель недоступна (лимит), подождать и повторить вопрос один раз",
    )
    args = parser.parse_args(argv)

    cases = [c for c in CASES if not args.case or c.id in args.case]
    today = clock.today()
    if args.dry_run:
        async with SessionLocal() as session:
            for case in cases:
                reference = await case.reference(session, today)
                print(
                    f"[{case.id}] {case.question(reference)}\n    эталон: {case.judge_notes(reference) or '—'}"
                )
        return []

    llm, recorders = recording_router(llm or get_llm())
    started_at = datetime.now(UTC)
    results = []
    skipped: list[str] = []
    for case in cases:
        if case.requires_files and llm.files is None:
            # Не провал модели: у провайдера нет файлов Code Interpreter (например, Groq)
            print(f"→ {case.id}: пропущен — у провайдера нет файлов Code Interpreter")
            skipped.append(f"{case.title} (`{case.id}`)")
            continue
        print(f"→ {case.id}: ", end="", flush=True)
        result, judge_input = await run_case(case, llm, recorders, today)
        if result.status == "error" and args.retry_wait > 0:
            # Модель недоступна (чаще всего лимит запросов) — это не ответ модели, повторим
            print(f"модель недоступна, жду {args.retry_wait:g} с… ", end="", flush=True)
            await asyncio.sleep(args.retry_wait)
            result, judge_input = await run_case(case, llm, recorders, today)
        if not args.no_judge:
            if args.pause:
                await asyncio.sleep(args.pause / 2)
            result.judge = await judge(llm, judge_input)
        score = result.judge.get("score") if result.judge else "—"
        print(
            f"{'OK' if result.passed else 'FAIL'} ({sum(c.passed for c in result.checks)}/{len(result.checks)}), судья {score}"
        )
        results.append(result)
        if args.pause and case is not cases[-1]:
            await asyncio.sleep(args.pause)

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = started_at.strftime("%Y%m%d-%H%M%S")
    markdown = render_markdown(results, started_at, not args.no_judge, skipped)
    (args.out / f"{stamp}.md").write_text(markdown, encoding="utf-8")
    (args.out / "latest.md").write_text(markdown, encoding="utf-8")
    (args.out / f"{stamp}.json").write_text(
        json.dumps(
            [{**asdict(r), "passed": r.passed} for r in results],
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    passed = sum(r.passed for r in results)
    print(f"\nИтого: {passed}/{len(results)} без замечаний. Отчёт: {args.out / 'latest.md'}")
    return results


if __name__ == "__main__":
    asyncio.run(main())
