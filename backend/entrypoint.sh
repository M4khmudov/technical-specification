#!/bin/sh
set -e

# Схема — только через миграции
alembic upgrade head

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
