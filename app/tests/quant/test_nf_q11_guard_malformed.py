"""NF/Q11: a value that is not a whole int never passes the pre-trade guard.

Red on main 7fb17b6: every comparison in ``Guards.check`` reads False for NaN and treats
``True`` as 1, so a NaN/bool/fractional size, price, limit or clock was allowed.
"""
from __future__ import annotations

import pytest

from quant.events import Side
from quant.guards import GuardLimits, Guards
from quant.oms import OMS, CancelOrder, NewOrder, ReplaceOrder

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
