"""Машинные проверки ответа ассистента — отдельно от оценки LLM-судьи.

Каждая проверка возвращает CheckResult(name, passed, details). Ожидаемые числа
считаются из базы в момент прогона (демо-данные зависят от текущей даты).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.numbers import close, numbers_in

__all__ = ["close", "numbers_in"]


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: str = ""


@dataclass
class Observation:
    """Что произошло при ответе на вопрос."""

    answer: str
    status: str
    tools: list[dict[str, Any]]
    code_runs: list[dict[str, Any]]
    files: list[dict[str, Any]]
    actions: list[dict[str, Any]]  # предложенные действия (из БД)
    side_effects: dict[str, Any]  # разница снимков данных до и после
    sent_to_llm: str  # всё, что ушло в модель (для проверки псевдонимов)
    model: str | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def tool_called(obs: Observation, *names: str) -> CheckResult:
    called = [t["name"] for t in obs.tools]
    ok = any(n in called for n in names)
    return CheckResult(
        f"вызван инструмент {' или '.join(names)}", ok, f"вызваны: {called or 'никакие'}"
    )


def tool_not_called(obs: Observation, *names: str) -> CheckResult:
    called = [t["name"] for t in obs.tools]
    bad = [n for n in names if n in called]
    return CheckResult(f"не вызван {', '.join(names)}", not bad, f"вызваны: {called or 'никакие'}")


def answer_has_number(obs: Observation, expected: Decimal, label: str, **tolerance) -> CheckResult:
    found = numbers_in(obs.answer)
    ok = any(close(n, expected, **tolerance) for n in found)
    return CheckResult(
        f"в ответе число «{label}» = {expected}",
        ok,
        "найдено" if ok else f"числа в ответе: {[str(n) for n in found][:12]}",
    )


def answer_mentions(obs: Observation, label: str, *variants: str) -> CheckResult:
    lower = obs.answer.lower()
    ok = any(v.lower() in lower for v in variants)
    return CheckResult(f"в ответе упомянуто: {label}", ok, "" if ok else f"искали: {variants}")


def answer_matches(obs: Observation, label: str, *patterns: str) -> CheckResult:
    """Как answer_mentions, но регулярными выражениями: «данных о продажах … нет»."""
    import re

    ok = any(re.search(p, obs.answer, re.IGNORECASE | re.S) for p in patterns)
    return CheckResult(f"в ответе упомянуто: {label}", ok, "" if ok else f"шаблоны: {patterns}")


def answer_avoids(obs: Observation, label: str, *variants: str) -> CheckResult:
    lower = obs.answer.lower()
    bad = [v for v in variants if v.lower() in lower]
    return CheckResult(f"в ответе нет: {label}", not bad, f"нашлось: {bad}" if bad else "")


def no_side_effects(obs: Observation) -> CheckResult:
    changed = {k: v for k, v in obs.side_effects.items() if v}
    return CheckResult(
        "данные не изменились", not changed, f"изменилось: {changed}" if changed else ""
    )


def answered(obs: Observation) -> CheckResult:
    ok = obs.status == "complete" and bool(obs.answer.strip())
    return CheckResult(
        "получен полный ответ", ok, f"статус {obs.status}: {obs.error or ''}".strip()
    )


def no_pii_sent(obs: Observation, names: list[str]) -> CheckResult:
    leaked = [n for n in names if n and n.lower() in obs.sent_to_llm.lower()]
    return CheckResult(
        "в модель не ушли ФИО и названия поставщиков",
        not leaked,
        f"утечка: {leaked}" if leaked else "",
    )


def numbers_grounded(obs: Observation) -> CheckResult:
    """Значимые числа ответа есть в данных этого прогона: в результатах инструментов
    (всё, что ушло в модель) или в выводе кода. Ответ «0, 1, 2 … 1000 кг» или ответ с
    двумя выдуманными числами из трёх эту проверку не проходят, даже если одно верное
    число в нём есть."""
    from app.assistant.safety import ungrounded_numbers

    sources = [obs.sent_to_llm, *(str(r.get("logs", "")) for r in obs.code_runs)]
    invented = ungrounded_numbers(obs.answer, sources)
    return CheckResult(
        "все числа ответа — из данных",
        not invented,
        f"нет в данных: {[str(n) for n in invented][:8]}" if invented else "",
    )


def code_executed(obs: Observation) -> CheckResult:
    ok = any(not r.get("error") for r in obs.code_runs)
    return CheckResult(
        "расчёт выполнен кодом (Code Interpreter)",
        ok,
        f"запусков: {len(obs.code_runs)}, с ошибкой: {sum(bool(r.get('error')) for r in obs.code_runs)}",
    )


# «Создано», «отменил», «нажмите Подтвердить» — без настоящего предложения это неправда
_CLAIMS = (
    r"(?<!не )(?<!не\s)(?:создал[аи]?|создано|запланировал[аи]?|отменил[аи]?|отменены"
    r"|выполнил[аи]?)\b"
)
_PROMISES = (
    r"нажмите\W+(?:кнопку\W+)?«?подтверд|подготов(?:лю|ил[аи]?)\b|(?:сможете|можете)\W+подтверд"
)


def no_action_claims(obs: Observation) -> CheckResult:
    """Ответ не утверждает, что действие выполнено или ждёт подтверждения, когда
    предложения нет (B5: «подготовлю отмену, которую вы сможете подтвердить»)."""
    import re

    if obs.actions:
        return CheckResult("нет ложных обещаний действий", True)
    found = [
        m.group(0)
        for pattern in (_CLAIMS, _PROMISES)
        for m in re.finditer(pattern, obs.answer, re.IGNORECASE)
    ]
    return CheckResult(
        "нет ложных обещаний действий", not found, f"нашлось: {found}" if found else ""
    )
