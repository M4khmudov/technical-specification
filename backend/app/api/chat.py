from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.assistant import chat as service
from app.assistant import runner
from app.assistant.tools import ToolRegistry, default_registry
from app.core.errors import NotFoundError
from app.llm.registry import get_llm
from app.llm.router import LLMRouter
from app.models import ChatMessage, Conversation
from app.models.enums import MESSAGE_STATUS_LABELS, MessageRole, MessageStatus

router = APIRouter(prefix="/chat", tags=["assistant"])


def get_tools() -> ToolRegistry:
    return default_registry()


class ConversationIn(BaseModel):
    title: Annotated[str, Field(max_length=200)] | None = None


class ConversationOut(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: int
    role: MessageRole
    content: str
    status: MessageStatus
    status_label: str
    model: str | None
    fallback_from: str | None
    tools: list[dict[str, Any]]
    error: str | None
    created_at: datetime


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class QuestionIn(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=service.MAX_QUESTION_LENGTH)]


def conversation_out(c: Conversation) -> ConversationOut:
    return ConversationOut(id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at)


def message_out(m: ChatMessage) -> MessageOut:
    status = service.effective_status(m)
    return MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        status=status,
        status_label=MESSAGE_STATUS_LABELS[status],
        model=m.model,
        fallback_from=m.fallback_from,
        tools=m.tools or [],
        error=m.error,
        created_at=m.created_at,
    )


@router.get("/conversations")
async def list_conversations(session: SessionDep, user: CurrentUser) -> list[ConversationOut]:
    return [conversation_out(c) for c in await service.list_conversations(session, user)]


@router.post("/conversations", status_code=201)
async def create_conversation(
    data: ConversationIn, session: SessionDep, user: CurrentUser
) -> ConversationOut:
    return conversation_out(await service.create_conversation(session, user, data.title))


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int, session: SessionDep, user: CurrentUser
) -> ConversationDetailOut:
    c = await service.get_conversation(session, user, conversation_id)
    return ConversationDetailOut(
        **conversation_out(c).model_dump(), messages=[message_out(m) for m in c.messages]
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: int, session: SessionDep, user: CurrentUser) -> None:
    await service.delete_conversation(session, user, conversation_id)


@router.post("/conversations/{conversation_id}/messages")
async def ask(
    conversation_id: int,
    data: QuestionIn,
    session: SessionDep,
    user: CurrentUser,
    llm: Annotated[LLMRouter, Depends(get_llm)],
    tools: Annotated[ToolRegistry, Depends(get_tools)],
) -> StreamingResponse:
    """Задать вопрос. Ответ приходит потоком SSE: start, model, delta…, tool…, done."""
    _, answer, items = await service.start_turn(session, user, conversation_id, data.text)
    turn = runner.Turn(user.id, conversation_id, answer.id, items)
    return StreamingResponse(
        runner.stream_turn(turn, llm, tools),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/messages/{message_id}/stop")
async def stop(message_id: int, session: SessionDep, user: CurrentUser) -> dict[str, bool]:
    owned = await session.scalar(
        select(ChatMessage.id)
        .join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .where(ChatMessage.id == message_id, Conversation.user_id == user.id)
    )
    if owned is None:
        raise NotFoundError("Сообщение не найдено")
    return {"stopped": runner.request_stop(message_id)}
