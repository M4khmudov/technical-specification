"""Тесты идут на настоящем PostgreSQL (отдельная база TEST_DATABASE_URL).

Перед прогоном схема пересоздаётся миграциями Alembic, чтобы тесты проверяли
ровно ту схему, которая поедет в прод.
"""

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://candles:candles@localhost:5433/candles_test",
)
# Приложение в тестах тоже смотрит в тестовую базу
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["JWT_SECRET"] = "test-secret-at-least-32-bytes-long-0123456789"
# Тесты не ходят в сеть: ключ LLM никогда не подставляется
os.environ.pop("OPENAI_API_KEY", None)


def _run_migrations() -> None:
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"db_url={TEST_DATABASE_URL}"]})()
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
async def engine():
    eng = create_async_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    # env.py вызывает asyncio.run, поэтому миграции гоняем в отдельном потоке
    await asyncio.to_thread(_run_migrations)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    """Сессия внутри внешней транзакции, которая откатывается после теста.

    commit() внутри кода превращается в SAVEPOINT, поэтому тесты не видят данных друг друга.
    Для тестов конкурентности (несколько независимых соединений) нужен отдельный фикстур.
    """
    async with engine.connect() as conn:
        trans = await conn.begin()
        s = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield s
        finally:
            await s.close()
            await trans.rollback()


@pytest.fixture
def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """Фабрика настоящих независимых сессий (данные коммитятся по-настоящему)."""
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def client(engine) -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
