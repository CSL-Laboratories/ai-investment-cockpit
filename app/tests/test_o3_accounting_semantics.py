"""CSL-215 O3 candidate validator. Provider-free: local files only, no network."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import warnings
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "docs" / "replacement" / "outcomes" / "O3" / "validate_candidate.py"
NEGATIVE_IDS = (
    "neg_double_counted_collateral_after_restart",
    "neg_voided_market_unreconciled",
    "neg_partial_fill_divergence",
    "neg_duplicate_fill_divergence",
    "neg_late_fill_divergence",
    "neg_offsetting_fill_prices",
)


def test_o3_negative_controls_fail_as_designed() -> None:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    for fixture_id in NEGATIVE_IDS:
        needle = f"O3 NEGATIVE CONTROL FAILED AS DESIGNED: {fixture_id} "
        assert needle in result.stdout
    assert "O3 candidate maturity remains researched" in result.stdout
    assert "contract_frozen" not in result.stdout
    for line in result.stdout.splitlines():
        if line.startswith("O3 NEGATIVE CONTROL FAILED AS DESIGNED:"):
            warnings.warn(line, UserWarning, stacklevel=1)


def _evaluate(fixture: dict) -> set[str]:
    spec = importlib.util.spec_from_file_location("o3_validate_candidate", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.evaluate_semantics(fixture)


def test_o3_fill_price_must_match_journal_cost() -> None:
    for name in ("equity_partial_then_dividend", "event_yes_settlement"):
        path = VALIDATOR.parent / "fixtures" / f"{name}.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        assert _evaluate(fixture) == set()
        fixture["fills"][0]["price_minor"] *= 2
        assert "balance_mismatch" in _evaluate(fixture)


def test_o3_execution_postings_require_cash_credit() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    reserve = next(entry for entry in fixture["journal"] if entry["reason"] == "reserve_collateral")
    cash_line = next(line for line in reserve["lines"] if line["account"] == "cash_available")
    amount = cash_line["amount_minor"]
    cash_line["account"] = "realized_pnl"
    fixture["reported"]["cash_available"] += amount
    fixture["reported"]["attribution"][0]["realized_pnl_minor"] += amount
    assert "balance_mismatch" in _evaluate(fixture)


def test_o3_fill_postings_require_fill_reference() -> None:
    path = VALIDATOR.parent / "fixtures" / "equity_partial_then_dividend.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    del fixture["journal"][0]["fill_id"]
    assert "balance_mismatch" in _evaluate(fixture)
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["journal"][0]["fill_id"] = "ghost-fill"
    assert "position_divergence" in _evaluate(fixture)


def test_o3_resting_order_reservation_is_not_fill_cost() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    del fixture["journal"][2]["order_id"]
    assert "balance_mismatch" in _evaluate(fixture)
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["journal"][2]["order_id"] = "ord-yes"
    assert "position_divergence" in _evaluate(fixture)
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["journal"][0]["order_id"] = "ord-rest"
    assert "position_divergence" in _evaluate(fixture)
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["journal"].append({
        "entry_id": "j-order-fee",
        "sequence": 4,
        "idempotency_key": "fee-rest-1",
        "order_id": "ord-rest",
        "strategy_id": "s-event",
        "reason": "trade_fee",
        "lines": [
            {"account": "fees_expense", "side": "dr", "amount_minor": 50000, "instrument_id": "ev-yes"},
            {"account": "cash_available", "side": "cr", "amount_minor": 50000, "instrument_id": "ev-yes"},
        ],
    })
    fixture["reported"]["cash_available"] -= 50000
    fixture["reported"]["attribution"][0]["fees_minor"] += 50000
    assert "balance_mismatch" in _evaluate(fixture)


def test_o3_order_reservation_before_partial_fill() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    for entry in fixture["journal"]:
        if entry["entry_id"] == "j-reserve-rest":
            entry["sequence"] = 6
    fixture["journal"].sort(key=lambda entry: entry["sequence"])
    assert "position_divergence" in _evaluate(fixture)


def test_o3_fill_lines_must_match_fill_instrument() -> None:
    path = VALIDATOR.parent / "fixtures" / "equity_partial_then_dividend.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["instruments"].append({
        "instrument_id": "eq-msft",
        "family": "equity",
        "venue_id": "venue-equity-candidate",
        "venue_symbol": "MSFT",
        "currency": "USD",
        "unit": "share",
    })
    fixture["orders"].append({
        "order_id": "ord-3",
        "instrument_id": "eq-msft",
        "family": "equity",
        "side": "buy",
        "qty": 1,
        "state": "resting",
    })
    for line in fixture["journal"][0]["lines"]:
        line["instrument_id"] = "eq-msft"
    assert "position_divergence" in _evaluate(fixture)


def test_o3_order_reserve_matches_fills_total() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    for entry in fixture["journal"]:
        if entry["entry_id"] == "j-reserve-rest":
            for line in entry["lines"]:
                line["amount_minor"] = 2000000
    fixture["reported"]["cash_available"] -= 1170000
    fixture["reported"]["collateral_reserved"] += 1170000
    fixture["reported"]["open_reservations"][1]["amount_minor"] = 2000000
    assert "balance_mismatch" in _evaluate(fixture)
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["fills"].append({
        "fill_id": "f-rest-2",
        "order_id": "ord-rest",
        "instrument_id": "ev-yes",
        "sequence": 5,
        "qty": 1,
        "price_minor": 400000,
        "fee_minor": 0,
        "side": "buy",
    })
    fixture["reported"]["positions"][0]["qty"] = 13
    assert "balance_mismatch" in _evaluate(fixture)


def test_o3_checkpointed_correction_admits_late_fill() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_restart_idempotent.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["instruments"].append({
        "instrument_id": "ev-two",
        "family": "event_contract",
        "venue_id": "venue-event-candidate",
        "venue_symbol": "OTHER-23DEC31",
        "currency": "USD",
        "unit": "contract",
        "contract_style": "binary",
        "outcome_side": "yes",
        "collateral_model": "fully_collateralized",
    })
    fixture["markets"].append({"instrument_id": "ev-two", "state": "open"})
    fixture["orders"].append({
        "order_id": "ord-x",
        "instrument_id": "ev-two",
        "family": "event",
        "side": "buy",
        "qty": 3,
        "state": "trade_cancelled",
        "terminal_sequence": 4,
    })
    # The admitted fill is a remainder, not the whole order. A full quantity
    # on this cancel would be the filled state.
    fixture["fills"].append({
        "fill_id": "f-late",
        "order_id": "ord-x",
        "instrument_id": "ev-two",
        "sequence": 5,
        "qty": 1,
        "price_minor": 400000,
        "fee_minor": 0,
        "side": "buy",
    })
    fixture["journal"].append({
        "entry_id": "j-late",
        "sequence": 6,
        "idempotency_key": "reserve-late-1",
        "fill_id": "f-late",
        "strategy_id": "s-event",
        "reason": "reserve_collateral",
        "lines": [
            {"account": "collateral_reserved", "side": "dr", "amount_minor": 400000, "instrument_id": "ev-two"},
            {"account": "cash_available", "side": "cr", "amount_minor": 400000, "instrument_id": "ev-two"},
        ],
    })
    fixture["corrections"].append({
        "correction_id": "c-late",
        "admits_fill_id": "f-late",
        "reverses_entry_id": "j-reserve",
        "reason": "admit late fill",
    })
    fixture["reported"]["cash_available"] -= 400000
    fixture["reported"]["collateral_reserved"] += 400000
    fixture["reported"]["positions"].append({"instrument_id": "ev-two", "qty": 1})
    fixture["reported"]["open_reservations"].append({
        "reservation_id": "reserve-late",
        "instrument_id": "ev-two",
        "amount_minor": 400000,
    })
    assert _evaluate(fixture) == set()
    fixture["corrections"][0]["reverses_entry_id"] = "j-ghost"
    assert "late_fill_divergence" in _evaluate(fixture)


def test_o3_filled_order_must_be_fully_executed() -> None:
    for name in ("equity_partial_then_dividend", "event_yes_settlement"):
        path = VALIDATOR.parent / "fixtures" / f"{name}.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        fixture["orders"][0]["qty"] += 1
        assert "partial_fill_divergence" in _evaluate(fixture)


def test_o3_corporate_action_must_reference_declared_instrument() -> None:
    path = VALIDATOR.parent / "fixtures" / "equity_partial_then_dividend.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["corporate_actions"][0]["instrument_id"] = "ghost"
    assert "unknown_instrument" in _evaluate(fixture)


def test_o3_settlement_payout_posting_shape() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["journal"][2]["lines"].extend([
        {"account": "realized_pnl", "side": "dr", "amount_minor": 1, "instrument_id": "ev-yes"},
        {"account": "cash_available", "side": "cr", "amount_minor": 1, "instrument_id": "ev-yes"},
    ])
    assert "settlement_unreconciled" in _evaluate(fixture)


def test_o3_market_must_reference_declared_instrument() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["markets"].append({"instrument_id": "ghost", "state": "open"})
    assert "unknown_instrument" in _evaluate(fixture)


def test_o3_unknown_instrument_references_rejected() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["orders"][0]["instrument_id"] = "ghost"
    assert "unknown_instrument" in _evaluate(fixture)
    fixture = json.loads(path.read_text(encoding="utf-8"))
    for entry in fixture["journal"]:
        for line in entry["lines"]:
            line["instrument_id"] = "ghost"
    assert "unknown_instrument" in _evaluate(fixture)


def test_o3_checkpoint_reservations_must_reconcile() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_restart_idempotent.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["checkpoint"]["open_reservations"][0]["amount_minor"] = 1
    assert "collateral_double_count" in _evaluate(fixture)


def test_o3_dividend_posting_shape() -> None:
    path = VALIDATOR.parent / "fixtures" / "equity_partial_then_dividend.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["journal"][2]["lines"][1]["account"] = "position_cost"
    assert "corporate_action_mismatch" in _evaluate(fixture)


def _resting_extension(fixture: dict) -> dict:
    fixture["instruments"].append({
        "instrument_id": "ev-two",
        "family": "event_contract",
        "venue_id": "venue-event-candidate",
        "venue_symbol": "OTHER-23DEC31",
        "currency": "USD",
        "unit": "contract",
        "contract_style": "binary",
        "outcome_side": "yes",
        "collateral_model": "fully_collateralized",
    })
    fixture["markets"].append({"instrument_id": "ev-two", "state": "open"})
    fixture["orders"].append({
        "order_id": "ord-rest",
        "instrument_id": "ev-two",
        "family": "event",
        "side": "buy",
        "qty": 5,
        "state": "filled",
    })
    fixture["journal"].append({
        "entry_id": "j-rest",
        "sequence": 4,
        "idempotency_key": "reserve-rest-1",
        "order_id": "ord-rest",
        "strategy_id": "s-event",
        "reason": "reserve_collateral",
        "lines": [
            {"account": "collateral_reserved", "side": "dr", "amount_minor": 2000000, "instrument_id": "ev-two"},
            {"account": "cash_available", "side": "cr", "amount_minor": 2000000, "instrument_id": "ev-two"},
        ],
    })
    fixture["fills"].append({
        "fill_id": "f-rest",
        "order_id": "ord-rest",
        "instrument_id": "ev-two",
        "sequence": 5,
        "qty": 5,
        "price_minor": 400000,
        "fee_minor": 0,
        "side": "buy",
    })
    fixture["reported"]["cash_available"] -= 2000000
    fixture["reported"]["collateral_reserved"] += 2000000
    fixture["reported"]["positions"].append({"instrument_id": "ev-two", "qty": 5})
    fixture["reported"]["open_reservations"].append({
        "reservation_id": "reserve-rest",
        "instrument_id": "ev-two",
        "amount_minor": 2000000,
    })
    return fixture


def test_o3_settlement_collateral_scoped_per_instrument() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = _resting_extension(json.loads(path.read_text(encoding="utf-8")))
    assert _evaluate(fixture) == set()
    fixture["journal"][2]["lines"][1]["amount_minor"] -= 1
    assert "settlement_unreconciled" in _evaluate(fixture)


def test_o3_void_collateral_scoped_per_instrument() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_void_reconciled.json"
    fixture = _resting_extension(json.loads(path.read_text(encoding="utf-8")))
    assert _evaluate(fixture) == set()


def test_o3_registry_maturity_stays_researched() -> None:
    program = json.loads((ROOT / "docs" / "replacement" / "program.json").read_text(encoding="utf-8"))
    outcome = next(item for item in program["outcomes"] if item["key"] == "O3")
    assert outcome["linear_issue"] == "CSL-215"
    assert outcome["evidence"]["claimed"] == "researched"
    assert "contract_frozen" in outcome["evidence"]["not_claimed"]
    assert outcome["completion_gate_status"] == "pending_outcome_artifact"
    assert outcome["discretion_class"] == "human_gated"


def test_accepted_csl212_contract_digests_unchanged() -> None:
    program = json.loads((ROOT / "docs" / "replacement" / "program.json").read_text(encoding="utf-8"))
    for record in program["contracts"]:
        digest = hashlib.sha256((ROOT / record["path"]).read_bytes()).hexdigest()
        assert digest == record["sha256"]


def test_o3_losing_settlement_posts_realized_loss() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_no_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["journal"][2]["lines"][0]["amount_minor"] -= 1
    assert "unbalanced_entry" in _evaluate(fixture)
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["journal"][2]["lines"].append({
        "account": "realized_pnl", "side": "dr", "amount_minor": 1, "instrument_id": "ev-no"
    })
    fixture["journal"][2]["lines"].append({
        "account": "cash_available", "side": "cr", "amount_minor": 1, "instrument_id": "ev-no"
    })
    assert "settlement_unreconciled" in _evaluate(fixture)


def test_o3_settlement_fee_must_be_posted_when_not_deducted() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["evidence"][0]["misc_fee_minor"] = 123
    assert "settlement_unreconciled" in _evaluate(fixture)


def test_o3_reservation_after_settlement_rejected() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["journal"][0]["sequence"] = 4
    assert "settlement_unreconciled" in _evaluate(fixture)


def test_o3_dividend_lines_bound_to_action_instrument() -> None:
    path = VALIDATOR.parent / "fixtures" / "equity_partial_then_dividend.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["instruments"].append({
        "instrument_id": "eq-ibm",
        "family": "equity_share",
        "venue_id": "venue-equity-candidate",
        "venue_symbol": "IBM",
        "currency": "USD",
        "unit": "share",
    })
    for line in fixture["journal"][2]["lines"]:
        line["instrument_id"] = "eq-ibm"
    assert "corporate_action_mismatch" in _evaluate(fixture)


def test_o3_filled_order_needs_checkpoint_evidence() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_restart_idempotent.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    del fixture["orders"][0]["checkpoint_filled_qty"]
    assert "partial_fill_divergence" in _evaluate(fixture)
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["orders"][0]["checkpoint_filled_qty"] = 9
    assert "partial_fill_divergence" in _evaluate(fixture)


def test_o3_checkpointed_entry_id_replay_detected() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_restart_idempotent.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["checkpoint"]["applied_entry_ids"].append("j-settle")
    assert "collateral_double_count" in _evaluate(fixture)


def test_o3_void_unwind_does_not_count_as_fill_premium() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    reserve = next(entry for entry in fixture["journal"] if entry["entry_id"] == "j-reserve")
    reserve["reason"] = "void_unwind"
    assert "balance_mismatch" in _evaluate(fixture)


def test_o3_unfilled_resting_order_may_hold_collateral() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    rest = next(order for order in fixture["orders"] if order["order_id"] == "ord-rest")
    rest["state"] = "resting"
    assert "partial_fill_divergence" in _evaluate(fixture)
    fixture["fills"] = [fill for fill in fixture["fills"] if fill["fill_id"] != "f-rest"]
    fixture["journal"] = [entry for entry in fixture["journal"] if entry["entry_id"] != "j-fee-rest"]
    fixture["reported"]["positions"][0]["qty"] = 10
    fixture["reported"]["cash_available"] += 30000
    fixture["reported"]["attribution"][0]["fees_minor"] = 70000
    assert _evaluate(fixture) == set()
    rest["state"] = "cancelled"
    rest["terminal_sequence"] = 4
    assert "balance_mismatch" in _evaluate(fixture)
    fixture["journal"].append({
        "entry_id": "j-release",
        "sequence": 6,
        "idempotency_key": "release-rest-1",
        "order_id": "ord-rest",
        "strategy_id": "s-event",
        "reason": "void_unwind",
        "lines": [
            {"account": "cash_available", "side": "dr", "amount_minor": 830000, "instrument_id": "ev-yes"},
            {"account": "collateral_reserved", "side": "cr", "amount_minor": 830000, "instrument_id": "ev-yes"},
        ],
    })
    fixture["reported"]["cash_available"] += 830000
    fixture["reported"]["collateral_reserved"] -= 830000
    fixture["reported"]["open_reservations"] = [
        row for row in fixture["reported"]["open_reservations"] if row["reservation_id"] != "reserve-rest"
    ]
    assert _evaluate(fixture) == set()


def test_o3_settlement_lines_share_one_settled_instrument() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["instruments"].append({
        "instrument_id": "ev-other",
        "family": "event_contract",
        "venue_id": "venue-event-candidate",
        "venue_symbol": "OTHER-23DEC31",
        "currency": "USD",
        "unit": "contract",
        "contract_style": "binary",
        "outcome_side": "yes",
        "collateral_model": "fully_collateralized",
    })
    settle = next(entry for entry in fixture["journal"] if entry["reason"] == "settlement_payout")
    gain = next(line for line in settle["lines"] if line["account"] == "realized_pnl")
    gain["instrument_id"] = "ev-other"
    assert "settlement_unreconciled" in _evaluate(fixture)


def test_o3_every_evidence_instrument_must_be_declared() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["evidence"].append({"kind": "market_void", "instrument_id": "ghost", "source_id": "void-1"})
    assert "unknown_instrument" in _evaluate(fixture)


def test_o3_fill_after_settlement_is_not_in_the_payout() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["fills"][0]["sequence"] = 4
    failures = _evaluate(fixture)
    assert "settlement_unreconciled" in failures
    assert "position_divergence" in failures


def test_o3_checkpoint_filled_qty_must_match_checkpoint_position() -> None:
    path = VALIDATOR.parent / "fixtures" / "equity_partial_then_dividend.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["orders"].append({
        "order_id": "ord-eq-2",
        "instrument_id": "eq-aapl",
        "family": "equity",
        "side": "buy",
        "qty": 10,
        "state": "filled",
        "checkpoint_filled_qty": 10,
    })
    assert "position_divergence" in _evaluate(fixture)


def test_o3_duplicate_order_id_rejected() -> None:
    path = VALIDATOR.parent / "fixtures" / "equity_partial_then_dividend.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["instruments"].append({
        "instrument_id": "eq-ibm",
        "family": "equity_share",
        "venue_id": "venue-equity-candidate",
        "venue_symbol": "IBM",
        "currency": "USD",
        "unit": "share",
    })
    fixture["orders"].insert(0, {
        "order_id": "ord-eq-1",
        "instrument_id": "eq-ibm",
        "family": "equity",
        "side": "buy",
        "qty": 100,
        "state": "filled",
    })
    assert "position_divergence" in _evaluate(fixture)


def test_o3_one_market_state_row_per_instrument() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["markets"].insert(0, {"instrument_id": "ev-yes", "state": "open"})
    assert "settlement_unreconciled" in _evaluate(fixture)


def test_o3_earlier_reservation_forbids_fill_linked_premium() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["journal"].append({
        "entry_id": "j-reserve-fill",
        "sequence": 4,
        "idempotency_key": "reserve-fill-rest",
        "fill_id": "f-rest",
        "strategy_id": "s-event",
        "reason": "reserve_collateral",
        "lines": [
            {
                "account": "collateral_reserved",
                "side": "dr",
                "amount_minor": 800000,
                "instrument_id": "ev-yes",
            },
            {
                "account": "cash_available",
                "side": "cr",
                "amount_minor": 800000,
                "instrument_id": "ev-yes",
            },
        ],
    })
    fixture["reported"]["cash_available"] -= 800000
    fixture["reported"]["collateral_reserved"] += 800000
    fixture["reported"]["open_reservations"].append({
        "reservation_id": "reserve-fill-rest",
        "instrument_id": "ev-yes",
        "amount_minor": 800000,
    })
    assert "balance_mismatch" in _evaluate(fixture)


def test_o3_release_must_match_the_owning_orders_hold() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_void_reconciled.json"
    owned = json.loads(path.read_text(encoding="utf-8"))
    owned["checkpoint"]["open_reservations"][0]["order_id"] = "ord-void"
    owned["journal"][0]["order_id"] = "ord-void"
    assert _evaluate(owned) == set()
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["checkpoint"]["positions"][0]["qty"] = 11
    fixture["checkpoint"]["open_reservations"][0]["order_id"] = "ord-void"
    fixture["orders"].append({
        "order_id": "ord-ghost",
        "instrument_id": "ev-void",
        "family": "event",
        "side": "buy",
        "qty": 1,
        "state": "cancelled",
        "terminal_sequence": 1,
        "checkpoint_filled_qty": 1,
    })
    fixture["journal"][0]["order_id"] = "ord-ghost"
    failures = _evaluate(fixture)
    assert "balance_mismatch" in failures
    assert "position_divergence" not in failures


def test_o3_void_evidence_contradicts_a_losing_settlement() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_no_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["evidence"].append({
        "kind": "market_void",
        "instrument_id": "ev-no",
        "source_id": "void-no-1",
    })
    assert "void_unreconciled" in _evaluate(fixture)
    fixture["corrections"].append({
        "correction_id": "c-void",
        "admits_fill_id": "f-no",
        "reverses_entry_id": "missing-entry",
        "reason": "supersede void",
        "supersedes_source_id": "void-no-1",
    })
    assert "void_unreconciled" in _evaluate(fixture)
    fixture["corrections"][0]["reverses_entry_id"] = "j-settle"
    assert "void_unreconciled" in _evaluate(fixture)


def _event_instrument(instrument_id: str, symbol: str, outcome: str = "yes") -> dict:
    return {
        "instrument_id": instrument_id,
        "family": "event_contract",
        "venue_id": "venue-event-candidate",
        "venue_symbol": symbol,
        "currency": "USD",
        "unit": "contract",
        "contract_style": "binary",
        "outcome_side": outcome,
        "collateral_model": "fully_collateralized",
    }


def _reversal_of(entry: dict, entry_id: str) -> dict:
    flipped = {"dr": "cr", "cr": "dr"}
    return {
        "entry_id": entry_id,
        "sequence": entry["sequence"] + 1,
        "idempotency_key": entry_id,
        "strategy_id": entry["strategy_id"],
        "reason": "settlement_payout",
        "lines": [
            {
                "account": line["account"],
                "side": flipped[line["side"]],
                "amount_minor": line["amount_minor"],
                "instrument_id": line["instrument_id"],
            }
            for line in entry["lines"]
        ],
    }


def test_o3_correction_must_match_instrument_cause_and_reversal() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_no_settlement.json"
    base = json.loads(path.read_text(encoding="utf-8"))
    base["evidence"].append({
        "kind": "market_void",
        "instrument_id": "ev-no",
        "source_id": "void-no-1",
    })
    settle = next(entry for entry in base["journal"] if entry["entry_id"] == "j-settle")

    named_only = json.loads(json.dumps(base))
    named_only["corrections"].append({
        "correction_id": "c-void",
        "admits_fill_id": "missing-fill",
        "reverses_entry_id": "j-settle",
        "reason": "supersede void",
        "supersedes_source_id": "void-no-1",
        "instrument_id": "ev-no",
        "cause": "settlement_payout",
    })
    assert "void_unreconciled" in _evaluate(named_only)

    wrong_instrument = json.loads(json.dumps(named_only))
    wrong_instrument["journal"].append(_reversal_of(settle, "j-reverse"))
    wrong_instrument["corrections"][0]["instrument_id"] = "ev-other"
    assert "void_unreconciled" in _evaluate(wrong_instrument)

    wrong_cause = json.loads(json.dumps(named_only))
    wrong_cause["journal"].append(_reversal_of(settle, "j-reverse"))
    wrong_cause["corrections"][0]["cause"] = "trade_fee"
    assert "void_unreconciled" in _evaluate(wrong_cause)

    reversed_posting = json.loads(json.dumps(named_only))
    reversed_posting["journal"].append(_reversal_of(settle, "j-reverse"))
    reversed_posting["corrections"][0]["admits_fill_id"] = "f-no"
    assert "void_unreconciled" not in _evaluate(reversed_posting)


def test_o3_void_unwind_direction_and_cause_without_market_state() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["journal"].append({
        "entry_id": "j-relabel",
        "sequence": 6,
        "idempotency_key": "relabel-premium",
        "fill_id": "f-rest",
        "strategy_id": "s-event",
        "reason": "void_unwind",
        "lines": [
            {
                "account": "collateral_reserved",
                "side": "dr",
                "amount_minor": 800000,
                "instrument_id": "ev-yes",
            },
            {
                "account": "cash_available",
                "side": "cr",
                "amount_minor": 800000,
                "instrument_id": "ev-yes",
            },
        ],
    })
    fixture["reported"]["cash_available"] -= 800000
    fixture["reported"]["collateral_reserved"] += 800000
    fixture["reported"]["open_reservations"].append({
        "reservation_id": "reserve-relabel",
        "instrument_id": "ev-yes",
        "amount_minor": 800000,
    })
    assert "balance_mismatch" in _evaluate(fixture)

    caused = json.loads(path.read_text(encoding="utf-8"))
    caused["journal"].append({
        "entry_id": "j-unowned",
        "sequence": 6,
        "idempotency_key": "unowned-unwind",
        "strategy_id": "s-event",
        "reason": "void_unwind",
        "lines": [
            {
                "account": "cash_available",
                "side": "dr",
                "amount_minor": 100,
                "instrument_id": "ev-yes",
            },
            {
                "account": "collateral_reserved",
                "side": "cr",
                "amount_minor": 100,
                "instrument_id": "ev-yes",
            },
        ],
    })
    caused["reported"]["cash_available"] += 100
    caused["reported"]["collateral_reserved"] -= 100
    caused["reported"]["open_reservations"][1]["amount_minor"] -= 100
    assert "balance_mismatch" in _evaluate(caused)

    wrong_direction = json.loads(path.read_text(encoding="utf-8"))
    wrong_direction["journal"].append({
        "entry_id": "j-backward",
        "sequence": 6,
        "idempotency_key": "backward-unwind",
        "order_id": "ord-rest",
        "strategy_id": "s-event",
        "reason": "void_unwind",
        "lines": [
            {
                "account": "collateral_reserved",
                "side": "dr",
                "amount_minor": 100,
                "instrument_id": "ev-yes",
            },
            {
                "account": "cash_available",
                "side": "cr",
                "amount_minor": 100,
                "instrument_id": "ev-yes",
            },
        ],
    })
    wrong_direction["reported"]["cash_available"] -= 100
    wrong_direction["reported"]["collateral_reserved"] += 100
    wrong_direction["reported"]["open_reservations"][1]["amount_minor"] += 100
    assert "balance_mismatch" in _evaluate(wrong_direction)


def test_o3_checkpoint_reservation_instrument_must_match_order() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_void_reconciled.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["instruments"].append(_event_instrument("ev-other", "OTHER-VOID"))
    fixture["orders"].append({
        "order_id": "ord-other",
        "instrument_id": "ev-other",
        "family": "event",
        "side": "buy",
        "qty": 1,
        "state": "resting",
    })
    fixture["checkpoint"]["open_reservations"][0]["order_id"] = "ord-other"
    assert "balance_mismatch" in _evaluate(fixture)


def test_o3_opening_hold_counts_in_order_reserve() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["checkpoint"]["collateral_reserved"] = 100000
    fixture["checkpoint"]["open_reservations"].append({
        "reservation_id": "reserve-open",
        "order_id": "ord-rest",
        "instrument_id": "ev-yes",
        "amount_minor": 100000,
    })
    fixture["reported"]["collateral_reserved"] += 100000
    fixture["reported"]["open_reservations"].append({
        "reservation_id": "reserve-open",
        "instrument_id": "ev-yes",
        "amount_minor": 100000,
    })
    assert "balance_mismatch" in _evaluate(fixture)


def test_o3_checkpoint_hold_funds_fill_premium() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["checkpoint"] = {
        "sequence": 3,
        "applied_keys": ["reserve-1", "fee-1", "reserve-rest-1"],
        "cash_available": 9995100000,
        "collateral_reserved": 4830000,
        "positions": [{"instrument_id": "ev-yes", "qty": 10}],
        "open_reservations": [
            {"reservation_id": "reserve-1", "instrument_id": "ev-yes", "amount_minor": 4000000},
            {
                "reservation_id": "reserve-rest",
                "order_id": "ord-rest",
                "instrument_id": "ev-yes",
                "amount_minor": 830000,
            },
        ],
        "applied_entry_ids": ["j-reserve", "j-fee", "j-reserve-rest"],
    }
    fixture["orders"][0]["checkpoint_filled_qty"] = 10
    fixture["fills"] = [fill for fill in fixture["fills"] if fill["fill_id"] == "f-rest"]
    fixture["journal"] = [entry for entry in fixture["journal"] if entry["entry_id"] == "j-fee-rest"]
    fixture["reported"]["attribution"][0]["fees_minor"] = 30000
    assert _evaluate(fixture) == set()


def test_o3_partial_checkpoint_release_keeps_ending_balance() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_void_reconciled.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["markets"][0]["state"] = "open"
    fixture["evidence"] = []
    fixture["checkpoint"]["open_reservations"][0]["order_id"] = "ord-void"
    release = fixture["journal"][0]
    release["order_id"] = "ord-void"
    for line in release["lines"]:
        line["amount_minor"] = 2000000
    fixture["reported"]["cash_available"] = 9998000000
    fixture["reported"]["collateral_reserved"] = 2000000
    fixture["reported"]["positions"][0]["qty"] = 10
    fixture["reported"]["open_reservations"] = [{
        "reservation_id": "reserve-void",
        "order_id": "ord-void",
        "instrument_id": "ev-void",
        "amount_minor": 2000000,
    }]
    assert _evaluate(fixture) == set()


def test_o3_order_release_after_fill_reconciles_pre_release_notional() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    reserve = next(entry for entry in fixture["journal"] if entry["entry_id"] == "j-reserve")
    del reserve["fill_id"]
    reserve["order_id"] = "ord-yes"
    for line in reserve["lines"]:
        line["amount_minor"] = 4070000
    settle = next(entry for entry in fixture["journal"] if entry["entry_id"] == "j-settle")
    settle["order_id"] = "ord-yes"
    for line in settle["lines"]:
        if line["account"] == "collateral_reserved":
            line["amount_minor"] = 4070000
        elif line["account"] == "realized_pnl":
            line["amount_minor"] = 5930000
    fixture["reported"]["cash_available"] = 10005860000
    fixture["reported"]["attribution"][0]["realized_pnl_minor"] = 5930000
    assert _evaluate(fixture) == set()


def test_o3_duplicate_instrument_id_rejected() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    duplicate = _event_instrument("ev-yes", "HIGHNY-23DEC31-B", outcome="no")
    duplicate["venue_id"] = "venue-other"
    fixture["instruments"].append(duplicate)
    assert "position_divergence" in _evaluate(fixture)


def test_o3_duplicate_reported_position_rejected() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["reported"]["positions"].insert(0, {"instrument_id": "ev-yes", "qty": 5})
    assert "position_divergence" in _evaluate(fixture)


def test_o3_fill_at_settlement_sequence_is_rejected() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["fills"][0]["sequence"] = 3
    assert "settlement_unreconciled" in _evaluate(fixture)


def _checkpoint_funded_resting_fill() -> dict:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["checkpoint"] = {
        "sequence": 3,
        "applied_keys": ["reserve-1", "fee-1", "reserve-rest-1"],
        "cash_available": 9995100000,
        "collateral_reserved": 4830000,
        "positions": [{"instrument_id": "ev-yes", "qty": 10}],
        "open_reservations": [
            {"reservation_id": "reserve-1", "instrument_id": "ev-yes", "amount_minor": 4000000},
            {
                "reservation_id": "reserve-rest",
                "order_id": "ord-rest",
                "instrument_id": "ev-yes",
                "amount_minor": 830000,
            },
        ],
        "applied_entry_ids": ["j-reserve", "j-fee", "j-reserve-rest"],
    }
    fixture["orders"][0]["checkpoint_filled_qty"] = 10
    fixture["fills"] = [fill for fill in fixture["fills"] if fill["fill_id"] == "f-rest"]
    fixture["journal"] = [entry for entry in fixture["journal"] if entry["entry_id"] == "j-fee-rest"]
    fixture["reported"]["attribution"][0]["fees_minor"] = 30000
    return fixture


def test_o3_refunded_hold_does_not_fund_a_later_fill() -> None:
    fixture = _checkpoint_funded_resting_fill()
    assert _evaluate(fixture) == set()
    for fill in fixture["fills"]:
        if fill["fill_id"] == "f-rest":
            fill["sequence"] = 5
    fixture["journal"].append({
        "entry_id": "j-release-rest",
        "sequence": 4,
        "idempotency_key": "release-rest",
        "order_id": "ord-rest",
        "strategy_id": "s-event",
        "reason": "void_unwind",
        "lines": [
            {
                "account": "cash_available",
                "side": "dr",
                "amount_minor": 830000,
                "instrument_id": "ev-yes",
            },
            {
                "account": "collateral_reserved",
                "side": "cr",
                "amount_minor": 830000,
                "instrument_id": "ev-yes",
            },
        ],
    })
    fixture["reported"]["cash_available"] += 830000
    fixture["reported"]["collateral_reserved"] -= 830000
    fixture["reported"]["open_reservations"] = [
        row for row in fixture["reported"]["open_reservations"] if row["amount_minor"] != 830000
    ]
    assert "balance_mismatch" in _evaluate(fixture)


def _two_equal_resting_holds() -> dict:
    path = VALIDATOR.parent / "fixtures" / "event_void_reconciled.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["instruments"].append(_event_instrument("ev-other", "OTHER-HOLD"))
    fixture["orders"] = [
        {
            "order_id": "ord-void",
            "instrument_id": "ev-void",
            "family": "event",
            "side": "buy",
            "qty": 1,
            "state": "resting",
        },
        {
            "order_id": "ord-other",
            "instrument_id": "ev-other",
            "family": "event",
            "side": "buy",
            "qty": 1,
            "state": "resting",
        },
    ]
    fixture["markets"] = [
        {"instrument_id": "ev-void", "state": "open"},
        {"instrument_id": "ev-other", "state": "open"},
    ]
    fixture["evidence"] = []
    fixture["journal"] = []
    fixture["checkpoint"] = {
        "sequence": 1,
        "applied_keys": ["r-void", "r-other"],
        "cash_available": 9996000000,
        "collateral_reserved": 4000000,
        "positions": [],
        "open_reservations": [
            {
                "reservation_id": "r-void",
                "order_id": "ord-void",
                "instrument_id": "ev-void",
                "amount_minor": 2000000,
            },
            {
                "reservation_id": "r-other",
                "order_id": "ord-other",
                "instrument_id": "ev-other",
                "amount_minor": 2000000,
            },
        ],
    }
    fixture["reported"] = {
        "cash_available": 9996000000,
        "collateral_reserved": 4000000,
        "positions": [],
        "open_reservations": [
            {
                "reservation_id": "r-void",
                "order_id": "ord-void",
                "instrument_id": "ev-void",
                "amount_minor": 2000000,
            },
            {
                "reservation_id": "r-other",
                "order_id": "ord-other",
                "instrument_id": "ev-other",
                "amount_minor": 2000000,
            },
        ],
        "attribution": [],
    }
    return fixture


def test_o3_reported_reservation_owner_is_not_forgeable() -> None:
    forged = _two_equal_resting_holds()
    assert _evaluate(forged) == set()
    forged["reported"]["open_reservations"][0]["order_id"] = "ord-ghost"
    assert "balance_mismatch" in _evaluate(forged)

    swapped = _two_equal_resting_holds()
    swapped["reported"]["open_reservations"][0]["order_id"] = "ord-other"
    swapped["reported"]["open_reservations"][1]["order_id"] = "ord-void"
    assert "balance_mismatch" in _evaluate(swapped)


def test_o3_noop_reversal_does_not_hide_the_settlement() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_no_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["evidence"].append({
        "kind": "market_void",
        "instrument_id": "ev-no",
        "source_id": "void-no-1",
    })
    zero_lines = [
        {"account": "cash_available", "side": "dr", "amount_minor": 0, "instrument_id": "ev-no"},
        {"account": "cash_available", "side": "cr", "amount_minor": 0, "instrument_id": "ev-no"},
    ]
    fixture["journal"].append({
        "entry_id": "j-zero-a",
        "sequence": 4,
        "idempotency_key": "zero-a",
        "strategy_id": "s-event",
        "reason": "settlement_payout",
        "lines": zero_lines,
    })
    fixture["journal"].append({
        "entry_id": "j-zero-b",
        "sequence": 5,
        "idempotency_key": "zero-b",
        "strategy_id": "s-event",
        "reason": "settlement_payout",
        "lines": [dict(line) for line in zero_lines],
    })
    fixture["corrections"].append({
        "correction_id": "c-void",
        "admits_fill_id": "f-no",
        "reverses_entry_id": "j-zero-a",
        "reason": "supersede void",
        "supersedes_source_id": "void-no-1",
        "instrument_id": "ev-no",
        "cause": "settlement_payout",
    })
    assert "void_unreconciled" in _evaluate(fixture)

    missing_fill = json.loads(path.read_text(encoding="utf-8"))
    missing_fill["evidence"].append({
        "kind": "market_void",
        "instrument_id": "ev-no",
        "source_id": "void-no-1",
    })
    settle = next(entry for entry in missing_fill["journal"] if entry["entry_id"] == "j-settle")
    missing_fill["journal"].append(_reversal_of(settle, "j-reverse"))
    missing_fill["corrections"].append({
        "correction_id": "c-void",
        "admits_fill_id": "missing-fill",
        "reverses_entry_id": "j-settle",
        "reason": "supersede void",
        "supersedes_source_id": "void-no-1",
        "instrument_id": "ev-no",
        "cause": "settlement_payout",
    })
    assert "void_unreconciled" in _evaluate(missing_fill)


def test_o3_void_unwind_balances_per_instrument() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_void_reconciled.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["markets"][0]["state"] = "open"
    fixture["reported"]["positions"][0]["qty"] = 10
    fixture["instruments"].append(_event_instrument("ev-other", "OTHER-VOID"))
    fixture["evidence"].append({
        "kind": "market_void",
        "instrument_id": "ev-other",
        "source_id": "void-other",
    })
    for line in fixture["journal"][0]["lines"]:
        if line["account"] == "cash_available":
            line["instrument_id"] = "ev-other"
            line["amount_minor"] = 2000000
        elif line["account"] == "collateral_reserved":
            line["amount_minor"] = 2000000
    fixture["reported"]["cash_available"] = 9998000000
    fixture["reported"]["collateral_reserved"] = 2000000
    fixture["reported"]["open_reservations"] = [{
        "reservation_id": "reserve-void",
        "instrument_id": "ev-void",
        "amount_minor": 2000000,
    }]
    assert "balance_mismatch" in _evaluate(fixture)


def _settlement_reversal_fixture() -> dict:
    path = VALIDATOR.parent / "fixtures" / "event_no_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    settle = next(entry for entry in fixture["journal"] if entry["entry_id"] == "j-settle")
    corrected = json.loads(json.dumps(settle))
    corrected["entry_id"] = "j-corrected"
    corrected["sequence"] = settle["sequence"] + 2
    corrected["idempotency_key"] = "settle-corrected"
    fixture["journal"].append(_reversal_of(settle, "j-reverse"))
    fixture["journal"].append(corrected)
    fixture["corrections"].append({
        "correction_id": "c-settle",
        "admits_fill_id": "f-no",
        "reverses_entry_id": "j-settle",
        "reason": "reverse settlement",
        "instrument_id": "ev-no",
        "cause": "settlement_payout",
    })
    return fixture


def test_o3_linked_settlement_reversal_reconciles_alone() -> None:
    fixture = _settlement_reversal_fixture()
    assert _evaluate(fixture) == set()

    partial = _settlement_reversal_fixture()
    reverse = next(entry for entry in partial["journal"] if entry["entry_id"] == "j-reverse")
    for line in reverse["lines"]:
        line["amount_minor"] = 1000000
    assert "settlement_unreconciled" in _evaluate(partial)

    missing = _settlement_reversal_fixture()
    missing["corrections"][0]["reverses_entry_id"] = "missing-entry"
    assert "settlement_unreconciled" in _evaluate(missing)

    noop = _settlement_reversal_fixture()
    zero_lines = [
        {"account": "cash_available", "side": "dr", "amount_minor": 0, "instrument_id": "ev-no"},
        {"account": "cash_available", "side": "cr", "amount_minor": 0, "instrument_id": "ev-no"},
    ]
    noop["journal"].append({
        "entry_id": "j-zero-a",
        "sequence": 6,
        "idempotency_key": "zero-a",
        "strategy_id": "s-event",
        "reason": "settlement_payout",
        "lines": zero_lines,
    })
    noop["journal"].append({
        "entry_id": "j-zero-b",
        "sequence": 7,
        "idempotency_key": "zero-b",
        "strategy_id": "s-event",
        "reason": "settlement_payout",
        "lines": [dict(line) for line in zero_lines],
    })
    noop["corrections"][0]["reverses_entry_id"] = "j-zero-a"
    assert "settlement_unreconciled" in _evaluate(noop)

    wrong = _settlement_reversal_fixture()
    corrected = next(entry for entry in wrong["journal"] if entry["entry_id"] == "j-corrected")
    for line in corrected["lines"]:
        line["amount_minor"] = 1000000
    assert "settlement_unreconciled" in _evaluate(wrong)


def test_o3_duplicate_entry_id_is_not_a_reversal() -> None:
    fixture = _settlement_reversal_fixture()
    fixture["journal"].append({
        "entry_id": "j-settle",
        "sequence": 6,
        "idempotency_key": "extra-payout",
        "strategy_id": "s-event",
        "reason": "settlement_payout",
        "lines": [
            {"account": "cash_available", "side": "dr", "amount_minor": 1000000, "instrument_id": "ev-no"},
            {"account": "cash_available", "side": "cr", "amount_minor": 1000000, "instrument_id": "ev-no"},
        ],
    })
    assert "schema_invalid" in _evaluate(fixture)


def test_o3_reversal_original_must_be_an_ordinary_settlement() -> None:
    fixture = _settlement_reversal_fixture()
    settle = next(entry for entry in fixture["journal"] if entry["entry_id"] == "j-settle")
    reverse = next(entry for entry in fixture["journal"] if entry["entry_id"] == "j-reverse")
    settle_lines = json.loads(json.dumps(settle["lines"]))
    settle["lines"] = json.loads(json.dumps(reverse["lines"]))
    reverse["lines"] = settle_lines
    assert "settlement_unreconciled" in _evaluate(fixture)


def test_o3_reversal_mirror_is_strictly_after_the_original() -> None:
    early = _settlement_reversal_fixture()
    settle = next(entry for entry in early["journal"] if entry["entry_id"] == "j-settle")
    reverse = next(entry for entry in early["journal"] if entry["entry_id"] == "j-reverse")
    corrected = next(entry for entry in early["journal"] if entry["entry_id"] == "j-corrected")
    settle["sequence"] = 4
    reverse["sequence"] = 3
    corrected["sequence"] = 5
    assert "settlement_unreconciled" in _evaluate(early)

    tied = _settlement_reversal_fixture()
    settle = next(entry for entry in tied["journal"] if entry["entry_id"] == "j-settle")
    reverse = next(entry for entry in tied["journal"] if entry["entry_id"] == "j-reverse")
    settle["sequence"] = 4
    reverse["sequence"] = 4
    assert "settlement_unreconciled" in _evaluate(tied)


def test_o3_checkpoint_only_fill_reserve_matches_checkpoint_hold() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["checkpoint"] = {
        "sequence": 3,
        "applied_keys": [],
        "cash_available": 10000000000,
        "collateral_reserved": 4070000,
        "positions": [{"instrument_id": "ev-yes", "qty": 15}],
        "open_reservations": [{
            "reservation_id": "reserve-yes",
            "order_id": "ord-yes",
            "instrument_id": "ev-yes",
            "amount_minor": 4070000,
        }],
    }
    fixture["orders"][0]["checkpoint_filled_qty"] = 10
    fixture["orders"][1]["state"] = "filled"
    fixture["orders"][1]["checkpoint_filled_qty"] = 5
    fixture["fills"] = [fill for fill in fixture["fills"] if fill["fill_id"] == "f-yes"]
    fixture["journal"] = [entry for entry in fixture["journal"] if entry["entry_id"] == "j-reserve-rest"]
    fixture["journal"][0]["sequence"] = 4
    fixture["reported"] = {
        "cash_available": 9999170000,
        "collateral_reserved": 4900000,
        "positions": [{"instrument_id": "ev-yes", "qty": 15}],
        "open_reservations": [
            {
                "reservation_id": "reserve-yes",
                "order_id": "ord-yes",
                "instrument_id": "ev-yes",
                "amount_minor": 4070000,
            },
            {
                "reservation_id": "reserve-rest",
                "instrument_id": "ev-yes",
                "amount_minor": 830000,
            },
        ],
        "attribution": [],
    }
    assert "balance_mismatch" in _evaluate(fixture)


def _partial_refund_fixture(posted: int) -> dict:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    for fill in fixture["fills"]:
        if fill["fill_id"] == "f-rest":
            fill["sequence"] = 5
    for entry in fixture["journal"]:
        if entry["entry_id"] == "j-fee-rest":
            entry["sequence"] = 6
    fixture["journal"].append({
        "entry_id": "j-release-rest",
        "sequence": 4,
        "idempotency_key": "release-rest-part",
        "order_id": "ord-rest",
        "strategy_id": "s-event",
        "reason": "void_unwind",
        "lines": [
            {"account": "cash_available", "side": "dr", "amount_minor": 100000, "instrument_id": "ev-yes"},
            {"account": "collateral_reserved", "side": "cr", "amount_minor": 100000, "instrument_id": "ev-yes"},
        ],
    })
    fixture["journal"].append({
        "entry_id": "j-top-up",
        "sequence": 5,
        "idempotency_key": "top-up-rest",
        "fill_id": "f-rest",
        "strategy_id": "s-event",
        "reason": "reserve_collateral",
        "lines": [
            {"account": "collateral_reserved", "side": "dr", "amount_minor": posted, "instrument_id": "ev-yes"},
            {"account": "cash_available", "side": "cr", "amount_minor": posted, "instrument_id": "ev-yes"},
        ],
    })
    fixture["reported"]["cash_available"] = 9995170000 - posted
    fixture["reported"]["collateral_reserved"] = 4730000 + posted
    fixture["reported"]["open_reservations"][1]["amount_minor"] = 730000
    fixture["reported"]["open_reservations"].append({
        "reservation_id": "reserve-topup",
        "instrument_id": "ev-yes",
        "amount_minor": posted,
    })
    return fixture


def test_o3_partial_refund_posts_only_uncovered_premium() -> None:
    shortfall = _partial_refund_fixture(70000)
    assert _evaluate(shortfall) == set()
    doubled = _partial_refund_fixture(800000)
    assert "balance_mismatch" in _evaluate(doubled)


def test_o3_overfill_guard_includes_checkpoint_filled_qty() -> None:
    path = VALIDATOR.parent / "fixtures" / "equity_partial_then_dividend.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["orders"] = [{
        "order_id": "ord-eq-1",
        "instrument_id": "eq-aapl",
        "family": "equity",
        "side": "buy",
        "qty": 100,
        "state": "cancelled",
        "terminal_sequence": 5,
        "checkpoint_filled_qty": 80,
    }]
    fixture["fills"] = [{
        "fill_id": "f-late",
        "order_id": "ord-eq-1",
        "instrument_id": "eq-aapl",
        "sequence": 4,
        "qty": 30,
        "price_minor": 1000,
        "fee_minor": 1,
        "side": "buy",
    }]
    fixture["journal"] = [{
        "entry_id": "j-late",
        "sequence": 4,
        "idempotency_key": "fill-late",
        "fill_id": "f-late",
        "strategy_id": "s-equity",
        "reason": "fill",
        "lines": [
            {"account": "position_cost", "side": "dr", "amount_minor": 30000, "instrument_id": "eq-aapl"},
            {"account": "fees_expense", "side": "dr", "amount_minor": 1, "instrument_id": "eq-aapl"},
            {"account": "cash_available", "side": "cr", "amount_minor": 30001, "instrument_id": "eq-aapl"},
        ],
    }]
    fixture["corporate_actions"] = []
    fixture["checkpoint"] = {
        "sequence": 2,
        "applied_keys": [],
        "cash_available": 10000000,
        "collateral_reserved": 0,
        "positions": [{"instrument_id": "eq-aapl", "qty": 80}],
        "open_reservations": [],
    }
    fixture["reported"] = {
        "cash_available": 9969999,
        "collateral_reserved": 0,
        "positions": [{"instrument_id": "eq-aapl", "qty": 110}],
        "open_reservations": [],
        "attribution": [{
            "strategy_id": "s-equity",
            "realized_pnl_minor": 0,
            "fees_minor": 1,
        }],
    }
    assert "partial_fill_divergence" in _evaluate(fixture)


def test_o3_event_instrument_requires_market_state_row() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["markets"] = []
    assert "settlement_unreconciled" in _evaluate(fixture)


def test_o3_fill_linked_entry_before_fill_rejected() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["fills"][0]["sequence"] = 2
    assert "position_divergence" in _evaluate(fixture)


def test_o3_duplicate_attribution_row_rejected() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_yes_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(fixture) == set()
    fixture["reported"]["attribution"].insert(0, {
        "strategy_id": "s-event",
        "realized_pnl_minor": 1,
        "fees_minor": 1,
    })
    assert "attribution_mismatch" in _evaluate(fixture)


def _collateral_pair(
    entry_id: str,
    sequence: int,
    key: str,
    reason: str,
    debit: str,
    credit: str,
    amount: int,
    *,
    order_id: str | None = None,
    fill_id: str | None = None,
) -> dict:
    entry = {
        "entry_id": entry_id,
        "sequence": sequence,
        "idempotency_key": key,
        "strategy_id": "s-event",
        "reason": reason,
        "lines": [
            {"account": debit, "side": "dr", "amount_minor": amount, "instrument_id": "ev-yes"},
            {"account": credit, "side": "cr", "amount_minor": amount, "instrument_id": "ev-yes"},
        ],
    }
    if order_id is not None:
        entry["order_id"] = order_id
    if fill_id is not None:
        entry["fill_id"] = fill_id
    return entry


def _two_fill_partial_refund(first: int, second: int) -> dict:
    """Two premiums share one partial hold. The second fill sees the remainder."""
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["orders"] = [{
        "order_id": "ord-two",
        "instrument_id": "ev-yes",
        "family": "event",
        "side": "buy",
        "qty": 2,
        "state": "filled",
    }]
    fixture["fills"] = [
        {
            "fill_id": "f1",
            "order_id": "ord-two",
            "instrument_id": "ev-yes",
            "sequence": 3,
            "qty": 1,
            "price_minor": 400000,
            "fee_minor": 15000,
            "side": "buy",
        },
        {
            "fill_id": "f2",
            "order_id": "ord-two",
            "instrument_id": "ev-yes",
            "sequence": 5,
            "qty": 1,
            "price_minor": 400000,
            "fee_minor": 15000,
            "side": "buy",
        },
    ]
    fixture["journal"] = [
        _collateral_pair(
            "j-reserve", 1, "reserve-two", "reserve_collateral",
            "collateral_reserved", "cash_available", 830000, order_id="ord-two",
        ),
        _collateral_pair(
            "j-release", 2, "release-two", "void_unwind",
            "cash_available", "collateral_reserved", 500000, order_id="ord-two",
        ),
        _collateral_pair(
            "j-f1", 3, "premium-f1", "reserve_collateral",
            "collateral_reserved", "cash_available", first, fill_id="f1",
        ),
        _collateral_pair(
            "j-fee-f1", 4, "fee-f1", "trade_fee",
            "fees_expense", "cash_available", 15000, fill_id="f1",
        ),
        _collateral_pair(
            "j-f2", 5, "premium-f2", "reserve_collateral",
            "collateral_reserved", "cash_available", second, fill_id="f2",
        ),
        _collateral_pair(
            "j-fee-f2", 6, "fee-f2", "trade_fee",
            "fees_expense", "cash_available", 15000, fill_id="f2",
        ),
    ]
    collateral = 330000 + first + second
    fixture["reported"] = {
        "cash_available": 9999640000 - first - second,
        "collateral_reserved": collateral,
        "positions": [{"instrument_id": "ev-yes", "qty": 2}],
        "open_reservations": [
            {
                "reservation_id": "reserve-left",
                "order_id": "ord-two",
                "instrument_id": "ev-yes",
                "amount_minor": 330000,
            },
            {"reservation_id": "reserve-f1", "instrument_id": "ev-yes", "amount_minor": first},
            {"reservation_id": "reserve-f2", "instrument_id": "ev-yes", "amount_minor": second},
        ],
        "attribution": [{
            "strategy_id": "s-event",
            "realized_pnl_minor": 0,
            "fees_minor": 30000,
        }],
    }
    return fixture


def test_o3_partial_refund_hold_is_consumed_across_fills() -> None:
    consumed = _two_fill_partial_refund(70000, 400000)
    assert _evaluate(consumed) == set()
    shared = _two_fill_partial_refund(70000, 70000)
    assert "balance_mismatch" in _evaluate(shared)


def test_o3_unnamed_release_of_owned_hold_is_rejected() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_void_reconciled.json"
    owned = json.loads(path.read_text(encoding="utf-8"))
    owned["checkpoint"]["open_reservations"][0]["order_id"] = "ord-void"
    assert "balance_mismatch" in _evaluate(owned)

    payout = json.loads(path.read_text(encoding="utf-8"))
    payout["checkpoint"]["open_reservations"][0]["order_id"] = "ord-void"
    payout["journal"][0]["reason"] = "settlement_payout"
    assert "balance_mismatch" in _evaluate(payout)

    # A credit sequenced before the reserve still has to leave the hold covered.
    early = json.loads(path.read_text(encoding="utf-8"))
    early["orders"] = [{
        "order_id": "ord-void",
        "instrument_id": "ev-void",
        "family": "event",
        "side": "buy",
        "qty": 1,
        "state": "resting",
    }]
    early["checkpoint"]["collateral_reserved"] = 0
    early["checkpoint"]["cash_available"] = 10000000000
    early["checkpoint"]["positions"] = []
    early["checkpoint"]["open_reservations"] = []
    early["checkpoint"]["applied_entry_ids"] = []
    early["journal"] = [
        {
            "entry_id": "j-credit",
            "sequence": 2,
            "idempotency_key": "credit-1",
            "strategy_id": "s-event",
            "reason": "void_unwind",
            "lines": [
                {
                    "account": "cash_available",
                    "side": "dr",
                    "amount_minor": 100,
                    "instrument_id": "ev-void",
                },
                {
                    "account": "collateral_reserved",
                    "side": "cr",
                    "amount_minor": 100,
                    "instrument_id": "ev-void",
                },
            ],
        },
        {
            "entry_id": "j-reserve-new",
            "sequence": 3,
            "idempotency_key": "reserve-new",
            "order_id": "ord-void",
            "strategy_id": "s-event",
            "reason": "reserve_collateral",
            "lines": [
                {
                    "account": "collateral_reserved",
                    "side": "dr",
                    "amount_minor": 100,
                    "instrument_id": "ev-void",
                },
                {
                    "account": "cash_available",
                    "side": "cr",
                    "amount_minor": 100,
                    "instrument_id": "ev-void",
                },
            ],
        },
    ]
    early["reported"] = {
        "cash_available": 10000000000,
        "collateral_reserved": 0,
        "positions": [{"instrument_id": "ev-void", "qty": 0}],
        "open_reservations": [],
        "attribution": [],
    }
    assert "balance_mismatch" in _evaluate(early)


def _void_supersession(sequence: int, reverses: str, entry_id: str) -> dict:
    path = VALIDATOR.parent / "fixtures" / "event_no_settlement.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["evidence"].append({
        "kind": "market_void",
        "instrument_id": "ev-no",
        "source_id": "void-no-1",
    })
    settle = next(entry for entry in fixture["journal"] if entry["entry_id"] == "j-settle")
    mirror = _reversal_of(settle, entry_id)
    mirror["sequence"] = sequence
    fixture["journal"].append(mirror)
    fixture["corrections"].append({
        "correction_id": "c-void",
        "admits_fill_id": "f-no",
        "reverses_entry_id": reverses,
        "reason": "supersede void",
        "supersedes_source_id": "void-no-1",
        "instrument_id": "ev-no",
        "cause": "settlement_payout",
    })
    return fixture


def test_o3_void_supersession_requires_a_strictly_later_reversal() -> None:
    early = _void_supersession(2, "j-settle", "j-reverse")
    assert "void_unreconciled" in _evaluate(early)
    tied = _void_supersession(3, "j-settle", "j-reverse")
    assert "void_unreconciled" in _evaluate(tied)
    # An earlier collateral debit is not the settlement being reversed.
    backward = _void_supersession(2, "j-backward", "j-backward")
    assert "void_unreconciled" in _evaluate(backward)
    reused = _void_supersession(4, "j-settle", "j-reverse")
    settle = next(entry for entry in reused["journal"] if entry["entry_id"] == "j-settle")
    copy = json.loads(json.dumps(settle))
    copy["sequence"] = 5
    copy["idempotency_key"] = "settle-copy"
    reused["journal"].append(copy)
    assert "void_unreconciled" in _evaluate(reused)


def _retained_checkpoint_fill(
    state: str,
    recorded: int,
    retained: int,
    sequence: int = 2,
) -> dict:
    """A fill at the checkpoint sequence, with the checkpoint quantity under test."""
    path = VALIDATOR.parent / "fixtures" / "event_restart_idempotent.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["markets"] = [{"instrument_id": "ev-yes", "state": "open"}]
    fixture["evidence"] = []
    fixture["journal"] = []
    fixture["corrections"] = []
    order = fixture["orders"][0]
    order["state"] = state
    order["qty"] = 2
    order["checkpoint_filled_qty"] = recorded
    if state in {"cancelled", "expired", "rejected"}:
        order["terminal_sequence"] = 2
    fixture["checkpoint"]["positions"] = (
        [{"instrument_id": "ev-yes", "qty": recorded}] if recorded else []
    )
    fixture["checkpoint"]["collateral_reserved"] = 0
    fixture["checkpoint"]["cash_available"] = 10000000000
    fixture["checkpoint"]["open_reservations"] = []
    fixture["fills"] = [{
        "fill_id": "f-before",
        "order_id": "ord-yes",
        "instrument_id": "ev-yes",
        "sequence": sequence,
        "qty": retained,
        "price_minor": 0,
        "fee_minor": 0,
        "side": "buy",
    }]
    fixture["reported"] = {
        "cash_available": 10000000000,
        "collateral_reserved": 0,
        "positions": [{"instrument_id": "ev-yes", "qty": recorded}],
        "open_reservations": [],
        "attribution": [],
    }
    return fixture


def test_o3_pre_checkpoint_fill_must_be_in_checkpoint_qty() -> None:
    cancelled = _retained_checkpoint_fill("cancelled", 0, 1)
    assert "position_divergence" in _evaluate(cancelled)
    earlier = _retained_checkpoint_fill("cancelled", 0, 1, sequence=1)
    assert "position_divergence" in _evaluate(earlier)
    resting = _retained_checkpoint_fill("resting", 0, 1)
    assert "position_divergence" in _evaluate(resting)
    covered = _retained_checkpoint_fill("cancelled", 1, 1)
    assert _evaluate(covered) == set()
    partial = _retained_checkpoint_fill("cancelled", 1, 2)
    assert "position_divergence" in _evaluate(partial)


def test_o3_open_market_rejects_terminal_evidence() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    settled = json.loads(path.read_text(encoding="utf-8"))
    assert _evaluate(settled) == set()
    settled["evidence"].append({
        "kind": "market_settlement",
        "instrument_id": "ev-yes",
        "source_id": "settle-open",
        "market_result": "yes",
        "long_qty": 12,
        "short_qty": 0,
        "payout_minor": 0,
        "misc_fee_minor": 0,
        "fee_already_deducted_from_payout": False,
        "pre_rounding_minor": 0,
    })
    assert "settlement_unreconciled" in _evaluate(settled)

    voided = json.loads(path.read_text(encoding="utf-8"))
    voided["evidence"].append({
        "kind": "market_void",
        "instrument_id": "ev-yes",
        "source_id": "void-open",
    })
    assert "void_unreconciled" in _evaluate(voided)

    listed = json.loads(path.read_text(encoding="utf-8"))
    listed["markets"][0]["state"] = "listed"
    assert _evaluate(listed) == set()
    listed["evidence"].append({
        "kind": "market_settlement",
        "instrument_id": "ev-yes",
        "source_id": "settle-listed",
    })
    assert "settlement_unreconciled" in _evaluate(listed)

    pending = json.loads(path.read_text(encoding="utf-8"))
    pending["markets"][0]["state"] = "closed_pending_determination"
    pending["evidence"].append({
        "kind": "market_void",
        "instrument_id": "ev-yes",
        "source_id": "void-pending",
    })
    assert "void_unreconciled" in _evaluate(pending)

    determined = json.loads(path.read_text(encoding="utf-8"))
    determined["markets"][0]["state"] = "determined"
    determined["evidence"].append({
        "kind": "market_settlement",
        "instrument_id": "ev-yes",
        "source_id": "settle-determined",
    })
    assert "settlement_unreconciled" in _evaluate(determined)


def _unnamed_release_then_refill() -> dict:
    """Owned hold released unnamed, then a fill reserve restores the ending total."""
    path = VALIDATOR.parent / "fixtures" / "event_void_reconciled.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["checkpoint"]["open_reservations"][0]["order_id"] = "ord-void"
    fixture["orders"].append({
        "order_id": "ord-new",
        "instrument_id": "ev-void",
        "family": "event",
        "side": "buy",
        "qty": 10,
        "state": "filled",
    })
    fixture["fills"] = [{
        "fill_id": "f-new",
        "order_id": "ord-new",
        "instrument_id": "ev-void",
        "sequence": 3,
        "qty": 10,
        "price_minor": 400000,
        "fee_minor": 0,
        "side": "buy",
    }]
    fixture["journal"].append({
        "entry_id": "j-refill",
        "sequence": 3,
        "idempotency_key": "refill-1",
        "fill_id": "f-new",
        "strategy_id": "s-event",
        "reason": "reserve_collateral",
        "lines": [
            {
                "account": "collateral_reserved",
                "side": "dr",
                "amount_minor": 4000000,
                "instrument_id": "ev-void",
            },
            {
                "account": "cash_available",
                "side": "cr",
                "amount_minor": 4000000,
                "instrument_id": "ev-void",
            },
        ],
    })
    fixture["journal"].append({
        "entry_id": "j-named-release",
        "sequence": 4,
        "idempotency_key": "release-void",
        "order_id": "ord-void",
        "strategy_id": "s-event",
        "reason": "void_unwind",
        "lines": [
            {
                "account": "cash_available",
                "side": "dr",
                "amount_minor": 4000000,
                "instrument_id": "ev-void",
            },
            {
                "account": "collateral_reserved",
                "side": "cr",
                "amount_minor": 4000000,
                "instrument_id": "ev-void",
            },
        ],
    })
    return fixture


def test_o3_unnamed_release_is_not_restored_by_a_later_reserve() -> None:
    # Checkpoint names ord-void. The shipped unwind does not. A later fill
    # reserve and a named release put the ending totals back in line.
    released = _unnamed_release_then_refill()
    assert "balance_mismatch" in _evaluate(released)


def _tied_release_and_refill(refill_first: bool) -> dict:
    """Unnamed release and fill reserve share sequence 3."""
    fixture = _unnamed_release_then_refill()
    for entry in fixture["journal"]:
        if entry["entry_id"] in {"j-void", "j-refill"}:
            entry["sequence"] = 3
    if refill_first:
        void = next(entry for entry in fixture["journal"] if entry["entry_id"] == "j-void")
        refill = next(entry for entry in fixture["journal"] if entry["entry_id"] == "j-refill")
        rest = [
            entry for entry in fixture["journal"]
            if entry["entry_id"] not in {"j-void", "j-refill"}
        ]
        fixture["journal"] = [refill, void, *rest]
    return fixture


def test_o3_same_instrument_collateral_tie_is_rejected() -> None:
    assert "balance_mismatch" in _evaluate(_tied_release_and_refill(False))
    assert "balance_mismatch" in _evaluate(_tied_release_and_refill(True))


def _dead_order_activity(
    state: str,
    reserve_sequence: int,
    release_sequence: int,
    terminal_sequence: int,
) -> dict:
    """Unfilled order with a reserve and a later release around its terminal."""
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["orders"].append({
        "order_id": "ord-dead",
        "instrument_id": "ev-yes",
        "family": "event",
        "side": "buy",
        "qty": 1,
        "state": state,
        "terminal_sequence": terminal_sequence,
    })
    fixture["journal"].append({
        "entry_id": "j-dead-reserve",
        "sequence": reserve_sequence,
        "idempotency_key": "reserve-dead",
        "order_id": "ord-dead",
        "strategy_id": "s-event",
        "reason": "reserve_collateral",
        "lines": [
            {
                "account": "collateral_reserved",
                "side": "dr",
                "amount_minor": 100000,
                "instrument_id": "ev-yes",
            },
            {
                "account": "cash_available",
                "side": "cr",
                "amount_minor": 100000,
                "instrument_id": "ev-yes",
            },
        ],
    })
    fixture["journal"].append({
        "entry_id": "j-dead-release",
        "sequence": release_sequence,
        "idempotency_key": "release-dead",
        "order_id": "ord-dead",
        "strategy_id": "s-event",
        "reason": "void_unwind",
        "lines": [
            {
                "account": "cash_available",
                "side": "dr",
                "amount_minor": 100000,
                "instrument_id": "ev-yes",
            },
            {
                "account": "collateral_reserved",
                "side": "cr",
                "amount_minor": 100000,
                "instrument_id": "ev-yes",
            },
        ],
    })
    return fixture


def test_o3_reservation_after_terminal_is_rejected() -> None:
    for state in ("cancelled", "expired_unfilled", "rejected_insufficient_collateral"):
        late = _dead_order_activity(state, 6, 7, 2)
        assert "position_divergence" in _evaluate(late)
    tied = _dead_order_activity("cancelled", 6, 7, 6)
    assert "position_divergence" in _evaluate(tied)
    # The reserve is open before the terminal. The release after it stays valid.
    earlier = _dead_order_activity("cancelled", 6, 8, 7)
    assert _evaluate(earlier) == set()


def _open_checkpoint_book(qty: int, recorded: int, state: str) -> dict:
    """Open market whose only quantity is the checkpoint, with no fill rows."""
    path = VALIDATOR.parent / "fixtures" / "event_restart_idempotent.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["markets"] = [{"instrument_id": "ev-yes", "state": "open"}]
    fixture["evidence"] = []
    fixture["journal"] = []
    fixture["corrections"] = []
    fixture["fills"] = []
    order = fixture["orders"][0]
    order["state"] = state
    order["qty"] = qty
    order["checkpoint_filled_qty"] = recorded
    if state in {"cancelled", "expired", "rejected", "trade_cancelled"}:
        order["terminal_sequence"] = 2
    fixture["checkpoint"]["positions"] = (
        [{"instrument_id": "ev-yes", "qty": recorded}] if recorded else []
    )
    fixture["checkpoint"]["collateral_reserved"] = 0
    fixture["checkpoint"]["cash_available"] = 10000000000
    fixture["checkpoint"]["open_reservations"] = []
    fixture["reported"] = {
        "cash_available": 10000000000,
        "collateral_reserved": 0,
        "positions": (
            [{"instrument_id": "ev-yes", "qty": recorded}] if recorded else []
        ),
        "open_reservations": [],
        "attribution": [],
    }
    return fixture


def test_o3_full_execution_must_be_filled() -> None:
    path = VALIDATOR.parent / "fixtures" / "equity_partial_then_dividend.json"
    assert _evaluate(json.loads(path.read_text(encoding="utf-8"))) == set()
    for state in ("cancelled", "expired", "rejected", "canary_breach", "submitted"):
        booked = json.loads(path.read_text(encoding="utf-8"))
        booked["orders"][0]["state"] = state
        booked["orders"][0]["terminal_sequence"] = 5
        assert "partial_fill_divergence" in _evaluate(booked)
    disguised = json.loads(path.read_text(encoding="utf-8"))
    disguised["orders"][0]["state"] = "cancelled"
    disguised["orders"][0]["terminal_state"] = "filled"
    disguised["orders"][0]["terminal_sequence"] = 5
    assert "partial_fill_divergence" in _evaluate(disguised)
    assert "partial_fill_divergence" in _evaluate(_open_checkpoint_book(2, 3, "cancelled"))
    assert "partial_fill_divergence" in _evaluate(_open_checkpoint_book(2, 2, "resting"))
    assert "partial_fill_divergence" in _evaluate(
        _open_checkpoint_book(2, 2, "trade_cancelled")
    )
    assert _evaluate(_open_checkpoint_book(2, 1, "cancelled")) == set()
    assert _evaluate(_open_checkpoint_book(2, 0, "cancelled")) == set()


def test_o3_determined_result_is_not_a_settlement_record() -> None:
    text = (VALIDATOR.parent / "CONTRACT.md").read_text(encoding="utf-8")
    lifecycle = " ".join(
        text.split("### Event-contract market", 1)[1].split("## Journal", 1)[0].split()
    )
    resolution = " ".join(
        text.split("## Resolution, void, and correction", 1)[1]
        .split("## Reconciliation and restart", 1)[0]
        .split()
    )
    phrase = "not a `market_settlement` record"
    kind = "no separate determination-evidence kind"
    assert "plus retained settlement evidence" not in lifecycle
    assert "carries a result" not in lifecycle
    assert "does not store" in lifecycle
    assert phrase in lifecycle
    assert kind in lifecycle
    assert "does not store" in resolution
    assert phrase in resolution
    assert kind in resolution
    schema = json.loads((VALIDATOR.parent / "schemas" / "market.schema.json").read_text(encoding="utf-8"))
    assert "result" not in schema["properties"]
    assert set(schema["required"]) == {"instrument_id", "state"}
    resting = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    determined = json.loads(resting.read_text(encoding="utf-8"))
    determined["markets"][0]["state"] = "determined"
    assert _evaluate(determined) == set()


def _split_collateral_line(entry: dict) -> None:
    """One collateral amount posted as two lines of the same entry."""
    lines = entry["lines"]
    index = next(i for i, line in enumerate(lines) if line["account"] == "collateral_reserved")
    line = lines[index]
    half = line["amount_minor"] // 2
    other = dict(line)
    other["amount_minor"] = line["amount_minor"] - half
    line["amount_minor"] = half
    lines.insert(index + 1, other)


def test_o3_split_collateral_lines_in_one_entry_reconcile() -> None:
    resting = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    reserve = json.loads(resting.read_text(encoding="utf-8"))
    _split_collateral_line(next(entry for entry in reserve["journal"] if entry["entry_id"] == "j-reserve"))
    assert _evaluate(reserve) == set()
    owned = json.loads(resting.read_text(encoding="utf-8"))
    _split_collateral_line(next(entry for entry in owned["journal"] if entry["entry_id"] == "j-reserve-rest"))
    assert _evaluate(owned) == set()
    settlement = json.loads(
        (VALIDATOR.parent / "fixtures" / "event_yes_settlement.json").read_text(encoding="utf-8")
    )
    _split_collateral_line(next(entry for entry in settlement["journal"] if entry["reason"] == "settlement_payout"))
    assert _evaluate(settlement) == set()
    unwind = json.loads(
        (VALIDATOR.parent / "fixtures" / "event_void_reconciled.json").read_text(encoding="utf-8")
    )
    _split_collateral_line(unwind["journal"][0])
    assert _evaluate(unwind) == set()


def test_o3_terminal_order_requires_terminal_sequence() -> None:
    for state in (
        "cancelled",
        "expired_unfilled",
        "rejected_insufficient_collateral",
        "trade_cancelled",
        "void_ab_initio",
    ):
        late = _dead_order_activity(state, 6, 7, 2)
        del late["orders"][-1]["terminal_sequence"]
        assert "schema_invalid" in _evaluate(late)
    checkpointed = _open_checkpoint_book(2, 0, "cancelled")
    del checkpointed["orders"][0]["terminal_sequence"]
    assert "schema_invalid" in _evaluate(checkpointed)
    path = VALIDATOR.parent / "fixtures" / "equity_partial_then_dividend.json"
    for state in ("cancelled", "expired", "rejected", "canary_breach"):
        equity = json.loads(path.read_text(encoding="utf-8"))
        equity["orders"] = [{
            "order_id": "ord-eq-1",
            "instrument_id": "eq-aapl",
            "family": "equity",
            "side": "buy",
            "qty": 1,
            "state": state,
        }]
        equity["fills"] = []
        equity["journal"] = []
        equity["corporate_actions"] = []
        equity["reported"] = {
            "cash_available": equity["checkpoint"]["cash_available"],
            "collateral_reserved": 0,
            "positions": [],
            "open_reservations": [],
            "attribution": [],
        }
        assert "schema_invalid" in _evaluate(equity)
        equity["orders"][0]["terminal_sequence"] = 1
        assert _evaluate(equity) == set()


def test_o3_corrected_market_requires_a_correction() -> None:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    bare = json.loads(path.read_text(encoding="utf-8"))
    bare["markets"][0]["state"] = "corrected"
    assert "settlement_unreconciled" in _evaluate(bare)
    other = json.loads(path.read_text(encoding="utf-8"))
    other["markets"][0]["state"] = "corrected"
    other["corrections"].append({
        "correction_id": "c-other",
        "admits_fill_id": "f-yes",
        "reverses_entry_id": "j-reserve",
        "reason": "correct another book",
        "instrument_id": "ev-other",
    })
    assert "settlement_unreconciled" in _evaluate(other)
    missing = json.loads(path.read_text(encoding="utf-8"))
    missing["markets"][0]["state"] = "corrected"
    missing["corrections"].append({
        "correction_id": "c-missing",
        "admits_fill_id": "f-yes",
        "reverses_entry_id": "j-ghost",
        "reason": "names a missing entry",
        "instrument_id": "ev-yes",
    })
    assert "settlement_unreconciled" in _evaluate(missing)
    metadata = json.loads(path.read_text(encoding="utf-8"))
    _mark_corrected(metadata)
    assert "settlement_unreconciled" in _evaluate(metadata)


def _resting_book() -> dict:
    path = VALIDATOR.parent / "fixtures" / "event_resting_order_reservation.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _post_correction_of(
    fixture: dict,
    reversal_sequence: int,
    replacement_sequence: int,
) -> None:
    """Append an exact reversal of j-reserve and a replacement posting."""
    original = next(entry for entry in fixture["journal"] if entry["entry_id"] == "j-reserve")
    flipped = {"dr": "cr", "cr": "dr"}

    def lines(reverse: bool) -> list[dict]:
        return [
            {
                "account": line["account"],
                "side": flipped[line["side"]] if reverse else line["side"],
                "amount_minor": line["amount_minor"],
                "instrument_id": line["instrument_id"],
            }
            for line in original["lines"]
        ]

    fixture["journal"].append({
        "entry_id": "j-rev-yes",
        "sequence": reversal_sequence,
        "idempotency_key": "reverse-yes",
        "fill_id": "f-yes",
        "strategy_id": original["strategy_id"],
        "reason": original["reason"],
        "lines": lines(True),
    })
    fixture["journal"].append({
        "entry_id": "j-replace-yes",
        "sequence": replacement_sequence,
        "idempotency_key": "replace-yes",
        "fill_id": "f-yes",
        "strategy_id": original["strategy_id"],
        "reason": original["reason"],
        "lines": lines(False),
    })


def _mark_corrected(fixture: dict, **extra: object) -> None:
    fixture["markets"][0]["state"] = "corrected"
    correction = {
        "correction_id": "c-yes",
        "admits_fill_id": "f-yes",
        "reverses_entry_id": "j-reserve",
        "reason": "append-only correction",
        "instrument_id": "ev-yes",
        "cause": "reserve_collateral",
    }
    correction.update(extra)
    fixture["corrections"].append(correction)


def _other_instrument_reserve(fixture: dict) -> None:
    """A resting reservation that posts only to ev-other."""
    fixture["instruments"].append({
        "instrument_id": "ev-other",
        "family": "event_contract",
        "venue_id": "venue-event-candidate",
        "venue_symbol": "OTHER-23DEC31",
        "currency": "USD",
        "unit": "contract",
        "contract_style": "binary",
        "outcome_side": "yes",
        "collateral_model": "fully_collateralized",
    })
    fixture["orders"].append({
        "order_id": "ord-other",
        "instrument_id": "ev-other",
        "family": "event",
        "side": "buy",
        "qty": 1,
        "state": "resting",
    })
    fixture["markets"].append({"instrument_id": "ev-other", "state": "open"})
    fixture["journal"].append({
        "entry_id": "j-other",
        "sequence": 8,
        "idempotency_key": "reserve-other",
        "order_id": "ord-other",
        "strategy_id": "s-event",
        "reason": "reserve_collateral",
        "lines": [
            {
                "account": "collateral_reserved",
                "side": "dr",
                "amount_minor": 100000,
                "instrument_id": "ev-other",
            },
            {
                "account": "cash_available",
                "side": "cr",
                "amount_minor": 100000,
                "instrument_id": "ev-other",
            },
        ],
    })
    fixture["reported"]["cash_available"] -= 100000
    fixture["reported"]["collateral_reserved"] += 100000
    fixture["reported"]["open_reservations"].append({
        "reservation_id": "reserve-other",
        "instrument_id": "ev-other",
        "amount_minor": 100000,
    })


def test_o3_corrected_market_requires_posted_reversal_and_replacement() -> None:
    posted = _resting_book()
    _post_correction_of(posted, 6, 7)
    _mark_corrected(posted)
    assert _evaluate(posted) == set()
    false_cause = _resting_book()
    _post_correction_of(false_cause, 6, 7)
    _mark_corrected(false_cause, cause="settlement_payout")
    assert "settlement_unreconciled" in _evaluate(false_cause)
    reversed_only = _resting_book()
    _post_correction_of(reversed_only, 6, 7)
    reversed_only["journal"] = [
        entry for entry in reversed_only["journal"] if entry["entry_id"] != "j-replace-yes"
    ]
    _mark_corrected(reversed_only)
    assert "settlement_unreconciled" in _evaluate(reversed_only)
    swapped = _resting_book()
    _post_correction_of(swapped, 7, 6)
    _mark_corrected(swapped)
    assert "settlement_unreconciled" in _evaluate(swapped)
    partial_mirror = _resting_book()
    _post_correction_of(partial_mirror, 6, 7)
    for entry in partial_mirror["journal"]:
        if entry["entry_id"] == "j-rev-yes":
            for line in entry["lines"]:
                line["amount_minor"] //= 2
    _mark_corrected(partial_mirror)
    assert "settlement_unreconciled" in _evaluate(partial_mirror)
    bare_swap = _resting_book()
    reserve = next(entry for entry in bare_swap["journal"] if entry["entry_id"] == "j-reserve")
    flipped = {"dr": "cr", "cr": "dr"}
    for line in reserve["lines"]:
        line["side"] = flipped[line["side"]]
    assert "balance_mismatch" in _evaluate(bare_swap)


def test_o3_corrected_reversal_must_post_the_market_instrument() -> None:
    crossed = _resting_book()
    _other_instrument_reserve(crossed)
    _mark_corrected(crossed, reverses_entry_id="j-other")
    assert "settlement_unreconciled" in _evaluate(crossed)
    checkpoint = _resting_book()
    checkpoint["checkpoint"]["applied_entry_ids"] = ["j-untyped"]
    _mark_corrected(checkpoint, reverses_entry_id="j-untyped")
    assert "settlement_unreconciled" in _evaluate(checkpoint)


def test_o3_either_order_field_requires_terminal_sequence() -> None:
    masked = _dead_order_activity("cancelled", 6, 7, 2)
    masked["orders"][-1]["terminal_state"] = "resting"
    del masked["orders"][-1]["terminal_sequence"]
    assert "schema_invalid" in _evaluate(masked)
    partial = _resting_book()
    rest = next(order for order in partial["orders"] if order["order_id"] == "ord-rest")
    rest["state"] = "cancelled"
    rest["terminal_state"] = "partially_filled"
    assert "schema_invalid" in _evaluate(partial)
    rest["terminal_sequence"] = 4
    assert _evaluate(partial) == set()
    rest["terminal_sequence"] = 3
    assert "late_fill_divergence" in _evaluate(partial)
    late = _resting_book()
    late["orders"].append({
        "order_id": "ord-late",
        "instrument_id": "ev-yes",
        "family": "event",
        "side": "buy",
        "qty": 1,
        "state": "cancelled",
        "terminal_state": "resting",
        "terminal_sequence": 2,
    })
    late["fills"].append({
        "fill_id": "f-late",
        "order_id": "ord-late",
        "instrument_id": "ev-yes",
        "sequence": 8,
        "qty": 1,
        "price_minor": 400000,
        "fee_minor": 0,
        "side": "buy",
    })
    assert "late_fill_divergence" in _evaluate(late)
    held = _dead_order_activity("cancelled", 6, 7, 7)
    held["orders"][-1]["terminal_state"] = "resting"
    held["journal"] = [
        entry for entry in held["journal"] if entry["entry_id"] != "j-dead-release"
    ]
    held["reported"]["cash_available"] -= 100000
    held["reported"]["collateral_reserved"] += 100000
    held["reported"]["open_reservations"].append({
        "reservation_id": "reserve-dead",
        "instrument_id": "ev-yes",
        "amount_minor": 100000,
    })
    assert "balance_mismatch" in _evaluate(held)
    released = _dead_order_activity("cancelled", 6, 8, 7)
    released["orders"][-1]["terminal_state"] = "resting"
    assert _evaluate(released) == set()
    path = VALIDATOR.parent / "fixtures" / "equity_partial_then_dividend.json"
    equity = json.loads(path.read_text(encoding="utf-8"))
    equity["orders"] = [{
        "order_id": "ord-eq-1",
        "instrument_id": "eq-aapl",
        "family": "equity",
        "side": "buy",
        "qty": 1,
        "state": "cancelled",
        "terminal_state": "partially_filled",
    }]
    equity["fills"] = []
    equity["journal"] = []
    equity["corporate_actions"] = []
    equity["reported"] = {
        "cash_available": equity["checkpoint"]["cash_available"],
        "collateral_reserved": 0,
        "positions": [],
        "open_reservations": [],
        "attribution": [],
    }
    assert "schema_invalid" in _evaluate(equity)
