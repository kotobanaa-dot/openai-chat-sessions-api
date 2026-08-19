"""End-to-end pass over the API with a fake provider.

Runs on SQLite and never calls OpenAI, so the whole request path - validation,
persistence, usage recording, cost accumulation - is testable offline.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_chat_service
from app.core.config import get_settings
from app.core.errors import UpstreamUnavailableError
from app.db.base import Base
from app.main import app
from app.models import chat as models  # noqa: F401 - registers tables
from app.providers.base import ProviderReply, ProviderUsage
from app.repositories.chat_repo import ChatRepository
from app.services.chat import ChatService
from app.services.pricing import get_pricing_service


class FakeProvider:
    """Echoes how many context messages it received, with fixed usage."""

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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.provider = provider
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_exchange_records_usage_and_cost(client: AsyncClient) -> None:
    created = await client.post("/api/v1/sessions", json={"title": "t"})
    assert created.status_code == 201
    sid = created.json()["id"]

    first = await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "hello"})
    assert first.status_code == 201
    body = first.json()
    assert body["usage"]["prompt_tokens"] == 100
    assert float(body["cost"]) > 0
    assert body["cost"] == body["total_accumulated_cost"]


@pytest.mark.asyncio
async def test_second_message_carries_previous_history(client: AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]

    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "first"})
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "second"})

    # Second call must see: first user msg, its reply, and the new message.
    assert len(client.provider.calls[0]) == 1
    assert len(client.provider.calls[1]) == 3


@pytest.mark.asyncio
async def test_cost_accumulates_across_the_session(client: AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]

    one = (await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "a"})).json()
    two = (await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "b"})).json()

    assert float(two["total_accumulated_cost"]) == pytest.approx(
        float(one["cost"]) + float(two["cost"])
    )

    session = (await client.get(f"/api/v1/sessions/{sid}")).json()
    assert len(session["messages"]) == 4
    assert session["total_tokens"] == 300
    assert float(session["total_cost"]) == pytest.approx(float(two["total_accumulated_cost"]))


@pytest.mark.asyncio
async def test_missing_session_returns_404(client: AsyncClient) -> None:
    response = await client.post("/api/v1/sessions/nope/messages", json={"content": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


@pytest.mark.asyncio
async def test_blank_message_is_rejected(client: AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    response = await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "   "})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unsupported_model_is_rejected_before_any_call(client: AsyncClient) -> None:
    response = await client.post("/api/v1/sessions", json={"model": "no-such-model"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_provider_failure_maps_to_503_and_keeps_the_user_message(
    client: AsyncClient,
) -> None:
    """A failed model call must not swallow what the user typed."""
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]

    async def failing_complete(messages, model):
        raise UpstreamUnavailableError()

    client.provider.complete = failing_complete

    response = await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "kept?"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_unavailable"

    session = (await client.get(f"/api/v1/sessions/{sid}")).json()
    assert [m["content"] for m in session["messages"]] == ["kept?"]
    # Nothing was charged for a call that produced no reply.
    assert float(session["total_cost"]) == 0
