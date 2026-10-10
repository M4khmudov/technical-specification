"""Генерация ответа в фоне и стриминг по SSE.

Ответ генерируется в отдельной задаче asyncio, SSE-ответ только читает её события
из очереди. Поэтому:
- «Остановить» (POST /chat/messages/{id}/stop) отменяет задачу: она сохраняет
  полученный текст со статусом «Остановлено» и аккуратно закрывает поток;
- если соединение с браузером пропало, задача отменяется, ответ сохраняется
  с пометкой «Ответ оборван»;
- сбой модели посреди ответа — тоже «оборван», текст до сбоя сохраняется.

Реестр задач хранится в памяти процесса (бэкенд запускается одним процессом).
Остановка закрытием соединения работает и без него.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select, update

from app.assistant.agent import ChatEvent, TurnState, run_turn
from app.assistant.code_interpreter import (
    CodeInterpreterSession,
    collect_generated,
    rewrite_sandbox_links,
)
from app.assistant.pii import load_pseudonymizer, mask_items
from app.assistant.tools import ToolContext, ToolRegistry
from app.core.errors import DomainError
from app.db.session import SessionLocal
from app.llm.errors import LLMError, LLMInterruptedError, LLMUnavailableError
from app.llm.prompts import load_prompt
from app.llm.router import LLMRouter
from app.llm.types import Item, UserMessage
from app.models import ChatFile, ChatMessage, Conversation, User
from app.models.enums import MESSAGE_STATUS_LABELS, ROLE_LABELS, FileOrigin, MessageStatus

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 15
MAX_ATTACHED = 5  # последних загруженных файлов диалога, доступных в Code Interpreter
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

_running: dict[int, asyncio.Task] = {}
_stop_requested: set[int] = set()


@dataclass
class Turn:
    user_id: int
    conversation_id: int
    message_id: int
    items: list[Item]


def system_prompt(user: User, today: date | None = None) -> str:
    today = today or date.today()
    return load_prompt(
        "assistant_system",
        today=today.strftime("%d.%m.%Y"),
        weekday=WEEKDAYS[today.weekday()],
        role=ROLE_LABELS[user.role],
        memory="",
    )


async def _save(turn: Turn, state: TurnState, status: MessageStatus, error: str | None) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.id == turn.message_id)
            .values(
                content=state.content,
                status=status,
                model=state.model,
                fallback_from=state.fallback_from,
                tools=state.tools,
                code_runs=state.code_runs,
                error=error,
                input_tokens=state.input_tokens or None,
                output_tokens=state.output_tokens or None,
                completed_at=datetime.now(UTC),
            )
        )
        await session.execute(
            update(Conversation)
            .where(Conversation.id == turn.conversation_id)
            .values(updated_at=datetime.now(UTC))
        )
        await session.commit()


def file_out(f: ChatFile) -> dict:
    return {
        "id": f.id,
        "filename": f.filename,
        "content_type": f.content_type,
        "size": f.size,
        "origin": f.origin.value,
        "url": f"/api/chat/files/{f.id}",
    }


def _done(
    turn: Turn,
    state: TurnState,
    status: MessageStatus,
    error: str | None,
    files: list[ChatFile] | None = None,
) -> ChatEvent:
    return ChatEvent(
        "done",
        {
            "message_id": turn.message_id,
            "status": status.value,
            "status_label": MESSAGE_STATUS_LABELS[status],
            "error": error,
            "model": state.model,
            "fallback_from": state.fallback_from,
            "tools": state.tools,
            "code_runs": state.code_runs,
            # итоговый текст: ссылки на файлы Code Interpreter заменены на наши
            "content": state.content,
            "files": [file_out(f) for f in files or []],
        },
    )


async def _attach_uploads(session, turn: Turn, ci: CodeInterpreterSession, items: list[Item]):
    """Файлы, загруженные пользователем в этот диалог, — в контейнер Code Interpreter
    (с псевдонимами). Модель узнаёт пути из приписки к вопросу."""
    if not ci.available:
        return items
    uploads = (
        await session.scalars(
            select(ChatFile)
            .where(
                ChatFile.conversation_id == turn.conversation_id,
                ChatFile.origin == FileOrigin.UPLOAD,
                ChatFile.message_id.is_not(None),
            )
            .order_by(ChatFile.id.desc())
            .limit(MAX_ATTACHED)
        )
    ).all()
    if not uploads:
        return items
    paths = []
    for upload in reversed(uploads):
        try:
            paths.append(await ci.add(upload.filename, upload.data))
        except (LLMError, DomainError) as exc:
            logger.warning("upload to code interpreter failed: %s", type(exc).__name__)
            paths.append(f"{ci.pii.mask(upload.filename)} (не удалось передать)")
    note = "\n\n[Файлы пользователя доступны в Code Interpreter: " + ", ".join(paths) + "]"
    last = items[-1]
    if isinstance(last, UserMessage):
        return [*items[:-1], UserMessage(last.text + note)]
    return items


async def _finalize(session, turn: Turn, state: TurnState, ci: CodeInterpreterSession):
    """Забрать файлы, созданные кодом модели, и заменить ссылки на них в ответе."""
    if not state.containers:
        return []
    files, problems = await collect_generated(
        session,
        ci,
        state.containers,
        conversation_id=turn.conversation_id,
        message_id=turn.message_id,
        user_id=turn.user_id,
    )
    content = rewrite_sandbox_links(state.content, files, ci.pii)
    if problems:
        content += "\n\n_" + "; ".join(dict.fromkeys(problems)) + "._"
    state.text = [content]
    return files


async def generate(
    turn: Turn, llm: LLMRouter, registry: ToolRegistry, queue: asyncio.Queue[ChatEvent]
) -> None:
    state = TurnState()
    status, error = MessageStatus.COMPLETE, None
    ci = CodeInterpreterSession(llm.files, None)
    files: list[ChatFile] = []
    try:
        async with SessionLocal() as session:
            user = await session.get(User, turn.user_id)
            # Имена сотрудников и поставщиков уходят в модель только псевдонимами
            pii = await load_pseudonymizer(session)
            ci.pii = pii
            ctx = ToolContext(session, user, turn.conversation_id, turn.message_id, ci=ci)
            items = await _attach_uploads(session, turn, ci, mask_items(pii, turn.items))
            try:
                async for event in run_turn(
                    llm, registry, ctx, system_prompt(user), items, state, pii
                ):
                    await queue.put(event)
            except LLMInterruptedError as exc:
                state.flush()
                status, error = MessageStatus.INTERRUPTED, exc.user_message
            except LLMUnavailableError as exc:
                status = MessageStatus.INTERRUPTED if state.content else MessageStatus.ERROR
                error = exc.user_message
            files = await _finalize(session, turn, state, ci)
            for f in files:
                await queue.put(ChatEvent("file", file_out(f)))
    except asyncio.CancelledError:
        state.flush()
        stopped = turn.message_id in _stop_requested
        status = MessageStatus.STOPPED if stopped else MessageStatus.INTERRUPTED
        error = None if stopped else "Соединение прервано до окончания ответа"
        await asyncio.shield(_save(turn, state, status, error))
        await asyncio.shield(ci.cleanup())
        queue.put_nowait(_done(turn, state, status, error))
        raise
    except Exception:
        logger.exception("assistant turn failed")
        status = MessageStatus.INTERRUPTED if state.content else MessageStatus.ERROR
        error = "Внутренняя ошибка ассистента. Попробуйте ещё раз."
    await ci.cleanup()  # копии файлов у провайдера больше не нужны
    await _save(turn, state, status, error)
    await queue.put(_done(turn, state, status, error, files))


def sse(event: ChatEvent) -> str:
    return (
        f"event: {event.type}\ndata: {json.dumps(event.data, ensure_ascii=False, default=str)}\n\n"
    )


async def stream_turn(turn: Turn, llm: LLMRouter, registry: ToolRegistry) -> AsyncIterator[str]:
    queue: asyncio.Queue[ChatEvent] = asyncio.Queue()
    task = asyncio.create_task(generate(turn, llm, registry, queue))
    # Ссылку держим до конца задачи (asyncio хранит задачи слабо): после отключения
    # браузера она ещё сохраняет ответ в БД
    _running[turn.message_id] = task
    task.add_done_callback(lambda _: _forget(turn.message_id))
    try:
        yield sse(ChatEvent("start", {"message_id": turn.message_id}))
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": keep-alive\n\n"  # прокси не закроют «молчащее» соединение
                continue
            yield sse(event)
            if event.type == "done":
                break
    finally:
        # Браузер отключился раньше конца ответа — останавливаем генерацию
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # ожидаемо: мы сами отменили задачу, ответ уже сохранён
            except Exception:
                logger.exception("assistant task failed after disconnect")


def _forget(message_id: int) -> None:
    _running.pop(message_id, None)
    _stop_requested.discard(message_id)


def request_stop(message_id: int) -> bool:
    task = _running.get(message_id)
    if task is None or task.done():
        return False
    _stop_requested.add(message_id)
    task.cancel()
    return True
