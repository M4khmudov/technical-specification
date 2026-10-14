"""S4: надёжность ответа ассистента на граничных путях."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import openai
import pytest
from sqlalchemy import select

from app.assistant import runner
from app.assistant.chat import create_conversation, start_turn
from app.assistant.code_interpreter import CodeInterpreterSession
from app.assistant.tools import Tool, ToolOutcome, ToolRegistry
from app.llm.errors import LLMError
from app.llm.fake import ScriptedProvider, Sleep, text_reply, tool_call_reply
from app.llm.openai_provider import OpenAIProvider, map_event
from app.llm.router import LLMRouter, Route
from app.llm.types import Completed, Heartbeat, LLMRequest, TextDelta, UserMessage
from app.models import ChatMessage
from app.models.enums import MessageStatus
from tests.factories import make_user
from tests.test_chat import parse_sse

REQUEST = LLMRequest(instructions="system", items=[UserMessage("Построй график")])


def text_of(events) -> str:
    return "".join(e.text for e in events if isinstance(e, TextDelta))


# ---------------------------------------------------------------- 1. Code Interpreter молчит


async def test_silent_code_execution_does_not_trip_the_event_timeout():
    """Пока код выполняется, событий нет дольше обычного таймаута — это не сбой."""
    primary = ScriptedProvider(
        [Heartbeat(slow=True), Sleep(0.4), TextDelta("готово"), Completed(model="m")]
    )
    fallback = ScriptedProvider(text_reply("запасная"))
    router = LLMRouter(
        [Route(primary, "main"), Route(fallback, "spare")],
        event_timeout=0.1,
        total_timeout=5,
        slow_event_timeout=2,
    )
    events = [e async for e in router.stream(REQUEST)]
    assert text_of(events) == "готово"
    assert fallback.requests == []


async def test_heartbeat_is_not_the_start_of_an_answer():
    """Служебные события — ещё не ответ: при ошибке после них включается запасная."""
    primary = ScriptedProvider([Heartbeat(), LLMError("HTTP 500", retryable=True, status=500)])
    fallback = ScriptedProvider(text_reply("запасная ответила"))
    router = LLMRouter(
        [Route(primary, "main"), Route(fallback, "spare")], event_timeout=1, total_timeout=5
    )
    events = [e async for e in router.stream(REQUEST)]
    assert text_of(events) == "запасная ответила"


# ---------------------------------------------------------------- 2. обрезано по лимиту


def test_incomplete_response_is_length_not_stop():
    response = SimpleNamespace(id="r1", model="gpt-4.1", status="incomplete", output=[], usage=None)
    event = map_event(SimpleNamespace(type="response.incomplete", response=response))
    assert event.finish == "length"


async def test_truncated_answer_is_not_saved_as_complete(session_factory):
    turn, llm = await _turn(session_factory, [TextDelta("Длинный ответ"), _length()])
    events = await _run(turn, llm)
    assert events[-1][1]["status"] == "interrupted"
    message = await _saved(session_factory, turn.message_id)
    assert message.status == MessageStatus.INTERRUPTED
    assert "предел длины" in message.error


def _length() -> Completed:
    return Completed(model="gpt-main", finish="length")


# ---------------------------------------------------------------- 3. ошибка внутри потока


async def test_error_event_inside_stream_switches_to_fallback():
    """Настоящий SDK: событие error в SSE-потоке — сбой провайдера, а не «внутренняя ошибка»."""
    body = (
        'event: error\ndata: {"type": "error", "code": "server_error", '
        '"message": "The server had an error", "param": null, "sequence_number": 0}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = openai.AsyncOpenAI(
        api_key="sk-test",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    primary = OpenAIProvider("sk-test", timeout=5, client=client)
    fallback = ScriptedProvider(text_reply("ответ запасной"))
    router = LLMRouter(
        [Route(primary, "gpt-4.1"), Route(fallback, "spare")], event_timeout=2, total_timeout=5
    )
    events = [e async for e in router.stream(REQUEST)]
    assert text_of(events) == "ответ запасной"
    assert events[0].fallback_from == "openai:gpt-4.1"


# ---------------------------------------------------------------- 4. «Остановить» при уборке


async def test_stop_during_file_cleanup_keeps_the_answer(session_factory, monkeypatch):
    """Ответ уже показан, идёт удаление файлов у провайдера — «Остановить» или закрытая
    вкладка не теряют ответ, поток SSE завершается, диалог не блокируется."""
    cleanup_started = asyncio.Event()

    async def slow_cleanup(self):
        cleanup_started.set()
        await asyncio.sleep(3)

    monkeypatch.setattr(CodeInterpreterSession, "cleanup", slow_cleanup)
    turn, llm = await _turn(session_factory, text_reply("Полный ответ"))

    events = await asyncio.wait_for(_run(turn, llm), timeout=2)  # поток закрылся сам
    assert events[-1][0] == "done"
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    assert runner.request_stop(turn.message_id) is False  # останавливать уже нечего

    message = await _saved(session_factory, turn.message_id)
    assert (message.content, message.status) == ("Полный ответ", MessageStatus.COMPLETE)


async def test_cancel_during_cleanup_still_saves(session_factory, monkeypatch):
    async def slow_cleanup(self):
        await asyncio.sleep(3)

    monkeypatch.setattr(CodeInterpreterSession, "cleanup", slow_cleanup)
    monkeypatch.setattr(runner, "CLEANUP_TIMEOUT_SECONDS", 0.3)
    turn, llm = await _turn(session_factory, text_reply("Ответ"))
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(runner.generate(turn, llm, ToolRegistry(), queue))
    done = await asyncio.wait_for(queue.get(), timeout=2)
    while done.type != "done":
        done = await asyncio.wait_for(queue.get(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)  # уборка ограничена по времени
    message = await _saved(session_factory, turn.message_id)
    assert (message.content, message.status) == ("Ответ", MessageStatus.COMPLETE)


# ---------------------------------------------------------------- 5. какая модель ответила


async def test_fallback_on_any_step_is_shown(session_factory):
    """Шаг 1 ответила запасная (основная недоступна), шаг 2 — основная: под ответом
    всё равно видно, что работала запасная."""
    primary = ScriptedProvider([LLMError("HTTP 503", retryable=True, status=503)])
    primary.push(*text_reply("Свободно 5 кг"))
    fallback = ScriptedProvider(tool_call_reply("probe", "{}"))
    llm = LLMRouter(
        [Route(primary, "gpt-main"), Route(fallback, "gpt-spare")], event_timeout=2, total_timeout=5
    )
    turn, _ = await _turn(session_factory, [])
    events = await _run(turn, llm, _probe_registry())
    done = events[-1][1]
    assert (done["model"], done["fallback_from"]) == ("gpt-spare", "fake:gpt-main")


# ---------------------------------------------------------------- 6. текст между шагами


async def test_text_between_steps_is_kept_and_separated(session_factory):
    """«…задание №27.» + вызов инструмента + «Подготовить…» — не склеиваются, и модель на
    втором шаге видит, что уже написала."""
    provider = ScriptedProvider(
        [TextDelta("Проверяю задание №27."), *tool_call_reply("probe", "{}")],
        text_reply("Готово."),
    )
    llm = LLMRouter([Route(provider, "gpt-main")], event_timeout=2, total_timeout=5)
    turn, _ = await _turn(session_factory, [])
    events = await _run(turn, llm, _probe_registry())
    assert events[-1][1]["content"] == "Проверяю задание №27.\n\nГотово."
    second_request = provider.requests[1][1]
    assert any(getattr(i, "text", None) == "Проверяю задание №27." for i in second_request.items)


# ---------------------------------------------------------------- помощники


class _NoArgs(__import__("pydantic").BaseModel):
    pass


def _probe_registry() -> ToolRegistry:
    async def probe(ctx, args):
        return ToolOutcome({"ok": True}, "проверено")

    return ToolRegistry([Tool("probe", "проверка", _NoArgs, probe)])


async def _turn(session_factory, steps):
    async with session_factory() as s:
        user = await make_user(s, login=f"rel-{uuid4().hex[:6]}")
        conversation = await create_conversation(s, user, None)
        _, answer, items = await start_turn(s, user, conversation.id, "Вопрос")
    turn = runner.Turn(user.id, conversation.id, answer.id, items)
    llm = LLMRouter([Route(ScriptedProvider(steps), "gpt-main")], event_timeout=2, total_timeout=5)
    return turn, llm


async def _run(turn, llm, registry=None) -> list:
    events = []
    async for chunk in runner.stream_turn(turn, llm, registry or ToolRegistry()):
        events.extend(parse_sse(chunk))
    return events


async def _saved(session_factory, message_id: int) -> ChatMessage:
    async with session_factory() as s:
        return await s.scalar(select(ChatMessage).where(ChatMessage.id == message_id))
