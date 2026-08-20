"""Model-specific pricing.

Tariffs are data, not code: they live in pricing.yaml and are loaded once.
Adding a model is a config edit. No route or service contains a price literal
or a cost formula - the only place that multiplies tokens by money is here.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import get_settings
from app.core.errors import UnknownModelPricingError

TOKENS_PER_UNIT = Decimal("1000000")

# Money resolution, matching the NUMERIC(18, 8) columns. Quantising here means
# the amount returned by the API is exactly the amount stored in the database,
# instead of two representations of the same number.
MONEY_QUANT = Decimal("0.00000001")


def to_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ModelPrice:
    """Price of one model, USD per 1M tokens, plus its output requirements."""

    model: str
    input: Decimal
    cached_input: Decimal
    output: Decimal
    # Smallest per-reply output budget this model needs to be useful. Reasoning
    # models consume part of it before producing any text, so the service-wide
    # cap is raised to this value for them.
    min_output_tokens: int = 0


@dataclass(frozen=True)
class CostBreakdown:
    input_cost: Decimal
    output_cost: Decimal
    total_cost: Decimal
    price: ModelPrice


class PricingService:
    def __init__(self, prices: dict[str, ModelPrice], currency: str = "USD") -> None:
        self._prices = prices
        self.currency = currency

    @classmethod
    def from_file(cls, path: Path) -> "PricingService":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        prices: dict[str, ModelPrice] = {}
        for model, entry in (raw.get("models") or {}).items():
            prices[model] = ModelPrice(
                model=model,
                # str() first: Decimal(float) would inherit the binary
                # rounding error that YAML floats already carry.
                input=Decimal(str(entry["input"])),
                cached_input=Decimal(str(entry.get("cached_input", entry["input"]))),
                output=Decimal(str(entry["output"])),
                min_output_tokens=int(entry.get("min_output_tokens", 0)),
            )
        return cls(prices, currency=raw.get("currency", "USD"))

    def known_models(self) -> list[str]:
        return sorted(self._prices)

    def price_for(self, model: str) -> ModelPrice:
        price = self._prices.get(model)
        if price is None:
            raise UnknownModelPricingError(
                f"No pricing configured for model '{model}'.",
                details={"known_models": self.known_models()},
            )
        return price

    def calculate(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
    ) -> CostBreakdown:
        """Cost of a single call.

        prompt_tokens already includes cached_tokens, and cached input is
        billed at a lower rate - so the cached part is subtracted from the
        regular input before charging, not counted twice.
        """
        price = self.price_for(model)

        cached = max(0, min(cached_tokens, prompt_tokens))
        fresh = prompt_tokens - cached

        input_cost = to_money(
            (Decimal(fresh) * price.input + Decimal(cached) * price.cached_input)
            / TOKENS_PER_UNIT
        )
        output_cost = to_money(Decimal(completion_tokens) * price.output / TOKENS_PER_UNIT)

        return CostBreakdown(
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=input_cost + output_cost,
            price=price,
        )


@lru_cache
def get_pricing_service() -> PricingService:
    return PricingService.from_file(get_settings().pricing_file)
