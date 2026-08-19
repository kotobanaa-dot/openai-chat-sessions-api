"""Cost arithmetic - the part where a bug turns into wrong money."""

from decimal import Decimal

import pytest

from app.core.errors import UnknownModelPricingError
from app.services.pricing import ModelPrice, PricingService

PRICES = {
    "gpt-4o-mini": ModelPrice(
        model="gpt-4o-mini",
        input=Decimal("0.15"),
        cached_input=Decimal("0.075"),
        output=Decimal("0.60"),
    )
}


@pytest.fixture
def pricing() -> PricingService:
    return PricingService(PRICES)


def test_cost_matches_published_rates(pricing: PricingService) -> None:
    # 1M input + 1M output at $0.15 / $0.60.
    cost = pricing.calculate("gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost.input_cost == Decimal("0.15")
    assert cost.output_cost == Decimal("0.60")
    assert cost.total_cost == Decimal("0.75")


def test_cached_tokens_are_billed_at_the_cached_rate(pricing: PricingService) -> None:
    # Half the prompt served from cache: 500k at $0.15 + 500k at $0.075.
    cost = pricing.calculate(
        "gpt-4o-mini",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cached_tokens=500_000,
    )
    assert cost.input_cost == Decimal("0.1125")


def test_cached_tokens_are_not_charged_twice(pricing: PricingService) -> None:
    """cached_tokens is a subset of prompt_tokens, not an extra bucket."""
    fully_cached = pricing.calculate(
        "gpt-4o-mini", prompt_tokens=1_000, completion_tokens=0, cached_tokens=1_000
    )
    assert fully_cached.input_cost == Decimal("1000") * Decimal("0.075") / Decimal("1000000")


def test_cached_tokens_cannot_exceed_prompt_tokens(pricing: PricingService) -> None:
    """A provider anomaly must not produce a negative charge."""
    cost = pricing.calculate(
        "gpt-4o-mini", prompt_tokens=100, completion_tokens=0, cached_tokens=999
    )
    assert cost.input_cost > 0


def test_result_is_exact_decimal_not_float(pricing: PricingService) -> None:
    """Summing many small calls must not drift the way floats do."""
    total = Decimal("0")
    for _ in range(1000):
        total += pricing.calculate(
            "gpt-4o-mini", prompt_tokens=1_000, completion_tokens=1_000
        ).total_cost
    assert total == Decimal("0.75")


def test_unknown_model_raises_instead_of_charging_zero(pricing: PricingService) -> None:
    with pytest.raises(UnknownModelPricingError):
        pricing.calculate("some-unlisted-model", prompt_tokens=10, completion_tokens=10)


def test_pricing_file_loads(tmp_path) -> None:
    file = tmp_path / "pricing.yaml"
    file.write_text(
        "currency: USD\nmodels:\n  demo:\n    input: 1.0\n    cached_input: 0.5\n    output: 2.0\n",
        encoding="utf-8",
    )
    service = PricingService.from_file(file)
    assert service.known_models() == ["demo"]
    assert service.price_for("demo").output == Decimal("2.0")
