"""Single-name size that can only shrink an existing cap.

Volatility weight follows Moreira and Muir: ``min(max_weight, target_vol / vol)``.
The Gaussian Kelly fraction is ``edge / vol**2``, then multiplied by ``kelly_frac``.
The suggestion is the minimum of those notionals and the caller-supplied cap, quantized
down to the cent.

``edge`` and ``vol`` must already share a horizon. This function does not annualize and
does not place an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from collections.abc import Sequence


class SizerError(ValueError):
    """An input cannot be sized without loosening a limit or inventing a number."""


@dataclass(frozen=True)
class SizerResult:
    suggested: Decimal
    vol_notional: Decimal
    kelly_notional: Decimal
    cap_notional: Decimal
    binding: str

    def as_dict(self) -> dict:
        return {
            "suggested": format(self.suggested, "f"),
            "vol_notional": format(self.vol_notional, "f"),
            "kelly_notional": format(self.kelly_notional, "f"),
            "cap_notional": format(self.cap_notional, "f"),
            "binding": self.binding,
        }


def _dec(name: str, value: Decimal) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise SizerError(f"{name} must be Decimal, not {type(value).__name__}")
    if not value.is_finite():
        raise SizerError(f"{name} must be finite")
    return value


def suggest_notional(
    edge: Decimal,
    vol: Decimal,
    target_vol: Decimal,
    kelly_frac: Decimal,
    caps: Decimal,
    equity: Decimal,
    *,
    max_weight: Decimal = Decimal("1"),
) -> SizerResult:
    edge = _dec("edge", edge)
    vol = _dec("vol", vol)
    target_vol = _dec("target_vol", target_vol)
    kelly_frac = _dec("kelly_frac", kelly_frac)
    caps = _dec("caps", caps)
    equity = _dec("equity", equity)
    max_weight = _dec("max_weight", max_weight)
    if vol <= 0 or target_vol <= 0 or equity <= 0:
        raise SizerError("vol, target_vol, and equity must be positive")
    if caps < 0:
        raise SizerError("caps must be non-negative")
    if not (Decimal(0) < kelly_frac <= Decimal(1)):
        raise SizerError("kelly_frac must be in (0, 1]")
    if not (Decimal(0) < max_weight <= Decimal(1)):
        raise SizerError("max_weight must be in (0, 1]")
    vol_weight = min(max_weight, target_vol / vol)
    vol_notional = equity * vol_weight
    if edge <= 0:
        kelly_notional = Decimal(0)
        binding = "nonpositive_edge"
        raw = Decimal(0)
    else:
        kelly_weight = min(max_weight, kelly_frac * edge / (vol * vol))
        kelly_notional = equity * kelly_weight
        cap_notional = min(caps, equity * max_weight)
        binding, raw = min(
            (("vol", vol_notional), ("kelly", kelly_notional), ("cap", cap_notional)),
            key=lambda item: item[1],
        )
    cap_notional = min(caps, equity * max_weight)
    if edge <= 0:
        raw = Decimal(0)
    suggested = raw.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if suggested > caps or suggested < 0:
        raise SizerError("suggestion escaped the cap")
    return SizerResult(
        suggested=suggested,
        vol_notional=vol_notional,
        kelly_notional=kelly_notional,
        cap_notional=cap_notional,
        binding=binding,
    )


def ewma_volatility(returns: Sequence[Decimal], lam: Decimal = Decimal("0.94")) -> Decimal:
    """RiskMetrics variance recursion. Seeded with the first squared return, not the sample.

    Seeding with the full-sample variance would use later returns in the initial level.
    The caller still has to pass a series that ends at the decision time.
    """
    lam = _dec("lam", lam)
    if not (Decimal(0) < lam < Decimal(1)):
        raise SizerError("lam must be in (0, 1)")
    if len(returns) < 2:
        raise SizerError("need at least 2 returns")
    cleaned = [_dec("return", item) for item in returns]
    variance = cleaned[0] * cleaned[0]
    one = Decimal(1)
    for value in cleaned[1:]:
        variance = lam * variance + (one - lam) * value * value
    if variance < 0:
        raise SizerError("negative variance")
    return variance.sqrt()
