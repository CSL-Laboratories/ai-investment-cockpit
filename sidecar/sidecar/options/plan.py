"""Turn a chain snapshot into credit-spread intents. No network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sidecar.options.config import (
    ALLOWLIST,
    CLOSE_DTE,
    DELTA,
    DELTA_MAX,
    DELTA_MIN,
    IV_RANK_MIN,
    IV_RV_MIN,
    MAX_QUOTE_SPREAD_FLOOR,
    MAX_QUOTE_SPREAD_FRAC,
    MIN_CREDIT,
    QTY,
    QUOTE_MAX_AGE_SECONDS,
    TARGET_DTE,
    WIDTH,
)
from sidecar.options.risk import (
    Leg,
    OptionsRiskError,
    admit_spread,
    assert_entry_dte,
    max_loss_dollars,
)
from sidecar.options.structures import client_order_id, closing_order, opening_order, parse_occ


@dataclass(frozen=True)
class ContractQuote:
    symbol: str
    underlying: str
    expiry: date
    right: str
    strike: Decimal
    delta: Decimal
    iv: Decimal
    bid: Decimal
    ask: Decimal
    quote_time: datetime


@dataclass
class Plan:
    opens: list[dict]
    closes: list[dict]
    state_upserts: list[dict]
    state_closes: list[str]
    refusals: list[str]
    unknown_short: bool


def _dec(value) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    if not number.is_finite():
        return None
    return number


def quote_from_parts(
    *,
    symbol: str,
    underlying: str,
    expiry: date,
    right: str,
    strike: Decimal,
    delta: Decimal,
    iv: Decimal,
    bid: Decimal,
    ask: Decimal,
    quote_time: datetime,
    now: datetime,
) -> ContractQuote | None:
    """Return a quote only when greeks, IV, and a fresh two-sided market are all present."""
    if underlying not in ALLOWLIST or right not in {"call", "put"}:
        return None
    if quote_time.tzinfo is None or now.tzinfo is None:
        return None
    if not all(number.is_finite() for number in (strike, delta, iv, bid, ask)):
        return None
    age = (now - quote_time).total_seconds()
    if age < 0 or age > QUOTE_MAX_AGE_SECONDS:
        return None
    if bid <= 0 or ask <= bid or iv <= 0:
        return None
    spread = ask - bid
    mid = (bid + ask) / 2
    cap = max(MAX_QUOTE_SPREAD_FLOOR, mid * MAX_QUOTE_SPREAD_FRAC)
    if spread > cap:
        return None
    if right == "put" and (delta > 0 or delta < -1):
        return None
    if right == "call" and (delta < 0 or delta > 1):
        return None
    return ContractQuote(
        symbol=symbol,
        underlying=underlying,
        expiry=expiry,
        right=right,
        strike=strike,
        delta=delta,
        iv=iv,
        bid=bid,
        ask=ask,
        quote_time=quote_time,
    )


def _closest_expiry(quotes: list[ContractQuote], today: date) -> date | None:
    ranked: list[tuple[int, date]] = []
    for expiry in {row.expiry for row in quotes}:
        dte = (expiry - today).days
        try:
            assert_entry_dte(dte)
        except OptionsRiskError:
            continue
        ranked.append((abs(dte - TARGET_DTE), expiry))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1].isoformat()))
    return ranked[0][1]


def _pick_short(quotes: list[ContractQuote], *, right: str, target: Decimal) -> ContractQuote | None:
    signed = -target if right == "put" else target
    band_low = -DELTA_MAX if right == "put" else DELTA_MIN
    band_high = -DELTA_MIN if right == "put" else DELTA_MAX
    pool = [row for row in quotes if row.right == right and band_low <= row.delta <= band_high]
    if not pool:
        return None
    pool.sort(key=lambda row: (abs(row.delta - signed), row.strike))
    return pool[0]


def _find_strike(quotes: list[ContractQuote], *, right: str, expiry: date, strike: Decimal) -> ContractQuote | None:
    matches = [
        row
        for row in quotes
        if row.right == right and row.expiry == expiry and row.strike == strike
    ]
    return matches[0] if matches else None


def _credit(short: ContractQuote, long: ContractQuote) -> Decimal:
    return short.bid - long.ask


def build_structure(
    quotes: list[ContractQuote],
    *,
    today: date,
    kind: str,
    open_losses: list[Decimal],
    kill_file,
    session_day: str,
) -> tuple[dict | None, dict | None, str | None]:
    expiry = _closest_expiry(quotes, today)
    if expiry is None:
        return None, None, "dte"
    underlying = quotes[0].underlying
    short_put = _pick_short([row for row in quotes if row.expiry == expiry], right="put", target=DELTA)
    if short_put is None:
        return None, None, "delta"
    long_put = _find_strike(quotes, right="put", expiry=expiry, strike=short_put.strike - WIDTH)
    if long_put is None:
        return None, None, "wing"
    legs = [
        Leg(underlying, expiry, "put", short_put.strike, "sell"),
        Leg(underlying, expiry, "put", long_put.strike, "buy"),
    ]
    symbols = [short_put.symbol, long_put.symbol]
    credit = _credit(short_put, long_put)
    if kind == "iron_condor":
        short_call = _pick_short([row for row in quotes if row.expiry == expiry], right="call", target=DELTA)
        if short_call is None:
            return None, None, "delta"
        long_call = _find_strike(
            quotes, right="call", expiry=expiry, strike=short_call.strike + WIDTH
        )
        if long_call is None:
            return None, None, "wing"
        legs.extend(
            [
                Leg(underlying, expiry, "call", short_call.strike, "sell"),
                Leg(underlying, expiry, "call", long_call.strike, "buy"),
            ]
        )
        symbols.extend([short_call.symbol, long_call.symbol])
        credit += _credit(short_call, long_call)
    elif kind != "put_credit":
        return None, None, "kind"
    if credit <= MIN_CREDIT:
        return None, None, "credit"
    try:
        loss = max_loss_dollars(legs, credit, QTY)
        admit_spread(loss, open_losses, kill_file)
        assert_entry_dte((expiry - today).days)
    except OptionsRiskError as exc:
        return None, None, exc.reason
    order_id = client_order_id(
        day=session_day, underlying=underlying, kind=kind, action="open", symbols=symbols
    )
    body = opening_order(legs, symbols, credit, QTY, order_id)
    state = {
        "underlying": underlying,
        "kind": kind,
        "expiry": expiry.isoformat(),
        "opened_on": session_day,
        "credit_per_share": format(credit, "f"),
        "max_loss": format(loss, "f"),
        "qty": QTY,
        "status": "open",
        "open_client_order_id": order_id,
        "legs": [
            {
                "symbol": symbol,
                "side": leg.side,
                "right": leg.right,
                "strike": format(leg.strike, "f"),
            }
            for leg, symbol in zip(legs, symbols, strict=True)
        ],
    }
    return body, state, None


def _state_legs(row: dict) -> tuple[list[Leg], list[str]]:
    underlying = str(row["underlying"])
    expiry = date.fromisoformat(str(row["expiry"]))
    legs: list[Leg] = []
    symbols: list[str] = []
    for leg in row["legs"]:
        strike = Decimal(str(leg["strike"]))
        right = str(leg["right"])
        side = str(leg["side"])
        symbol = str(leg["symbol"])
        legs.append(Leg(underlying, expiry, right, strike, side))
        symbols.append(symbol)
    return legs, symbols


def close_decision(
    row: dict,
    quotes: dict[str, ContractQuote],
    *,
    today: date,
    session_day: str,
) -> tuple[dict | None, str | None]:
    """Close on the executable debit. Missing quotes refuse the close."""
    if str(row.get("opened_on")) >= session_day:
        # Same-session take-profit and stop are not armed. Expiry still is.
        same_session = True
    else:
        same_session = False
    expiry = date.fromisoformat(str(row["expiry"]))
    dte = (expiry - today).days
    legs, symbols = _state_legs(row)
    priced: list[ContractQuote] = []
    for symbol in symbols:
        quote = quotes.get(symbol)
        if quote is None:
            return None, "unpriced_close"
        priced.append(quote)
    by_symbol = {quote.symbol: quote for quote in priced}
    debit = Decimal("0")
    for leg, symbol in zip(legs, symbols, strict=True):
        quote = by_symbol[symbol]
        if leg.side == "sell":
            debit += quote.ask
        else:
            debit -= quote.bid
    credit = Decimal(str(row["credit_per_share"]))
    reason = None
    if dte <= CLOSE_DTE:
        reason = "dte"
    elif not same_session and debit <= credit * Decimal("0.50"):
        reason = "take_profit"
    elif not same_session and debit >= credit * Decimal("2"):
        reason = "stop"
    if reason is None:
        return None, None
    if debit <= 0:
        return None, "unpriced_close"
    order_id = client_order_id(
        day=session_day,
        underlying=str(row["underlying"]),
        kind=str(row["kind"]),
        action="close",
        symbols=symbols,
    )
    try:
        body = closing_order(legs, symbols, debit, int(row.get("qty", 1)), order_id)
    except OptionsRiskError as exc:
        return None, exc.reason
    body["close_reason"] = reason
    return body, reason


def short_symbols(state: list[dict]) -> set[str]:
    found = set()
    for row in state:
        for leg in row.get("legs", []):
            if str(leg.get("side")) == "sell":
                found.add(str(leg.get("symbol")))
    return found


def unknown_option_positions(state: list[dict], positions: list[dict]) -> list[str]:
    known = {str(leg.get("symbol")) for row in state for leg in row.get("legs", [])}
    unknown = []
    for row in positions:
        symbol = str(row.get("symbol", ""))
        if parse_occ(symbol) is None:
            continue
        qty = _dec(row.get("qty", "0"))
        if qty is None or qty == 0:
            continue
        if symbol not in known:
            unknown.append(symbol)
    return unknown


def plan_actions(
    *,
    today: date,
    session_day: str,
    views: dict[str, list[ContractQuote]],
    signals: dict[str, bool],
    state: list[dict],
    positions: list[dict],
    kill_file,
    kind: str,
    options_approved: bool,
) -> Plan:
    refusals: list[str] = []
    unknown = unknown_option_positions(state, positions)
    opens: list[dict] = []
    closes: list[dict] = []
    upserts: list[dict] = []
    closed_ids: list[str] = []
    quote_index = {quote.symbol: quote for rows in views.values() for quote in rows}
    for row in state:
        body, reason = close_decision(row, quote_index, today=today, session_day=session_day)
        if body is None:
            if reason:
                refusals.append(f"close:{row.get('underlying')}:{reason}")
            continue
        closes.append(body)
        closed_ids.append(str(row.get("open_client_order_id", "")))
    if unknown:
        refusals.append("unknown_option_position")
    if not options_approved:
        refusals.append("options_level")
    if kill_file is not None and kill_file.exists():
        refusals.append("kill_switch")
    blocked = bool(unknown) or not options_approved or (kill_file is not None and kill_file.exists())
    open_losses = []
    held = set()
    for row in state:
        if str(row.get("open_client_order_id", "")) in closed_ids:
            continue
        open_losses.append(Decimal(str(row["max_loss"])))
        held.add(str(row["underlying"]))
    if not blocked:
        for underlying in ALLOWLIST:
            if underlying in held:
                continue
            if not signals.get(underlying, False):
                refusals.append(f"signal:{underlying}")
                continue
            quotes = views.get(underlying) or []
            if not quotes:
                refusals.append(f"chain:{underlying}")
                continue
            body, state_row, reason = build_structure(
                quotes,
                today=today,
                kind=kind,
                open_losses=open_losses,
                kill_file=kill_file,
                session_day=session_day,
            )
            if body is None or state_row is None:
                refusals.append(f"open:{underlying}:{reason}")
                continue
            opens.append(body)
            upserts.append(state_row)
            open_losses.append(Decimal(state_row["max_loss"]))
            held.add(underlying)
    if blocked:
        opens = []
        upserts = []
        if kill_file is not None and kill_file.exists():
            closes = []
    return Plan(
        opens=opens,
        closes=closes,
        state_upserts=upserts,
        state_closes=closed_ids,
        refusals=refusals,
        unknown_short=bool(unknown),
    )


def signal_ok(*, iv: Decimal | None, iv_rank: Decimal | None, realized: Decimal | None) -> bool:
    if iv is None or iv_rank is None or realized is None:
        return False
    if iv_rank < IV_RANK_MIN:
        return False
    if iv - realized <= IV_RV_MIN:
        return False
    return True
