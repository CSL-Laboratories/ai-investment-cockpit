"""Score one option chain. No network and no clock reads."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from sidecar.flow.config import (
    ALERT_SCORE,
    ALERT_SCORE_WITHOUT_Z,
    BASELINE_MIN,
    BASELINE_WINDOW,
    MAX_DTE,
    MAX_PER_UNDERLYING,
    MIN_OI,
    MIN_VOLUME,
    NEAR_DTE_MAX,
    OI_MAX_AGE_DAYS,
    OTM_PCT,
    PERCENTILE_FLOOR,
    PERCENTILE_MIN_N,
    PREMIUM_MIN,
    SKEW_MIN_SIDE,
    SKEW_SHIFT_MIN,
    SWEEP_MIN_PRINTS,
    SWEEP_SECONDS,
    TOP_N,
    VOL_OI_CAP,
    Z_CAP,
    Z_MIN,
    FlowError,
    resolve_feed,
)

VOLUME_Z_NOTE = (
    "Volume z is unavailable until that contract has 20 prior sessions. "
    "While z is unavailable the alert bar is "
    f"{ALERT_SCORE_WITHOUT_Z:g} instead of {ALERT_SCORE:g}, so a vol/OI percentile "
    "or a three-point print combination is not an alert by itself."
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


def _sold(price: float, bid: float | None) -> bool:
    quoted = _num(bid)
    return quoted is not None and quoted > 0 and price <= quoted


def dte_bucket(dte: int) -> str:
    if dte <= NEAR_DTE_MAX:
        return "0-2"
    if dte <= MAX_DTE:
        return "3-14"
    if dte <= 45:
        return "15-45"
    if dte <= 180:
        return "46-180"
    return "181+"


def empirical_percentile(values: list[float], target: float) -> float | None:
    """Share of the bucket strictly below `target`. Ties do not all rank at the top.

    A bucket smaller than PERCENTILE_MIN_N has no percentile. One contract cannot
    be unusual relative to itself.
    """
    if len(values) < PERCENTILE_MIN_N:
        return None
    below = sum(1 for item in values if item < target)
    return below / (len(values) - 1)


def _vol_oi_points(percentile: float | None) -> float:
    if percentile is None or percentile < PERCENTILE_FLOOR:
        return 0.0
    if percentile >= 0.99:
        return VOL_OI_CAP
    if percentile >= 0.97:
        return 2.0
    return 1.0


def _premium_points(percentile: float | None) -> float:
    if percentile is None or percentile < PERCENTILE_FLOOR:
        return 0.0
    return 1.0


def flow_direction(right: str, aggressor: str) -> str:
    """Call bought or put sold is bullish. Put bought or call sold is bearish.

    Unknown aggressor follows the contract right and is labeled as such.
    """
    if right == "call" and aggressor == "sell":
        return "bearish"
    if right == "put" and aggressor == "buy":
        return "bearish"
    if right == "put" and aggressor == "sell":
        return "bullish"
    if right == "call":
        return "bullish"
    if right == "put":
        return "bearish"
    return "unknown"


def print_flags(contract: ContractView) -> dict:
    premiums: list[float] = []
    aggressive_premiums: list[float] = []
    sell_premiums: list[float] = []
    aggressive: list[OptionPrint] = []
    best: tuple[float, str] | None = None
    total = 0.0
    for item in contract.prints:
        premium = _premium(item.price, item.size)
        if premium is None:
            continue
        premiums.append(premium)
        total += premium
        side = "unknown"
        if _aggressive(item.price, item.ask):
            side = "buy"
            aggressive.append(item)
            aggressive_premiums.append(premium)
        elif _sold(item.price, contract.bid):
            side = "sell"
            sell_premiums.append(premium)
        if best is None or premium > best[0]:
            best = (premium, side)
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
        cluster_total = 0.0
        for item in cluster:
            premium = _premium(item.price, item.size)
            if premium is not None:
                cluster_total += premium
        if cluster_total >= PREMIUM_MIN:
            sweep = True
            break
    return {
        "max_premium": None if not premiums else max(premiums),
        "premium_total": None if not premiums else total,
        "large_premium": large,
        "aggressive_buy": aggressive_large,
        "aggressive_sell": bool(sell_premiums) and max(sell_premiums) >= PREMIUM_MIN,
        "sweep": sweep,
        "aggressor": "unknown" if best is None else best[1],
    }


def short_dated_otm(contract: ContractView) -> bool:
    dte = (contract.expiration - contract.session_date).days
    # 0–2 DTE is where volume over open interest is ordinary. It does not score.
    if dte <= NEAR_DTE_MAX or dte > MAX_DTE:
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


def _eligible_ratio(contract: ContractView, dte: int, volume: float | None, interest: float | None) -> float | None:
    if dte <= NEAR_DTE_MAX or volume is None or volume < MIN_VOLUME:
        return None
    if interest is None or interest < MIN_OI:
        return None
    return volume / interest


def select_top_alerts(rows: list[dict], *, limit: int = TOP_N, per_underlying: int = MAX_PER_UNDERLYING) -> list[dict]:
    """Highest scores first, at most `per_underlying` names from each ticker."""
    ranked = sorted(
        (row for row in rows if row["alert"]),
        key=lambda row: (-(row["score"] or 0.0), -(row["premium"] or 0.0), row["contract"]),
    )
    chosen: list[dict] = []
    counts: dict[str, int] = {}
    for row in ranked:
        name = row["ticker"]
        if counts.get(name, 0) >= per_underlying:
            continue
        chosen.append(row)
        counts[name] = counts.get(name, 0) + 1
        if len(chosen) >= limit:
            break
    return chosen


def build_clusters(rows: list[dict], *, limit: int = TOP_N) -> list[dict]:
    """Sum premium for one underlying, expiration, and direction."""
    groups: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        premium = row.get("premium")
        if premium is None or premium <= 0 or not row.get("direction"):
            continue
        key = (row["ticker"], row["expiration"], row["direction"])
        bucket = groups.get(key)
        if bucket is None:
            bucket = {
                "ticker": row["ticker"],
                "expiration": row["expiration"],
                "direction": row["direction"],
                "contracts": 0,
                "alert_count": 0,
                "premium": 0.0,
                "max_score": 0.0,
            }
            groups[key] = bucket
        bucket["contracts"] += 1
        bucket["premium"] += premium
        bucket["max_score"] = max(bucket["max_score"], row["score"] or 0.0)
        if row["alert"]:
            bucket["alert_count"] += 1
    clusters = list(groups.values())
    for bucket in clusters:
        bucket["premium"] = _fmt(bucket["premium"])
        bucket["max_score"] = _fmt(bucket["max_score"])
    clusters.sort(key=lambda item: (-(item["premium"] or 0.0), item["ticker"], item["expiration"], item["direction"]))
    return clusters[:limit]


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

    prepared = []
    ratio_peers: dict[tuple[str, str], list[float]] = {}
    premium_peers: dict[tuple[str, str], list[float]] = {}
    for contract in contracts:
        dte = (contract.expiration - contract.session_date).days
        volume = _num(contract.volume)
        interest = usable_open_interest(contract)
        ratio = None
        if volume is not None and interest is not None and interest > 0:
            ratio = volume / interest
        prints = print_flags(contract)
        key = (contract.underlying, dte_bucket(dte))
        eligible = _eligible_ratio(contract, dte, volume, interest)
        if eligible is not None:
            ratio_peers.setdefault(key, []).append(eligible)
        if prints["max_premium"] is not None:
            premium_peers.setdefault(key, []).append(prints["max_premium"])
        prepared.append((contract, dte, volume, interest, ratio, prints, key, eligible))

    scored: list[dict] = []
    published: list[dict] = []
    new_history: list[dict] = []
    for contract, dte, volume, interest, ratio, prints, key, eligible in prepared:
        prior = _history_volumes(history, contract.symbol, session_date)
        z_value = None if volume is None else trailing_z(prior, volume)
        z_status = "ok" if z_value is not None else "unavailable"
        otm = short_dated_otm(contract)
        shift = skew_shift.get(contract.underlying)
        vol_pct = None if eligible is None else empirical_percentile(ratio_peers[key], eligible)
        prem_pct = (
            None
            if prints["max_premium"] is None
            else empirical_percentile(premium_peers[key], prints["max_premium"])
        )
        z_flag = z_value is not None and z_value >= Z_MIN and volume is not None and volume >= MIN_VOLUME
        skew_flag = bool(otm and shift is not None and shift >= SKEW_SHIFT_MIN)
        parts = {
            "vol_oi": _vol_oi_points(vol_pct),
            "volume_z": min(Z_CAP, z_value) if z_flag and z_value is not None else 0.0,
            "premium": _premium_points(prem_pct),
            "aggressive": 1.0 if prints["aggressive_buy"] else 0.0,
            "sweep": 1.0 if prints["sweep"] else 0.0,
            "short_dated_otm": 1.0 if otm else 0.0,
            "skew_shift": 1.0 if skew_flag else 0.0,
        }
        score = sum(parts.values())
        bar = ALERT_SCORE if z_status == "ok" else ALERT_SCORE_WITHOUT_Z
        flags = [name for name, value in parts.items() if value > 0]
        direction = flow_direction(contract.right, prints["aggressor"])
        premium_value = prints["premium_total"]
        alert = score >= bar
        row = {
            "underlying": contract.underlying,
            "symbol": contract.symbol,
            "right": contract.right,
            "strike": contract.strike,
            "expiration": contract.expiration.isoformat(),
            "dte": dte,
            "dte_bucket": key[1],
            "volume": _fmt(volume),
            "open_interest": _fmt(interest),
            "vol_oi": _fmt(ratio),
            "vol_oi_percentile": _fmt(vol_pct),
            "volume_z": _fmt(z_value),
            "volume_z_status": z_status,
            "max_premium": _fmt(prints["max_premium"]),
            "premium_total": _fmt(premium_value),
            "premium_percentile": _fmt(prem_pct),
            "large_premium": prints["large_premium"],
            "aggressive_buy": prints["aggressive_buy"],
            "sweep": prints["sweep"],
            "aggressor": prints["aggressor"],
            "direction": direction,
            "short_dated_otm": otm,
            "skew": _fmt(skew_now.get(contract.underlying)),
            "skew_shift": _fmt(shift),
            "components": {key_name: _fmt(value) for key_name, value in parts.items()},
            "flags": flags,
            "score": _fmt(score),
            "alert_bar": bar,
            "alert": alert,
        }
        scored.append(row)
        published.append(
            {
                "ticker": contract.underlying,
                "contract": contract.symbol,
                "direction": direction,
                "aggressor": prints["aggressor"],
                "premium": _fmt(premium_value),
                "flags": flags,
                "score": _fmt(score),
                "alert": alert,
                "expiration": contract.expiration.isoformat(),
                "right": contract.right,
                "strike": contract.strike,
                "dte": dte,
                "volume_z_status": z_status,
                "alert_bar": bar,
            }
        )
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
    top_alerts = select_top_alerts(published)
    raw_alert_count = sum(1 for row in scored if row["alert"])
    return {
        "schema_version": "sidecar.flow.scan.v1",
        "orders_submitted": 0,
        "live_trading": False,
        "feed": feed,
        "session_date": session_date.isoformat(),
        "alert_score": ALERT_SCORE,
        "alert_score_without_z": ALERT_SCORE_WITHOUT_Z,
        "volume_z_note": VOLUME_Z_NOTE,
        "scanned_contracts": len(scored),
        "raw_alert_count": raw_alert_count,
        "top_n": TOP_N,
        "max_per_underlying": MAX_PER_UNDERLYING,
        "contracts": scored,
        "top_alerts": top_alerts,
        "clusters": build_clusters(published),
        "underlyings": underlying_rows,
        "history_rows": new_history,
    }


def top_alerts_payload(scan: dict) -> dict:
    """Slim file for an external check. It does not contain the whole chain."""
    return {
        "schema_version": "sidecar.flow.top_alerts.v1",
        "session_date": scan["session_date"],
        "feed": scan.get("feed"),
        "tape": scan.get("tape"),
        "orders_submitted": 0,
        "live_trading": False,
        "scanned_contracts": scan.get("scanned_contracts"),
        "raw_alert_count": scan.get("raw_alert_count"),
        "top_n": scan.get("top_n", TOP_N),
        "max_per_underlying": scan.get("max_per_underlying", MAX_PER_UNDERLYING),
        "alert_score": scan.get("alert_score"),
        "alert_score_without_z": scan.get("alert_score_without_z"),
        "volume_z_note": scan.get("volume_z_note"),
        "alerts": scan.get("top_alerts") or [],
        "clusters": scan.get("clusters") or [],
    }


def render_markdown(scan: dict) -> str:
    lines = [
        "# Unusual options alerts",
        "",
        f"Feed: {scan['feed']}. Session: {scan['session_date']}. Orders submitted: {scan['orders_submitted']}.",
        "",
    ]
    if scan.get("session_note"):
        lines.append(str(scan["session_note"]))
        lines.append("")
    if scan.get("tape") == "snapshot_only":
        lines.append(str(scan.get("tape_reason") or "Tape unavailable. Snapshot latest trades only."))
        lines.append("")
    note = scan.get("volume_z_note")
    if note:
        lines.append(str(note))
        lines.append("")
    raw = scan.get("raw_alert_count")
    scanned = scan.get("scanned_contracts")
    lines.append(
        f"Raw alerts: {raw} of {scanned}. "
        f"The table is the top {scan.get('top_n', TOP_N)}, "
        f"at most {scan.get('max_per_underlying', MAX_PER_UNDERLYING)} per underlying. "
        f"The alert bar is {scan.get('alert_score')} when volume z exists and "
        f"{scan.get('alert_score_without_z')} when it does not."
    )
    lines.extend(
        [
            "",
            "| Rank | Ticker | Contract | Direction | Score | Flags | Premium | Aggressor |",
            "|---:|---|---|---|---:|---|---:|---|",
        ]
    )
    alerts = scan.get("top_alerts") or []
    if not alerts:
        lines.append("| | | none | | | | | |")
    for index, row in enumerate(alerts, start=1):
        lines.append(
            "| {rank} | {ticker} | {contract} | {direction} | {score} | {flags} | {premium} | {aggressor} |".format(
                rank=index,
                ticker=row["ticker"],
                contract=row["contract"],
                direction=row["direction"],
                score=row["score"],
                flags=",".join(row["flags"]),
                premium="" if row["premium"] is None else row["premium"],
                aggressor=row["aggressor"],
            )
        )
    lines.extend(
        [
            "",
            "## Clusters",
            "",
            "Same underlying, expiration, and direction. Premium is the sum of print premium.",
            "",
            "| Ticker | Expiration | Direction | Contracts | Alerts | Premium | Max score |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    clusters = scan.get("clusters") or []
    if not clusters:
        lines.append("| | | none | | | | |")
    for row in clusters:
        lines.append(
            "| {ticker} | {expiration} | {direction} | {contracts} | {alerts} | {premium} | {score} |".format(
                ticker=row["ticker"],
                expiration=row["expiration"],
                direction=row["direction"],
                contracts=row["contracts"],
                alerts=row["alert_count"],
                premium=row["premium"],
                score=row["max_score"],
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
