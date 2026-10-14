#!/bin/sh
# Отдельная база для pytest (тесты идут на настоящем PostgreSQL). Владелец — тот же
# пользователь, что задан в POSTGRES_USER: раньше он был зашит («candles»), и смена
# пользователя ломала инициализацию базы.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -c "CREATE DATABASE candles_test OWNER \"$POSTGRES_USER\";"
