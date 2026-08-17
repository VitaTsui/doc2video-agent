"""Token prices, so a run can report what it cost.

USD per **million** tokens, as published for the first-party Claude API. Cache
reads and writes are multiples of the model's own input price rather than
separate line items, which is why they are ratios here instead of a second
table. Anything not listed prices as ``None`` — an unknown model reports "cost
unavailable" rather than a confident zero.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float


PRICES: dict[str, Price] = {
    "claude-opus-5": Price(5.00, 25.00),
    "claude-opus-4-8": Price(5.00, 25.00),
    "claude-sonnet-5": Price(3.00, 15.00),
    "claude-sonnet-4-6": Price(3.00, 15.00),
    "claude-haiku-4-5": Price(1.00, 5.00),
    "claude-fable-5": Price(10.00, 50.00),
}

# Writing to the cache costs a premium over plain input; reading is a fraction
# of it. These are the 5-minute-TTL rates — the pipeline never asks for 1h.
CACHE_WRITE_RATE = 1.25
CACHE_READ_RATE = 0.10


def cost_usd(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """What one call cost, or None when the model has no published price."""
    price = PRICES.get(model)
    if price is None:
        return None
    billed_input = (
        input_tokens
        + cache_write_tokens * CACHE_WRITE_RATE
        + cache_read_tokens * CACHE_READ_RATE
    )
    return (billed_input * price.input_per_mtok + output_tokens * price.output_per_mtok) / 1e6
