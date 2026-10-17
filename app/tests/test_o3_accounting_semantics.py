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
