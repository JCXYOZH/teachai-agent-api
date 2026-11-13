import asyncio
import json
from typing import Any, AsyncIterator, Dict, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import ChatMessageRequest, ChatReplyView, ChatThreadView
from app.services.chat import ChatService

router = APIRouter(prefix="/courses", tags=["chat"])

SSE_KEEPALIVE_SECONDS = 15.0


def get_chat_service(db: AsyncSession = Depends(get_db)) -> ChatService:
    return ChatService(db, settings)


@router.get("/{course_id}/chat", response_model=ChatThreadView)
async def get_course_chat(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> ChatThreadView:
    return await service.get_thread(current_user, course_id)


@router.post("/{course_id}/chat/messages", response_model=ChatReplyView)
async def send_course_chat_message(
    course_id: UUID,
    payload: ChatMessageRequest,
    current_user: User = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> ChatReplyView:
    return await service.send_message(current_user, course_id, payload)


@router.post("/{course_id}/chat/messages/stream")
async def stream_course_chat_message(
    course_id: UUID,
    payload: ChatMessageRequest,
    current_user: User = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    events = await service.open_message_stream(current_user, course_id, payload)
    return StreamingResponse(
        _sse_response(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/{course_id}/chat", status_code=status.HTTP_204_NO_CONTENT)
async def clear_course_chat(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> Response:
    await service.clear(current_user, course_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _sse_response(
    events: AsyncIterator[Tuple[str, Dict[str, Any]]],
) -> AsyncIterator[str]:
    iterator = events.__aiter__()
    pending: Optional[Any] = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait(
                {pending}, timeout=SSE_KEEPALIVE_SECONDS
            )
            if not done:
                yield ": keep-alive\n\n"
                continue
            current = pending
            pending = None
            try:
                event, data = current.result()
            except StopAsyncIteration:
                return
            encoded = json.dumps(data, ensure_ascii=False, default=str)
            yield f"event: {event}\ndata: {encoded}\n\n"
            if event in {"done", "error"}:
                return
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except BaseException:
                pass
        close = getattr(iterator, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass
