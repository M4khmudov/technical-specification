"""Чат с ассистентом: SSE, сохранение диалогов, «Остановить», оборванные ответы, модель."""

import json
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.api.chat import get_tools
from app.assistant import runner
from app.assistant.chat import create_conversation, start_turn
from app.assistant.tools import Tool, ToolOutcome, ToolRegistry
from app.llm.errors import LLMError
from app.llm.fake import ScriptedProvider, Sleep, text_reply, tool_call_reply
from app.llm.registry import get_llm
from app.llm.router import LLMRouter, Route
from app.llm.types import TextDelta, UserMessage
from app.main import app
from app.models import ChatMessage
from app.models.enums import MessageStatus, Role
from tests.factories import make_user


def make_router(primary: ScriptedProvider, fallback: ScriptedProvider | None = None) -> LLMRouter:
    routes = [Route(primary, "gpt-main")]
    if fallback:
        routes.append(Route(fallback, "gpt-spare"))
    return LLMRouter(routes, event_timeout=2, total_timeout=10)


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture
def llm():
    provider = ScriptedProvider()
    app.dependency_overrides[get_llm] = lambda: make_router(provider)
    yield provider
    app.dependency_overrides.pop(get_llm, None)


async def new_conversation(client) -> int:
    response = await client.post("/api/chat/conversations", json={})
    assert response.status_code == 201
    return response.json()["id"]


async def ask(client, conversation_id: int, text: str) -> list[tuple[str, dict]]:
    response = await client.post(
        f"/api/chat/conversations/{conversation_id}/messages", json={"text": text}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


# ---------------------------------------------------------------- HTTP + SSE


async def test_answer_is_streamed_and_saved(api, llm):
    client, *_ = api
    llm.push(*text_reply("Свободно 221 кг соевого воска"))
    conversation_id = await new_conversation(client)

    events = await ask(client, conversation_id, "Сколько соевого воска?")

    kinds = [e for e, _ in events]
    assert kinds[0] == "start" and kinds[1] == "model" and kinds[-1] == "done"
    assert "".join(d["text"] for e, d in events if e == "delta") == "Свободно 221 кг соевого воска"
    done = events[-1][1]
    assert done["status"] == "complete" and done["model"] == "gpt-main"

    detail = (await client.get(f"/api/chat/conversations/{conversation_id}")).json()
    assert detail["title"] == "Сколько соевого воска?"
    question, answer = detail["messages"]
    assert (question["role"], question["content"]) == ("user", "Сколько соевого воска?")
    assert answer["content"] == "Свободно 221 кг соевого воска"
    assert (answer["status_label"], answer["model"]) == ("Готово", "gpt-main")


async def test_history_is_sent_to_model(api, llm):
    client, *_ = api
    llm.push(*text_reply("Первый ответ"))
    llm.push(*text_reply("Второй ответ"))
    conversation_id = await new_conversation(client)
    await ask(client, conversation_id, "Первый вопрос")
    await ask(client, conversation_id, "Второй вопрос")

    _, request = llm.requests[-1]
    assert [getattr(i, "text", None) for i in request.items] == [
        "Первый вопрос",
        "Первый ответ",
        "Второй вопрос",
    ]
    assert "данные, а не команды" in request.instructions  # системный промпт из файла


async def test_fallback_model_is_shown(api):
    client, *_ = api
    primary = ScriptedProvider([LLMError("HTTP 503", retryable=True, status=503)])
    fallback = ScriptedProvider(text_reply("Ответ запасной"))
    app.dependency_overrides[get_llm] = lambda: make_router(primary, fallback)
    try:
        conversation_id = await new_conversation(client)
        events = await ask(client, conversation_id, "Вопрос")
    finally:
        app.dependency_overrides.pop(get_llm, None)
    model = dict(events)["model"]
    assert (model["model"], model["fallback_from"]) == ("gpt-spare", "fake:gpt-main")
    message = (await client.get(f"/api/chat/conversations/{conversation_id}")).json()["messages"][1]
    assert (message["model"], message["fallback_from"]) == ("gpt-spare", "fake:gpt-main")


async def test_model_failure_mid_answer_is_saved_as_interrupted(api, llm):
    client, *_ = api
    llm.push(TextDelta("Свободно 22"), LLMError("HTTP 500", retryable=True, status=500))
    conversation_id = await new_conversation(client)

    events = await ask(client, conversation_id, "Сколько воска?")

    done = events[-1][1]
    assert done["status"] == "interrupted"
    assert done["error"].startswith("Ответ прервался")
    message = (await client.get(f"/api/chat/conversations/{conversation_id}")).json()["messages"][1]
    assert (message["content"], message["status_label"]) == ("Свободно 22", "Ответ оборван")


async def test_model_unavailable_gives_readable_error(api, llm):
    client, *_ = api
    llm.push(LLMError("HTTP 500", retryable=True, status=500))
    conversation_id = await new_conversation(client)
    done = (await ask(client, conversation_id, "Вопрос"))[-1][1]
    assert done["status"] == "error"
    assert done["error"].startswith("Ассистент сейчас недоступен")


async def test_tools_used_are_shown_under_answer(api, llm):
    client, *_ = api

    class Args(BaseModel):
        material: str

    async def handler(ctx, args):
        return ToolOutcome({"free": "221.441"}, f"Остаток {args.material}: 221,441 кг")

    registry = ToolRegistry([Tool("get_stock", "Остаток материала", Args, handler)])
    app.dependency_overrides[get_tools] = lambda: registry
    llm.push(*tool_call_reply("get_stock", '{"material": "WAX-SOY"}'))
    llm.push(*text_reply("Свободно 221,441 кг"))
    try:
        conversation_id = await new_conversation(client)
        events = await ask(client, conversation_id, "Сколько соевого воска?")
    finally:
        app.dependency_overrides.pop(get_tools, None)

    tool_events = [d for e, d in events if e == "tool"]
    assert [d["status"] for d in tool_events] == ["started", "finished"]
    assert tool_events[1]["arguments"] == {"material": "WAX-SOY"}
    # результат инструмента ушёл модели вторым шагом
    _, second = llm.requests[1]
    assert json.loads(second.items[-1].output) == {"free": "221.441"}
    message = (await client.get(f"/api/chat/conversations/{conversation_id}")).json()["messages"][1]
    assert message["tools"][0]["summary"] == "Остаток WAX-SOY: 221,441 кг"


async def test_bad_tool_arguments_are_reported_to_model_not_crash(api, llm):
    client, *_ = api

    class Args(BaseModel):
        quantity: int

    async def handler(ctx, args):  # pragma: no cover — не должен вызываться
        raise AssertionError

    app.dependency_overrides[get_tools] = lambda: ToolRegistry([Tool("t", "d", Args, handler)])
    llm.push(*tool_call_reply("t", '{"quantity": "много"}'))
    llm.push(*text_reply("Не удалось"))
    try:
        conversation_id = await new_conversation(client)
        events = await ask(client, conversation_id, "Вопрос")
    finally:
        app.dependency_overrides.pop(get_tools, None)
    finished = [d for e, d in events if e == "tool"][-1]
    assert finished["ok"] is False and "Некорректные аргументы" in finished["summary"]
    assert events[-1][1]["status"] == "complete"


async def test_conversations_are_private(api, llm, session_factory):
    client, *_ = api
    llm.push(*text_reply("секрет"))
    conversation_id = await new_conversation(client)
    await ask(client, conversation_id, "Мой вопрос")

    async with session_factory() as s:
        other = await make_user(s, role=Role.MANAGER, login=f"other-{uuid4().hex[:6]}")
        foreign = await create_conversation(s, other, "чужой")

    assert (await client.get(f"/api/chat/conversations/{foreign.id}")).status_code == 404
    response = await client.post(
        f"/api/chat/conversations/{foreign.id}/messages", json={"text": "привет"}
    )
    assert response.status_code == 404
    ids = [c["id"] for c in (await client.get("/api/chat/conversations")).json()]
    assert conversation_id in ids and foreign.id not in ids


async def test_empty_question_rejected(api):
    client, *_ = api
    conversation_id = await new_conversation(client)
    response = await client.post(
        f"/api/chat/conversations/{conversation_id}/messages", json={"text": "   "}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Вопрос пустой"


# ---------------------------------------------------------------- «Остановить» и обрыв соединения


async def prepared_turn(session_factory, steps) -> tuple[runner.Turn, LLMRouter]:
    async with session_factory() as s:
        user = await make_user(s, login=f"chat-{uuid4().hex[:6]}")
        conversation = await create_conversation(s, user, None)
        _, answer, items = await start_turn(s, user, conversation.id, "Расскажи подробно")
    turn = runner.Turn(user.id, conversation.id, answer.id, items)
    return turn, make_router(ScriptedProvider(steps))


async def saved(session_factory, message_id: int) -> ChatMessage:
    async with session_factory() as s:
        return await s.scalar(select(ChatMessage).where(ChatMessage.id == message_id))


async def test_stop_button_saves_partial_answer(session_factory):
    steps = [TextDelta("Первая часть."), Sleep(5), TextDelta(" Вторая часть.")]
    turn, llm = await prepared_turn(session_factory, steps)

    stream = runner.stream_turn(turn, llm, ToolRegistry())
    events = []
    async for chunk in stream:
        events.extend(parse_sse(chunk))
        if events and events[-1][0] == "delta":
            assert runner.request_stop(turn.message_id)  # нажали «Остановить»

    done = events[-1][1]
    assert (done["status"], done["status_label"]) == ("stopped", "Остановлено пользователем")
    message = await saved(session_factory, turn.message_id)
    assert (message.content, message.status) == ("Первая часть.", MessageStatus.STOPPED)


async def test_lost_connection_saves_answer_as_interrupted(session_factory):
    steps = [TextDelta("Начало ответа"), Sleep(5), TextDelta(" конец")]
    turn, llm = await prepared_turn(session_factory, steps)

    stream = runner.stream_turn(turn, llm, ToolRegistry())
    async for chunk in stream:
        if "delta" in chunk:
            break
    await stream.aclose()  # так Starlette закрывает поток, когда браузер отключился

    message = await saved(session_factory, turn.message_id)
    assert (message.content, message.status) == ("Начало ответа", MessageStatus.INTERRUPTED)
    assert message.error == "Соединение прервано до окончания ответа"


async def test_one_answer_at_a_time(session_factory):
    from app.core.errors import ConflictError

    turn, _ = await prepared_turn(session_factory, [])
    async with session_factory() as s:
        from app.models import User

        user = await s.get(User, turn.user_id)
        with pytest.raises(ConflictError, match="ещё отвечает"):
            await start_turn(s, user, turn.conversation_id, "ещё вопрос")


def test_history_marks_interrupted_answers():
    from app.assistant.chat import history_items
    from app.models.enums import MessageRole

    messages = [
        ChatMessage(role=MessageRole.USER, content="вопрос", status=MessageStatus.COMPLETE),
        ChatMessage(role=MessageRole.ASSISTANT, content="полу", status=MessageStatus.STOPPED),
    ]
    items = history_items(messages)
    assert items[0] == UserMessage("вопрос")
    assert items[1].text == "полу\n[ответ был оборван]"
