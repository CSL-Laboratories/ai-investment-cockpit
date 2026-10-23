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
    fixture["orders"].append({
        "order_id": "ord-x",
        "instrument_id": "ev-two",
        "family": "event",
        "side": "buy",
        "qty": 3,
        "state": "trade_cancelled",
        "terminal_sequence": 4,
    })
    fixture["fills"].append({
        "fill_id": "f-late",
        "order_id": "ord-x",
        "instrument_id": "ev-two",
        "sequence": 5,
        "qty": 3,
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
            {"account": "collateral_reserved", "side": "dr", "amount_minor": 1200000, "instrument_id": "ev-two"},
            {"account": "cash_available", "side": "cr", "amount_minor": 1200000, "instrument_id": "ev-two"},
        ],
    })
    fixture["corrections"].append({
        "correction_id": "c-late",
        "admits_fill_id": "f-late",
        "reverses_entry_id": "j-reserve",
        "reason": "admit late fill",
    })
    fixture["reported"]["cash_available"] -= 1200000
    fixture["reported"]["collateral_reserved"] += 1200000
    fixture["reported"]["positions"].append({"instrument_id": "ev-two", "qty": 3})
    fixture["reported"]["open_reservations"].append({
        "reservation_id": "reserve-late",
        "instrument_id": "ev-two",
        "amount_minor": 1200000,
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
    fixture["orders"].append({
        "order_id": "ord-rest",
        "instrument_id": "ev-two",
        "family": "event",
        "side": "buy",
        "qty": 5,
        "state": "resting",
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
    fixture["markets"] = []
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
