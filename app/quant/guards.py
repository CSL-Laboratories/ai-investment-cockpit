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
from typing import Callable

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

    max_loss: float | None = None
    """Session stop, in ticks*shares. None disables it. When breached the kill switch trips
    and stays tripped — it does not re-arm on a recovery, because a strategy that lost its
    limit and then recovered has still demonstrated it can lose its limit."""


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

        # A REPLACE IS A NEW ORDER FOR LIMIT PURPOSES, AND MUST BE CHECKED LIKE ONE.
        #
        # This previously returned ok() unconditionally, which was a total bypass of every
        # size limit in this class: send a 1-share order (passes), then replace it to
        # 1,000,000 shares (unchecked). max_order_qty, max_position and the exposure cap all
        # fell to a two-step sequence, in the ONE tier that has no human in the loop.
        #
        # The replace carries no symbol or side of its own, so the resting order is looked up
        # to recover them. Without an OMS to look it up in, the replace is REFUSED rather than
        # waved through — an unverifiable size change is exactly what the bypass looked like.
        if isinstance(intent, ReplaceOrder):
            if oms is None:
                return self._blocked(
                    "cannot size-check a replace without an OMS to resolve the resting order"
                )
            existing = oms.get(intent.client_id)
            if existing is None:
                return self._blocked(f"replace names unknown order {intent.client_id!r}")
            return self._check_size(
                symbol=existing.symbol, side=existing.side, qty=intent.new_qty,
                price_ticks=intent.new_price_ticks, oms=oms, ts=ts,
                # The order being replaced is still live and already counted in exposure; its
                # remaining size is released by the replace, so netting it out avoids blocking
                # a same-size reprice for exposure the strategy is not actually adding.
                releasing=existing,
            )

        if not isinstance(intent, NewOrder):
            return self._blocked(f"unknown intent type {type(intent).__name__}")

        return self._check_size(symbol=intent.symbol, side=intent.side, qty=intent.qty,
                                price_ticks=intent.price_ticks, oms=oms, ts=ts)

    def _check_size(self, *, symbol, side, qty: int, price_ticks: int | None,
                    oms, ts: int, releasing=None) -> GuardVerdict:
        """The size limits, shared by NewOrder and ReplaceOrder so they cannot drift apart."""
        limits = self.limits
        if qty > limits.max_order_qty:
            return self._blocked(
                f"order qty {qty} exceeds max_order_qty {limits.max_order_qty}"
            )

        if oms is not None:
            position = oms.position(symbol)
            projected = position + side.sign * qty
            if abs(projected) > limits.max_position:
                return self._blocked(
                    f"projected position {projected} exceeds max_position {limits.max_position}"
                )

            released = 0
            if releasing is not None:
                released = abs((releasing.price_ticks or 0) * releasing.remaining_qty)
            exposure = oms.exposure_ticks() - released + abs((price_ticks or 0) * qty)
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

    def observe_pnl(self, total: float) -> None:
        """Feed session PnL so the loss stop can latch. Called by the loop, not the strategy."""
        if self.limits.max_loss is None:
            return
        if total <= -abs(self.limits.max_loss):
            self.trip(f"session loss {total} breached max_loss {self.limits.max_loss}")

    def _blocked(self, reason: str) -> GuardVerdict:
        self.blocked += 1
        return GuardVerdict.block(reason)
