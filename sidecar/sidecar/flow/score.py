"""Score one option chain. No network and no clock reads."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from sidecar.flow.config import (
    ALERT_SCORE,
    BASELINE_MIN,
    BASELINE_WINDOW,
    MAX_DTE,
    MIN_VOLUME,
    OI_MAX_AGE_DAYS,
    OTM_PCT,
    PREMIUM_MIN,
    SKEW_MIN_SIDE,
    SKEW_SHIFT_MIN,
    SWEEP_MIN_PRINTS,
    SWEEP_SECONDS,
    VOL_OI_CAP,
    VOL_OI_MIN,
    Z_CAP,
    Z_MIN,
    FlowError,
    resolve_feed,
)


@dataclass(frozen=True)
class OptionPrint:
    price: float
    size: float
    timestamp: datetime
    ask: float | None


@dataclass(frozen=True)
class ContractView:
    underlying: str
    symbol: str
    expiration: date
    strike: float
    right: str
    spot: float | None
    volume: float | None
    open_interest: float | None
    open_interest_date: date | None
    bid: float | None
    ask: float | None
    delta: float | None
    iv: float | None
    prints: tuple[OptionPrint, ...]
    session_date: date


def trailing_z(prior: list[float], current: float, *, window: int = BASELINE_WINDOW, minimum: int = BASELINE_MIN) -> float | None:
    sample = list(prior)[-window:]
    if len(sample) < minimum:
        return None
    mean = sum(sample) / len(sample)
    variance = sum((item - mean) ** 2 for item in sample) / (len(sample) - 1)
    if variance <= 0 or not math.isfinite(variance):
        return None
    value = (current - mean) / math.sqrt(variance)
    if not math.isfinite(value):
        return None
    return value


def _num(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def usable_open_interest(contract: ContractView) -> float | None:
    interest = _num(contract.open_interest)
    if interest is None or interest <= 0 or contract.open_interest_date is None:
        return None
    age = (contract.session_date - contract.open_interest_date).days
    if age < 0 or age > OI_MAX_AGE_DAYS:
        return None
    return interest


def _premium(price: float | None, size: float | None) -> float | None:
    px = _num(price)
    qty = _num(size)
    if px is None or qty is None or px <= 0 or qty <= 0:
        return None
    return px * qty * 100.0


def _aggressive(price: float, ask: float | None) -> bool:
    quoted = _num(ask)
    return quoted is not None and quoted > 0 and price >= quoted


def print_flags(contract: ContractView) -> dict:
    premiums: list[float] = []
    aggressive_premiums: list[float] = []
    aggressive: list[OptionPrint] = []
    for item in contract.prints:
        premium = _premium(item.price, item.size)
        if premium is None:
            continue
        premiums.append(premium)
        if _aggressive(item.price, item.ask):
            aggressive.append(item)
            aggressive_premiums.append(premium)
    large = bool(premiums) and max(premiums) >= PREMIUM_MIN
    aggressive_large = bool(aggressive_premiums) and max(aggressive_premiums) >= PREMIUM_MIN
    aggressive.sort(key=lambda item: item.timestamp)
    sweep = False
    for index, left in enumerate(aggressive):
        cluster = [left]
        for right in aggressive[index + 1 :]:
            gap = (right.timestamp - left.timestamp).total_seconds()
            if gap < 0:
                continue
            if gap <= SWEEP_SECONDS:
                cluster.append(right)
            else:
                break
        if len(cluster) < SWEEP_MIN_PRINTS:
            continue
        total = 0.0
        for item in cluster:
            premium = _premium(item.price, item.size)
            if premium is not None:
                total += premium
        if total >= PREMIUM_MIN:
            sweep = True
            break
    return {
        "max_premium": None if not premiums else max(premiums),
        "large_premium": large,
        "aggressive_buy": aggressive_large,
        "sweep": sweep,
    }


def short_dated_otm(contract: ContractView) -> bool:
    dte = (contract.expiration - contract.session_date).days
    if dte < 0 or dte > MAX_DTE:
        return False
    spot = _num(contract.spot)
    if spot is not None and spot > 0:
        if contract.right == "call":
            return contract.strike > spot * (1.0 + OTM_PCT)
        if contract.right == "put":
            return contract.strike < spot * (1.0 - OTM_PCT)
        return False
    delta = _num(contract.delta)
    if delta is None:
        return False
    if contract.right == "call":
        return 0.0 < delta <= 0.25
    if contract.right == "put":
        return -0.25 <= delta < 0.0
    return False


def _iv_mean(contracts: list[ContractView], right: str, spot: float) -> float | None:
    values: list[float] = []
    for contract in contracts:
        iv = _num(contract.iv)
        if contract.right != right or iv is None or iv <= 0:
            continue
        if right == "put" and not (spot * (1.0 - 0.10) <= contract.strike < spot * (1.0 - OTM_PCT)):
            continue
        if right == "call" and not (spot * (1.0 + OTM_PCT) < contract.strike <= spot * (1.0 + 0.10)):
            continue
        values.append(iv)
    if len(values) < SKEW_MIN_SIDE:
        return None
    return sum(values) / len(values)


def chain_skew(contracts: list[ContractView]) -> float | None:
    if not contracts:
        return None
    spot = _num(contracts[0].spot)
    if spot is None or spot <= 0:
        return None
    puts = _iv_mean(contracts, "put", spot)
    calls = _iv_mean(contracts, "call", spot)
    if puts is None or calls is None:
        return None
    return puts - calls


def _fmt(value: float | None) -> float | None:
    if value is None:
        return None
    return float(format(value, ".12g"))


def _history_volumes(history: list[dict], symbol: str, session: date) -> list[float]:
    rows = []
    for row in history:
        if row.get("kind") != "volume" or row.get("symbol") != symbol:
            continue
        day = date.fromisoformat(str(row["session_date"]))
        if day < session:
            rows.append((day, float(row["volume"])))
    rows.sort(key=lambda item: item[0])
    return [volume for _, volume in rows]


def _prior_skew(history: list[dict], underlying: str, session: date) -> float | None:
    rows = []
    for row in history:
        if row.get("kind") != "skew" or row.get("underlying") != underlying:
            continue
        day = date.fromisoformat(str(row["session_date"]))
        if day < session and row.get("skew") is not None:
            rows.append((day, float(row["skew"])))
    if not rows:
        return None
    rows.sort(key=lambda item: item[0])
    return rows[-1][1]


def score_book(
    contracts: list[ContractView],
    history: list[dict],
    *,
    feed: str,
    session_date: date,
) -> dict:
    resolve_feed(feed)
    if any(item.session_date != session_date for item in contracts):
        raise FlowError("every contract must use the scan session date")
    underlyings = sorted({item.underlying for item in contracts})
    by_underlying: dict[str, list[ContractView]] = {name: [] for name in underlyings}
    for contract in contracts:
        by_underlying[contract.underlying].append(contract)

    skew_now: dict[str, float | None] = {}
    skew_shift: dict[str, float | None] = {}
    for name, rows in by_underlying.items():
        current = chain_skew(rows)
        skew_now[name] = current
        previous = _prior_skew(history, name, session_date)
        if current is None or previous is None:
            skew_shift[name] = None
        else:
            skew_shift[name] = current - previous

    scored: list[dict] = []
    new_history: list[dict] = []
    for contract in contracts:
        volume = _num(contract.volume)
        interest = usable_open_interest(contract)
        ratio = None
        if volume is not None and interest is not None and interest > 0:
            ratio = volume / interest
        prior = _history_volumes(history, contract.symbol, session_date)
        z_value = None if volume is None else trailing_z(prior, volume)
        prints = print_flags(contract)
        otm = short_dated_otm(contract)
        shift = skew_shift.get(contract.underlying)
        vol_oi_flag = ratio is not None and ratio >= VOL_OI_MIN and volume is not None and volume >= MIN_VOLUME
        z_flag = z_value is not None and z_value >= Z_MIN and volume is not None and volume >= MIN_VOLUME
        skew_flag = bool(otm and shift is not None and shift >= SKEW_SHIFT_MIN)
        parts = {
            "vol_oi": min(VOL_OI_CAP, ratio) if vol_oi_flag and ratio is not None else 0.0,
            "volume_z": min(Z_CAP, z_value) if z_flag and z_value is not None else 0.0,
            "premium": 1.0 if prints["large_premium"] else 0.0,
            "aggressive": 1.0 if prints["aggressive_buy"] else 0.0,
            "sweep": 1.0 if prints["sweep"] else 0.0,
            "short_dated_otm": 1.0 if otm else 0.0,
            "skew_shift": 1.0 if skew_flag else 0.0,
        }
        score = sum(parts.values())
        flags = [name for name, value in parts.items() if value > 0]
        row = {
            "underlying": contract.underlying,
            "symbol": contract.symbol,
            "right": contract.right,
            "strike": contract.strike,
            "expiration": contract.expiration.isoformat(),
            "volume": _fmt(volume),
            "open_interest": _fmt(interest),
            "vol_oi": _fmt(ratio),
            "volume_z": _fmt(z_value),
            "max_premium": _fmt(prints["max_premium"]),
            "large_premium": prints["large_premium"],
            "aggressive_buy": prints["aggressive_buy"],
            "sweep": prints["sweep"],
            "short_dated_otm": otm,
            "skew": _fmt(skew_now.get(contract.underlying)),
            "skew_shift": _fmt(shift),
            "components": {key: _fmt(value) for key, value in parts.items()},
            "flags": flags,
            "score": _fmt(score),
            "alert": score >= ALERT_SCORE,
        }
        scored.append(row)
        if volume is not None:
            new_history.append(
                {
                    "kind": "volume",
                    "session_date": session_date.isoformat(),
                    "symbol": contract.symbol,
                    "volume": volume,
                }
            )

    underlying_rows = []
    for name in underlyings:
        rows = [row for row in scored if row["underlying"] == name]
        call_volume = sum(row["volume"] or 0.0 for row in rows if row["right"] == "call")
        put_volume = sum(row["volume"] or 0.0 for row in rows if row["right"] == "put")
        total = call_volume + put_volume
        otm_volume = sum(row["volume"] or 0.0 for row in rows if row["short_dated_otm"])
        scores = [row["score"] for row in rows if row["score"] is not None]
        current = skew_now[name]
        if current is not None:
            new_history.append(
                {
                    "kind": "skew",
                    "session_date": session_date.isoformat(),
                    "underlying": name,
                    "skew": current,
                }
            )
        underlying_rows.append(
            {
                "underlying": name,
                "session_date": session_date.isoformat(),
                "call_volume": _fmt(call_volume),
                "put_volume": _fmt(put_volume),
                "put_call_volume_ratio": _fmt(put_volume / call_volume) if call_volume > 0 else None,
                "short_dated_otm_volume_share": _fmt(otm_volume / total) if total > 0 else None,
                "skew": _fmt(current),
                "skew_shift": _fmt(skew_shift[name]),
                "max_contract_score": None if not scores else max(scores),
                "flagged_count": sum(1 for row in rows if row["flags"]),
                "alert_count": sum(1 for row in rows if row["alert"]),
            }
        )

    scored.sort(key=lambda row: (-(row["score"] or 0.0), row["symbol"]))
    return {
        "schema_version": "sidecar.flow.scan.v1",
        "orders_submitted": 0,
        "live_trading": False,
        "feed": feed,
        "session_date": session_date.isoformat(),
        "alert_score": ALERT_SCORE,
        "contracts": scored,
        "underlyings": underlying_rows,
        "history_rows": new_history,
    }


def render_markdown(scan: dict) -> str:
    lines = [
        "# Unusual options alerts",
        "",
        f"Feed: {scan['feed']}. Session: {scan['session_date']}. Orders submitted: {scan['orders_submitted']}.",
        "",
        "A row is an alert only when the predeclared score is at least "
        f"{scan['alert_score']}. A single flag is listed and is not, by itself, an alert "
        "unless its component reaches that score.",
        "",
        "| Alert | Score | Underlying | Contract | Flags | Vol/OI | Volume z | Premium |",
        "|---|---:|---|---|---|---:|---:|---:|",
    ]
    flagged = [row for row in scan["contracts"] if row["flags"]]
    if not flagged:
        lines.append("| | | | none | | | | |")
    for row in flagged:
        lines.append(
            "| {alert} | {score} | {underlying} | {symbol} | {flags} | {vol_oi} | {volume_z} | {premium} |".format(
                alert="yes" if row["alert"] else "no",
                score=row["score"],
                underlying=row["underlying"],
                symbol=row["symbol"],
                flags=",".join(row["flags"]),
                vol_oi="" if row["vol_oi"] is None else row["vol_oi"],
                volume_z="" if row["volume_z"] is None else row["volume_z"],
                premium="" if row["max_premium"] is None else row["max_premium"],
            )
        )
    lines.append("")
    return "\n".join(lines)


def events_from_scan(scan: dict) -> list[dict]:
    """One event per underlying and session: the highest alert score that day."""
    best: dict[tuple[str, str], dict] = {}
    for row in scan["contracts"]:
        if not row["alert"]:
            continue
        key = (row["underlying"], scan["session_date"])
        current = best.get(key)
        if current is None or row["score"] > current["score"]:
            best[key] = {
                "date": scan["session_date"],
                "symbol": row["underlying"],
                "score": row["score"],
                "source": "options_flow",
            }
    return [best[key] for key in sorted(best)]
