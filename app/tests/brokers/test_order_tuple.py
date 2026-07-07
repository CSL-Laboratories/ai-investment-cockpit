"""WP7 shared seam — canonical order tuple + hash (SEC-07).

Guards the mint/verify boundary between bridge/ (approval) and brokers/ (submit): a Modify
that changes ANY field must change the hash; two semantically-equal orders must NOT.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from brokers.order_tuple import canonical_order_tuple, order_tuple_hash


def _base(**over):
    fields = dict(symbol="AAPL", side="buy", qty=10, order_type="market", tif="day")
    fields.update(over)
    return fields


def test_semantically_equal_orders_hash_identically():
    a = order_tuple_hash(_base(symbol="aapl", qty=10, side="BUY", tif="DAY"))
    b = order_tuple_hash(_base(symbol="AAPL", qty=Decimal("10.00"), side="buy", tif="day"))
    assert a == b


def test_hash_accepts_canonical_dict_roundtrip():
    canon = canonical_order_tuple(**_base(order_type="limit", limit_price="150.5"))
    # feeding the canonical dict back must reproduce the same hash
    assert order_tuple_hash(canon) == order_tuple_hash(_base(order_type="limit", limit_price="150.50"))


@pytest.mark.parametrize("field,newval", [
    ("symbol", "MSFT"),
    ("side", "sell"),
    ("qty", 11),
    ("order_type", "limit"),   # also flips limit_price requirement
    ("tif", "gtc"),
])
def test_any_field_change_changes_the_hash(field, newval):
    over = {field: newval}
    if newval == "limit":
        over["limit_price"] = "150.00"
    assert order_tuple_hash(_base()) != order_tuple_hash(_base(**over))


def test_limit_price_change_changes_hash():
    h1 = order_tuple_hash(_base(order_type="limit", limit_price="150.00"))
    h2 = order_tuple_hash(_base(order_type="limit", limit_price="151.00"))
    assert h1 != h2


def test_limit_order_requires_limit_price():
    with pytest.raises(ValueError, match="requires a limit_price"):
        canonical_order_tuple(**_base(order_type="limit", limit_price=None))


def test_market_order_forbids_limit_price():
    with pytest.raises(ValueError, match="must not carry a limit_price"):
        canonical_order_tuple(**_base(order_type="market", limit_price="10"))


@pytest.mark.parametrize("bad", [0, -1, "0", Decimal("-5")])
def test_qty_must_be_positive(bad):
    with pytest.raises(ValueError, match="qty must be positive"):
        canonical_order_tuple(**_base(qty=bad))


@pytest.mark.parametrize("field,bad", [
    ("side", "hodl"),
    ("order_type", "iceberg"),
    ("tif", "forever"),
])
def test_enums_are_fail_closed(field, bad):
    with pytest.raises(ValueError):
        canonical_order_tuple(**_base(**{field: bad}))


def test_empty_symbol_rejected():
    with pytest.raises(ValueError, match="symbol"):
        canonical_order_tuple(**_base(symbol="   "))
