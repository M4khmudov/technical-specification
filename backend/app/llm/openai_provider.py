"""Провайдер OpenAI через Responses API (нужен для встроенного Code Interpreter)."""

from collections.abc import AsyncIterator
from typing import Any

import openai

from app.llm.errors import LLMError
from app.llm.types import (
    AssistantMessage,
    CodeExecuted,
    Completed,
    Event,
    FileCited,
    LLMRequest,
    TextDelta,
    ToolCall,
    ToolCallRequested,
    ToolResult,
    UserMessage,
)

RETRYABLE_STATUSES = {408, 429}


def to_input(request: LLMRequest) -> list[dict[str, Any]]:
    """Нейтральные элементы диалога → input Responses API."""
    result: list[dict[str, Any]] = []
    for item in request.items:
        match item:
            case UserMessage(text=text):
                result.append({"role": "user", "content": text})
            case AssistantMessage(text=text):
                result.append({"role": "assistant", "content": text})
            case ToolCall(call_id=call_id, name=name, arguments=arguments):
                result.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                    }
                )
            case ToolResult(call_id=call_id, output=output):
                result.append(
                    {"type": "function_call_output", "call_id": call_id, "output": output}
                )
    return result


def to_tools(request: LLMRequest) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
            "strict": False,  # аргументы всё равно валидируем сами, как внешний ввод
        }
        for t in request.tools
    ]
    if request.code_interpreter is not None:
        tools.append(
            {
                "type": "code_interpreter",
                "container": {"type": "auto", "file_ids": list(request.code_interpreter.file_ids)},
            }
        )
    return tools


def map_event(event: Any) -> Event | None:
    """Событие SDK → нейтральное событие (или None, если оно нам не нужно)."""
    kind = getattr(event, "type", None)
    if kind == "response.output_text.delta":
        return TextDelta(event.delta)
    if kind == "response.output_item.done":
        item = event.item
        if item.type == "function_call":
            return ToolCallRequested(ToolCall(item.call_id, item.name, item.arguments or "{}"))
        if item.type == "code_interpreter_call":
            outputs = tuple(
                o.model_dump() if hasattr(o, "model_dump") else dict(o)
                for o in (item.outputs or [])
            )
            return CodeExecuted(item.code or "", getattr(item, "container_id", None), outputs)
        return None
    if kind == "response.output_text.annotation.added":
        annotation = event.annotation
        get = (
            annotation.get
            if isinstance(annotation, dict)
            else lambda k: getattr(annotation, k, None)
        )
        if get("type") == "container_file_citation":
            return FileCited(get("container_id"), get("file_id"), get("filename") or "")
        return None
    if kind == "response.completed":
        response = event.response
        usage = getattr(response, "usage", None)
        has_calls = any(o.type == "function_call" for o in (response.output or []))
        incomplete = getattr(response, "status", None) == "incomplete"
        return Completed(
            model=response.model,
            response_id=response.id,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            finish="tool_calls" if has_calls else ("length" if incomplete else "stop"),
        )
    if kind in ("response.failed", "error"):
        error = getattr(getattr(event, "response", None), "error", None) or event
        code = str(getattr(error, "code", "") or "")
        message = str(getattr(error, "message", "") or kind)
        # server_error / rate_limit_exceeded — сбой провайдера, остальное — ошибка запроса
        retryable = code in ("server_error", "rate_limit_exceeded", "") or "rate" in code
        raise LLMError(f"{kind}: {code} {message}", retryable=retryable)
    return None


class OpenAIProvider:
    """OpenAI Responses API. Тот же класс работает с совместимыми провайдерами (Groq):
    другой base_url и флаги того, что провайдер поддерживает."""

    def __init__(
        self,
        api_key: str | None,
        timeout: float,
        client: Any = None,
        *,
        name: str = "openai",
        base_url: str | None = None,
        key_env: str = "OPENAI_API_KEY",
        supports_store: bool = True,  # параметр store=False (не хранить диалоги)
        supports_include: bool = True,  # include=[логи Code Interpreter]
        supports_files: bool = True,  # Files API и файлы контейнеров Code Interpreter
        supports_code_interpreter: bool = True,
    ):
        self.name = name
        self.key_env = key_env
        self.supports_store = supports_store
        self.supports_include = supports_include
        self.supports_files = supports_files
        self.supports_code_interpreter = supports_code_interpreter
        # max_retries=0: повторы и переход на запасную модель делает наш роутер
        self._client = client or (
            openai.AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout)
            if api_key
            else None
        )

    async def stream(self, request: LLMRequest, model: str) -> AsyncIterator[Event]:
        if self._client is None:
            raise LLMError(f"{self.key_env} не задан", retryable=False)
        params: dict[str, Any] = {
            "model": model,
            "instructions": request.instructions,
            "input": to_input(request),
            "stream": True,
        }
        if self.supports_store:
            params["store"] = False  # не храним диалоги на стороне провайдера
        if not self.supports_code_interpreter:
            request = LLMRequest(
                request.instructions,
                request.items,
                request.tools,
                None,
                request.max_output_tokens,
            )
        tools = to_tools(request)
        if tools:
            params["tools"] = tools
        if request.code_interpreter is not None and self.supports_include:
            # логи выполнения кода — чтобы показать их и понять ошибку
            params["include"] = ["code_interpreter_call.outputs"]
        if request.max_output_tokens:
            params["max_output_tokens"] = request.max_output_tokens
        try:
            stream = await self._client.responses.create(**params)
            async for raw in stream:
                event = map_event(raw)
                if event is not None:
                    yield event
        except LLMError:
            raise
        except openai.APITimeoutError as exc:
            raise LLMError("timeout", retryable=True) from exc
        except openai.APIConnectionError as exc:
            raise LLMError(f"connection error: {exc}", retryable=True) from exc
        except openai.APIStatusError as exc:
            retryable = exc.status_code >= 500 or exc.status_code in RETRYABLE_STATUSES
            raise LLMError(
                f"HTTP {exc.status_code}: {exc.message}",
                retryable=retryable,
                status=exc.status_code,
            ) from exc

    # ---------------------------------------------------------------- файлы Code Interpreter

    def _require_client(self) -> Any:
        if self._client is None:
            raise LLMError(f"{self.key_env} не задан", retryable=False)
        if not self.supports_files:
            raise LLMError(
                f"{self.name}: файлы Code Interpreter не поддерживаются", retryable=False
            )
        return self._client

    async def upload_file(self, filename: str, data: bytes) -> str:
        client = self._require_client()
        try:
            uploaded = await client.files.create(file=(filename, data), purpose="user_data")
        except openai.APIError as exc:
            raise LLMError(f"file upload failed: {exc.__class__.__name__}", retryable=True) from exc
        return uploaded.id

    async def delete_file(self, file_id: str) -> None:
        try:
            await self._require_client().files.delete(file_id)
        except openai.APIError:
            pass  # файл всё равно удалится по сроку хранения; ответу это не мешает

    async def list_generated_files(self, container_id: str) -> list[tuple[str, str]]:
        client = self._require_client()
        page = await client.containers.files.list(container_id, limit=100)
        return [(f.id, f.path) for f in page.data if f.source == "assistant"]

    async def download_container_file(self, container_id: str, file_id: str) -> bytes:
        client = self._require_client()
        response = await client.containers.files.content.retrieve(
            file_id, container_id=container_id
        )
        return response.content  # HttpxBinaryResponseContent: тело уже прочитано
