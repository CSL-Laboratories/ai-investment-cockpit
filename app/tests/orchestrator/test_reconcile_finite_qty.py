"""NF/W6: a fill quantity that is not a finite number is never broker evidence of landing.

Red on main 7fb17b6: ``float(filled_qty) > 0`` read "Infinity"/inf as landed and True as 1.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from orchestrator.reconcile import _order_landed


# The sweep's own witness (s6-ck-workflows F06), verbatim.
@pytest.mark.parametrize("bad_quantity", ["Infinity", float("inf"), True])
def test_f06_nonfinite_or_boolean_fill_cannot_heal(bad_quantity):
    record = {"order_id": "synthetic-order", "status": "unknown", "filled_qty": bad_quantity}
    assert not _order_landed(record, "synthetic-order")
    assert _order_landed({**record, "filled_qty": "1"}, "synthetic-order")


@pytest.mark.parametrize("bad_quantity", ["inf", "+Infinity", Decimal("Infinity"), "NaN",
                                          float("nan"), "-Infinity", False, "abc", None, [], b"1"])
def test_unreadable_fill_quantity_is_not_landing_evidence(bad_quantity):
    record = {"order_id": "o", "status": "unknown", "filled_qty": bad_quantity}
    assert not _order_landed(record, "o")
    assert not _order_landed([record], "o")


@pytest.mark.parametrize("good", ["1", "0.5", 2, 0.25, Decimal("10"), " 3 ", "1e400"])
def test_finite_positive_fill_quantity_is_still_landing_evidence(good):
    assert _order_landed({"order_id": "o", "status": "unknown", "filled_qty": good}, "o")


@pytest.mark.parametrize("zero", ["0", 0, Decimal("0"), "-1", -0.5])
def test_zero_or_negative_fill_is_not_evidence(zero):
    assert not _order_landed({"order_id": "o", "status": "unknown", "filled_qty": zero}, "o")


_INF = {"order_id": "o", "filled_qty": "Infinity"}
_FILLED = {"order_id": "o", "filled_qty": "5"}
_REJECTED = {"order_id": "o", "status": "rejected"}


@pytest.mark.parametrize("history", [
    [_INF, _REJECTED],
    [_INF, _FILLED, _REJECTED],
    [_REJECTED, _FILLED, _INF],
    [_FILLED, _INF, _REJECTED],
])
def test_infinite_fill_beside_a_negative_status_still_vetoes(history):
    # The finite fill would heal on its own: only the global negative veto refuses these.
    assert not _order_landed(history, "o")
    assert _order_landed([_FILLED, _INF], "o")
