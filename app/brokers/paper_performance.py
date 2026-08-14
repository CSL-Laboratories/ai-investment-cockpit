"""W-22 audited paper-performance reconstruction.

Daily loss means **intraday equity drawdown**: the positive decline from the first signed
Alpaca equity snapshot of the New York trading day to the latest signed snapshot. It is not
called realized loss because broker equity includes open-position marks.

Consecutive losses are derived from completed FIFO round trips reconstructed from cumulative
fills in the verified audit chain. Mutable ``Order`` rows are deliberately not an authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import select

from orchestrator.state import EVENT_ORDER_FILL_PROGRESS, EVENT_ORDER_TRANSITION
from schemas.models.audit_entry import AuditEntry

EASTERN = ZoneInfo("America/New_York")
EVENT_PORTFOLIO_SNAPSHOT = "portfolio_snapshot"


def _dec(value) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("NaN")
    return result


def _utc_datetime(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class PaperPerformance:
    ok: bool
    trading_day: str
    as_of: datetime | None = None
    day_open_as_of: datetime | None = None
    day_open_equity: Decimal | None = None
    current_equity: Decimal | None = None
    equity_drawdown_today: Decimal | None = None
    consecutive_losses: int | None = None
    closed_round_trips: int = 0
    realized_pnl: Decimal | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "trading_day": self.trading_day,
            "as_of": None if self.as_of is None else self.as_of.isoformat(),
            "day_open_as_of": (
                None if self.day_open_as_of is None else self.day_open_as_of.isoformat()
            ),
            "day_open_equity": (
                None if self.day_open_equity is None else str(self.day_open_equity)
            ),
            "current_equity": (
                None if self.current_equity is None else str(self.current_equity)
            ),
            "equity_drawdown_today": (
                None
                if self.equity_drawdown_today is None
                else str(self.equity_drawdown_today)
            ),
            "consecutive_losses": self.consecutive_losses,
            "closed_round_trips": self.closed_round_trips,
            "realized_pnl": None if self.realized_pnl is None else str(self.realized_pnl),
            "reason": self.reason,
        }


@dataclass
class _Position:
    qty: Decimal = Decimal("0")
    average_price: Decimal = Decimal("0")
    cycle_pnl: Decimal = Decimal("0")


def _trade(position: _Position, signed_qty: Decimal, price: Decimal) -> Decimal | None:
    """Apply one signed fill. Return P&L only when an existing position is fully closed.

    The average-cost representation is equivalent to FIFO for aggregate P&L at the point a
    whole round trip closes. A flip closes the old cycle and opens the remainder as a new one.
    """
    if position.qty == 0 or (position.qty > 0) == (signed_qty > 0):
        total = abs(position.qty) + abs(signed_qty)
        position.average_price = (
            (abs(position.qty) * position.average_price + abs(signed_qty) * price) / total
        )
        position.qty += signed_qty
        return None

    closing = min(abs(position.qty), abs(signed_qty))
    if position.qty > 0:
        position.cycle_pnl += closing * (price - position.average_price)
    else:
        position.cycle_pnl += closing * (position.average_price - price)

    old_qty = position.qty
    new_qty = old_qty + signed_qty
    if new_qty != 0 and (new_qty > 0) == (old_qty > 0):
        position.qty = new_qty
        return None

    closed_pnl = position.cycle_pnl
    position.cycle_pnl = Decimal("0")
    position.qty = new_qty
    position.average_price = price if new_qty != 0 else Decimal("0")
    return closed_pnl


def reconstruct_paper_performance(state, *, now=None) -> PaperPerformance:
    """Reconstruct breaker inputs exclusively from signed, verified observations."""
    now = now or state.clock.now()
    trading_day = now.astimezone(EASTERN).date().isoformat()
    verified = state.audit.verify_chain()
    if not verified.ok:
        return PaperPerformance(False, trading_day, reason="audit chain verification failed")

    with state.session_factory() as session:
        rows = session.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()

    snapshots: list[tuple[datetime, Decimal]] = []
    intents: dict[int, dict] = {}
    intent_for_order: dict[str, dict] = {}
    for row in rows:
        payload = row.payload or {}
        if (row.event_type == EVENT_PORTFOLIO_SNAPSHOT
                and row.actor == "alpaca_paper" and payload.get("kind") == "positions"):
            at = _utc_datetime(payload.get("_ts"))
            equity = _dec(payload.get("equity"))
            if (at is not None and at.astimezone(EASTERN).date().isoformat() == trading_day
                    and equity.is_finite() and equity >= 0):
                snapshots.append((at, equity))
        elif row.event_type == "intent:order_submit":
            order_id = str((row.refs or {}).get("order_id") or "")
            scope = payload.get("execution_scope")
            # Pre-W-22 paper intents can be identified by their audited paper mode. New
            # intents carry the explicit execution scope; live-broker fills never enter paper
            # performance even though they share the generic order audit vocabulary.
            if scope != "paper" and not (scope is None and payload.get("mode") == "paper"):
                continue
            metadata = {
                "order_id": order_id,
                "symbol": str(payload.get("symbol") or "").upper(),
                "side": str(payload.get("side") or "").lower(),
                "qty": _dec(payload.get("qty")),
            }
            intents[row.seq] = metadata
            if order_id:
                intent_for_order[order_id] = metadata

    if not snapshots:
        return PaperPerformance(
            False, trading_day, reason="no signed Alpaca equity snapshot for trading day"
        )
    snapshots.sort(key=lambda item: item[0])
    opened_at, day_open = snapshots[0]
    as_of, current = snapshots[-1]
    drawdown = max(Decimal("0"), day_open - current)

    cumulative: dict[str, tuple[Decimal, Decimal]] = {}
    positions: dict[str, _Position] = {}
    closed: list[tuple[int, Decimal]] = []
    incomplete_fill = False

    for row in rows:
        payload = row.payload or {}
        metadata = None
        order_id = ""
        filled_qty = None
        avg_price = None
        fill_claimed = False

        if row.event_type == "result:order_submit":
            metadata = intents.get((row.refs or {}).get("intent_seq"))
            if metadata is None:
                continue
            order_id = metadata["order_id"]
            filled_qty = payload.get("filled_qty")
            avg_price = payload.get("avg_fill_price")
            fill_claimed = str(payload.get("status") or "").lower() in {
                "partially_filled", "filled",
            }
        elif row.event_type in {EVENT_ORDER_TRANSITION, EVENT_ORDER_FILL_PROGRESS}:
            order_id = str((row.refs or {}).get("row_id") or "")
            metadata = intent_for_order.get(order_id) or {
                "order_id": order_id,
                "symbol": str(payload.get("symbol") or "").upper(),
                "side": str(payload.get("side") or "").lower(),
                "qty": Decimal("NaN"),
            }
            filled_qty = payload.get("filled_qty")
            avg_price = payload.get("avg_fill_price")
            fill_claimed = (
                row.event_type == EVENT_ORDER_FILL_PROGRESS
                or payload.get("to") in {"partially_filled", "filled"}
            )
        else:
            continue

        qty = _dec(filled_qty)
        price = _dec(avg_price)
        if not qty.is_finite() or qty <= 0:
            if fill_claimed:
                incomplete_fill = True
            continue
        if not price.is_finite() or price <= 0 or not order_id:
            incomplete_fill = True
            continue
        symbol = str(metadata.get("symbol") or "").upper()
        side = str(metadata.get("side") or "").lower()
        intended_qty = _dec(metadata.get("qty"))
        if not symbol or side not in {"buy", "sell"}:
            incomplete_fill = True
            continue
        if (not intended_qty.is_finite() or intended_qty <= 0 or qty > intended_qty
                or (str(payload.get("status") or "").lower() == "filled"
                    and qty != intended_qty)):
            return PaperPerformance(False, trading_day, reason="fill exceeds signed order size")

        old_qty, old_avg = cumulative.get(order_id, (Decimal("0"), Decimal("0")))
        if qty < old_qty:
            return PaperPerformance(False, trading_day, reason="cumulative fill quantity regressed")
        if qty == old_qty:
            continue
        # A cumulative average determines the exact incremental fill notional.
        incremental_notional = qty * price - old_qty * old_avg
        delta_qty = qty - old_qty
        incremental_price = incremental_notional / delta_qty
        if incremental_price <= 0 or not incremental_price.is_finite():
            return PaperPerformance(False, trading_day, reason="incremental fill price invalid")
        cumulative[order_id] = (qty, price)
        signed_qty = delta_qty if side == "buy" else -delta_qty
        pnl = _trade(positions.setdefault(symbol, _Position()), signed_qty, incremental_price)
        if pnl is not None:
            closed.append((row.seq, pnl))

    if incomplete_fill:
        return PaperPerformance(False, trading_day, reason="signed fill evidence is incomplete")

    consecutive = 0
    for _seq, pnl in reversed(closed):
        if pnl < 0:
            consecutive += 1
        else:
            break
    realized = sum((pnl for _seq, pnl in closed), Decimal("0"))
    return PaperPerformance(
        True,
        trading_day,
        as_of=as_of,
        day_open_as_of=opened_at,
        day_open_equity=day_open,
        current_equity=current,
        equity_drawdown_today=drawdown,
        consecutive_losses=consecutive,
        closed_round_trips=len(closed),
        realized_pnl=realized,
    )
