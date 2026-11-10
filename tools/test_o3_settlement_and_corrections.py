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


if __name__ == "__main__":
    unittest.main()
