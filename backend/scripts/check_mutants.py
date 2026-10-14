"""Мутационная проверка тестов: вносит в код реалистичные ошибки по одной и проверяет,
что тесты их ловят. Код возвращается на место после каждой мутации.

    cd backend && python scripts/check_mutants.py

Нужен тот же PostgreSQL, что для pytest. Выживший мутант (тесты прошли с ошибкой в
коде) — место, которое тесты не защищают; скрипт тогда завершается с кодом 1.
"""

import subprocess  # noqa: S404 — запускаем свой же pytest
import sys
from pathlib import Path

BATCH_QUERY_END = (
    "        .execution_options(populate_existing=True)\n"
    "    )\n"
    "    return list((await session.scalars(stmt)).all())"
)
LOCK = "        .with_for_update()\n"
CLAIM = (
    "    action.status = ActionStatus.CONFIRMED\n"
    "    action.decided_at = _now()\n"
    "    await session.commit()\n"
    "    try:"
)

# (что сломано, файл, было, стало, какие тесты должны упасть)
MUTANTS = [
    (
        "снята блокировка партий при резерве",
        "app/services/production.py",
        LOCK + BATCH_QUERY_END,
        BATCH_QUERY_END,
        "tests/test_guards.py -k locked_batch",
    ),
    (
        "снята блокировка строки задания",
        "app/services/production.py",
        "        .with_for_update(of=ProductionJob)\n",
        "",
        "tests/test_guards.py -k one_outcome",
    ),
    (
        "снят populate_existing у партий",
        "app/services/production.py",
        LOCK + BATCH_QUERY_END,
        LOCK + "    )\n    return list((await session.scalars(stmt)).all())",
        "tests/test_guards.py -k stale",
    ),
    (
        "дорезерв новой строкой в той же партии",
        "app/services/production.py",
        "        if batch.id in existing:",
        "        if False:",
        "tests/test_guards.py -k rereserve",
    ),
    (
        "перерасход не по FIFO",
        "app/services/production.py",
        "        for batch in own:\n            if left <= 0:\n                break",
        "        for batch in reversed(own):\n            if left <= 0:\n                break",
        "tests/test_guards.py -k overrun",
    ),
    (
        "снята блокировка партии при приёмке",
        "app/services/batches.py",
        "select(Batch).where(Batch.id == batch_id).with_for_update()",
        "select(Batch).where(Batch.id == batch_id)",
        "tests/test_guards.py -k accept_and_reject",
    ),
    (
        "банковское округление",
        "app/services/batches.py",
        ".quantize(UNIT_COST_STEP, ROUND_HALF_UP)",
        ".quantize(UNIT_COST_STEP)",
        "tests/test_guards.py -k rounded",
    ),
    (
        "история без маскирования",
        "app/assistant/runner.py",
        "mask_items(pii, turn.items)",
        "turn.items",
        "tests/test_guards.py -k masked",
    ),
    (
        "нет лимита шагов",
        "app/assistant/agent.py",
        "    for _ in range(MAX_STEPS):",
        "    for _ in range(MAX_STEPS + 2):",
        "tests/test_guards.py -k step_limit",
    ),
    (
        "дедупликация памяти без user_id",
        "app/services/memory.py",
        "UserMemory.user_id == user.id, UserMemory.normalized == key",
        "UserMemory.normalized == key",
        "tests/test_guards.py -k two_users",
    ),
    (
        "ошибка в потоке не классифицируется",
        "app/llm/openai_provider.py",
        "        except openai.APIError as exc:",
        "        except ZeroDivisionError as exc:",
        "tests/test_reliability.py -k inside_stream",
    ),
    (
        "подтверждение без захвата предложения",
        "app/services/actions.py",
        CLAIM,
        "    try:",
        "tests/test_tools.py -k concurrent",
    ),
]


def run(name: str, path: str, old: str, new: str, target: str) -> bool:
    """Внести ошибку, прогнать тесты, вернуть код. True — тесты ошибку поймали."""
    file = Path(path)
    original = file.read_text(encoding="utf-8")
    if old not in original:
        raise SystemExit(f"{name}: шаблон не найден в {path} — обновите скрипт")
    file.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
    try:
        result = subprocess.run(  # noqa: S603 — фиксированные аргументы
            [sys.executable, "-m", "pytest", "-q", "-x", *target.split()],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    finally:
        file.write_text(original, encoding="utf-8", newline="\n")
    lines = result.stdout.strip().splitlines()
    caught = result.returncode != 0
    print(f"{'ПОЙМАН' if caught else 'ВЫЖИЛ '}  {name}: {lines[-1] if lines else ''}")
    return caught


if __name__ == "__main__":
    survived = [m[0] for m in MUTANTS if not run(*m)]
    print(f"\nПоймано {len(MUTANTS) - len(survived)} из {len(MUTANTS)}")
    sys.exit(1 if survived else 0)
