"""Shared test fixtures.

The suite runs against SQLite with a faked provider, so it needs neither a
database server nor network access - and costs nothing to run.
"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_chat_service, require_api_key
from app.core.config import get_settings
from app.db.base import Base
from app.main import app
from app.models import chat as models  # noqa: F401 - registers tables on Base.metadata
from app.providers.base import ProviderReply, ProviderUsage
from app.repositories.chat_repo import ChatRepository
from app.services.chat import ChatService
from app.services.pricing import get_pricing_service


class FakeProvider:
    """Records the context it was given and returns fixed usage.

    Fixed usage is what makes pricing assertions meaningful: when the token
    counts are identical, any difference in cost comes from the tariff.
    """

    def __init__(self) -> None:
        self.calls: list[list] = []

    async def complete(self, messages, model):
        self.calls.append(messages)
        return ProviderReply(
            content=f"reply to {len(messages)} context messages",
            model=model,
            finish_reason="stop",
            response_id="resp_fake",
            usage=ProviderUsage(
                prompt_tokens=100, completion_tokens=50, total_tokens=150, cached_tokens=0
            ),
        )


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    provider = FakeProvider()
    settings = get_settings()

    async def override_chat_service():
        async with factory() as db:
            try:
                yield ChatService(
                    repo=ChatRepository(db),
                    provider=provider,
                    pricing=get_pricing_service(),
                    settings=settings,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    app.dependency_overrides[get_chat_service] = override_chat_service
    # The guard is environment-driven (API_KEY). Overriding it keeps the suite
    # deterministic: these tests cover behaviour, and running them against a
    # deployment-shaped .env must not turn every request into a 401.
    app.dependency_overrides[require_api_key] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.provider = provider
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()
