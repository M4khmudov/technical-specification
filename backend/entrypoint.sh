#!/bin/sh
set -e

# Схема — только через миграции
alembic upgrade head

# Демо-данные (идемпотентно: повторный запуск ничего не дублирует)
if [ "${SEED_DEMO_DATA:-true}" = "true" ]; then
    python -m app.seed
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
