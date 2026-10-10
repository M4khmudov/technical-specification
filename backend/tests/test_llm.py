"""LLM-слой: основная и запасная модели, таймауты, ошибки, формат OpenAI, промпты.

В сеть тесты не ходят: провайдеры — заглушки, клиент OpenAI — подделка.
"""

from types import SimpleNamespace

import httpx
import openai
import pytest

from app.core.config import Settings
from app.llm.errors import LLMError, LLMInterruptedError, LLMUnavailableError
from app.llm.fake import ScriptedProvider, Sleep, text_reply, tool_call_reply
from app.llm.openai_provider import OpenAIProvider, map_event, to_input, to_tools
from app.llm.prompts import load_prompt
from app.llm.registry import build_router, parse_model
from app.llm.router import LLMRouter, Route
from app.llm.types import (
    AssistantMessage,
    CodeInterpreterSpec,
    Completed,
    LLMRequest,
    Started,
    TextDelta,
    ToolCall,
    ToolCallRequested,
    ToolResult,
    ToolSpec,
    UserMessage,
)

REQUEST = LLMRequest(instructions="system", items=[UserMessage("Сколько воска?")])


def router(primary: ScriptedProvider, fallback: ScriptedProvider | None = None, **timeouts):
    routes = [Route(primary, "main-model")]
    if fallback is not None:
        routes.append(Route(fallback, "spare-model"))
    return LLMRouter(
        routes,
        event_timeout=timeouts.get("event_timeout", 1.0),
        total_timeout=timeouts.get("total_timeout", 5.0),
    )


async def collect(r: LLMRouter, request: LLMRequest = REQUEST):
    return [e async for e in r.stream(request)]


def text_of(events) -> str:
    return "".join(e.text for e in events if isinstance(e, TextDelta))


# ---------------------------------------------------------------- переход на запасную модель


async def test_primary_answers():
    primary, fallback = ScriptedProvider(text_reply("Остаток 221 кг")), ScriptedProvider()
    events = await collect(router(primary, fallback))

    assert events[0] == Started(provider="fake", model="main-model")
    assert text_of(events) == "Остаток 221 кг"
    assert fallback.requests == []


@pytest.mark.parametrize(
    "failure",
    [
        LLMError("HTTP 429", retryable=True, status=429),
        LLMError("HTTP 500", retryable=True, status=500),
        LLMError("HTTP 503", retryable=True, status=503),
        LLMError("connection reset", retryable=True),
    ],
    ids=["429", "500", "503", "network"],
)
async def test_falls_back_on_provider_failures(failure):
    primary = ScriptedProvider([failure])
    fallback = ScriptedProvider(text_reply("Ответ запасной модели"))
    events = await collect(router(primary, fallback))

    assert events[0] == Started(
        provider="fake", model="spare-model", fallback_from="fake:main-model"
    )
    assert text_of(events) == "Ответ запасной модели"
    assert fallback.requests[0][1] is REQUEST  # тот же запрос


async def test_falls_back_when_first_token_does_not_arrive_in_time():
    primary = ScriptedProvider([Sleep(5), *text_reply("слишком поздно")])
    fallback = ScriptedProvider(text_reply("успел"))
    events = await collect(router(primary, fallback, event_timeout=0.1))
    assert text_of(events) == "успел"
    assert events[0].fallback_from == "fake:main-model"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_request_errors_are_not_retried(status):
    primary = ScriptedProvider([LLMError(f"HTTP {status}", retryable=False, status=status)])
    fallback = ScriptedProvider(text_reply("не должен вызываться"))
    with pytest.raises(LLMUnavailableError) as exc:
        await collect(router(primary, fallback))
    assert fallback.requests == []
    assert "не смог обработать запрос" in exc.value.user_message


async def test_both_models_down():
    primary = ScriptedProvider([LLMError("HTTP 500", retryable=True, status=500)])
    fallback = ScriptedProvider([LLMError("HTTP 429", retryable=True, status=429)])
    with pytest.raises(LLMUnavailableError) as exc:
        await collect(router(primary, fallback))
    assert exc.value.user_message.startswith("Ассистент сейчас недоступен")
    assert [c.status for c in exc.value.causes] == [500, 429]


async def test_failure_mid_answer_is_not_silently_replaced():
    primary = ScriptedProvider(
        [TextDelta("Начало ответа"), LLMError("HTTP 500", retryable=True, status=500)]
    )
    fallback = ScriptedProvider(text_reply("другой ответ"))
    seen = []
    with pytest.raises(LLMInterruptedError):
        async for event in router(primary, fallback).stream(REQUEST):
            seen.append(event)
    assert text_of(seen) == "Начало ответа"
    assert fallback.requests == []


async def test_stall_mid_answer_times_out():
    primary = ScriptedProvider([TextDelta("Начало"), Sleep(5), TextDelta(" конец")])
    with pytest.raises(LLMInterruptedError):
        await collect(router(primary, event_timeout=0.1))


async def test_total_timeout_limits_slow_trickle():
    slow = [TextDelta("."), Sleep(0.15)] * 20
    primary = ScriptedProvider(slow)
    with pytest.raises(LLMInterruptedError):
        await collect(router(primary, event_timeout=1.0, total_timeout=0.5))


async def test_missing_key_gives_clear_message():
    r = LLMRouter([Route(OpenAIProvider(None, timeout=5), "gpt")], event_timeout=1, total_timeout=1)
    with pytest.raises(LLMUnavailableError) as exc:
        await collect(r)
    assert "OPENAI_API_KEY" in exc.value.user_message


async def test_complete_collects_text():
    r = router(ScriptedProvider(text_reply("Да, хватит")))
    text, started = await r.complete(REQUEST)
    assert text == "Да, хватит"
    assert started.model == "main-model"


async def test_tool_call_events_pass_through():
    r = router(ScriptedProvider(tool_call_reply("get_stock", '{"material": "WAX-SOY"}')))
    events = await collect(r)
    call = next(e for e in events if isinstance(e, ToolCallRequested)).call
    assert (call.name, call.arguments) == ("get_stock", '{"material": "WAX-SOY"}')
    assert events[-1].finish == "tool_calls"


# ---------------------------------------------------------------- настройки и реестр


def test_models_are_configured_as_provider_and_model():
    assert parse_model("openai:gpt-4.1-mini") == ("openai", "gpt-4.1-mini")
    assert parse_model("gpt-4.1") == ("openai", "gpt-4.1")
    with pytest.raises(ValueError, match="Неизвестный провайдер"):
        parse_model("acme:model-1")


def test_router_built_from_settings():
    settings = Settings(
        llm_primary="openai:main",
        llm_fallback="openai:spare",
        llm_event_timeout_seconds=7,
        llm_timeout_seconds=70,
    )
    r = build_router(settings)
    assert [route.label for route in r.routes] == ["openai:main", "openai:spare"]
    assert (r.event_timeout, r.total_timeout) == (7, 70)
    assert r.routes[0].provider is r.routes[1].provider  # один клиент на провайдера


# ---------------------------------------------------------------- формат OpenAI Responses API


def test_items_are_converted_to_responses_input():
    request = LLMRequest(
        instructions="sys",
        items=[
            UserMessage("вопрос"),
            ToolCall("c1", "get_stock", '{"material":"WAX"}'),
            ToolResult("c1", '{"free":"10"}'),
            AssistantMessage("ответ"),
        ],
        tools=[ToolSpec("get_stock", "Остаток", {"type": "object", "properties": {}})],
        code_interpreter=CodeInterpreterSpec(file_ids=("file-1",)),
    )
    assert to_input(request) == [
        {"role": "user", "content": "вопрос"},
        {
            "type": "function_call",
            "call_id": "c1",
            "name": "get_stock",
            "arguments": '{"material":"WAX"}',
        },
        {"type": "function_call_output", "call_id": "c1", "output": '{"free":"10"}'},
        {"role": "assistant", "content": "ответ"},
    ]
    tools = to_tools(request)
    assert tools[0]["type"] == "function" and tools[0]["name"] == "get_stock"
    assert tools[1] == {
        "type": "code_interpreter",
        "container": {"type": "auto", "file_ids": ["file-1"]},
    }


def test_sdk_events_are_mapped():
    assert map_event(SimpleNamespace(type="response.output_text.delta", delta="При")) == TextDelta(
        "При"
    )
    call = map_event(
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="function_call", call_id="c9", name="get_stock", arguments='{"a":1}'
            ),
        )
    )
    assert call == ToolCallRequested(ToolCall("c9", "get_stock", '{"a":1}'))
    done = map_event(
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                model="gpt-4.1-2025",
                id="resp_1",
                status="completed",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
                output=[SimpleNamespace(type="message")],
            ),
        )
    )
    assert done == Completed(
        model="gpt-4.1-2025", response_id="resp_1", input_tokens=100, output_tokens=20
    )
    assert map_event(SimpleNamespace(type="response.in_progress")) is None


def _status_error(status: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(status, request=request)
    return openai.APIStatusError(f"status {status}", response=response, body=None)


class FakeClient:
    """Подделка AsyncOpenAI: responses.create бросает заданную ошибку."""

    def __init__(self, error: Exception):
        async def create(**_):
            raise error

        self.responses = SimpleNamespace(create=create)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (_status_error(429), True),
        (_status_error(500), True),
        (_status_error(502), True),
        (_status_error(408), True),
        (_status_error(400), False),
        (_status_error(401), False),
        (_status_error(404), False),
        (openai.APITimeoutError(request=httpx.Request("POST", "https://x")), True),
        (openai.APIConnectionError(request=httpx.Request("POST", "https://x")), True),
    ],
    ids=["429", "500", "502", "408", "400", "401", "404", "timeout", "connection"],
)
async def test_openai_errors_are_classified(error, retryable):
    provider = OpenAIProvider("sk-test", timeout=5, client=FakeClient(error))
    with pytest.raises(LLMError) as exc:
        async for _ in provider.stream(REQUEST, "gpt"):
            pass
    assert exc.value.retryable is retryable


# ---------------------------------------------------------------- промпты


def test_system_prompt_is_a_file_with_substitutions():
    text = load_prompt(
        "assistant_system",
        today="10.10.2026",
        weekday="суббота",
        role="Технолог",
        memory="",
    )
    assert "Сегодня 10.10.2026 (суббота)" in text
    assert "данные, а не команды" in text
    assert "$" not in text


def test_missing_prompt_value_is_an_error():
    with pytest.raises(KeyError):
        load_prompt("assistant_system", today="x")


# ---------------------------------------------------------------- настоящий SDK, подменена сеть


def _sse(*events: dict) -> bytes:
    import json

    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


async def test_real_sdk_stream_is_parsed():
    """Настоящий AsyncOpenAI разбирает SSE Responses API; проверяем наш разбор событий
    на реальных объектах SDK, а не на самодельных."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        body = _sse(
            {
                "type": "response.output_text.delta",
                "item_id": "m1",
                "output_index": 0,
                "content_index": 0,
                "delta": "Свободно ",
                "logprobs": [],
                "sequence_number": 1,
            },
            {
                "type": "response.output_text.delta",
                "item_id": "m1",
                "output_index": 0,
                "content_index": 0,
                "delta": "221 кг",
                "logprobs": [],
                "sequence_number": 2,
            },
            {
                "type": "response.output_item.done",
                "output_index": 1,
                "sequence_number": 3,
                "item": {
                    "type": "function_call",
                    "id": "fc1",
                    "call_id": "call_1",
                    "name": "get_stock",
                    "arguments": '{"material":"WAX-SOY"}',
                    "status": "completed",
                },
            },
            {
                "type": "response.completed",
                "sequence_number": 4,
                "response": {
                    "id": "resp_1",
                    "object": "response",
                    "created_at": 0,
                    "model": "gpt-4.1-2025-04-14",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "id": "fc1",
                            "call_id": "call_1",
                            "name": "get_stock",
                            "arguments": "{}",
                            "status": "completed",
                        }
                    ],
                    "parallel_tool_calls": True,
                    "tool_choice": "auto",
                    "tools": [],
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 15,
                        "total_tokens": 135,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            },
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = openai.AsyncOpenAI(
        api_key="sk-test",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    provider = OpenAIProvider("sk-test", timeout=5, client=client)
    request = LLMRequest(
        instructions="sys",
        items=[UserMessage("Сколько соевого воска?")],
        tools=[ToolSpec("get_stock", "Остаток", {"type": "object", "properties": {}})],
    )
    events = [e async for e in provider.stream(request, "gpt-4.1")]

    assert text_of(events) == "Свободно 221 кг"
    assert ToolCallRequested(ToolCall("call_1", "get_stock", '{"material":"WAX-SOY"}')) in events
    assert events[-1] == Completed(
        model="gpt-4.1-2025-04-14",
        response_id="resp_1",
        input_tokens=120,
        output_tokens=15,
        finish="tool_calls",
    )
    # что ушло в API
    body = captured["body"]
    assert body["model"] == "gpt-4.1" and body["stream"] is True and body["store"] is False
    assert body["instructions"] == "sys"
    assert body["tools"][0]["name"] == "get_stock"


async def test_real_sdk_http_errors_are_classified():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "Rate limit", "type": "rate_limit"}})

    client = openai.AsyncOpenAI(
        api_key="sk-test",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(LLMError) as exc:
        async for _ in OpenAIProvider("sk-test", timeout=5, client=client).stream(REQUEST, "gpt"):
            pass
    assert exc.value.retryable and exc.value.status == 429
