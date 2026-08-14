"""BUG-058 — an unmeasured breaker signal must be visibly absent, never silently healthy.

Before the fix, ``BreakerSignals`` defaulted every field to a benign VALUE and the only
production caller (the triage tick) passed schema counts alone — so the daily-loss,
consecutive-loss, stale-data and broker-error breakers could never fire, while reading as
"healthy". ``None`` now means NOT SAMPLED: it cannot trip, and :func:`unsampled` names it.
The broker-error signal gains a real production source: the gateway increments
``st.broker_errors_since_sample`` on a failed broker submit and the triage tick
samples-and-resets it.
"""
from __future__ import annotations

from decimal import Decimal

from orchestrator import breakers as bk


def test_unsampled_signals_cannot_trip_and_are_reported():
    signals = bk.BreakerSignals()                      # nothing sampled
    assert bk.evaluate(signals) == []
    assert set(bk.unsampled(signals)) == {
        bk.BRK_DAILY_DRAWDOWN, bk.BRK_CONSECUTIVE_LOSSES, bk.BRK_SCHEMA_FAILURES,
        bk.BRK_STALE_DATA, bk.BRK_BROKER_ERRORS,
    }


def test_sampled_benign_signals_neither_trip_nor_read_unsampled():
    signals = bk.BreakerSignals(equity_drawdown_today=Decimal("0"), consecutive_losses=0,
                                schema_failures=0, schema_validations=10,
                                max_data_age_s=1.0, broker_errors=0)
    assert bk.evaluate(signals) == []
    assert bk.unsampled(signals) == []


def test_each_sampled_signal_still_trips_alone():
    cases = [
        (bk.BRK_DAILY_DRAWDOWN, bk.BreakerSignals(equity_drawdown_today=Decimal("600"))),
        (bk.BRK_CONSECUTIVE_LOSSES, bk.BreakerSignals(consecutive_losses=3)),
        (bk.BRK_SCHEMA_FAILURES, bk.BreakerSignals(schema_failures=5, schema_validations=10)),
        (bk.BRK_STALE_DATA, bk.BreakerSignals(max_data_age_s=121.0)),
        (bk.BRK_BROKER_ERRORS, bk.BreakerSignals(broker_errors=5)),
    ]
    for name, signals in cases:
        assert bk.evaluate(signals) == [name]
