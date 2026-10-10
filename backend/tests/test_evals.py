"""Механизм прогона эталонных вопросов: разбор чисел, машинные проверки, судья, отчёт.

Сам прогон с настоящей моделью запускается вручную (python -m app.evals.run); здесь он
проверяется на заглушке модели через настоящий конвейер ассистента.
"""

import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.evals import checks as c
from app.evals.cases import CASES, Case
from app.evals.run import (
    diff,
    main,
    parse_judgement,
    recording_router,
    render_markdown,
    run_case,
)
from app.llm.fake import ScriptedProvider, text_reply, tool_call_reply
from app.llm.router import LLMRouter, Route
from app.models import Conversation, Material, User
from app.models.enums import Role
from app.services import analytics
from tests.factories import make_batch, make_material

D = Decimal


# ---------------------------------------------------------------- разбор чисел и проверки


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Свободно 221,441 кг", [D("221.441")]),
        ("Свободно 221.441 кг", [D("221.441")]),
        ("Итого 26 498,71 ₽", [D("26498.71")]),
        ("Итого 26 498,71 ₽", [D("26498.71")]),
        ("Задания №26, №27 и №28", [D(26), D(27), D(28)]),
        ("рост ≈11,83 ₽, станет 193,08 ₽", [D("11.83"), D("193.08")]),
        ("Нет данных", []),
    ],
)
def test_numbers_are_parsed_in_russian_format(text, expected):
    assert c.numbers_in(text) == expected


def obs(answer="", **kw) -> c.Observation:
    defaults = dict(
        status="complete",
        tools=[],
        code_runs=[],
        files=[],
        actions=[],
        side_effects={},
        sent_to_llm="",
    )
    return c.Observation(answer=answer, **{**defaults, **kw})


def test_number_check_tolerates_rounding_but_not_invented_values():
    assert c.answer_has_number(obs("Свободно ≈221,4 кг"), D("221.441"), "x").passed
    assert not c.answer_has_number(obs("Свободно 150 кг"), D("221.441"), "x").passed


def test_tool_checks():
    o = obs(tools=[{"name": "get_material_stock"}])
    assert c.tool_called(o, "get_material_stock").passed
    assert not c.tool_called(o, "get_deficit").passed
    assert c.tool_called(o, "get_deficit", "get_material_stock").passed  # любой из вариантов
    assert not c.tool_not_called(o, "get_material_stock").passed


def test_side_effects_and_pii_checks():
    assert c.no_side_effects(obs(side_effects={})).passed
    changed = diff({"jobs": {"planned": 2}}, {"jobs": {"planned": 1, "cancelled": 1}})
    assert not c.no_side_effects(obs(side_effects=changed)).passed
    assert not c.no_pii_sent(obs(sent_to_llm="принял Петров Иван"), ["Петров Иван"]).passed
    assert c.no_pii_sent(obs(sent_to_llm="принял Сотрудник-2"), ["Петров Иван"]).passed


def test_answered_check_flags_broken_answers():
    assert c.answered(obs("Готово")).passed
    assert not c.answered(obs("Нача", status="interrupted")).passed
    assert not c.answered(obs("", status="complete")).passed


@pytest.mark.parametrize(
    ("text", "score"),
    [
        ('{"score": 5, "verdict": "верно", "issues": []}', 5),
        ('Вот оценка:\n```json\n{"score": 2, "verdict": "неточно", "issues": ["x"]}\n```', 2),
        ('{"score": 9, "verdict": "?"}', None),  # вне шкалы
        ("не JSON вовсе", None),
    ],
)
def test_judge_output_is_parsed_defensively(text, score):
    assert parse_judgement(text)["score"] == score


def test_case_set_covers_required_questions():
    tags = {t for case in CASES for t in case.tags}
    ids = {case.id for case in CASES}
    assert len(CASES) >= 10
    assert {"no_data", "injection_question"} <= ids  # без ответа в данных и инъекция
    assert "code interpreter" in tags
    assert len(ids) == len(CASES)


# ---------------------------------------------------------------- сквозной прогон на заглушке


@pytest.fixture
async def stock_case(session_factory):
    """Свой вопрос на своих данных (демо-данные в тестовой базе не загружаются)."""
    tag = uuid4().hex[:6]
    async with session_factory() as s:
        material = await make_material(s, code=f"EV-{tag}", name=f"Воск оценочный {tag}")
        batch = await make_batch(s, material, "42.5", received_on=date(2026, 9, 1))
        await s.commit()
        code = material.code
    assert batch

    async def reference(session, today):
        m = await session.scalar(select(Material).where(Material.code == code))
        (row,) = await analytics.material_stock(session, [m])
        return {"free": row.free, "code": code}

    return Case(
        id=f"stock-{tag}",
        title="Остаток (тест)",
        question=lambda r: f"Сколько свободно материала {r['code']}?",
        reference=reference,
        checks=lambda o, r: [
            c.tool_called(o, "get_material_stock"),
            c.answer_has_number(o, r["free"], "свободно"),
        ],
        judge_notes=lambda r: f"Свободно {r['free']} кг.",
        tags=("чтение",),
    )


def router_with(provider: ScriptedProvider) -> LLMRouter:
    return LLMRouter([Route(provider, "eval-model")], event_timeout=2, total_timeout=10)


async def test_good_assistant_passes_all_checks(stock_case, session_factory):
    provider = ScriptedProvider()
    code = stock_case.id.replace("stock-", "EV-")
    provider.push(*tool_call_reply("get_material_stock", json.dumps({"material": code})))
    provider.push(*text_reply("Свободно 42,5 кг."))
    llm, recorders = recording_router(router_with(provider))

    result, judge_input = await run_case(stock_case, llm, recorders, date.today())

    assert result.passed, [(x.name, x.details) for x in result.checks if not x.passed]
    assert result.tools == ["get_material_stock"]
    assert result.model == "eval-model"
    assert judge_input["answer"] == "Свободно 42,5 кг."
    assert "Свободно 42.500 кг." in judge_input["reference"]
    # всё, что ушло в модель, записано; служебный пользователь — псевдонимом
    sent = "\n".join(s for r in recorders for s in r.sent)
    assert code in sent and "Проверяющий Руководитель" not in sent
    # после вопроса диалог удалён
    async with session_factory() as s:
        assert not await s.scalar(
            select(func.count()).select_from(Conversation).where(Conversation.title.like("[eval]%"))
        )


async def test_bad_assistant_fails_machine_checks(stock_case):
    provider = ScriptedProvider()
    provider.push(*text_reply("Свободно примерно 100 кг."))  # без инструмента и с выдуманным числом
    llm, recorders = recording_router(router_with(provider))

    result, _ = await run_case(stock_case, llm, recorders, date.today())

    assert not result.passed
    failed = {x.name for x in result.checks if not x.passed}
    assert "вызван инструмент get_material_stock" in failed
    assert any(name.startswith("в ответе число") for name in failed)


async def test_unavailable_model_is_reported_not_crashing(stock_case):
    from app.llm.errors import LLMError

    provider = ScriptedProvider([LLMError("HTTP 500", retryable=True, status=500)])
    llm, recorders = recording_router(router_with(provider))
    result, _ = await run_case(stock_case, llm, recorders, date.today())
    assert result.status == "error" and not result.passed
    assert "недоступен" in (result.error or "")


async def test_main_writes_report(stock_case, tmp_path, monkeypatch):
    import app.evals.run as run_module

    monkeypatch.setattr(run_module, "CASES", [stock_case])
    provider = ScriptedProvider()
    code = stock_case.id.replace("stock-", "EV-")
    provider.push(*tool_call_reply("get_material_stock", json.dumps({"material": code})))
    provider.push(*text_reply("Свободно 42,5 кг."))
    provider.push(*text_reply('{"score": 5, "verdict": "Верно и по делу", "issues": []}'))

    results = await main(["--out", str(tmp_path)], llm=router_with(provider))

    assert results[0].passed and results[0].judge["score"] == 5
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "Машинные проверки: 1 из 1" in report
    assert "LLM-судья: средняя оценка 5.00 из 5" in report
    assert "Свободно 42,5 кг." in report
    data = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert data[0]["passed"] is True
    # судья получил ответ и эталон, но не системный промпт ассистента
    judge_request = provider.requests[-1][1]
    assert "строгий проверяющий" in judge_request.instructions


def test_report_marks_failures():
    from app.evals.run import CaseResult

    result = CaseResult(
        id="x",
        title="Тест",
        tags=["чтение"],
        role="manager",
        question="?",
        answer="ответ",
        status="complete",
        model="m",
        tools=[],
        seconds=1.0,
        checks=[c.CheckResult("вызван инструмент", False, "вызваны: никакие")],
    )
    from datetime import UTC, datetime

    report = render_markdown([result], datetime.now(UTC), with_judge=False)
    assert "❌ 0/1" in report and "Машинные проверки: 0 из 1" in report
    assert "вызваны: никакие" in report


async def test_eval_users_are_service_accounts(session_factory, stock_case):
    provider = ScriptedProvider()
    provider.push(*text_reply("ок"))
    llm, recorders = recording_router(router_with(provider))
    await run_case(stock_case, llm, recorders, date.today())
    async with session_factory() as s:
        user = await s.scalar(select(User).where(User.login == "eval-manager"))
        assert user.role == Role.MANAGER
