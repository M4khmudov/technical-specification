"""Память ассистента: факты о пользователе, список и удаление, изоляция между пользователями."""

import json
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, DomainError, NotFoundError
from app.core.security import hash_password
from app.llm.fake import ScriptedProvider, text_reply, tool_call_reply
from app.llm.registry import get_llm
from app.llm.router import LLMRouter, Route
from app.main import app
from app.models import User
from app.models.enums import Role
from app.services import memory
from tests.factories import make_user
from tests.test_chat import parse_sse

# ---------------------------------------------------------------- сервис


async def test_remember_list_and_forget(session):
    user = await make_user(session)
    fact, created = await memory.remember(session, user, "  Считать   стоимость в евро ")
    assert created and fact.text == "Считать стоимость в евро"

    again, created = await memory.remember(session, user, "считать стоимость в евро.")
    assert not created and again.id == fact.id  # дубль не плодится

    assert [f.text for f in await memory.list_facts(session, user)] == ["Считать стоимость в евро"]
    await memory.forget(session, user, fact.id)
    assert await memory.list_facts(session, user) == []


async def test_cannot_forget_someone_elses_fact(session):
    owner = await make_user(session)
    stranger = await make_user(session)
    fact, _ = await memory.remember(session, owner, "Мой цех — второй")
    with pytest.raises(NotFoundError):
        await memory.forget(session, stranger, fact.id)
    assert len(await memory.list_facts(session, owner)) == 1


async def test_limits(session):
    user = await make_user(session)
    with pytest.raises(DomainError, match="слишком длинный"):
        await memory.remember(session, user, "а" * 301)
    for i in range(memory.MAX_FACTS):
        await memory.remember(session, user, f"Факт номер {i}")
    with pytest.raises(ConflictError, match="удалите ненужные"):
        await memory.remember(session, user, "Ещё один факт")


# ---------------------------------------------------------------- через чат


@pytest.fixture
def recorder():
    provider = ScriptedProvider()
    app.dependency_overrides[get_llm] = lambda: LLMRouter(
        [Route(provider, "m")], event_timeout=2, total_timeout=10
    )
    yield provider
    app.dependency_overrides.pop(get_llm, None)


async def chat(client, text: str) -> list:
    conversation_id = (await client.post("/api/chat/conversations", json={})).json()["id"]
    response = await client.post(
        f"/api/chat/conversations/{conversation_id}/messages", json={"text": text}
    )
    return parse_sse(response.text)


async def test_fact_remembered_in_chat_is_used_in_a_new_conversation(api, recorder):
    client, *_ = api
    fact = f"Считать стоимость в евро {uuid4().hex[:6]}"
    recorder.push(
        *tool_call_reply(
            "remember_fact", json.dumps({"fact": fact, "user_quote": fact}, ensure_ascii=False)
        )
    )
    recorder.push(*text_reply("Запомнил"))

    events = await chat(client, f"Запомни: {fact}")

    finished = [d for e, d in events if e == "tool" and d["status"] == "finished"][0]
    assert finished["memory"]["text"] == fact and finished["memory"]["new"] is True
    facts = (await client.get("/api/memory")).json()
    assert [f["text"] for f in facts] == [fact]

    # Новый диалог: факт уже в системном промпте
    recorder.push(*text_reply("Считаю в евро"))
    await chat(client, "Сколько стоит задание №1?")
    instructions = recorder.requests[-1][1].instructions
    assert "Что известно о пользователе" in instructions
    assert fact in instructions

    # Удалили — в следующем диалоге факта нет
    assert (await client.delete(f"/api/memory/{facts[0]['id']}")).status_code == 204
    recorder.push(*text_reply("ок"))
    await chat(client, "Ещё вопрос")
    assert fact not in recorder.requests[-1][1].instructions


async def test_facts_never_leak_to_other_users(api, recorder, session_factory, client):
    """Чужие факты не попадают в промпт никогда."""
    client_a, *_ = api
    secret = f"Мой цех — второй, код сейфа {uuid4().hex[:8]}"
    recorder.push(
        *tool_call_reply(
            "remember_fact", json.dumps({"fact": secret, "user_quote": secret}, ensure_ascii=False)
        )
    )
    recorder.push(*text_reply("Запомнил"))
    await chat(client_a, f"Запомни: {secret}")

    # Второй пользователь — отдельный клиент со своим токеном
    async with session_factory() as s:
        login = f"mem-b-{uuid4().hex[:6]}"
        s.add(
            User(
                login=login,
                full_name="Другой Пользователь",
                password_hash=hash_password("pw"),
                role=Role.MANAGER,
            )
        )
        await s.commit()
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_b:
        token = (
            await client_b.post("/api/auth/login", json={"login": login, "password": "pw"})
        ).json()["access_token"]
        client_b.headers["Authorization"] = f"Bearer {token}"
        assert (await client_b.get("/api/memory")).json() == []

        before = len(recorder.requests)
        recorder.push(*text_reply("Ответ B"))
        await chat(client_b, "Какой у меня цех?")

        fact_id = (await client_a.get("/api/memory")).json()[0]["id"]
        assert (await client_b.delete(f"/api/memory/{fact_id}")).status_code == 404

    for _, request in recorder.requests[before:]:
        sent = request.instructions + "".join(getattr(i, "text", "") for i in request.items)
        assert secret not in sent
        assert "Что известно о пользователе" not in request.instructions
    # у владельца факт на месте
    assert [f["text"] for f in (await client_a.get("/api/memory")).json()] == [secret]


async def test_names_in_facts_are_masked_in_prompt(session):
    from app.assistant.pii import Pseudonymizer
    from app.assistant.runner import system_prompt

    user = await make_user(session, role=Role.TECHNOLOGIST)
    pii = Pseudonymizer({42: "Лебедев Олег Ильич"}, {7: "ООО «Медовый луг»"})
    prompt = system_prompt(
        user, ["Мой начальник — Лебедев О. И.", "Воск беру у Медового луга"], pii=pii
    )
    assert "Лебедев" not in prompt and "Медов" not in prompt
    assert "Мой начальник — Сотрудник-42" in prompt
    assert "Воск беру у Поставщик-7" in prompt


async def test_memory_changes_are_in_the_audit_log(session):
    """S10: что ассистент запомнил и что пользователь удалил — видно в журнале."""
    from sqlalchemy import select

    from app.models import AuditLog

    user = await make_user(session)
    fact, _ = await memory.remember(session, user, "Считать в евро")
    await memory.forget(session, user, fact.id)
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == "memory", AuditLog.entity_id == fact.id)
            .order_by(AuditLog.id)
        )
    ).all()
    assert [(r.action, r.user_id) for r in rows] == [("remember", user.id), ("forget", user.id)]
    assert rows[0].after == {"text": "Считать в евро"}
