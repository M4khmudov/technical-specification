"""Диалоги: создание, список, история, начало хода. Пользователь видит только свои диалоги."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import ConflictError, DomainError, NotFoundError
from app.llm.types import AssistantMessage, Item, UserMessage
from app.models import ChatFile, ChatMessage, Conversation, User
from app.models.enums import FileOrigin, MessageRole, MessageStatus

HISTORY_LIMIT = 20  # последних реплик в контексте модели
MAX_QUESTION_LENGTH = 4000
INTERRUPTED_NOTE = "[ответ был оборван]"


def _now() -> datetime:
    return datetime.now(UTC)


def effective_status(message: ChatMessage) -> MessageStatus:
    """«Отвечает» дольше таймаута модели — значит, процесс упал посреди ответа."""
    if message.status == MessageStatus.STREAMING:
        limit = timedelta(seconds=get_settings().llm_timeout_seconds * 2)
        if message.created_at and _now() - message.created_at > limit:
            return MessageStatus.INTERRUPTED
    return message.status


async def list_conversations(session: AsyncSession, user: User) -> list[Conversation]:
    rows = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    )
    return list(rows.all())


async def create_conversation(session: AsyncSession, user: User, title: str | None) -> Conversation:
    conversation = Conversation(user_id=user.id, title=(title or "Новый диалог")[:200])
    session.add(conversation)
    await session.commit()
    return conversation


async def get_conversation(session: AsyncSession, user: User, conversation_id: int) -> Conversation:
    conversation = await session.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id, Conversation.user_id == user.id)
        .execution_options(populate_existing=True)
    )
    if conversation is None:  # чужой диалог неотличим от несуществующего
        raise NotFoundError("Диалог не найден")
    return conversation


async def delete_conversation(session: AsyncSession, user: User, conversation_id: int) -> None:
    conversation = await get_conversation(session, user, conversation_id)
    await session.delete(conversation)
    await session.commit()


def history_items(messages: list[ChatMessage]) -> list[Item]:
    """История для модели: тексты реплик. Результаты инструментов не переносим —
    при новом вопросе данные запрашиваются заново и всегда свежие."""
    items: list[Item] = []
    for m in messages[-HISTORY_LIMIT:]:
        if m.role == MessageRole.USER:
            items.append(UserMessage(m.content))
        elif m.content:
            status = effective_status(m)
            suffix = f"\n{INTERRUPTED_NOTE}" if status != MessageStatus.COMPLETE else ""
            items.append(AssistantMessage(m.content + suffix))
    return items


async def start_turn(
    session: AsyncSession,
    user: User,
    conversation_id: int,
    text: str,
    file_ids: list[int] | None = None,
) -> tuple[ChatMessage, ChatMessage, list[Item]]:
    """Сохранить вопрос и заготовку ответа. Один ответ в диалоге за раз."""
    text = text.strip()
    if not text:
        raise DomainError("Вопрос пустой")
    if len(text) > MAX_QUESTION_LENGTH:
        raise DomainError(f"Вопрос слишком длинный: не больше {MAX_QUESTION_LENGTH} символов")

    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user.id)
        .with_for_update()
    )
    if conversation is None:
        raise NotFoundError("Диалог не найден")
    messages = list(
        (
            await session.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation.id)
                .order_by(ChatMessage.id)
            )
        ).all()
    )
    if any(effective_status(m) == MessageStatus.STREAMING for m in messages):
        raise ConflictError("Ассистент ещё отвечает на предыдущий вопрос")

    history = history_items(messages)
    if not messages and conversation.title == "Новый диалог":
        conversation.title = text[:60] + ("…" if len(text) > 60 else "")
    conversation.updated_at = _now()
    question = ChatMessage(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=text,
        status=MessageStatus.COMPLETE,
    )
    answer = ChatMessage(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="",
        status=MessageStatus.STREAMING,
        tools=[],
    )
    session.add_all([question, answer])
    await session.flush()
    if file_ids:
        # Прикрепить загруженные файлы к вопросу: только свои, из этого диалога, ещё не
        # прикреплённые
        uploads = (
            await session.scalars(
                select(ChatFile).where(
                    ChatFile.id.in_(file_ids),
                    ChatFile.conversation_id == conversation.id,
                    ChatFile.user_id == user.id,
                    ChatFile.origin == FileOrigin.UPLOAD,
                    ChatFile.message_id.is_(None),
                )
            )
        ).all()
        if len(uploads) != len(set(file_ids)):
            raise NotFoundError("Прикреплённый файл не найден — загрузите его заново")
        for upload in uploads:
            upload.message_id = question.id
    await session.commit()
    return question, answer, [*history, UserMessage(text)]


async def save_upload(
    session: AsyncSession, user: User, conversation_id: int, filename: str, data: bytes
) -> ChatFile:
    """Загруженный файл (CSV/XLSX) — хранится у нас; в Code Interpreter уйдёт копия
    с псевдонимами, когда пользователь задаст вопрос с этим файлом."""
    from app.assistant.code_interpreter import validate_upload

    await get_conversation(session, user, conversation_id)
    content_type = validate_upload(filename, data)
    upload = ChatFile(
        conversation_id=conversation_id,
        user_id=user.id,
        origin=FileOrigin.UPLOAD,
        filename=filename[:255],
        content_type=content_type,
        size=len(data),
        data=data,
    )
    session.add(upload)
    await session.commit()
    return upload


async def get_file(session: AsyncSession, user: User, file_id: int) -> ChatFile:
    chat_file = await session.scalar(
        select(ChatFile)
        .join(Conversation, Conversation.id == ChatFile.conversation_id)
        .where(ChatFile.id == file_id, Conversation.user_id == user.id)
    )
    if chat_file is None:  # чужой файл неотличим от несуществующего
        raise NotFoundError("Файл не найден")
    return chat_file


async def files_by_message(
    session: AsyncSession, conversation_id: int
) -> dict[int, list[ChatFile]]:
    rows = await session.scalars(
        select(ChatFile)
        .where(ChatFile.conversation_id == conversation_id, ChatFile.message_id.is_not(None))
        .order_by(ChatFile.id)
    )
    grouped: dict[int, list[ChatFile]] = {}
    for f in rows:
        grouped.setdefault(f.message_id, []).append(f)
    return grouped
