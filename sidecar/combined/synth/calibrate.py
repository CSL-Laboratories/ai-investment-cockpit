"""Moment match of the regime model to the committed daily bars.

The requested span was 2016–2026. The fixture starts on 2021-10-18 and contains only
SPY, QQQ, and IWM. Single-name betas are labeled assumptions. Spreads are not in the
bars; the half-spread schedule is structural. None of these numbers is a trading signal.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from sidecar.harness.backtest import load_bars

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BARS = ROOT / "fixtures" / "etf_daily.csv"
REGIMES = ("bull", "bear", "sideways", "crisis")
WARMUP = 60
VOL_WINDOW = 20
BLOCK = 20
CRISIS_DAY = -0.03
BEAR_TREND = -0.08
BULL_TREND = 0.08

# Structural, not estimated from quotes. The bars have no bid or ask.
SPREAD = {
    "bull": 0.00005,
    "sideways": 0.00005,
    "bear": 0.00020,
    "crisis": 0.00150,
}
# Multiplier on printed volume when the impact formula asks for depth.
# Crisis volume in the tape is high; depth is the thing that dries up.
DEPTH_SCALE = {"bull": 1.0, "sideways": 1.0, "bear": 0.6, "crisis": 0.25}
IDIO_SCALE = {"bull": 1.0, "sideways": 1.0, "bear": 0.70, "crisis": 0.35}
NAME_SPREAD_MULT = 3.0

SINGLE_NAMES = {
    "NEXA": {
        "company": "Nexa Biolabs",
        "beta": 1.30,
        "idio_vol": 0.020,
        "start_price": 50.0,
        "volume_vs_spy": 0.02,
        "source": "assumption",
    },
    "BRKQ": {
        "company": "Brickline Holdings",
        "beta": 1.10,
        "idio_vol": 0.016,
        "start_price": 30.0,
        "volume_vs_spy": 0.03,
        "source": "assumption",
    },
}

_CACHE: Calibration | None = None


@dataclass
class Calibration:
    first_date: str
    last_date: str
    n_sessions: int
    n_panel: int
    requested_span: str
    symbols_in_tape: tuple[str, ...]
    transition: dict[str, dict[str, float]]
    regime_mean: dict[str, float]
    regime_var: dict[str, float]
    regime_count: dict[str, int]
    regime_mean_sample: dict[str, float]
    regime_var_sample: dict[str, float]
    regime_mean_fallback: dict[str, bool]
    raw_excess_kurtosis: float
    garch_alpha: float
    garch_beta: float
    garch_omega: float
    garch_loglik: float
    student_nu: float
    excess_kurtosis: float
    jump_prob: float
    jump_size: float
    jump_neg_prob: float
    continuous_var_share: float
    spy_mean: float
    spy_var: float
    alpha: dict[str, float]
    beta: dict[str, float]
    idio_vol: dict[str, float]
    gap_share: float
    range_frac: dict[str, float]
    volume: dict[str, dict[str, float]]
    start_price: dict[str, float]
    corr_etf_calm: float
    corr_etf_crisis: float
    median_rv: dict[str, float]
    vol_crisis_cutoff: float
    idio_scale: dict[str, float] = field(default_factory=lambda: dict(IDIO_SCALE))
    spread: dict[str, float] = field(default_factory=lambda: dict(SPREAD))
    depth_scale: dict[str, float] = field(default_factory=lambda: dict(DEPTH_SCALE))
    single_names: dict = field(default_factory=lambda: dict(SINGLE_NAMES))
    notes: list[str] = field(default_factory=list)
    panel_spy: np.ndarray = field(default_factory=lambda: np.zeros(0))
    panel_qqq: np.ndarray = field(default_factory=lambda: np.zeros(0))
    panel_iwm: np.ndarray = field(default_factory=lambda: np.zeros(0))
    panel_gap_spy: np.ndarray = field(default_factory=lambda: np.zeros(0))
    panel_gap_qqq: np.ndarray = field(default_factory=lambda: np.zeros(0))
    panel_gap_iwm: np.ndarray = field(default_factory=lambda: np.zeros(0))
    panel_regime: list[str] = field(default_factory=list)
    panel_range: np.ndarray = field(default_factory=lambda: np.zeros(0))
    panel_volume_spy: np.ndarray = field(default_factory=lambda: np.zeros(0))

    def public(self) -> dict:
        """JSON-safe parameters. The return panel stays in memory."""
        return {
            "first_date": self.first_date,
            "last_date": self.last_date,
            "n_sessions": self.n_sessions,
            "n_panel": self.n_panel,
            "requested_span": self.requested_span,
            "symbols_in_tape": list(self.symbols_in_tape),
            "transition": self.transition,
            "regime_mean": self.regime_mean,
            "regime_var": self.regime_var,
            "regime_count": self.regime_count,
            "regime_mean_sample": self.regime_mean_sample,
            "regime_var_sample": self.regime_var_sample,
            "regime_mean_fallback": self.regime_mean_fallback,
            "raw_excess_kurtosis": self.raw_excess_kurtosis,
            "garch_alpha": self.garch_alpha,
            "garch_beta": self.garch_beta,
            "garch_omega": self.garch_omega,
            "garch_loglik": self.garch_loglik,
            "student_nu": self.student_nu,
            "excess_kurtosis": self.excess_kurtosis,
            "jump_prob": self.jump_prob,
            "jump_size": self.jump_size,
            "jump_neg_prob": self.jump_neg_prob,
            "continuous_var_share": self.continuous_var_share,
            "spy_mean": self.spy_mean,
            "spy_var": self.spy_var,
            "alpha": self.alpha,
            "beta": self.beta,
            "idio_vol": self.idio_vol,
            "gap_share": self.gap_share,
            "range_frac": self.range_frac,
            "volume": self.volume,
            "start_price": self.start_price,
            "corr_etf_calm": self.corr_etf_calm,
            "corr_etf_crisis": self.corr_etf_crisis,
            "median_rv": self.median_rv,
            "vol_crisis_cutoff": self.vol_crisis_cutoff,
            "idio_scale": self.idio_scale,
            "spread": self.spread,
            "depth_scale": self.depth_scale,
            "single_names": self.single_names,
            "block_length": BLOCK,
            "notes": self.notes,
        }


def _ols(y: np.ndarray, x: np.ndarray) -> tuple[float, float, float]:
    x_mean = float(x.mean())
    y_mean = float(y.mean())
    var = float(np.sum((x - x_mean) ** 2))
    beta = float(np.sum((x - x_mean) * (y - y_mean)) / var)
    alpha = y_mean - beta * x_mean
    resid = y - alpha - beta * x
    return alpha, beta, float(resid.std(ddof=1))


def _garch(log_returns: np.ndarray) -> tuple[float, float, float, float, np.ndarray]:
    demeaned = log_returns - float(log_returns.mean())
    sample_var = float(np.var(demeaned, ddof=1))
    best: tuple[float, float, float, float] | None = None
    best_var = None
    for alpha in (0.02, 0.05, 0.08, 0.12):
        for beta in (0.80, 0.85, 0.90, 0.94):
            if alpha + beta >= 0.99:
                continue
            omega = sample_var * (1.0 - alpha - beta)
            var = sample_var
            loglik = 0.0
            path = np.empty(len(demeaned))
            for index, value in enumerate(demeaned):
                var = omega + alpha * value * value + beta * var
                if var < 1e-12:
                    var = 1e-12
                path[index] = var
                loglik += -0.5 * (math.log(var) + value * value / var)
            if best is None or loglik > best[0]:
                best = (loglik, alpha, beta, omega)
                best_var = path
    assert best is not None and best_var is not None
    return best[1], best[2], best[3], best[0], best_var


def _student_nu(log_returns: np.ndarray, variance: np.ndarray) -> tuple[float, float]:
    demeaned = log_returns - float(log_returns.mean())
    scale = np.sqrt(np.maximum(variance, 1e-12))
    resid = demeaned / scale
    centered = resid - float(resid.mean())
    m2 = float(np.mean(centered**2))
    m4 = float(np.mean(centered**4))
    excess = m4 / (m2 * m2) - 3.0 if m2 > 0 else 0.0
    if excess <= 0.2:
        return 30.0, excess
    nu = 4.0 + 6.0 / excess
    return float(min(30.0, max(5.0, nu))), excess


def _labels(closes: np.ndarray, simple: np.ndarray) -> tuple[list[str | None], float]:
    vol = np.full(len(simple), np.nan)
    for index in range(VOL_WINDOW - 1, len(simple)):
        window = simple[index - VOL_WINDOW + 1 : index + 1]
        vol[index] = float(window.std(ddof=1))
    finite = vol[np.isfinite(vol)]
    cutoff = float(np.quantile(finite, 0.80))
    labels: list[str | None] = [None] * len(simple)
    for index in range(WARMUP, len(simple)):
        trend = closes[index + 1] / closes[index + 1 - WARMUP] - 1.0
        if simple[index] <= CRISIS_DAY or vol[index] >= cutoff:
            labels[index] = "crisis"
        elif trend <= BEAR_TREND:
            labels[index] = "bear"
        elif trend >= BULL_TREND:
            labels[index] = "bull"
        else:
            labels[index] = "sideways"
    return labels, cutoff


def fit(path: Path | None = None, *, refresh: bool = False) -> Calibration:
    global _CACHE
    if _CACHE is not None and path is None and not refresh:
        return _CACHE
    bars_path = Path(path) if path is not None else DEFAULT_BARS
    source = json.loads((bars_path.parent / "SOURCE.json").read_text(encoding="utf-8"))
    grouped = load_bars(bars_path)
    days = sorted(set.intersection(*(set(bar.day for bar in grouped[symbol]) for symbol in ("SPY", "QQQ", "IWM"))))
    by_day = {symbol: {bar.day: bar for bar in grouped[symbol]} for symbol in ("SPY", "QQQ", "IWM")}
    closes = {symbol: np.array([float(by_day[symbol][day].close_adj) for day in days]) for symbol in ("SPY", "QQQ", "IWM")}
    opens = {symbol: np.array([float(by_day[symbol][day].open_adj) for day in days]) for symbol in ("SPY", "QQQ", "IWM")}
    ranges = np.array(
        [(float(by_day["SPY"][day].high) - float(by_day["SPY"][day].low)) / float(by_day["SPY"][day].close) for day in days]
    )
    volumes = {symbol: np.array([float(by_day[symbol][day].volume) for day in days]) for symbol in ("SPY", "QQQ", "IWM")}
    simple = {symbol: closes[symbol][1:] / closes[symbol][:-1] - 1.0 for symbol in ("SPY", "QQQ", "IWM")}
    gaps = {symbol: opens[symbol][1:] / closes[symbol][:-1] - 1.0 for symbol in ("SPY", "QQQ", "IWM")}
    labels, cutoff = _labels(closes["SPY"], simple["SPY"])
    counts = {name: 0 for name in REGIMES}
    means: dict[str, list[float]] = {name: [] for name in REGIMES}
    for index, label in enumerate(labels):
        if label is None:
            continue
        counts[label] += 1
        means[label].append(float(simple["SPY"][index]))
    regime_mean = {}
    regime_var = {}
    regime_mean_sample = {}
    regime_var_sample = {}
    regime_mean_fallback = {}
    unconditional_var = float(np.var(simple["SPY"], ddof=1))
    unconditional_mean = float(np.mean(simple["SPY"]))
    for name in REGIMES:
        sample = np.array(means[name]) if means[name] else np.array([unconditional_mean])
        regime_mean_sample[name] = float(sample.mean())
        regime_var_sample[name] = float(sample.var(ddof=1)) if len(sample) >= 2 else unconditional_var
        # A handful of days is not a drift. Thin regimes use the unconditional moments.
        if len(means[name]) >= 30:
            regime_mean[name] = regime_mean_sample[name]
            regime_var[name] = regime_var_sample[name]
            regime_mean_fallback[name] = False
        else:
            regime_mean[name] = unconditional_mean
            regime_var[name] = unconditional_var
            regime_mean_fallback[name] = True
    trans_counts = np.ones((len(REGIMES), len(REGIMES)))
    index_of = {name: pos for pos, name in enumerate(REGIMES)}
    for left, right in zip(labels, labels[1:], strict=False):
        if left is None or right is None:
            continue
        trans_counts[index_of[left], index_of[right]] += 1
    transition = {}
    for name in REGIMES:
        row = trans_counts[index_of[name]]
        total = float(row.sum())
        transition[name] = {other: float(row[index_of[other]] / total) for other in REGIMES}
    log_spy = np.diff(np.log(closes["SPY"]))
    alpha_g, beta_g, omega, loglik, garch_var = _garch(log_spy)
    nu, excess = _student_nu(log_spy, garch_var)
    sigma = float(np.std(simple["SPY"], ddof=1))
    tail = np.abs(simple["SPY"]) > 3.0 * sigma
    jump_prob = float(tail.mean()) if len(simple["SPY"]) else 0.0
    if int(tail.sum()) > 0:
        jump_size = float(np.median(np.abs(simple["SPY"][tail])))
        jump_neg = float(np.mean(simple["SPY"][tail] < 0))
    else:
        jump_size = 0.0
        jump_neg = 0.5
    jump_var = jump_prob * jump_size * jump_size
    share = 1.0 - jump_var / unconditional_var if unconditional_var > 0 else 1.0
    share = float(min(1.0, max(0.25, share)))
    alpha = {}
    beta = {}
    idio = {}
    for symbol in ("QQQ", "IWM"):
        a, b, resid = _ols(simple[symbol], simple["SPY"])
        alpha[symbol] = a
        beta[symbol] = b
        idio[symbol] = resid
    for symbol, spec in SINGLE_NAMES.items():
        beta[symbol] = float(spec["beta"])
        alpha[symbol] = 0.0
        idio[symbol] = float(spec["idio_vol"])
    gap_var = float(np.var(gaps["SPY"], ddof=1))
    cc_var = float(np.var(simple["SPY"], ddof=1))
    gap_share = float(min(0.80, max(0.20, gap_var / cc_var))) if cc_var > 0 else 0.4
    range_frac = {}
    volume: dict[str, dict[str, float]] = {symbol: {} for symbol in ("SPY", "QQQ", "IWM")}
    rv_bucket: dict[str, list[float]] = {name: [] for name in REGIMES}
    for name in REGIMES:
        mask = np.array([label == name for label in labels])
        if int(mask.sum()) == 0:
            range_frac[name] = float(np.median(ranges[1:]))
            for symbol in ("SPY", "QQQ", "IWM"):
                volume[symbol][name] = float(np.median(volumes[symbol][1:]))
            continue
        range_frac[name] = float(np.median(ranges[1:][mask]))
        for symbol in ("SPY", "QQQ", "IWM"):
            volume[symbol][name] = float(np.median(volumes[symbol][1:][mask]))
    for index, label in enumerate(labels):
        if label is None or index < VOL_WINDOW:
            continue
        window = simple["SPY"][index - VOL_WINDOW + 1 : index + 1]
        rv_bucket[label].append(float(window.std(ddof=1) * math.sqrt(252.0)))
    median_rv = {}
    fallback_rv = float(np.std(simple["SPY"], ddof=1) * math.sqrt(252.0))
    for name in REGIMES:
        median_rv[name] = float(np.median(rv_bucket[name])) if rv_bucket[name] else fallback_rv
    calm = np.array([label is not None and label != "crisis" for label in labels])
    crisis = np.array([label == "crisis" for label in labels])

    def _pair(mask: np.ndarray) -> float:
        if int(mask.sum()) < 20:
            return float("nan")
        return float(np.corrcoef(simple["QQQ"][mask], simple["IWM"][mask])[0, 1])

    panel = np.array([label is not None for label in labels])
    centered = simple["SPY"] - unconditional_mean
    moment2 = float(np.mean(centered**2))
    moment4 = float(np.mean(centered**4))
    raw_excess = moment4 / (moment2 * moment2) - 3.0 if moment2 > 0 else 0.0
    thin = [name for name in REGIMES if regime_mean_fallback[name]]
    notes = [
        "The fit was asked to cover 2016 through 2026. "
        f"The committed fixture is {source['first_date']} through {source['last_date']} "
        f"and only {', '.join(source['symbols'])}.",
        "2016-01-01 through the session before the fixture is not in the tree, so it is not in the fit.",
        "Regime labels use the full-sample 80th percentile of 20-day volatility. That cutoff sees the future. It is a calibration label, not a signal.",
        "Single-name beta, idiosyncratic vol, start price, and volume are assumptions. No single-name tape was loaded.",
        "Half-spreads and the crisis depth multiplier are structural. The bars contain no quotes. Printed volume is not reduced in a crisis; the impact formula's depth input is.",
        "Student-t degrees of freedom come from excess kurtosis of GARCH residuals, clipped to [5, 30]. Jumps are the |return| > 3 sigma days. Continuous variance is scaled so jumps are not added on top of that variance.",
        "ETF betas are in-sample OLS on the same fixture the generator is judged against. They are not a walk-forward estimate.",
    ]
    if thin:
        detail = ", ".join(
            f"{name} n={counts[name]} sample mean {regime_mean_sample[name]:.5f}" for name in thin
        )
        notes.append(
            "Regimes with fewer than 30 labeled days use the unconditional SPY mean and variance. "
            f"{detail}. That sample mean is not used as a drift."
        )
    result = Calibration(
        first_date=str(source["first_date"]),
        last_date=str(source["last_date"]),
        n_sessions=len(days),
        n_panel=int(panel.sum()),
        requested_span="2016-01-01 through 2026-12-31",
        symbols_in_tape=tuple(source["symbols"]),
        transition=transition,
        regime_mean=regime_mean,
        regime_var=regime_var,
        regime_count=counts,
        regime_mean_sample=regime_mean_sample,
        regime_var_sample=regime_var_sample,
        regime_mean_fallback=regime_mean_fallback,
        raw_excess_kurtosis=raw_excess,
        garch_alpha=alpha_g,
        garch_beta=beta_g,
        garch_omega=omega,
        garch_loglik=loglik,
        student_nu=nu,
        excess_kurtosis=excess,
        jump_prob=jump_prob,
        jump_size=jump_size,
        jump_neg_prob=jump_neg,
        continuous_var_share=share,
        spy_mean=unconditional_mean,
        spy_var=unconditional_var,
        alpha=alpha,
        beta=beta,
        idio_vol=idio,
        gap_share=gap_share,
        range_frac=range_frac,
        volume=volume,
        start_price={
            "SPY": float(closes["SPY"][-1]),
            "QQQ": float(closes["QQQ"][-1]),
            "IWM": float(closes["IWM"][-1]),
            "NEXA": float(SINGLE_NAMES["NEXA"]["start_price"]),
            "BRKQ": float(SINGLE_NAMES["BRKQ"]["start_price"]),
        },
        corr_etf_calm=_pair(calm),
        corr_etf_crisis=_pair(crisis),
        median_rv=median_rv,
        vol_crisis_cutoff=cutoff,
        notes=notes,
        panel_spy=simple["SPY"][panel],
        panel_qqq=simple["QQQ"][panel],
        panel_iwm=simple["IWM"][panel],
        panel_gap_spy=gaps["SPY"][panel],
        panel_gap_qqq=gaps["QQQ"][panel],
        panel_gap_iwm=gaps["IWM"][panel],
        panel_regime=[label for label in labels if label is not None],
        panel_range=ranges[1:][panel],
        panel_volume_spy=volumes["SPY"][1:][panel],
    )
    if path is None:
        _CACHE = result
    return result
