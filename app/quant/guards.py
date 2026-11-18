"""QT-07 — pre-trade limits and the local kill switch for the autonomous loop.

WHY NOT ``quant/risk.py``
--------------------------
Top-level ``risk`` is the cockpit's deterministic order-path gate: HMAC-anchored, property-
tested, guarded by a CI purity gate, and the thing standing between a model and a broker. A
second module named ``risk`` inside this package would read as the same thing to anyone
scanning the tree, and it is emphatically not — it has no authority over any real order and
never will. So it is ``guards.py``, and the difference in name is the difference in power.

WHAT THIS IS FOR
-----------------
Tiers A and B put an execution verdict in front of a HUMAN-APPROVED order. Tier C has no
human in the loop: the autonomous loop places its own orders at market-making frequency, so
every limit it has must be checked mechanically, before each intent, in code that the
strategy cannot reach around. That is this module.

CHECKED ON INTENTS, BEFORE ANYTHING ACTS
------------------------------------------
``Runner._permitted`` calls ``check`` on every intent before it reaches the book. A limit
evaluated after the order is live is a report, not a control — and for an unattended loop
the difference is the entire safety argument.

THE LIMITS ARE ON EXPOSURE INCLUDING IN-FLIGHT ORDERS
------------------------------------------------------
``OMS.exposure_ticks`` counts orders that have not been acknowledged yet, and the guards use
it. A limit that only counts acknowledged orders is a limit you can exceed by sending fast,
which is precisely the capability an autonomous high-frequency loop has and a human does not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable
from core.numeric import finite_decimal, is_int_value

from quant.oms import CancelOrder, NewOrder, ReplaceOrder


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    allowed: bool
    reason: str = ""

    @classmethod
    def ok(cls) -> "GuardVerdict":
        return cls(allowed=True)

    @classmethod
    def block(cls, reason: str) -> "GuardVerdict":
        return cls(allowed=False, reason=reason)


@dataclass(slots=True)
class GuardLimits:
    """Every bound the autonomous loop operates under. All are hard."""

    max_position: int = 5_000
    max_order_qty: int = 2_000
    max_gross_exposure_ticks: int = 50_000_000
    max_orders_per_window: int = 500
    order_rate_window_ns: int = 1_000_000_000
    """A message-rate ceiling. Not a financial limit — a blast-radius one. A strategy stuck
    in a requote loop can emit tens of thousands of orders a second; the exposure caps would
    all still pass, because each individual order is small and none of them rest. This is the
    only limit that catches that shape of failure."""

    max_loss_dollars: float | None = None
    """Session stop in dollars. ``PnLTracker`` converts ticks through ``PriceScale`` before
    this limit is observed. None disables it. When breached the kill switch trips and stays
    tripped: recovery does not erase evidence that the strategy exceeded its loss bound."""


@dataclass(slots=True)
class Guards:
    """Pre-trade limits, a rate ceiling, and a latching kill switch.

    ``halt_fn`` and ``watch_only_fn`` are injected rather than imported so this module stays
    testable without a filesystem or an orchestrator, and so the autonomous loop honours the
    SAME halt file and breaker state as the rest of the cockpit. A separate stop for the
    quant loop would be a second control surface that operators would have to remember.
    """

    limits: GuardLimits = field(default_factory=GuardLimits)
    halt_fn: Callable[[], bool] | None = None
    watch_only_fn: Callable[[], bool] | None = None

    tripped: bool = field(default=False, init=False)
    trip_reason: str | None = field(default=None, init=False)
    blocked: int = field(default=0, init=False)
    _recent_orders: list[int] = field(default_factory=list, init=False, repr=False)

    def trip(self, reason: str) -> None:
        """Latch the kill switch. Once tripped it stays tripped for the session.

        Latching rather than auto-clearing is deliberate: an unattended loop that re-armed
        itself after a breach would re-breach, and the operator would see a flapping counter
        instead of a stop. Re-arming is an explicit ``reset()`` a human calls.
        """
        if not self.tripped:
            self.tripped = True
            self.trip_reason = reason

    def reset(self) -> None:
        self.tripped = False
        self.trip_reason = None
        self._recent_orders.clear()

    def check(self, intent, *, strategy=None, oms=None, books=None, ts: int = 0) -> GuardVerdict:
        """The pre-trade gate. Called on EVERY intent before it reaches the book."""
        # A cancel is always permitted, in EVERY state — including a tripped kill switch and
        # an active HALT. This check comes FIRST for that reason: blocking cancels would
        # strand live orders at exactly the moment the system decided something was wrong,
        # which is the one moment you most want the ability to withdraw. A kill switch that
        # also freezes your outstanding risk is not a kill switch.
        if isinstance(intent, CancelOrder):
            return GuardVerdict.ok()

        if self.tripped:
            return self._blocked(f"kill switch tripped: {self.trip_reason}")

        if self.halt_fn is not None and self.halt_fn():
            return self._blocked("HALT is set")
        if self.watch_only_fn is not None and self.watch_only_fn():
            return self._blocked("system is watch_only")

        # NF/Q11: a NaN or an infinity compares False against every bound below and bool is
        # an int, so a malformed size, price, limit or clock passed each comparison and
        # reached the book (a resting NaN order then made exposure_ticks() NaN, disabling
        # the position and gross caps for every later intent). Refused before any compare.
        malformed = self._malformed(intent, ts)
        if malformed is not None:
            return self._blocked(malformed)

        # A replace is NOT waved through. It used to be, and that made every limit below
        # bypassable: an intent blocked as a NewOrder was allowed as a resize of a one-lot.
        # Worst of all it exempted the requote loop that ``order_rate_window_ns`` documents
        # itself as "the only limit that catches" — because a requote IS a ReplaceOrder.
        if isinstance(intent, ReplaceOrder):
            return self._check_replace(intent, oms=oms, ts=ts)

        if not isinstance(intent, NewOrder):
            return self._blocked(f"unknown intent type {type(intent).__name__}")

        limits = self.limits
        if intent.qty <= 0:
            # BUG-089: the gate only had an UPPER bound, so a zero/negative-qty intent
            # passed and killed the whole run downstream on AggressiveOrder's ValueError —
            # the one class of intent a pre-trade gate exists to intercept.
            return self._blocked(f"order qty {intent.qty} must be positive")
        if intent.price_ticks is None:
            # BUG-074: an unpriced (market) intent contributes exactly 0 to every
            # tick-denominated exposure number (``abs((price or 0) * qty)`` here and in
            # ``OMS.exposure_ticks``), so the gross cap — whose own docstring says a limit
            # that undercounts is "a limit you can exceed by sending fast" — measured
            # nothing for the market-order path. Until exposure can be marked against a
            # reference price, refuse at the gate: fail closed beats a bypassable cap.
            return self._blocked(
                "market order (price_ticks=None): no price for exposure accounting"
            )
        if intent.qty > limits.max_order_qty:
            return self._blocked(
                f"order qty {intent.qty} exceeds max_order_qty {limits.max_order_qty}"
            )

        if oms is not None:
            # BUG-091: project against the WORST-CASE fill — realized position plus every
            # live same-side order's remaining size. Position alone let N same-side orders
            # each pass individually while their sum blew through the cap; the gross check
            # counted in-flight, the position check did not.
            position = oms.position(intent.symbol)
            open_same_side = oms.open_side_qty(intent.symbol, intent.side)
            oms_exposure = oms.exposure_ticks()
            # Answer to the first reads: these three come from the OMS, not from the intent, so
            # the checks above never saw them. A NaN here compares False and disables the cap.
            if not (is_int_value(position) and is_int_value(open_same_side)
                    and is_int_value(oms_exposure)):
                return self._blocked(_oms_unreadable(
                    position=position, open_side_qty=open_same_side,
                    exposure_ticks=oms_exposure))
            projected = position + intent.side.sign * (open_same_side + intent.qty)
            if abs(projected) > limits.max_position:
                return self._blocked(
                    f"projected position {projected} (incl. {open_same_side} in-flight "
                    f"same-side) exceeds max_position {limits.max_position}"
                )

            exposure = oms_exposure + abs(intent.price_ticks * intent.qty)
            if exposure > limits.max_gross_exposure_ticks:
                return self._blocked(
                    f"gross exposure {exposure} exceeds "
                    f"{limits.max_gross_exposure_ticks} (in-flight orders included)"
                )

        if not self._rate_ok(ts):
            return self._blocked(
                f"order rate exceeded {limits.max_orders_per_window} per "
                f"{limits.order_rate_window_ns}ns"
            )
        return GuardVerdict.ok()

    def _check_replace(self, intent: ReplaceOrder, *, oms, ts: int) -> GuardVerdict:
        """The same four limits a NewOrder faces, measured against the order's NEW terms.

        Two things differ from the NewOrder path, both because the order already exists:

        * an omitted ``new_qty``/``new_price_ticks`` means "keep it", so the existing value is
          what gets checked — not zero, which would make a pure reprice look free; and
        * the order is ALREADY counted in ``exposure_ticks()``, so its old contribution is
          swapped out rather than added on top. Adding would make an unchanged reprice read as
          a second order and trip the gross cap on a strategy that is not growing its risk.
        """
        limits = self.limits
        if intent.new_qty is not None and intent.new_qty <= 0:
            return self._blocked(f"replace qty {intent.new_qty} must be positive")  # BUG-089
        if intent.new_qty is not None and intent.new_qty > limits.max_order_qty:
            return self._blocked(
                f"replace qty {intent.new_qty} exceeds max_order_qty {limits.max_order_qty}"
            )

        if oms is not None:
            order = oms.get(intent.client_id)
            if order is not None:
                # Answer to the first reads: the order's own terms and the OMS's derived reads
                # are compared below and were never validated; refuse any that is not a whole
                # int (a missing price is None and is handled further down).
                oms_position = oms.position(order.symbol)
                oms_side_qty = oms.open_side_qty(order.symbol, order.side)
                oms_exposure = oms.exposure_ticks()
                if not (is_int_value(order.qty) and is_int_value(order.filled_qty)
                        and (order.price_ticks is None or is_int_value(order.price_ticks))
                        and is_int_value(oms_position) and is_int_value(oms_side_qty)
                        and is_int_value(oms_exposure)):
                    return self._blocked(_oms_unreadable(
                        order_qty=order.qty, order_filled_qty=order.filled_qty,
                        order_price_ticks=(0 if order.price_ticks is None
                                           else order.price_ticks),
                        position=oms_position, open_side_qty=oms_side_qty,
                        exposure_ticks=oms_exposure))
                # BUG-075: ``new_qty`` is a NEW TOTAL (OMS sets ``order.qty = new_qty``)
                # while position is derived from fills — so ``position + sign*new_qty``
                # counted this order's own filled shares TWICE, and a legitimate resize
                # landing exactly on a limit was refused against a number describing
                # nothing. Project the worst case from the REMAINING size after the
                # replace: filled shares live in position, remaining lives here, once each.
                filled = order.qty - order.remaining_qty
                new_total = intent.new_qty if intent.new_qty is not None else order.qty
                remaining_after = max(new_total - filled, 0)
                price = (intent.new_price_ticks if intent.new_price_ticks is not None
                         else order.price_ticks)
                if price is None:
                    return self._blocked(
                        "replace: no price for exposure accounting"      # BUG-074, same hole
                    )

                other_same_side = (oms_side_qty
                                   - order.remaining_qty)                # BUG-091 parity
                projected = (oms_position
                             + order.side.sign * (other_same_side + remaining_after))
                if abs(projected) > limits.max_position:
                    return self._blocked(
                        f"projected position {projected} exceeds max_position "
                        f"{limits.max_position}"
                    )

                current = abs((order.price_ticks or 0) * order.remaining_qty)
                exposure = oms_exposure - current + abs(price * remaining_after)
                if exposure > limits.max_gross_exposure_ticks:
                    return self._blocked(
                        f"gross exposure {exposure} exceeds "
                        f"{limits.max_gross_exposure_ticks} (in-flight orders included)"
                    )

        if not self._rate_ok(ts):
            return self._blocked(
                f"order rate exceeded {limits.max_orders_per_window} per "
                f"{limits.order_rate_window_ns}ns"
            )
        return GuardVerdict.ok()

    def _rate_ok(self, ts: int) -> bool:
        window_start = ts - self.limits.order_rate_window_ns
        self._recent_orders = [t for t in self._recent_orders if t >= window_start]
        if len(self._recent_orders) >= self.limits.max_orders_per_window:
            return False
        self._recent_orders.append(ts)
        return True

    def _malformed(self, intent, ts) -> str | None:
        """Why a limit, ``ts`` or the intent's size/price is not a whole int; else None.

        Every comparison in ``check`` reads False for NaN and treats ``True`` as 1, so a
        value that is not an int is refused here, never compared (NF/Q11).
        """
        limits = self.limits
        if not (is_int_value(limits.max_position) and is_int_value(limits.max_order_qty)
                and is_int_value(limits.max_gross_exposure_ticks)
                and is_int_value(limits.max_orders_per_window)
                and is_int_value(limits.order_rate_window_ns)):
            return "guard limits must be whole ints"
        if not is_int_value(ts):
            return f"intent timestamp {_shown(ts)} is not int nanoseconds"
        if isinstance(intent, NewOrder):
            if not is_int_value(intent.qty):
                return f"order qty {_shown(intent.qty)} is not a whole int"
            price = intent.price_ticks
        elif isinstance(intent, ReplaceOrder):
            if intent.new_qty is not None and not is_int_value(intent.new_qty):
                return f"replace qty {_shown(intent.new_qty)} is not a whole int"
            price = intent.new_price_ticks
        else:
            return None
        if price is not None and not is_int_value(price):
            return f"price_ticks {_shown(price)} is not a whole int"
        return None

    def observe_pnl(self, total: float) -> None:
        """Feed session PnL so the loss stop can latch. Called by the loop, not the strategy."""
        if self.limits.max_loss_dollars is None:
            return
        if not (_readable_number(self.limits.max_loss_dollars) and _readable_number(total)):
            # NF/Q11, the breakers' BUG-130 rule: a loss stop that cannot read its limit or
            # the PnL cannot prove the loss is within bounds, so it latches. Readable means
            # an exact int, float or Decimal the helper finds finite: a str, bytes, bool,
            # int subclass, None, NaN or infinity is never compared (it raised or passed).
            self.trip(
                f"loss stop cannot compare session PnL {_shown(total)} with max_loss_dollars "
                f"{_shown(self.limits.max_loss_dollars)}"
            )
            return
        if total <= -abs(self.limits.max_loss_dollars):
            self.trip(
                f"session loss ${total} breached max_loss_dollars "
                f"${self.limits.max_loss_dollars}"
            )

    def _blocked(self, reason: str) -> GuardVerdict:
        self.blocked += 1
        return GuardVerdict.block(reason)


def _shown(value) -> str:
    """``repr(value)`` for a refusal text; a repr that raises falls back to the type name."""
    try:
        return repr(value)
    except Exception:
        return f"<{type(value).__name__}>"


def _readable_number(value) -> bool:
    """An exact int, float or Decimal that is finite: the only values the loss stop compares."""
    return type(value) in (int, float, Decimal) and finite_decimal(value) is not None


def _oms_unreadable(**reads) -> str:
    """Why a value read from the OMS is not a whole int (the first one that is not).

    Called only after the inline whole-int test failed, so the loop is off the hot path. If
    none is found (cannot happen) it still names the OMS: it fails closed, never a pass.
    """
    for name, value in reads.items():
        if not is_int_value(value):
            return f"OMS {name} {_shown(value)} is not a whole int"
    return "OMS read is not a whole int"
