import secrets
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import UnauthorizedError
from app.db.session import get_db
from app.providers.openai_provider import OpenAIProvider
from app.repositories.chat_repo import ChatRepository
from app.services.chat import ChatService
from app.services.pricing import PricingService, get_pricing_service

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
PricingDep = Annotated[PricingService, Depends(get_pricing_service)]


async def require_api_key(
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Shared-secret guard for public deployments.

    Disabled when API_KEY is empty, so local development stays frictionless.
    Compared with compare_digest to avoid leaking the key through timing.
    """
    if not settings.auth_enabled:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.api_key):
        raise UnauthorizedError()


def get_chat_service(
    db: DbDep,
    settings: SettingsDep,
    pricing: PricingDep,
) -> ChatService:
    return ChatService(
        repo=ChatRepository(db),
        provider=OpenAIProvider(settings),
        pricing=pricing,
        settings=settings,
    )


ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
