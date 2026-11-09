from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import ChatMessageRequest, ChatReplyView, ChatThreadView
from app.services.chat import ChatService

router = APIRouter(prefix="/courses", tags=["chat"])


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


@router.delete("/{course_id}/chat", status_code=status.HTTP_204_NO_CONTENT)
async def clear_course_chat(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> Response:
    await service.clear(current_user, course_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
