#!/bin/sh
set -e

# Схема — только через миграции
alembic upgrade head

# Демо-данные (идемпотентно: повторный запуск ничего не дублирует)
if [ "${SEED_DEMO_DATA:-true}" = "true" ]; then
    python -m app.seed
fi

# --proxy-headers: адрес клиента из X-Forwarded-For, который ставит nginx (нужен для
# ограничения попыток входа); порт бэкенда снаружи доступен только с этой машины
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
