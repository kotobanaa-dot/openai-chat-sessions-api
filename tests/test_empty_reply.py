"""A model that answers with nothing, and the money that was still spent.

Reasoning models consume part of the output budget before writing any text. If
the budget runs out first, the API returns an empty message and OpenAI bills
for it anyway. Treating that as a normal reply put blank turns into the history
and made the charge look like a successful exchange.
"""

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.services.pricing import get_pricing_service


@pytest.mark.asyncio
async def test_empty_reply_is_reported_as_an_error(client: AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    client.provider.return_empty = True

    response = await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "hi"})

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "empty_reply"
    # The caller is told what happened, not just that something failed.
    assert error["details"]["finish_reason"] == "length"
    assert error["details"]["reasoning_tokens"] == 400


@pytest.mark.asyncio
async def test_empty_reply_is_still_charged(client: AsyncClient) -> None:
    """The provider billed for it, so the accounting must show it."""
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    client.provider.return_empty = True

    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "hi"})

    session = (await client.get(f"/api/v1/sessions/{sid}")).json()
    assert float(session["total_cost"]) > 0
    assert float(session["lifetime_cost"]) > 0
    assert session["total_tokens"] == 500


@pytest.mark.asyncio
async def test_empty_reply_does_not_pollute_the_next_context(client: AsyncClient) -> None:
    """A blank turn must not be replayed to the model as conversation."""
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]

    client.provider.return_empty = True
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "first"})

    client.provider.return_empty = False
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "second"})

    # The failed assistant turn is skipped; the earlier user message stays.
    contents = [m.content for m in client.provider.calls[-1]]
    assert "" not in contents
    assert contents[-1] == "second"


@pytest.mark.asyncio
async def test_reasoning_models_get_a_larger_output_budget(client: AsyncClient) -> None:
    """The cap is a property of the model, not a single global number."""
    # Read the configured cap rather than hardcoding it: a deployment sets its
    # own value, and the assertion is about the relationship, not the number.
    settings_cap = get_settings().openai_max_output_tokens
    sid = (await client.post("/api/v1/sessions", json={"model": "gpt-4o-mini"})).json()["id"]

    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "a"})
    plain_cap = client.provider.caps[-1]

    await client.post(
        f"/api/v1/sessions/{sid}/messages", json={"content": "b", "model": "gpt-5-nano"}
    )
    reasoning_cap = client.provider.caps[-1]

    assert reasoning_cap > plain_cap
    assert reasoning_cap == get_pricing_service().price_for("gpt-5-nano").min_output_tokens
    assert plain_cap == settings_cap
