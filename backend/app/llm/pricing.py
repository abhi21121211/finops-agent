"""List prices per 1M tokens (USD), used to report a 'list-price equivalent' cost.

Development runs on free tiers, so the real cost is usually 0. To keep cost numbers
meaningful we also record what the same tokens would cost at the provider's paid
list price. The LiteLLM proxy reports this in `x-litellm-response-cost` for models it
knows; this table is the fallback. Keys are matched as substrings of the model name.
"""

from decimal import Decimal

# (input per 1M, output per 1M). Update when providers change prices.
LIST_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "gemini-flash-lite": (Decimal("0.10"), Decimal("0.40")),
    "gemini-flash": (Decimal("0.30"), Decimal("2.50")),
    "mistral-medium": (Decimal("0.40"), Decimal("2.00")),
    "gemma-4": (Decimal("0.10"), Decimal("0.20")),
    "qwen": (Decimal("0.10"), Decimal("0.40")),
    "nemotron": (Decimal("0.20"), Decimal("0.80")),
    "gpt-oss-20b": (Decimal("0.075"), Decimal("0.30")),
    "llama3.2": (Decimal("0"), Decimal("0")),
    "lfm": (Decimal("0.02"), Decimal("0.05")),
}

_MILLION = Decimal(1_000_000)


def list_price_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    name = model.lower()
    for key, (inp, out) in LIST_PRICES.items():
        if key in name:
            return (inp * input_tokens + out * output_tokens) / _MILLION
    return Decimal("0")


def actual_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """What we actually pay. Every deployment in the router is a free tier today."""
    return Decimal("0")
