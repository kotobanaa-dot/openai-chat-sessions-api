"""The spending report.

Every figure here is derived from the same usage rows the chat endpoints
write, so these tests drive the report through the public API rather than
inserting rows by hand: if the two ever disagreed, the report would be the
one lying.
"""

from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.api.deps import require_api_key
from app.core.config import get_settings
from app.main import app


@pytest.mark.asyncio
async def test_empty_database_reports_zeros(client: AsyncClient) -> None:
    """No traffic yet is a normal state, not a division by zero."""
    response = await client.get("/api/v1/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["overall"]["sessions"] == 0
    assert body["overall"]["replies"] == 0
    assert body["overall"]["total_cost"] == "0.00000000"
    assert body["by_model"] == []
    assert body["by_day"] == []
    assert body["failed"]["replies"] == 0


@pytest.mark.asyncio
async def test_totals_match_what_was_actually_sent(client: AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    for text in ("one", "two", "three"):
        await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": text})

    body = (await client.get("/api/v1/stats")).json()

    assert body["overall"]["sessions"] == 1
    assert body["overall"]["replies"] == 3
    # The fake provider reports 150 tokens per reply.
    assert body["overall"]["total_tokens"] == 450

    session = (await client.get(f"/api/v1/sessions/{sid}")).json()
    assert body["overall"]["total_cost"] == session["lifetime_cost"]


@pytest.mark.asyncio
async def test_breakdown_separates_models_and_prices_each_at_its_own_tariff(
    client: AsyncClient,
) -> None:
    """Same token counts, different tariffs - the gap is the tariff alone."""
    sid = (await client.post("/api/v1/sessions", json={"model": "gpt-4o-mini"})).json()["id"]
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "a"})
    await client.post(
        f"/api/v1/sessions/{sid}/messages", json={"content": "b", "model": "gpt-4.1-mini"}
    )

    rows = {row["model"]: row for row in (await client.get("/api/v1/stats")).json()["by_model"]}

    assert set(rows) == {"gpt-4o-mini", "gpt-4.1-mini"}
    assert rows["gpt-4o-mini"]["replies"] == 1
    assert Decimal(rows["gpt-4.1-mini"]["total_cost"]) > Decimal(rows["gpt-4o-mini"]["total_cost"])
    # Cost per 1000 characters needs delivered text to divide by.
    assert rows["gpt-4o-mini"]["answer_chars"] > 0
    assert Decimal(rows["gpt-4o-mini"]["cost_per_1k_chars"]) > 0


@pytest.mark.asyncio
async def test_empty_replies_are_reported_as_money_spent_on_nothing(
    client: AsyncClient,
) -> None:
    """An empty answer is billed. The report must show it, not hide it."""
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    client.provider.return_empty = True
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "hi"})

    body = (await client.get("/api/v1/stats")).json()

    assert body["failed"]["replies"] == 1
    assert Decimal(body["failed"]["total_cost"]) > 0
    # It is part of the total too - the provider charged for it either way.
    assert body["overall"]["replies"] == 1
    # No text was delivered, so cost per character has no meaning here.
    assert body["by_model"][0]["cost_per_1k_chars"] is None


@pytest.mark.asyncio
async def test_reset_does_not_erase_spending_from_the_report(client: AsyncClient) -> None:
    """Reset clears the context, never the invoice."""
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "before reset"})
    await client.post(f"/api/v1/sessions/{sid}/reset")

    body = (await client.get("/api/v1/stats")).json()

    assert body["overall"]["replies"] == 1
    assert Decimal(body["overall"]["total_cost"]) > 0


@pytest.mark.asyncio
async def test_daily_curve_has_one_point_per_day_with_traffic(client: AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions", json={})).json()["id"]
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "a"})
    await client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "b"})

    days = (await client.get("/api/v1/stats")).json()["by_day"]

    assert len(days) == 1
    assert days[0]["replies"] == 2


@pytest.mark.asyncio
async def test_stats_are_behind_the_api_key_guard(client: AsyncClient) -> None:
    """Spending figures are not public on a deployed stand."""
    settings = get_settings()
    app.dependency_overrides.pop(require_api_key, None)
    original = settings.api_key
    settings.api_key = "secret-test-key"
    try:
        assert (await client.get("/api/v1/stats")).status_code == 401
        assert (
            await client.get("/api/v1/stats", headers={"X-API-Key": "secret-test-key"})
        ).status_code == 200
    finally:
        settings.api_key = original
        app.dependency_overrides[require_api_key] = lambda: None
