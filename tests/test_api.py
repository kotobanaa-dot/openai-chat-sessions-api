"""End-to-end pass over the API with a fake provider.

Runs on SQLite and never calls OpenAI, so the whole request path - validation,
persistence, usage recording, cost accumulation - is testable offline.

The client fixture and the fake provider live in conftest.py.
"""

import pytest
from httpx import AsyncClient

from app.api.deps import require_api_key
from app.core.config import get_settings
from app.core.errors import UpstreamUnavailableError
from app.main import app


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
async def test_api_key_guard_blocks_and_admits(client: AsyncClient) -> None:
    """The shared-secret guard itself, exercised without the override."""
    settings = get_settings()
    app.dependency_overrides.pop(require_api_key, None)
    original = settings.api_key
    settings.api_key = "secret-test-key"
    try:
        assert (await client.get("/api/v1/models")).status_code == 401
        assert (
            await client.get("/api/v1/models", headers={"X-API-Key": "wrong"})
        ).status_code == 401
        assert (
            await client.get("/api/v1/models", headers={"X-API-Key": "secret-test-key"})
        ).status_code == 200
    finally:
        settings.api_key = original
        app.dependency_overrides[require_api_key] = lambda: None


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
