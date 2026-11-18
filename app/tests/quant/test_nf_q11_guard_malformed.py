"""NF/Q11: a value that is not a whole int never passes the pre-trade guard.

Red on main 7fb17b6: every comparison in ``Guards.check`` reads False for NaN and treats
``True`` as 1, so a NaN/bool/fractional size, price, limit or clock was allowed.
"""
from __future__ import annotations

import enum

import pytest

from quant.events import Side
from quant.guards import GuardLimits, Guards
from quant.oms import OMS, CancelOrder, NewOrder, OrderState, ReplaceOrder

NAN, INF = float("nan"), float("inf")


def _new(qty=1, price=100, cid="n"):
    return NewOrder(client_id=cid, symbol="S", side=Side.BUY, qty=qty, price_ticks=price)


def _oms_with_live(qty=10, price=100):
    oms = OMS()
    oms.register(_new(qty=qty, price=price, cid="a"), strategy="t")
    oms.on_acknowledged("a")
    return oms


# The sweep's own witness (s6-ck-quant F11), verbatim in substance.
def test_F11_nan_quantity_is_refused_before_registration():
    oms = _oms_with_live(qty=1)
    request = NewOrder(client_id="nan", symbol="S", side=Side.BUY, qty=NAN, price_ticks=100)
    assert not Guards().check(request, oms=oms).allowed


@pytest.mark.parametrize("qty", [NAN, -INF, True, 2.5, "5", 5.0])
@pytest.mark.parametrize("with_oms", [True, False])
def test_malformed_new_qty_is_blocked(qty, with_oms):
    verdict = Guards().check(_new(qty=qty), oms=OMS() if with_oms else None)
    assert not verdict.allowed
    assert verdict.reason


@pytest.mark.parametrize("price", [NAN, INF, True, 100.5, "100"])
def test_malformed_new_price_is_blocked(price):
    assert not Guards().check(_new(qty=10, price=price), oms=OMS()).allowed


@pytest.mark.parametrize("field,value", [("new_qty", NAN), ("new_qty", True), ("new_qty", 2.5),
                                         ("new_price_ticks", NAN), ("new_price_ticks", 99.5),
                                         ("new_price_ticks", True)])
@pytest.mark.parametrize("with_oms", [True, False])
def test_malformed_replace_terms_are_blocked(field, value, with_oms):
    intent = ReplaceOrder(client_id="a", **{field: value})
    verdict = Guards().check(intent, oms=_oms_with_live() if with_oms else None)
    assert not verdict.allowed


@pytest.mark.parametrize("name", ["max_position", "max_order_qty", "max_gross_exposure_ticks",
                                  "max_orders_per_window", "order_rate_window_ns"])
@pytest.mark.parametrize("value", [NAN, INF, True, 10.5])
def test_malformed_limit_blocks_every_new_intent(name, value):
    guards = Guards(limits=GuardLimits(**{name: value}))
    assert not guards.check(_new(), oms=OMS()).allowed
    assert not guards.check(ReplaceOrder(client_id="a", new_price_ticks=101),
                            oms=_oms_with_live()).allowed


def test_nan_rate_window_cannot_lift_the_message_rate_ceiling():
    guards = Guards(limits=GuardLimits(max_orders_per_window=NAN))
    allowed = sum(guards.check(_new(), oms=OMS(), ts=1).allowed for _ in range(600))
    assert allowed == 0          # main: 600/600 (default ceiling is 500)


def test_nan_clock_cannot_reset_the_rate_window():
    guards = Guards(limits=GuardLimits(max_orders_per_window=2))
    allowed = sum(guards.check(_new(), oms=OMS(), ts=NAN).allowed for _ in range(10))
    assert allowed == 0          # main: 10/10 with a ceiling of 2


def test_a_resting_nan_order_cannot_be_placed_to_disable_the_caps():
    """Main: the NaN order is allowed, rests, and exposure_ticks() turns NaN, after which a
    gross-cap breach reads as within the cap. The order must never get in."""
    guards = Guards(limits=GuardLimits(max_gross_exposure_ticks=1_000))
    oms = OMS()
    poison = _new(qty=NAN, cid="p")
    assert not guards.check(poison, oms=oms).allowed
    oms.register(_new(qty=5, cid="ok"), strategy="t")
    assert not guards.check(_new(qty=100, price=100, cid="big"), oms=oms).allowed


@pytest.mark.parametrize("total", [NAN, INF, -INF])
def test_unreadable_pnl_latches_the_loss_stop(total):
    guards = Guards(limits=GuardLimits(max_loss_dollars=100.0))
    guards.observe_pnl(total)
    assert guards.tripped


@pytest.mark.parametrize("limit", [NAN, INF, True])
def test_unreadable_loss_limit_latches_the_loss_stop(limit):
    guards = Guards(limits=GuardLimits(max_loss_dollars=limit))
    guards.observe_pnl(-1.0)
    assert guards.tripped


# ---- controls: what must keep working ----

def test_whole_int_terms_still_pass():
    assert Guards().check(_new(qty=10, price=100), oms=OMS()).allowed
    assert Guards().check(ReplaceOrder(client_id="a", new_qty=12), oms=_oms_with_live()).allowed
    assert Guards().check(ReplaceOrder(client_id="a"), oms=_oms_with_live()).allowed


def test_cancel_is_still_permitted_with_malformed_limits_and_a_tripped_switch():
    guards = Guards(limits=GuardLimits(max_position=NAN))
    guards.trip("breach")
    assert guards.check(CancelOrder(client_id="a"), ts=NAN).allowed


def test_disabled_and_finite_loss_stops_are_unchanged():
    off = Guards()
    off.observe_pnl(NAN)
    assert not off.tripped        # None disables the stop (guards.py:66-69)
    on = Guards(limits=GuardLimits(max_loss_dollars=100.0))
    on.observe_pnl(-50.0)
    assert not on.tripped
    on.observe_pnl(-150.0)
    assert on.tripped and "max_loss_dollars $100.0" in on.trip_reason


# ---- answer to the first reads (DeepSeek and Hy4, head d64f1d0): OMS-derived values,
# ---- unreadable loss-stop inputs, and a malformed value whose repr raises ----


class _Lot(enum.IntEnum):
    ONE = 1


class _BadRepr:
    def __repr__(self):
        raise RuntimeError("repr refuses")


class _SickOms(OMS):
    """An OMS whose derived reads return a chosen value (a stub: what a poisoned book would say)."""

    def __init__(self, **reads):
        super().__init__()
        self._reads = reads

    def position(self, symbol):
        return self._reads.get("position", super().position(symbol))

    def open_side_qty(self, symbol, side):
        return self._reads.get("open_side_qty", super().open_side_qty(symbol, side))

    def exposure_ticks(self, symbol=None):
        return self._reads.get("exposure_ticks", super().exposure_ticks(symbol))


def _sick(**reads):
    oms = _SickOms(**reads)
    oms.register(_new(qty=1, cid="a"), strategy="t")
    oms.on_acknowledged("a")
    return oms


@pytest.mark.parametrize("read", ["position", "open_side_qty", "exposure_ticks"])
@pytest.mark.parametrize("value", [NAN, INF, 2.5, True])
def test_a_non_int_oms_read_blocks_a_new_order(read, value):
    # A loose cap is used so only the poisoned read can decide: on d64f1d0 NaN, a float and a
    # bool are compared (False) or summed and the order is allowed.
    guards = Guards()
    verdict = guards.check(_new(qty=1, price=1), oms=_sick(**{read: value}))
    assert not verdict.allowed
    assert "OMS" in verdict.reason


@pytest.mark.parametrize("read", ["position", "open_side_qty", "exposure_ticks"])
@pytest.mark.parametrize("value", [NAN, INF, 2.5, True])
def test_a_non_int_oms_read_blocks_a_replace(read, value):
    verdict = Guards().check(ReplaceOrder(client_id="a", new_price_ticks=101),
                             oms=_sick(**{read: value}))
    assert not verdict.allowed
    assert "OMS" in verdict.reason


@pytest.mark.parametrize("field,value", [("qty", NAN), ("qty", True), ("price_ticks", NAN),
                                         ("price_ticks", 2.5)])
def test_a_resting_order_with_a_non_int_term_blocks_a_replace_and_a_new_order(field, value):
    oms = _oms_with_live(qty=5)
    setattr(oms.get("a"), field, value)           # what a poisoned fill or replace report would leave
    assert not Guards().check(ReplaceOrder(client_id="a", new_price_ticks=101), oms=oms).allowed
    if value is not True:        # True - 0 is the int 1, so a bool qty is already whole by then
        assert not Guards().check(_new(qty=1, cid="b"), oms=oms).allowed


def test_whole_int_oms_reads_still_pass():
    assert Guards().check(_new(qty=1), oms=_sick(position=3, open_side_qty=2,
                                                 exposure_ticks=500)).allowed
    assert Guards().check(ReplaceOrder(client_id="a", new_price_ticks=101),
                          oms=_sick(position=3, exposure_ticks=500)).allowed


@pytest.mark.parametrize("total", ["5", b"5", None, True, False, _Lot.ONE, [5]])
def test_a_total_the_loss_stop_cannot_read_latches_and_never_raises(total):
    guards = Guards(limits=GuardLimits(max_loss_dollars=100.0))
    guards.observe_pnl(total)
    assert guards.tripped
    assert "loss stop cannot compare" in guards.trip_reason


@pytest.mark.parametrize("limit", ["5", "5.0", " 5", b"5", False, _Lot.ONE, [5]])
def test_a_loss_limit_the_stop_cannot_read_latches_and_never_raises(limit):
    guards = Guards(limits=GuardLimits(max_loss_dollars=limit))
    guards.observe_pnl(-1.0)
    assert guards.tripped
    assert "loss stop cannot compare" in guards.trip_reason


def test_a_none_limit_stays_off_whatever_the_total():
    guards = Guards(limits=GuardLimits(max_loss_dollars=None))
    for total in (NAN, "5", None, True):
        guards.observe_pnl(total)
    assert not guards.tripped


def test_ints_floats_and_decimals_still_feed_the_loss_stop():
    from decimal import Decimal
    for limit, total in ((100, -150), (100.0, -150.0), (Decimal("100"), -150.0),
                         (100.0, Decimal("-150"))):
        guards = Guards(limits=GuardLimits(max_loss_dollars=limit))
        guards.observe_pnl(total)
        assert guards.tripped and "breached" in guards.trip_reason
        calm = Guards(limits=GuardLimits(max_loss_dollars=limit))
        calm.observe_pnl(-50)
        assert not calm.tripped


@pytest.mark.parametrize("make", [
    lambda b: _new(qty=b),
    lambda b: _new(qty=1, price=b),
    lambda b: ReplaceOrder(client_id="a", new_qty=b),
    lambda b: ReplaceOrder(client_id="a", new_price_ticks=b),
])
def test_a_value_whose_repr_raises_is_refused_not_raised(make):
    verdict = Guards().check(make(_BadRepr()), oms=_oms_with_live())
    assert not verdict.allowed
    assert "_BadRepr" in verdict.reason


def test_a_clock_whose_repr_raises_is_refused_not_raised():
    verdict = Guards().check(_new(), oms=OMS(), ts=_BadRepr())
    assert not verdict.allowed and "_BadRepr" in verdict.reason


def test_an_oms_read_whose_repr_raises_is_refused_not_raised():
    verdict = Guards().check(_new(qty=1), oms=_sick(position=_BadRepr()))
    assert not verdict.allowed and "_BadRepr" in verdict.reason


@pytest.mark.parametrize("where", ["total", "limit"])
def test_a_loss_stop_input_whose_repr_raises_latches_not_raises(where):
    bad = _BadRepr()
    guards = Guards(limits=GuardLimits(max_loss_dollars=bad if where == "limit" else 100.0))
    guards.observe_pnl(bad if where == "total" else -1.0)
    assert guards.tripped and "_BadRepr" in guards.trip_reason


# ---- answer to read 2 (DeepSeek, head 906c9c19): the resting order's own terms ----


def _terminal_order_with(**terms):
    """An order that is no longer live, so open_side_qty() and exposure_ticks() do not count
    it: only the term checks in the replace path can see a bad value on it."""
    oms = _oms_with_live(qty=5)
    order = oms.get("a")
    order.state = OrderState.CANCELLED
    for name, value in terms.items():
        setattr(order, name, value)
    return oms


@pytest.mark.parametrize("field,value", [
    ("filled_qty", NAN), ("filled_qty", INF), ("filled_qty", 2.5), ("filled_qty", True),
    ("price_ticks", NAN), ("price_ticks", INF), ("price_ticks", 2.5), ("price_ticks", True),
])
def test_a_terminal_order_with_a_non_int_term_blocks_a_replace(field, value):
    # Removing the filled_qty check, or the price_ticks check, from the replace path leaves
    # every other test green: this one is what names them.
    oms = _terminal_order_with(**{field: value})
    verdict = Guards().check(ReplaceOrder(client_id="a", new_price_ticks=101), oms=oms)
    assert not verdict.allowed
    assert f"order_{field}" in verdict.reason


def test_a_terminal_order_with_whole_int_terms_is_not_blocked_by_the_term_checks():
    oms = _terminal_order_with(filled_qty=2, price_ticks=100)
    assert Guards().check(ReplaceOrder(client_id="a", new_price_ticks=101), oms=oms).allowed
