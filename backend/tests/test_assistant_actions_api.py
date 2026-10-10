"""Сквозной сценарий через чат: модель предлагает действие → карточка → «Подтвердить»."""

import json
from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.llm.fake import ScriptedProvider, text_reply, tool_call_reply
from app.llm.registry import get_llm
from app.llm.router import LLMRouter, Route
from app.main import app
from app.models import Recipe, RecipeItem
from app.models.enums import Role
from tests.test_chat import parse_sse

PLANNED_ON = (date.today() + timedelta(days=3)).isoformat()


@pytest.fixture
def api_role():
    return Role.TECHNOLOGIST


@pytest.fixture
async def recipe_name(api, session_factory):
    _, material, _ = api
    name = f"Лаванда {uuid4().hex[:6]}"
    async with session_factory() as s:
        s.add(Recipe(name=name, items=[RecipeItem(material_id=material.id, qty_per_unit="0.2")]))
        await s.commit()
    return name


@pytest.fixture
def llm():
    provider = ScriptedProvider()
    app.dependency_overrides[get_llm] = lambda: LLMRouter(
        [Route(provider, "gpt-main")], event_timeout=2, total_timeout=10
    )
    yield provider
    app.dependency_overrides.pop(get_llm, None)


async def chat(client, text: str) -> tuple[int, list]:
    conversation_id = (await client.post("/api/chat/conversations", json={})).json()["id"]
    response = await client.post(
        f"/api/chat/conversations/{conversation_id}/messages", json={"text": text}
    )
    return conversation_id, parse_sse(response.text)


async def test_plan_candles_via_assistant_then_confirm(api, llm, recipe_name):
    client, *_ = api
    arguments = json.dumps({"recipe": recipe_name, "quantity": 200, "planned_on": PLANNED_ON})
    llm.push(*tool_call_reply("create_job", arguments))
    llm.push(*text_reply("Подготовил задание. Нажмите «Подтвердить»."))

    conversation_id, events = await chat(client, f"Запланируй 200 свечей «{recipe_name}»")

    finished = [d for e, d in events if e == "tool" and d["status"] == "finished"][0]
    card = finished["action"]
    assert card["status"] == "pending"
    assert card["preview"]["quantity"] == 200
    # модель получила «не выполнено, нужно подтверждение»
    tool_result = json.loads(llm.requests[1][1].items[-1].output)
    assert tool_result["status"] == "awaiting_user_confirmation"

    jobs_before = (await client.get("/api/jobs")).json()["total"]
    detail = (await client.get(f"/api/chat/conversations/{conversation_id}")).json()
    assert detail["messages"][1]["actions"][0]["id"] == card["id"]

    confirmed = await client.post(f"/api/chat/actions/{card['id']}/confirm")
    assert confirmed.status_code == 200
    result = confirmed.json()
    assert result["status_label"] == "Выполнено"
    assert (await client.get("/api/jobs")).json()["total"] == jobs_before + 1
    job = (await client.get(f"/api/jobs/{result['result']['job_id']}")).json()
    assert (job["quantity"], job["status"], job["planned_on"]) == (200, "planned", PLANNED_ON)

    # статус в истории диалога обновился
    detail = (await client.get(f"/api/chat/conversations/{conversation_id}")).json()
    assert detail["messages"][1]["actions"][0]["status"] == "confirmed"

    again = await client.post(f"/api/chat/actions/{card['id']}/confirm")
    assert again.status_code == 409


async def test_reject_over_http(api, llm, recipe_name):
    client, *_ = api
    arguments = json.dumps({"recipe": recipe_name, "quantity": 5, "planned_on": PLANNED_ON})
    llm.push(*tool_call_reply("create_job", arguments))
    llm.push(*text_reply("Подтвердите"))
    _, events = await chat(client, "Запланируй 5 штук")
    card = [d for e, d in events if e == "tool" and d["status"] == "finished"][0]["action"]

    response = await client.post(f"/api/chat/actions/{card['id']}/reject")
    assert response.json()["status"] == "rejected"


@pytest.mark.parametrize("api_role", [Role.STOREKEEPER])
async def test_storekeeper_gets_refusal_through_chat(api, llm, recipe_name):
    client, *_ = api
    arguments = json.dumps({"recipe": recipe_name, "quantity": 5, "planned_on": PLANNED_ON})
    llm.push(*tool_call_reply("create_job", arguments))
    llm.push(*text_reply("У вас нет прав"))

    _, events = await chat(client, "Запланируй 5 свечей")

    finished = [d for e, d in events if e == "tool" and d["status"] == "finished"][0]
    assert finished["ok"] is False
    assert "action" not in finished
    assert finished["summary"].startswith("Недостаточно прав")
    # модели передан отказ, чтобы она объяснила его пользователю
    assert "Недостаточно прав" in llm.requests[1][1].items[-1].output
