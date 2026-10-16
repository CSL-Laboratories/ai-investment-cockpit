"""Per-decision cost quote for the workflow audit log (CSL-42 / CSL-98).

Rates are the public uncached standard prices. Cache hits and batch discounts
are not assumed, so the quote is an upper bound on the list price. Models
without a confirmed rate are logged as unpriced rather than given a guessed
number. Haiku 5.5 uses the short rate at or under 100k input tokens and the
long rate above it; the long rate applies to output as well.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from config.models import HAIKU_55_LONG_CONTEXT_TOKENS

_MILLION = Decimal(1_000_000)
_USD_QUANTUM = Decimal("0.00000001")

# (input USD per 1M, output USD per 1M, tier name)
_HAIKU_45 = (Decimal("1"), Decimal("5"), "haiku-4.5")
_HAIKU_55_SHORT = (Decimal("0.10"), Decimal("0.50"), "haiku-5.5-short")
_HAIKU_55_LONG = (Decimal("0.50"), Decimal("2.50"), "haiku-5.5-long")

_FLAT_RATES = {
    "claude-haiku-4-5": _HAIKU_45,
    "claude-haiku-4-5-20251001": _HAIKU_45,
    "haiku": _HAIKU_45,
}


@dataclass(frozen=True)
class CostQuote:
    model_pinned: str
    input_tokens: int
    output_tokens: int | None
    input_token_source: str
    rate_tier: str
    usd: str | None
    usd_scope: str

    def as_payload(self) -> dict:
        return {
            "model_pinned": self.model_pinned,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_token_source": self.input_token_source,
            "rate_tier": self.rate_tier,
            "usd": self.usd,
            "usd_scope": self.usd_scope,
        }


def _usd(amount: Decimal) -> str:
    return str(amount.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP))


def sum_quotes(quotes: list[CostQuote]) -> CostQuote:
    """Add separately-tiered quotes. Does not re-quote the combined token counts."""
    if not quotes:
        raise ValueError("sum_quotes requires at least one quote")
    if len(quotes) == 1:
        return quotes[0]
    first = quotes[0]
    input_tokens = 0
    output_tokens = 0
    saw_output = False
    usd_total = Decimal("0")
    unpriced = False
    sources = set()
    tiers = []
    for quote in quotes:
        input_tokens += quote.input_tokens
        if quote.output_tokens is not None:
            output_tokens += quote.output_tokens
            saw_output = True
        sources.add(quote.input_token_source)
        tiers.append(quote.rate_tier)
        if quote.usd is None:
            unpriced = True
        else:
            usd_total += Decimal(quote.usd)
    rate_tier = tiers[0] if len(set(tiers)) == 1 else "+".join(tiers)
    source = "vendor" if sources == {"vendor"} else (
        "estimate" if sources == {"estimate"} else "mixed"
    )
    return CostQuote(
        model_pinned=first.model_pinned,
        input_tokens=input_tokens,
        output_tokens=output_tokens if saw_output else None,
        input_token_source=source,
        rate_tier="unpriced" if unpriced else rate_tier,
        usd=None if unpriced else _usd(usd_total),
        usd_scope="unpriced" if unpriced else (
            "input_output" if saw_output else "input_only"
        ),
    )


def _tier(model_pinned: str, input_tokens: int):
    if model_pinned == "claude-haiku-5-5":
        if input_tokens > HAIKU_55_LONG_CONTEXT_TOKENS:
            return _HAIKU_55_LONG
        return _HAIKU_55_SHORT
    return _FLAT_RATES.get(model_pinned)


def quote_cost(model_pinned: str, *, input_tokens: int, output_tokens: int | None,
               input_token_source: str) -> CostQuote:
    """Quote one call. ``output_tokens is None`` prices input only."""
    if input_tokens < 0 or (output_tokens is not None and output_tokens < 0):
        raise ValueError("token counts must be >= 0")
    if input_token_source not in {"estimate", "vendor"}:
        raise ValueError(f"unknown token source {input_token_source!r}")
    tier = _tier(model_pinned, input_tokens)
    if tier is None:
        return CostQuote(
            model_pinned=model_pinned,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_token_source=input_token_source,
            rate_tier="unpriced",
            usd=None,
            usd_scope="unpriced",
        )
    in_rate, out_rate, name = tier
    amount = (Decimal(input_tokens) * in_rate) / _MILLION
    scope = "input_only"
    if output_tokens is not None:
        amount += (Decimal(output_tokens) * out_rate) / _MILLION
        scope = "input_output"
    return CostQuote(
        model_pinned=model_pinned,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_token_source=input_token_source,
        rate_tier=name,
        usd=_usd(amount),
        usd_scope=scope,
    )


def _token_pair(usage: dict) -> tuple[int, int] | None:
    inp = usage.get("input_tokens")
    out = usage.get("output_tokens")
    if not (type(inp) is int and type(out) is int and inp >= 0 and out >= 0):
        return None
    # Claude reports cached prompt tokens separately from input_tokens. Count them at the
    # base rate (an upper bound) so cost and the long-context tier see the whole prompt.
    for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        extra = usage.get(key)
        if extra is None:
            continue
        if type(extra) is not int or extra < 0:
            return None
        inp += extra
    return inp, out


def parse_vendor_usage(raw: str) -> tuple[int, int] | None:
    """Read ``(input_tokens, output_tokens)`` from a Claude envelope or Codex JSONL.

    Returns None when the vendor did not report a usable pair. Model prose is
    not scanned for numbers.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        usage = parsed.get("usage")
        if isinstance(usage, dict):
            return _token_pair(usage)
        return None
    found = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("usage"), dict):
            pair = _token_pair(event["usage"])
            if pair is not None:
                found = pair
    return found
