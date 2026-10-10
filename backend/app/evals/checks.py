"""Машинные проверки ответа ассистента — отдельно от оценки LLM-судьи.

Каждая проверка возвращает CheckResult(name, passed, details). Ожидаемые числа
считаются из базы в момент прогона (демо-данные зависят от текущей даты).
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

NUMBER_RE = re.compile(r"(?<![\d.,])-?\d{1,3}(?:[\s  ]\d{3})+(?:[.,]\d+)?|-?\d+(?:[.,]\d+)?")


def numbers_in(text: str) -> list[Decimal]:
    """Все числа из текста: «1 250,5», «1250.50», «5,467», «29.124»."""
    result = []
    for raw in NUMBER_RE.findall(text):
        cleaned = re.sub(r"[\s  ]", "", raw).replace(",", ".")
        try:
            result.append(Decimal(cleaned))
        except InvalidOperation:
            continue
    return result


def close(a: Decimal, b: Decimal, rel: Decimal = Decimal("0.005"), abs_: Decimal = Decimal("0.01")):
    return abs(a - b) <= max(abs_, abs(b) * rel)


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
