# runtime — то, что работает в backend и worker: без тестов и dev-зависимостей.
# dev — runtime + pytest/ruff и тесты (сервис tests в docker-compose, профиль test).
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Сначала зависимости (слой кэшируется, пока не меняется pyproject.toml)
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install "setuptools>=68" . && rm -rf app build

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
RUN pip install --no-deps --no-build-isolation -e . && rm -rf build

COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

EXPOSE 8000
CMD ["/entrypoint.sh"]


FROM runtime AS dev

RUN pip install --no-build-isolation -e ".[dev]" && rm -rf build
COPY tests ./tests
COPY scripts ./scripts
CMD ["pytest", "-q"]
