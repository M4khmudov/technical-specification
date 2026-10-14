"""Мутационная проверка тестов: вносит в код реалистичные ошибки по одной и проверяет,
что тесты их ловят. Код возвращается на место после каждой мутации.

    cd backend && python scripts/check_mutants.py

Нужен тот же PostgreSQL, что для pytest. Выживший мутант (тесты прошли с ошибкой в
коде) — место, которое тесты не защищают; скрипт тогда завершается с кодом 1.
"""

import subprocess
import sys
from pathlib import Path

MUTANTS = [
    (
        "снята блокировка партий при резерве",
        "app/services/production.py",
        "        .with_for_update()\n        .execution_options(populate_existing=True)\n    )\n    return list((await session.scalars(stmt)).all())",
        "        .execution_options(populate_existing=True)\n    )\n    return list((await session.scalars(stmt)).all())",
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
        "        .with_for_update()\n        .execution_options(populate_existing=True)\n    )\n    return list((await session.scalars(stmt)).all())",
        "        .with_for_update()\n    )\n    return list((await session.scalars(stmt)).all())",
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
        "select(UserMemory).where(UserMemory.user_id == user.id, UserMemory.normalized == key)",
        "select(UserMemory).where(UserMemory.normalized == key)",
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
        "    action.status = ActionStatus.CONFIRMED\n    action.decided_at = _now()\n    await session.commit()\n    try:",
        "    try:",
        "tests/test_tools.py -k concurrent",
    ),
]

failed = []
for name, path, old, new, target in MUTANTS:
    p = Path(path)
    original = p.read_text(encoding="utf-8")
    assert old in original, f"{name}: шаблон не найден"
    p.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-x", *target.split()],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        p.write_text(original, encoding="utf-8", newline="\n")
    caught = r.returncode != 0
    print(
        f"{'ПОЙМАН ' if caught else 'ВЫЖИЛ  '} {name}: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''}"
    )
    if not caught:
        failed.append(name)
sys.exit(1 if failed else 0)
