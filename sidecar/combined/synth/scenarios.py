"""Stylized paths. These are not replays of the named historical episodes.

The fixture does not contain 2018 or the spring of 2020. The 2022 grind below is a
constant drift, not the actual 2022 tape. Each path forces the shape the name
describes so a book can be walked through that shape once.
"""

from __future__ import annotations

import numpy as np

from combined.synth.calibrate import Calibration, fit
from combined.synth.market import SYMBOLS, SyntheticPath, assemble, business_days

SHOCK_AT = 80


def _base(n: int, spy: np.ndarray, regimes: list[str], cal: Calibration, name: str, seed: int) -> SyntheticPath:
    sessions = business_days(n)
    totals = {symbol: np.zeros(n - 1) for symbol in SYMBOLS}
    totals["SPY"] = spy
    for symbol in SYMBOLS:
        if symbol == "SPY":
            continue
        totals[symbol] = cal.alpha.get(symbol, 0.0) + cal.beta[symbol] * (spy - cal.spy_mean)
    overnights = {symbol: cal.gap_share * totals[symbol] for symbol in SYMBOLS}
    return assemble(
        cal,
        sessions=sessions,
        regimes=regimes,
        totals={symbol: [float(value) for value in totals[symbol]] for symbol in SYMBOLS},
        overnights={symbol: [float(value) for value in overnights[symbol]] for symbol in SYMBOLS},
        generator=f"scenario:{name}",
        seed=seed,
    )


def _regimes(n: int, default: str = "sideways") -> list[str]:
    return [default] * n


def flash_crash(cal: Calibration | None = None) -> SyntheticPath:
    cal = cal or fit()
    n = 120
    spy = np.zeros(n - 1)
    regimes = _regimes(n, "sideways")
    spy[SHOCK_AT] = -0.09
    spy[SHOCK_AT + 1] = 0.045
    regimes[SHOCK_AT + 1] = "crisis"
    regimes[SHOCK_AT + 2] = "crisis"
    return _base(n, spy, regimes, cal, "flash_crash", 11)


def crash_2020(cal: Calibration | None = None) -> SyntheticPath:
    """A sharp multi-week drop and a partial rebound. Not the 2020 tape."""
    cal = cal or fit()
    n = 160
    spy = np.zeros(n - 1)
    regimes = _regimes(n, "sideways")
    spy[SHOCK_AT : SHOCK_AT + 15] = -0.025
    spy[SHOCK_AT + 15 : SHOCK_AT + 35] = 0.012
    for index in range(SHOCK_AT, SHOCK_AT + 35):
        regimes[index + 1] = "crisis"
    return _base(n, spy, regimes, cal, "crash_2020", 12)


def grind_2022(cal: Calibration | None = None) -> SyntheticPath:
    """A slow negative drift. Not the actual 2022 path."""
    cal = cal or fit()
    n = 200
    spy = np.full(n - 1, -0.0012)
    regimes = _regimes(n, "bear")
    return _base(n, spy, regimes, cal, "grind_2022", 13)


def volmageddon(cal: Calibration | None = None) -> SyntheticPath:
    """A short burst of large alternating days. Not February 2018."""
    cal = cal or fit()
    n = 120
    spy = np.zeros(n - 1)
    regimes = _regimes(n, "sideways")
    pattern = (-0.04, 0.035, -0.045, 0.03, -0.02)
    for offset, value in enumerate(pattern):
        spy[SHOCK_AT + offset] = value
        regimes[SHOCK_AT + offset + 1] = "crisis"
    return _base(n, spy, regimes, cal, "volmageddon", 14)


def melt_up(cal: Calibration | None = None) -> SyntheticPath:
    cal = cal or fit()
    n = 160
    spy = np.full(n - 1, 0.0022)
    regimes = _regimes(n, "bull")
    return _base(n, spy, regimes, cal, "melt_up", 15)


def sideways_chop(cal: Calibration | None = None) -> SyntheticPath:
    cal = cal or fit()
    n = 140
    rng = np.random.default_rng(16)
    spy = rng.normal(0.0, 0.006, n - 1)
    regimes = _regimes(n, "sideways")
    return _base(n, spy, regimes, cal, "sideways_chop", 16)


def rate_shock(cal: Calibration | None = None) -> SyntheticPath:
    """One day on which every name takes the same return, so correlation is 1 that day."""
    cal = cal or fit()
    n = 120
    sessions = business_days(n)
    totals = {symbol: np.zeros(n - 1) for symbol in SYMBOLS}
    regimes = _regimes(n, "sideways")
    for symbol in SYMBOLS:
        totals[symbol][SHOCK_AT] = -0.035
    regimes[SHOCK_AT + 1] = "crisis"
    overnights = {symbol: np.array(totals[symbol]) for symbol in SYMBOLS}
    return assemble(
        cal,
        sessions=sessions,
        regimes=regimes,
        totals={symbol: [float(value) for value in totals[symbol]] for symbol in SYMBOLS},
        overnights={symbol: [float(value) for value in overnights[symbol]] for symbol in SYMBOLS},
        generator="scenario:rate_shock",
        seed=17,
    )


def earnings_gaps(cal: Calibration | None = None) -> SyntheticPath:
    """Single-name open gaps with the ETFs left flat that day."""
    cal = cal or fit()
    n = 120
    sessions = business_days(n)
    totals = {symbol: np.zeros(n - 1) for symbol in SYMBOLS}
    overnights = {symbol: np.zeros(n - 1) for symbol in SYMBOLS}
    regimes = _regimes(n, "sideways")
    totals["NEXA"][SHOCK_AT] = 0.08
    overnights["NEXA"][SHOCK_AT] = 0.08
    totals["BRKQ"][SHOCK_AT + 10] = -0.06
    overnights["BRKQ"][SHOCK_AT + 10] = -0.06
    regimes[SHOCK_AT + 1] = "sideways"
    return assemble(
        cal,
        sessions=sessions,
        regimes=regimes,
        totals={symbol: [float(value) for value in totals[symbol]] for symbol in SYMBOLS},
        overnights={symbol: [float(value) for value in overnights[symbol]] for symbol in SYMBOLS},
        generator="scenario:earnings_gaps",
        seed=18,
    )


def library(cal: Calibration | None = None) -> dict[str, SyntheticPath]:
    cal = cal or fit()
    builders = (
        flash_crash,
        crash_2020,
        grind_2022,
        volmageddon,
        melt_up,
        sideways_chop,
        rate_shock,
        earnings_gaps,
    )
    return {builder.__name__: builder(cal) for builder in builders}
