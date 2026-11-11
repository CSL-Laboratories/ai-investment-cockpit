#!/usr/bin/env python3
"""O3 settlement and correction controls outside the six designed fixtures.

    python -X utf8 -B tools/test_o3_settlement_and_corrections.py

These cases are a separate control set. They are not added to the
manifest or oracle, so the frozen app O3 suite still runs the original
six designed negatives unchanged.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "docs" / "replacement" / "outcomes" / "O3" / "validate_candidate.py"
FIXTURES = VALIDATOR.parent / "fixtures"


def _load_validator():
    spec = importlib.util.spec_from_file_location("o3_validate_candidate", VALIDATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {VALIDATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _reservation_after_settlement() -> dict:
    """Item 2 / r4409154404: a new order-linked reserve after the payout."""
    fixture = _load_fixture("event_yes_settlement")
    fixture["orders"].append({
        "order_id": "ord-after",
        "instrument_id": "ev-yes",
        "family": "event",
        "side": "buy",
        "qty": 1,
        "state": "resting",
    })
    fixture["journal"].append({
        "entry_id": "j-reserve-after",
        "sequence": 4,
        "idempotency_key": "reserve-after-1",
        "order_id": "ord-after",
        "strategy_id": "s-event",
        "reason": "reserve_collateral",
        "lines": [
            {"account": "collateral_reserved", "side": "dr", "amount_minor": 100, "instrument_id": "ev-yes"},
            {"account": "cash_available", "side": "cr", "amount_minor": 100, "instrument_id": "ev-yes"},
        ],
    })
    fixture["reported"]["cash_available"] -= 100
    fixture["reported"]["collateral_reserved"] += 100
    fixture["reported"]["open_reservations"] = [{
        "reservation_id": "reserve-after",
        "instrument_id": "ev-yes",
        "order_id": "ord-after",
        "amount_minor": 100,
    }]
    return fixture


def _settlement_mirror_without_replacement() -> dict:
    """Item 3 / r4529748086: error + later mirror, correct payout before the mirror."""
    fixture = _load_fixture("event_yes_settlement")
    settle = next(entry for entry in fixture["journal"] if entry["entry_id"] == "j-settle")
    settle["sequence"] = 4
    error_amt = 123456
    fixture["journal"].append({
        "entry_id": "j-error",
        "sequence": 3,
        "idempotency_key": "settle-error",
        "strategy_id": "s-event",
        "reason": "settlement_payout",
        "lines": [
            {"account": "cash_available", "side": "dr", "amount_minor": error_amt, "instrument_id": "ev-yes"},
            {"account": "realized_pnl", "side": "cr", "amount_minor": error_amt, "instrument_id": "ev-yes"},
        ],
    })
    fixture["journal"].append({
        "entry_id": "j-error-mirror",
        "sequence": 5,
        "idempotency_key": "settle-error-mirror",
        "strategy_id": "s-event",
        "reason": "settlement_payout",
        "lines": [
            {"account": "cash_available", "side": "cr", "amount_minor": error_amt, "instrument_id": "ev-yes"},
            {"account": "realized_pnl", "side": "dr", "amount_minor": error_amt, "instrument_id": "ev-yes"},
        ],
    })
    fixture["corrections"].append({
        "correction_id": "c-error",
        "admits_fill_id": "f-yes",
        "reverses_entry_id": "j-error",
        "reason": "reverse erroneous settlement",
        "instrument_id": "ev-yes",
        "cause": "settlement_payout",
    })
    return fixture


def _void_unwind_release(amount: int, entry_id: str, sequence: int, *, reverse: bool = False) -> dict:
    return {
        "entry_id": entry_id,
        "sequence": sequence,
        "idempotency_key": entry_id,
        "order_id": "ord-rest",
        "strategy_id": "s-event",
        "reason": "void_unwind",
        "lines": [
            {
                "account": "cash_available",
                "side": "cr" if reverse else "dr",
                "amount_minor": amount,
                "instrument_id": "ev-yes",
            },
            {
                "account": "collateral_reserved",
                "side": "dr" if reverse else "cr",
                "amount_minor": amount,
                "instrument_id": "ev-yes",
            },
        ],
    }


def _apply_net_release(fixture: dict, amount: int) -> None:
    fixture["reported"]["cash_available"] += amount
    fixture["reported"]["collateral_reserved"] -= amount
    for row in fixture["reported"]["open_reservations"]:
        if row["reservation_id"] == "reserve-rest":
            row["amount_minor"] -= amount


def _valid_void_unwind_correction() -> dict:
    """Item 4 / r4530073842: original → mirror → replacement release."""
    fixture = _load_fixture("event_resting_order_reservation")
    fixture["journal"].append(_void_unwind_release(100000, "j-release-wrong", 6))
    fixture["journal"].append(_void_unwind_release(100000, "j-release-mirror", 7, reverse=True))
    fixture["journal"].append(_void_unwind_release(200000, "j-release-correct", 8))
    fixture["corrections"].append({
        "correction_id": "c-release",
        "admits_fill_id": "f-rest",
        "reverses_entry_id": "j-release-wrong",
        "reason": "correct collateral release",
        "instrument_id": "ev-yes",
        "cause": "void_unwind",
    })
    _apply_net_release(fixture, 200000)
    return fixture


def _only_correct_void_unwind_release() -> dict:
    fixture = _load_fixture("event_resting_order_reservation")
    fixture["journal"].append(_void_unwind_release(200000, "j-release-correct", 8))
    _apply_net_release(fixture, 200000)
    return fixture


def _void_refund(amount: int, entry_id: str, sequence: int, *, reverse: bool = False) -> dict:
    entry = _void_unwind_release(amount, entry_id, sequence, reverse=reverse)
    del entry["order_id"]
    for line in entry["lines"]:
        line["instrument_id"] = "ev-void"
    return entry


def _voided_market_refund_correction(*, with_correction: bool = True) -> dict:
    """Finding A: wrong refund → mirror → replacement refund on a voided market."""
    fixture = _load_fixture("event_void_reconciled")
    fixture["fills"].append({
        "fill_id": "f-void",
        "order_id": "ord-void",
        "instrument_id": "ev-void",
        "sequence": 1,
        "qty": 10,
        "price_minor": 400000,
        "fee_minor": 0,
        "side": "buy",
    })
    fixture["journal"] = [
        _void_refund(3000000, "j-void", 2),
        _void_refund(3000000, "j-void-mirror", 3, reverse=True),
        _void_refund(4000000, "j-void-correct", 4),
    ]
    fixture["journal"][0]["idempotency_key"] = "void-1"
    if with_correction:
        fixture["corrections"].append({
            "correction_id": "c-void",
            "admits_fill_id": "f-void",
            "reverses_entry_id": "j-void",
            "reason": "correct void refund",
            "instrument_id": "ev-void",
            "cause": "void_unwind",
        })
    return fixture


def _settlement_payout(
    cash: int, entry_id: str, sequence: int, *, reverse: bool = False, instrument_id: str = "ev-yes"
) -> dict:
    dr, cr = ("cr", "dr") if reverse else ("dr", "cr")
    return {
        "entry_id": entry_id,
        "sequence": sequence,
        "idempotency_key": entry_id,
        "strategy_id": "s-event",
        "reason": "settlement_payout",
        "lines": [
            {"account": "cash_available", "side": dr, "amount_minor": cash, "instrument_id": instrument_id},
            {"account": "collateral_reserved", "side": cr, "amount_minor": 4000000, "instrument_id": instrument_id},
            {"account": "realized_pnl", "side": cr, "amount_minor": cash - 4000000, "instrument_id": instrument_id},
        ],
    }


def _settlement_correction(original_id: str) -> dict:
    return {
        "correction_id": f"c-{original_id}",
        "admits_fill_id": "f-yes",
        "reverses_entry_id": original_id,
        "reason": "correct settlement payout",
        "instrument_id": "ev-yes",
        "cause": "settlement_payout",
    }


def _settlement_corrected_twice() -> dict:
    """Finding B: the first replacement is itself reversed and replaced."""
    fixture = _load_fixture("event_yes_settlement")
    fixture["journal"] = fixture["journal"][:2] + [
        _settlement_payout(9000000, "j-settle-1", 3),
        _settlement_payout(9000000, "j-mirror-1", 4, reverse=True),
        _settlement_payout(9500000, "j-settle-2", 5),
        _settlement_payout(9500000, "j-mirror-2", 6, reverse=True),
        _settlement_payout(10000000, "j-settle-3", 7),
    ]
    fixture["corrections"] = [
        _settlement_correction("j-settle-1"),
        _settlement_correction("j-settle-2"),
    ]
    return fixture


def _settlement_replacement_drops_fill_reference(*, replacement_instrument: str = "ev-yes") -> dict:
    """Finding C: fill-linked original and mirror, bare replacement."""
    fixture = _load_fixture("event_yes_settlement")
    original = _settlement_payout(10000000, "j-settle", 3)
    mirror = _settlement_payout(10000000, "j-settle-mirror", 4, reverse=True)
    original["fill_id"] = mirror["fill_id"] = "f-yes"
    original["idempotency_key"] = "settle-1"
    fixture["journal"] = fixture["journal"][:2] + [
        original,
        mirror,
        _settlement_payout(
            10000000, "j-settle-replacement", 5, instrument_id=replacement_instrument
        ),
    ]
    fixture["corrections"] = [_settlement_correction("j-settle")]
    return fixture


def _settlement_funded_by_later_payout() -> dict:
    """Finding D: no opening cash, so the reserve and fee overdraw it."""
    fixture = _load_fixture("event_yes_settlement")
    fixture["reported"]["cash_available"] -= fixture["checkpoint"]["cash_available"]
    fixture["checkpoint"]["cash_available"] = 0
    return fixture


def _dividend_correction() -> dict:
    """Finding E: wrong dividend → mirror → replacement dividend."""
    fixture = _load_fixture("equity_partial_then_dividend")

    def dividend(amount: int, entry_id: str, sequence: int, *, reverse: bool = False) -> dict:
        dr, cr = ("cr", "dr") if reverse else ("dr", "cr")
        return {
            "entry_id": entry_id,
            "sequence": sequence,
            "idempotency_key": entry_id,
            "strategy_id": "s-equity",
            "reason": "corporate_action",
            "lines": [
                {"account": "cash_available", "side": dr, "amount_minor": amount, "instrument_id": "eq-aapl"},
                {"account": "realized_pnl", "side": cr, "amount_minor": amount, "instrument_id": "eq-aapl"},
            ],
        }

    fixture["journal"] = fixture["journal"][:2] + [
        dividend(4000, "j3", 3),
        dividend(4000, "j3-mirror", 4, reverse=True),
        dividend(5000, "j3-correct", 5),
    ]
    fixture["journal"][2]["idempotency_key"] = "div-1"
    fixture["corrections"].append({
        "correction_id": "c-div",
        "admits_fill_id": "f1",
        "reverses_entry_id": "j3",
        "reason": "correct dividend amount",
        "instrument_id": "eq-aapl",
        "cause": "corporate_action",
    })
    return fixture


class SettlementAndCorrectionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_validator()

    def _evaluate(self, fixture: dict) -> set[str]:
        return self.mod.evaluate_semantics(copy.deepcopy(fixture))

    def test_reservation_after_settlement_is_rejected(self) -> None:
        failures = self._evaluate(_reservation_after_settlement())
        self.assertIn("settlement_unreconciled", failures)

    def test_settlement_mirror_without_later_replacement_is_rejected(self) -> None:
        failures = self._evaluate(_settlement_mirror_without_replacement())
        self.assertIn("settlement_unreconciled", failures)
        repeated = self.mod._repeated_entry_ids(
            _settlement_mirror_without_replacement()["journal"]
        )
        fixture = _settlement_mirror_without_replacement()
        paired, _mirrors = self.mod._linked_reversal_ids(
            fixture, repeated, require_replacement=True
        )
        self.assertFalse(paired)
        supported, mirrors, _ = self.mod._posted_correction_facts(fixture)
        self.assertFalse(supported)
        self.assertFalse(mirrors)

    def test_valid_void_unwind_release_correction_is_accepted(self) -> None:
        self.assertEqual(self._evaluate(_valid_void_unwind_correction()), set())
        self.assertEqual(self._evaluate(_only_correct_void_unwind_release()), set())

    def test_voided_market_refund_correction_is_accepted(self) -> None:
        self.assertEqual(self._evaluate(_voided_market_refund_correction()), set())

    def test_voided_market_unvalidated_mirror_is_rejected(self) -> None:
        failures = self._evaluate(_voided_market_refund_correction(with_correction=False))
        self.assertIn("void_unreconciled", failures)

    def test_replacement_settlement_can_itself_be_corrected(self) -> None:
        fixture = _settlement_corrected_twice()
        self.assertEqual(self._evaluate(fixture), set())
        fixture["corrections"].reverse()
        self.assertEqual(self._evaluate(fixture), set())

    def test_settlement_replacement_may_drop_fill_reference(self) -> None:
        self.assertEqual(self._evaluate(_settlement_replacement_drops_fill_reference()), set())

    def test_settlement_replacement_on_other_instrument_is_rejected(self) -> None:
        fixture = _settlement_replacement_drops_fill_reference(replacement_instrument="ev-other")
        self.assertIn("settlement_unreconciled", self._evaluate(fixture))
        paired, _mirrors = self.mod._linked_reversal_ids(
            fixture, set(), require_replacement=True
        )
        self.assertFalse(paired)

    def test_negative_available_cash_before_payout_is_rejected(self) -> None:
        self.assertEqual(self._evaluate(_load_fixture("event_yes_settlement")), set())
        failures = self._evaluate(_settlement_funded_by_later_payout())
        self.assertIn("balance_mismatch", failures)

    def test_same_sequence_inflow_does_not_fund_overdraft(self) -> None:
        # The fee shares the payout's sequence. Cash covers the reserve only.
        for opening, expected in ((10000000000, set()), (4000000, {"balance_mismatch"})):
            fixture = _load_fixture("event_yes_settlement")
            fixture["reported"]["cash_available"] -= (
                fixture["checkpoint"]["cash_available"] - opening
            )
            fixture["checkpoint"]["cash_available"] = opening
            fixture["journal"][1]["sequence"] = 3
            self.assertEqual(self._evaluate(fixture), expected)
            fixture["journal"].reverse()
            self.assertEqual(self._evaluate(fixture), expected)

    def test_correction_pairing_ignores_journal_array_order(self) -> None:
        for build in (
            _valid_void_unwind_correction,
            _voided_market_refund_correction,
            _settlement_corrected_twice,
            _settlement_replacement_drops_fill_reference,
            _dividend_correction,
        ):
            fixture = build()
            expected = self._evaluate(fixture)
            facts = self.mod._posted_correction_facts(fixture)
            for shuffled in (fixture["journal"][::-1], fixture["journal"][2:] + fixture["journal"][:2]):
                fixture["journal"] = shuffled
                with self.subTest(build=build.__name__):
                    self.assertEqual(self._evaluate(fixture), expected)
                    self.assertEqual(self.mod._posted_correction_facts(fixture), facts)

    def test_validated_dividend_correction_is_accepted(self) -> None:
        fixture = _dividend_correction()
        self.assertEqual(self._evaluate(fixture), set())
        fixture["corrections"] = []
        self.assertIn("corporate_action_mismatch", self._evaluate(fixture))


if __name__ == "__main__":
    unittest.main()
