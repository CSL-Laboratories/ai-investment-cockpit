"""Predeclared volatility-premium limits.

The primary row is fixed here. Offline trials may change delta, the IV-rank floor,
or the structure. They are not allowed to replace this primary after the result is seen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal

ALLOWLIST = ("SPY", "QQQ", "IWM")
MAX_LOSS_PER_TRADE = Decimal("1000")
MAX_TOTAL_LOSS = Decimal("5000")
MAX_OPEN_SPREADS = 5
MIN_DTE = 7
MAX_DTE = 45
TARGET_DTE = 30
CLOSE_DTE = 7
WIDTH = Decimal("5")
DELTA = Decimal("0.20")
DELTA_MIN = Decimal("0.12")
DELTA_MAX = Decimal("0.30")
IV_RANK_MIN = Decimal("0.60")
IV_RV_MIN = Decimal("0.02")
TP_FRACTION = Decimal("0.50")
STOP_MULTIPLE = Decimal("2")
QTY = 1
IV_WINDOW = 60
RV_WINDOW = 10
RANK_WINDOW = 252
# Fewer than this many prior sessions and the runner computes no IV rank.
# 252 remains the percentile length whenever that many sessions exist.
MIN_IV_HISTORY = 60
STARTING_EQUITY = Decimal("100000")
SPREAD_FLOOR = Decimal("0.05")
SPREAD_FRAC = Decimal("0.08")
MIN_CREDIT = Decimal("0.05")
CONTRACT_MULTIPLIER = Decimal("100")
QUOTE_MAX_AGE_SECONDS = 900
# A live quote wider than this is unusable. Fail closed instead of stopping out on it.
MAX_QUOTE_SPREAD_FRAC = Decimal("0.25")
MAX_QUOTE_SPREAD_FLOOR = Decimal("0.10")


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    kind: str
    delta: Decimal = DELTA
    iv_rank_min: Decimal = IV_RANK_MIN
    iv_rv_min: Decimal = IV_RV_MIN
    target_dte: int = TARGET_DTE
    close_dte: int = CLOSE_DTE
    width: Decimal = WIDTH
    tp_fraction: Decimal = TP_FRACTION
    stop_multiple: Decimal = STOP_MULTIPLE
    qty: int = QTY
    iv_window: int = IV_WINDOW
    rv_window: int = RV_WINDOW
    rank_window: int = RANK_WINDOW

    def as_json(self) -> dict:
        payload = asdict(self)
        payload["primary"] = self.name == PRIMARY.name and self.kind == PRIMARY.kind
        return payload


PRIMARY = StrategyConfig(name="put_credit_d20_ivr60", kind="put_credit")


def trial_configs() -> tuple[StrategyConfig, ...]:
    """Primary first. Later rows are the trial ledger, not a selection set."""
    return (
        PRIMARY,
        replace(PRIMARY, name="put_credit_d15", delta=Decimal("0.15")),
        replace(PRIMARY, name="put_credit_ivr40", iv_rank_min=Decimal("0.40")),
        replace(PRIMARY, name="iron_condor_d20", kind="iron_condor"),
    )


def friday_expiry(trade_day: date, target_dte: int = TARGET_DTE) -> date | None:
    """Snap a target tenor to a Friday inside the 7–45 DTE window."""
    raw = trade_day.toordinal() + int(target_dte)
    candidate = date.fromordinal(raw)
    # Monday is 0. Step forward to Friday.
    candidate = date.fromordinal(candidate.toordinal() + ((4 - candidate.weekday()) % 7))
    dte = (candidate - trade_day).days
    if dte < MIN_DTE:
        candidate = date.fromordinal(candidate.toordinal() + 7)
        dte = (candidate - trade_day).days
    if dte < MIN_DTE or dte > MAX_DTE:
        return None
    return candidate
