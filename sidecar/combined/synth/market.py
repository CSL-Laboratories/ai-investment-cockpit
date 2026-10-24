"""Regime-switching and block-bootstrap generators.

Both emit the harness ``Bar`` type. Dates are a weekday calendar starting
2024-01-02. They are session indexes, not a claim that those historical days
were resimulated. ETF bootstrap blocks are contiguous slices of the calibrated
panel, including the wrap at the end of the panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import numpy as np

from combined.synth.calibrate import BLOCK, IDIO_SCALE, NAME_SPREAD_MULT, REGIMES, Calibration, fit
from combined.synth.surface import iv_path
from sidecar.harness.backtest import Bar

ETFS = ("SPY", "QQQ", "IWM")
NAMES = ("NEXA", "BRKQ")
SYMBOLS = ETFS + NAMES
START = date(2024, 1, 2)
RETURN_FLOOR = -0.40
RETURN_CAP = 0.60


@dataclass
class SyntheticPath:
    grouped: dict[str, list[Bar]]
    micro: dict[date, dict[str, dict[str, Decimal]]]
    regimes: list[str]
    sessions: list[date]
    generator: str
    returns: dict[str, list[float]]
    atm_iv: list[float]
    block_index: list[int] | None = None
    block_starts: list[int] = field(default_factory=list)
    seed: int = 0


def business_days(n: int, start: date = START) -> list[date]:
    """Weekdays only. Exchange holidays are not removed."""
    out = []
    day = start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _money(value: float) -> Decimal:
    return Decimal(str(round(float(value), 6)))


def _unit_t(rng: np.random.Generator, nu: float) -> float:
    draw = float(rng.standard_t(nu))
    scale = math_sqrt((nu - 2.0) / nu)
    return draw * scale


def math_sqrt(value: float) -> float:
    return float(np.sqrt(value))


def _clip(value: float) -> float:
    if value < RETURN_FLOOR:
        return RETURN_FLOOR
    if value > RETURN_CAP:
        return RETURN_CAP
    return value


def assemble(
    cal: Calibration,
    *,
    sessions: list[date],
    regimes: list[str],
    totals: dict[str, list[float]],
    overnights: dict[str, list[float]],
    generator: str,
    seed: int,
    block_index: list[int] | None = None,
    block_starts: list[int] | None = None,
    range_override: list[float] | None = None,
    volume_override: list[float] | None = None,
) -> SyntheticPath:
    if len(regimes) != len(sessions):
        raise ValueError("regimes and sessions must match")
    n_returns = len(sessions) - 1
    for symbol in SYMBOLS:
        if len(totals[symbol]) != n_returns or len(overnights[symbol]) != n_returns:
            raise ValueError(f"{symbol} return length does not match the calendar")
    grouped: dict[str, list[Bar]] = {symbol: [] for symbol in SYMBOLS}
    micro: dict[date, dict[str, dict[str, Decimal]]] = {}
    price = dict(cal.start_price)
    for symbol in SYMBOLS:
        grouped[symbol].append(_bar(sessions[0], symbol, price[symbol], price[symbol], regimes[0], cal, None, None))
    micro[sessions[0]] = _micro(cal, regimes[0])
    stored = {symbol: [] for symbol in SYMBOLS}
    for index, day in enumerate(sessions[1:]):
        regime = regimes[index + 1]
        for symbol in SYMBOLS:
            total = float(totals[symbol][index])
            overnight = float(overnights[symbol][index])
            prev = price[symbol]
            close = prev * (1.0 + total)
            open_ = prev * (1.0 + overnight)
            if close < 0.5:
                close = 0.5
            if open_ < 0.5:
                open_ = 0.5
            stored[symbol].append(close / prev - 1.0)
            price[symbol] = close
            range_frac = None if range_override is None else range_override[index]
            volume = volume_for(cal, symbol, None if volume_override is None else volume_override[index])
            grouped[symbol].append(_bar(day, symbol, open_, close, regime, cal, range_frac, volume))
        micro[day] = _micro(cal, regime)
    fallback = float(np.sqrt(cal.spy_var * 252.0))
    atm = iv_path(stored["SPY"], regimes, fallback)
    return SyntheticPath(
        grouped=grouped,
        micro=micro,
        regimes=regimes,
        sessions=sessions,
        generator=generator,
        returns=stored,
        atm_iv=atm,
        block_index=block_index,
        block_starts=list(block_starts or []),
        seed=seed,
    )


def volume_for(cal: Calibration, symbol: str, spy_volume: float | None) -> float | None:
    if spy_volume is None:
        return None
    if symbol == "SPY":
        return spy_volume
    if symbol in NAMES:
        return spy_volume * float(cal.single_names[symbol]["volume_vs_spy"])
    return None


def _bar(
    day: date,
    symbol: str,
    open_: float,
    close: float,
    regime: str,
    cal: Calibration,
    range_frac: float | None,
    volume: float | None,
) -> Bar:
    span = cal.range_frac[regime] if range_frac is None else range_frac
    if symbol in NAMES:
        span *= 1.5
    high = max(open_, close) * (1.0 + span / 2.0)
    low = max(min(open_, close) * (1.0 - span / 2.0), 0.01)
    if volume is None:
        if symbol in ETFS:
            volume = cal.volume[symbol][regime]
        else:
            volume = cal.volume["SPY"][regime] * float(cal.single_names[symbol]["volume_vs_spy"])
    open_d = _money(open_)
    close_d = _money(close)
    high_d = max(_money(high), open_d, close_d)
    low_d = min(_money(low), open_d, close_d)
    return Bar(
        day=day,
        symbol=symbol,
        open=open_d,
        high=high_d,
        low=low_d,
        close=close_d,
        adj_close=close_d,
        volume=_money(max(volume, 1.0)),
    )


def _micro(cal: Calibration, regime: str) -> dict[str, dict[str, Decimal]]:
    out = {}
    for symbol in SYMBOLS:
        spread = cal.spread[regime]
        depth = cal.depth_scale[regime]
        if symbol in NAMES:
            spread *= NAME_SPREAD_MULT
            depth *= 0.05
        out[symbol] = {
            "half_spread": Decimal(str(spread)),
            "depth_scale": Decimal(str(depth)),
        }
    return out


def _transition(cal: Calibration, regime: str, rng: np.random.Generator) -> str:
    probs = [cal.transition[regime][name] for name in REGIMES]
    return str(rng.choice(list(REGIMES), p=np.array(probs) / sum(probs)))


def generate_markov(cal: Calibration | None = None, n_sessions: int = 252, seed: int = 1) -> SyntheticPath:
    cal = cal or fit()
    if n_sessions < 3:
        raise ValueError("n_sessions must be at least 3")
    rng = np.random.default_rng(seed)
    sessions = business_days(n_sessions)
    regime = "sideways"
    variance = cal.regime_var[regime] * cal.continuous_var_share
    eps = 0.0
    regimes = ["sideways"]
    totals = {symbol: [] for symbol in SYMBOLS}
    overnights = {symbol: [] for symbol in SYMBOLS}
    for _index in range(1, n_sessions):
        regime = _transition(cal, regime, rng)
        target = cal.regime_var[regime] * cal.continuous_var_share
        omega = target * (1.0 - cal.garch_alpha - cal.garch_beta)
        variance = omega + cal.garch_alpha * eps * eps + cal.garch_beta * variance
        if variance < 1e-10:
            variance = 1e-10
        shock = math_sqrt(variance) * _unit_t(rng, cal.student_nu)
        if float(rng.random()) < cal.jump_prob and cal.jump_size > 0:
            sign = -1.0 if float(rng.random()) < cal.jump_neg_prob else 1.0
            shock += sign * cal.jump_size
        shock = _clip(shock)
        expected_jump = cal.jump_prob * cal.jump_size * (1.0 - 2.0 * cal.jump_neg_prob)
        spy = _clip(cal.regime_mean[regime] + shock - expected_jump)
        eps = spy - cal.regime_mean[regime]
        totals["SPY"].append(spy)
        scale = IDIO_SCALE[regime]
        for symbol in SYMBOLS:
            if symbol == "SPY":
                continue
            idiosyncratic = scale * cal.idio_vol[symbol] * float(rng.normal())
            totals[symbol].append(_clip(cal.alpha[symbol] + cal.beta[symbol] * (spy - cal.spy_mean) + idiosyncratic))
        for symbol in SYMBOLS:
            overnights[symbol].append(cal.gap_share * totals[symbol][-1])
        regimes.append(regime)
    return assemble(
        cal,
        sessions=sessions,
        regimes=regimes,
        totals=totals,
        overnights=overnights,
        generator="markov",
        seed=seed,
    )


def generate_bootstrap(cal: Calibration | None = None, n_sessions: int = 252, seed: int = 1) -> SyntheticPath:
    """Joint circular blocks of the three ETFs. Single names are the SPY factor plus fresh noise."""
    cal = cal or fit()
    if cal.n_panel < BLOCK:
        raise ValueError("panel is shorter than the block")
    rng = np.random.default_rng(seed)
    sessions = business_days(n_sessions)
    need = n_sessions - 1
    indexes: list[int] = []
    starts: list[int] = []
    while len(indexes) < need:
        start = int(rng.integers(0, cal.n_panel))
        starts.append(start)
        for step in range(BLOCK):
            indexes.append((start + step) % cal.n_panel)
            if len(indexes) == need:
                break
    regimes = ["sideways"]
    totals = {symbol: [] for symbol in SYMBOLS}
    overnights = {symbol: [] for symbol in SYMBOLS}
    ranges = []
    volumes = []
    for index in indexes:
        regime = cal.panel_regime[index]
        spy = float(cal.panel_spy[index])
        totals["SPY"].append(spy)
        totals["QQQ"].append(float(cal.panel_qqq[index]))
        totals["IWM"].append(float(cal.panel_iwm[index]))
        overnights["SPY"].append(float(cal.panel_gap_spy[index]))
        overnights["QQQ"].append(float(cal.panel_gap_qqq[index]))
        overnights["IWM"].append(float(cal.panel_gap_iwm[index]))
        scale = IDIO_SCALE[regime]
        for symbol in NAMES:
            idiosyncratic = scale * cal.idio_vol[symbol] * float(rng.normal())
            total = _clip(cal.beta[symbol] * (spy - cal.spy_mean) + idiosyncratic)
            totals[symbol].append(total)
            overnights[symbol].append(cal.gap_share * total)
        regimes.append(regime)
        ranges.append(float(cal.panel_range[index]))
        volumes.append(float(cal.panel_volume_spy[index]))
    return assemble(
        cal,
        sessions=sessions,
        regimes=regimes,
        totals=totals,
        overnights=overnights,
        generator="block_bootstrap",
        seed=seed,
        block_index=indexes,
        block_starts=starts,
        range_override=ranges,
        volume_override=volumes,
    )


def bars_are_ordered(path: SyntheticPath) -> bool:
    for symbol, bars in path.grouped.items():
        previous = None
        for bar in bars:
            if bar.symbol != symbol or bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
                return False
            if bar.close != bar.adj_close or bar.volume <= 0:
                return False
            if previous is not None and bar.day <= previous:
                return False
            previous = bar.day
    return True
