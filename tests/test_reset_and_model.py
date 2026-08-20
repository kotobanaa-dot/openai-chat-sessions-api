"""Session reset and per-message model selection."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_reset_keeps_the_session_id(client: AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "hello"})

    body = (await client.post(f"/api/v1/sessions/{sid}/reset")).json()

    assert body["session_id"] == sid
    assert body["generation"] == 2


@pytest.mark.asyncio
async def test_reset_clears_history_and_active_cost(client: AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "first"})
    before = (await client.get(f"/api/v1/sessions/{sid}")).json()
    assert len(before["messages"]) == 2
    assert float(before["total_cost"]) > 0

    await client.post(f"/api/v1/sessions/{sid}/reset")
    after = (await client.get(f"/api/v1/sessions/{sid}")).json()

    assert after["messages"] == []
    assert float(after["total_cost"]) == 0
    assert after["total_tokens"] == 0


@pytest.mark.asyncio
async def test_reset_preserves_lifetime_spending(client: AsyncClient) -> None:
    """The money was really spent - a reset must not erase the record of it."""
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "first"})
    spent = float((await client.get(f"/api/v1/sessions/{sid}")).json()["lifetime_cost"])

    await client.post(f"/api/v1/sessions/{sid}/reset")
    after = (await client.get(f"/api/v1/sessions/{sid}")).json()

    assert float(after["lifetime_cost"]) == spent
    assert after["lifetime_tokens"] > 0


@pytest.mark.asyncio
async def test_model_stops_seeing_the_archived_context(client: AsyncClient) -> None:
    """The point of a reset: the next reply is built from an empty context."""
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "first"})
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "second"})
    assert len(client.provider.calls[-1]) == 3  # two past turns plus the new message

    await client.post(f"/api/v1/sessions/{sid}/reset")
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "after reset"})

    assert len(client.provider.calls[-1]) == 1  # only the new message


@pytest.mark.asyncio
async def test_cost_accumulates_again_after_reset(client: AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "first"})
    await client.post(f"/api/v1/sessions/{sid}/reset")

    body = (
        await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "again"})
    ).json()

    # The new context starts from zero, so this exchange is the whole total.
    assert body["cost"] == body["total_accumulated_cost"]
    assert body["generation"] == 2


@pytest.mark.asyncio
async def test_reset_of_an_empty_context_is_a_no_op(client: AsyncClient) -> None:
    """Resetting twice in a row must not pile up empty generations."""
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "hello"})

    first = (await client.post(f"/api/v1/sessions/{sid}/reset")).json()
    second = (await client.post(f"/api/v1/sessions/{sid}/reset")).json()

    assert first["generation"] == 2
    assert second["generation"] == 2
    assert second["messages_archived"] == 0


@pytest.mark.asyncio
async def test_money_is_rendered_with_fixed_decimals(client: AsyncClient) -> None:
    """Zero must read as 0.00000000, not as scientific notation."""
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]

    fresh = (await client.get(f"/api/v1/sessions/{sid}")).json()
    assert fresh["total_cost"] == "0.00000000"

    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "hi"})
    reset = (await client.post(f"/api/v1/sessions/{sid}/reset")).json()
    assert reset["total_cost"] == "0.00000000"
    assert len(reset["lifetime_cost"].split(".")[1]) == 8


@pytest.mark.asyncio
async def test_reset_of_a_missing_session_returns_404(client: AsyncClient) -> None:
    response = await client.post("/api/v1/sessions/nope/reset")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


@pytest.mark.asyncio
async def test_message_uses_the_session_model_by_default(client: AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions", json={"model": "gpt-4o-mini"})).json()["id"]
    body = (
        await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "hi"})
    ).json()
    assert body["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_per_message_model_overrides_the_session_default(client: AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions", json={"model": "gpt-4o-mini"})).json()["id"]

    body = (
        await client.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"content": "hi", "model": "gpt-5-nano"},
        )
    ).json()

    assert body["model"] == "gpt-5-nano"
    # The session default is untouched by a one-off override.
    assert (await client.get(f"/api/v1/sessions/{sid}")).json()["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_cost_follows_the_model_actually_used(client: AsyncClient) -> None:
    """Same fake usage, different tariffs - the cheaper model must cost less."""
    sid = (await client.post("/api/v1/sessions", json={"model": "gpt-4o-mini"})).json()["id"]

    expensive = (
        await client.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"content": "a", "model": "gpt-4.1-mini"},
        )
    ).json()
    cheap = (
        await client.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"content": "b", "model": "gpt-5-nano"},
        )
    ).json()

    assert float(cheap["cost"]) < float(expensive["cost"])


@pytest.mark.asyncio
async def test_unsupported_model_on_a_message_is_rejected(client: AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]

    response = await client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "hi", "model": "totally-made-up"},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    # The caller is told what they can actually use.
    assert "known_models" in error["details"]


@pytest.mark.asyncio
async def test_rejected_model_costs_nothing(client: AsyncClient) -> None:
    """The model is validated before the provider is called at all."""
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    calls_before = len(client.provider.calls)

    await client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "hi", "model": "totally-made-up"},
    )

    assert len(client.provider.calls) == calls_before
    assert float((await client.get(f"/api/v1/sessions/{sid}")).json()["total_cost"]) == 0
