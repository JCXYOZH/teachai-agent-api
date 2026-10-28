from typing import List, Optional

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.provider import (
    ProviderConfigUpdate,
    ProviderTestRequest,
    ProviderTestResponse,
    ProviderView,
)
from app.services.providers import ProviderConfigService

router = APIRouter(prefix="/providers", tags=["providers"])


def get_provider_service(db: AsyncSession = Depends(get_db)) -> ProviderConfigService:
    return ProviderConfigService(db, settings)


@router.get("", response_model=List[ProviderView])
async def list_providers(
    current_user: User = Depends(get_current_user),
    service: ProviderConfigService = Depends(get_provider_service),
) -> List[ProviderView]:
    return await service.list_providers(current_user)


@router.get("/{provider_id}", response_model=ProviderView)
async def get_provider(
    provider_id: str,
    current_user: User = Depends(get_current_user),
    service: ProviderConfigService = Depends(get_provider_service),
) -> ProviderView:
    return await service.get_provider(current_user, provider_id)


@router.put("/{provider_id}/config", response_model=ProviderView)
async def update_provider_config(
    provider_id: str,
    payload: ProviderConfigUpdate,
    current_user: User = Depends(get_current_user),
    service: ProviderConfigService = Depends(get_provider_service),
) -> ProviderView:
    return await service.update_config(current_user, provider_id, payload)


@router.delete("/{provider_id}/config", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_config(
    provider_id: str,
    current_user: User = Depends(get_current_user),
    service: ProviderConfigService = Depends(get_provider_service),
) -> None:
    await service.delete_config(current_user, provider_id)


@router.post("/{provider_id}/test", response_model=ProviderTestResponse)
async def test_provider(
    provider_id: str,
    payload: Optional[ProviderTestRequest] = None,
    current_user: User = Depends(get_current_user),
    service: ProviderConfigService = Depends(get_provider_service),
) -> ProviderTestResponse:
    return await service.test_connection(current_user, provider_id, payload)
