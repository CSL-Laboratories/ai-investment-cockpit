#!/usr/bin/env python3
"""Provider-free validator for the CSL-215 O3 candidate.

This is a candidate-contract check, not a runtime ledger and not a broker.
It reads only the files next to this script. It does not open a socket, import
a provider SDK, or read credentials. A clean run means the hash-pinned fixtures
behaved as specified. It does not promote maturity past ``researched`` and it
does not freeze the contract.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCHEMA_DIR = ROOT / "schemas"

EQUITY_STATES = frozenset({
    "proposed",
    "approved",
    "submitted",
    "partially_filled",
    "filled",
    "cancelled",
    "expired",
    "rejected",
    "canary_breach",
})
EVENT_STATES = frozenset({
    "pending_collateral_check",
    "resting",
    "partially_filled",
    "filled",
    "cancelled",
    "rejected_insufficient_collateral",
    "expired_unfilled",
    "trade_cancelled",
    "void_ab_initio",
})
LATE_TERMINALS = frozenset({
    "cancelled",
    "expired",
    "rejected",
    "canary_breach",
    "rejected_insufficient_collateral",
    "expired_unfilled",
    "trade_cancelled",
    "void_ab_initio",
})
FILL_DEFECTS = frozenset({
    "duplicate_fill_divergence",
    "late_fill_divergence",
    "partial_fill_divergence",
})
NOT_CLAIMED = (
    "human_decided",
    "contract_frozen",
    "prototyped",
    "implemented",
    "provider_free_verified",
    "demo_or_paper_qualified",
    "shadow_qualified",
    "limited_live_qualified",
    "release_qualified",
)


class CandidateError(Exception):
    pass


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _int(value: object) -> int | None:
    if type(value) is int:
        return value
    return None


def _schema_validators() -> dict[str, object]:
    import jsonschema
    from jsonschema import Draft202012Validator

    validators: dict[str, object] = {}
    for name in (
        "fixture",
        "instrument",
        "order",
        "fill",
        "market",
        "journal_entry",
        "evidence",
        "corporate_action",
        "correction",
    ):
        schema = _load(SCHEMA_DIR / f"{name}.schema.json")
        Draft202012Validator.check_schema(schema)
        validators[name] = jsonschema.Draft202012Validator(schema)
    return validators


def _schema_ok(validators: dict[str, object], fixture: dict) -> bool:
    if not validators["fixture"].is_valid(fixture):
        return False
    arrays = (
        ("instrument", "instruments"),
        ("order", "orders"),
        ("fill", "fills"),
        ("market", "markets"),
        ("journal_entry", "journal"),
        ("evidence", "evidence"),
        ("corporate_action", "corporate_actions"),
        ("correction", "corrections"),
    )
    for schema_name, field in arrays:
        validator = validators[schema_name]
        for item in fixture[field]:
            if not validator.is_valid(item):
                return False
    return True


def _line_amount(line: dict, failures: set[str]) -> int:
    amount = _int(line.get("amount_minor"))
    if amount is None or amount < 0:
        failures.add("schema_invalid")
        return 0
    return amount


def evaluate_semantics(fixture: dict) -> set[str]:
    """Return accounting failure codes. Empty means the fixture is consistent."""
    failures: set[str] = set()
    book = fixture["book"]
    book_id = book["book_id"]
    scale = _int(book.get("scale"))
    if book.get("currency") != "USD" or scale is None:
        failures.add("scale_mismatch")
    elif book_id == "equity" and scale != 2:
        failures.add("scale_mismatch")
    elif book_id == "event" and scale != 6:
        failures.add("scale_mismatch")

    known_instruments = set()
    for instrument in fixture["instruments"]:
        known_instruments.add(instrument["instrument_id"])
        event_only = ("contract_style", "outcome_side", "collateral_model")
        if instrument["family"] == "equity_share":
            if instrument["unit"] != "share" or any(key in instrument for key in event_only):
                failures.add("family_crossed")
        elif instrument["family"] == "event_contract":
            if instrument["unit"] != "contract" or any(key not in instrument for key in event_only):
                failures.add("family_crossed")
            elif instrument["collateral_model"] != "fully_collateralized":
                failures.add("family_crossed")
        else:
            failures.add("family_crossed")
        if book_id == "equity" and instrument["family"] != "equity_share":
            failures.add("family_crossed")
        if book_id == "event" and instrument["family"] != "event_contract":
            failures.add("family_crossed")

    if book_id == "equity" and fixture["markets"]:
        failures.add("family_crossed")
    if book_id == "event" and fixture["corporate_actions"]:
        failures.add("family_crossed")

    orders = {}
    for order in fixture["orders"]:
        orders[order["order_id"]] = order
        if order["family"] != book_id:
            failures.add("family_crossed")
        allowed = EQUITY_STATES if book_id == "equity" else EVENT_STATES
        if order["state"] not in allowed:
            failures.add("family_crossed")
        terminal = order.get("terminal_state")
        if terminal is not None and terminal not in allowed:
            failures.add("family_crossed")

    for action in fixture["corporate_actions"]:
        if action["kind"] != "cash_dividend":
            failures.add("corporate_action_unsupported")

    admitted = {item["admits_fill_id"] for item in fixture["corrections"]}
    seen_fills: set[str] = set()
    accepted_qty: dict[str, int] = {}
    checkpoint = fixture["checkpoint"]
    derived = {row["instrument_id"]: row["qty"] for row in checkpoint["positions"]}
    applied_fill_fees = 0

    for fill in sorted(fixture["fills"], key=lambda item: item["sequence"]):
        if fill["order_id"] not in orders:
            failures.add("position_divergence")
            continue
        order = orders[fill["order_id"]]
        if fill["instrument_id"] != order["instrument_id"]:
            failures.add("position_divergence")
            continue
        if fill["fill_id"] in seen_fills:
            failures.add("duplicate_fill_divergence")
            continue
        seen_fills.add(fill["fill_id"])
        terminal = order.get("terminal_state")
        terminal_sequence = order.get("terminal_sequence")
        late = (
            terminal in LATE_TERMINALS
            and terminal_sequence is not None
            and fill["sequence"] > terminal_sequence
            and fill["fill_id"] not in admitted
        )
        if late:
            failures.add("late_fill_divergence")
            continue
        filled = accepted_qty.get(order["order_id"], 0)
        if filled + fill["qty"] > order["qty"]:
            failures.add("partial_fill_divergence")
            continue
        accepted_qty[order["order_id"]] = filled + fill["qty"]
        derived[fill["instrument_id"]] = derived.get(fill["instrument_id"], 0) + fill["qty"]
        applied_fill_fees += fill["fee_minor"]

    pre_settlement = dict(derived)
    for market in fixture["markets"]:
        if market["state"] != "settled":
            continue
        instrument_id = market["instrument_id"]
        evidence = [
            item
            for item in fixture["evidence"]
            if item["kind"] == "market_settlement" and item["instrument_id"] == instrument_id
        ]
        if len(evidence) != 1:
            failures.add("settlement_unreconciled")
            continue
        record = evidence[0]
        matches = (
            record["long_qty"] == pre_settlement.get(instrument_id, 0)
            and record["short_qty"] == 0
            and record["market_result"] in {"yes", "no", "scalar"}
        )
        if not matches:
            failures.add("settlement_unreconciled")
            continue
        if record["misc_fee_minor"] == 0 and record["pre_rounding_minor"] != record["payout_minor"]:
            failures.add("settlement_unreconciled")
        if record["fee_already_deducted_from_payout"]:
            if record["payout_minor"] + record["misc_fee_minor"] != record["pre_rounding_minor"]:
                failures.add("fee_double_count")
        elif record["misc_fee_minor"] != 0 and record["pre_rounding_minor"] != record["payout_minor"]:
            failures.add("settlement_unreconciled")
        derived[instrument_id] = 0

    voided = {market["instrument_id"] for market in fixture["markets"] if market["state"] == "voided"}
    reported_positions = {row["instrument_id"]: row["qty"] for row in fixture["reported"]["positions"]}
    for instrument_id in set(reported_positions) | set(derived):
        if instrument_id not in known_instruments:
            failures.add("position_divergence")
            continue
        if instrument_id in voided:
            continue
        reported_qty = reported_positions.get(instrument_id, 0)
        got_qty = derived.get(instrument_id, 0)
        if reported_qty == got_qty:
            continue
        if failures & FILL_DEFECTS:
            continue
        underfilled = any(
            accepted_qty.get(order["order_id"], 0) < order["qty"]
            for order in fixture["orders"]
            if order["instrument_id"] == instrument_id
        )
        if underfilled and reported_qty > got_qty:
            failures.add("partial_fill_divergence")
        else:
            failures.add("position_divergence")

    for market in fixture["markets"]:
        if market["state"] != "voided":
            continue
        instrument_id = market["instrument_id"]
        has_void = any(
            item["kind"] == "market_void" and item["instrument_id"] == instrument_id
            for item in fixture["evidence"]
        )
        has_settlement = any(
            item["kind"] == "market_settlement" and item["instrument_id"] == instrument_id
            for item in fixture["evidence"]
        )
        has_payout = any(entry["reason"] == "settlement_payout" for entry in fixture["journal"])
        if (
            not has_void
            or has_settlement
            or has_payout
            or reported_positions.get(instrument_id, 0) != 0
            or fixture["reported"]["collateral_reserved"] != 0
        ):
            failures.add("void_unreconciled")

    cash = checkpoint["cash_available"]
    collateral = checkpoint["collateral_reserved"]
    seen_keys = set(checkpoint["applied_keys"])
    floor = checkpoint["sequence"]
    attribution: dict[str, dict[str, int]] = {}
    replay_detected = False
    journal_fees = 0
    corporate_cash = 0
    settlement_cash_dr = 0
    settlement_collateral_cr = 0
    collateral_before_settlement: int | None = None
    fee_on_net_payout = False

    for entry in sorted(fixture["journal"], key=lambda item: item["sequence"]):
        debits = 0
        credits = 0
        for line in entry["lines"]:
            amount = _line_amount(line, failures)
            if line["side"] == "dr":
                debits += amount
            elif line["side"] == "cr":
                credits += amount
            else:
                failures.add("schema_invalid")
            if book_id == "equity" and line["account"] == "collateral_reserved":
                failures.add("family_crossed")
        if debits != credits:
            failures.add("unbalanced_entry")
        replay = entry["sequence"] <= floor or entry["idempotency_key"] in seen_keys
        if replay:
            replay_detected = True
            if book_id == "event":
                failures.add("collateral_double_count")
            else:
                failures.add("restart_replay")
            continue
        seen_keys.add(entry["idempotency_key"])
        strategy_id = entry["strategy_id"]
        if entry["reason"] == "settlement_payout" and collateral_before_settlement is None:
            collateral_before_settlement = collateral
        for line in entry["lines"]:
            amount = _line_amount(line, failures)
            account = line["account"]
            side = line["side"]
            if account == "cash_available":
                cash += amount if side == "dr" else -amount
                if entry["reason"] == "corporate_action" and side == "dr":
                    corporate_cash += amount
                if entry["reason"] == "settlement_payout" and side == "dr":
                    settlement_cash_dr += amount
            elif account == "collateral_reserved":
                collateral += amount if side == "dr" else -amount
                if entry["reason"] == "settlement_payout" and side == "cr":
                    settlement_collateral_cr += amount
            elif account == "realized_pnl":
                delta = amount if side == "cr" else -amount
                bucket = attribution.setdefault(strategy_id, {"realized_pnl_minor": 0, "fees_minor": 0})
                bucket["realized_pnl_minor"] += delta
            elif account == "fees_expense":
                delta = amount if side == "dr" else -amount
                journal_fees += delta
                bucket = attribution.setdefault(strategy_id, {"realized_pnl_minor": 0, "fees_minor": 0})
                bucket["fees_minor"] += delta
                if entry["reason"] == "settlement_payout":
                    fee_on_net_payout = True
            elif account != "position_cost":
                failures.add("schema_invalid")

    if not (failures & FILL_DEFECTS) and journal_fees != applied_fill_fees:
        failures.add("fee_mismatch")

    expected_dividend = sum(
        action["cash_minor"] for action in fixture["corporate_actions"] if action["kind"] == "cash_dividend"
    )
    if corporate_cash != expected_dividend:
        failures.add("corporate_action_mismatch")

    settled = [market for market in fixture["markets"] if market["state"] == "settled"]
    if settled:
        instrument_id = settled[0]["instrument_id"]
        evidence = [
            item
            for item in fixture["evidence"]
            if item["kind"] == "market_settlement" and item["instrument_id"] == instrument_id
        ]
        if len(evidence) == 1:
            payout = evidence[0]["payout_minor"]
            if settlement_cash_dr != payout:
                failures.add("settlement_unreconciled")
            if (
                collateral_before_settlement is None
                or settlement_collateral_cr != collateral_before_settlement
            ):
                failures.add("settlement_unreconciled")
            if evidence[0]["fee_already_deducted_from_payout"] and fee_on_net_payout:
                failures.add("fee_double_count")

    reported = fixture["reported"]
    cash_differs = cash != reported["cash_available"]
    collateral_differs = collateral != reported["collateral_reserved"]
    if cash_differs or collateral_differs:
        if book_id == "event" and (replay_detected or collateral_differs):
            failures.add("collateral_double_count")
        else:
            failures.add("balance_mismatch")

    if book_id == "equity" and (
        reported["collateral_reserved"] != 0 or reported["open_reservations"]
    ):
        failures.add("family_crossed")
    if book_id == "event":
        reserved = sum(item["amount_minor"] for item in reported["open_reservations"])
        if reserved != reported["collateral_reserved"]:
            failures.add("collateral_double_count")

    reported_attribution = {row["strategy_id"]: row for row in reported["attribution"]}
    if set(reported_attribution) != set(attribution):
        failures.add("attribution_mismatch")
    else:
        for strategy_id, bucket in attribution.items():
            row = reported_attribution[strategy_id]
            if (
                row["realized_pnl_minor"] != bucket["realized_pnl_minor"]
                or row["fees_minor"] != bucket["fees_minor"]
            ):
                failures.add("attribution_mismatch")
    return failures


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CandidateError(message)


def _check_claim(document: dict, label: str) -> None:
    _require(document.get("maturity_claimed") == "researched", f"{label} maturity is not researched")
    missing = [item for item in NOT_CLAIMED if item not in document.get("not_claimed", [])]
    _require(not missing, f"{label} omits not-claimed levels: {', '.join(missing)}")


def validate_manifest(manifest: dict, oracle: dict, validators: dict[str, object]) -> list[str]:
    _check_claim(manifest, "manifest")
    _check_claim(oracle, "oracle")
    _require(manifest.get("outcome") == "O3" and manifest.get("linear_issue") == "CSL-215", "manifest identity")
    _require(oracle.get("outcome") == "O3" and oracle.get("linear_issue") == "CSL-215", "oracle identity")
    oracle_hash = _sha256(ROOT / manifest["oracle"]["path"])
    _require(oracle_hash == manifest["oracle"]["sha256"], "oracle hash mismatch")

    oracle_controls = {item["id"]: item["failure_codes"] for item in oracle["negative_controls"]}
    lines: list[str] = []
    seen_roles: dict[str, list[str]] = {"positive": [], "negative": []}
    for record in manifest["fixtures"]:
        path = ROOT / record["path"]
        digest = _sha256(path)
        _require(digest == record["sha256"], f"{record['id']} hash mismatch")
        fixture = _load(path)
        _require(fixture["fixture_id"] == record["id"], f"{record['id']} fixture_id mismatch")
        schema_ok = _schema_ok(validators, fixture)
        failures = set() if schema_ok else {"schema_invalid"}
        failures |= evaluate_semantics(fixture)
        expected = record["expected_failure_codes"]
        role = record["role"]
        seen_roles[role].append(record["id"])
        if role == "positive":
            _require(expected == [], f"{record['id']} positive record is not empty")
            _require(not failures, f"{record['id']} positive failed: {', '.join(sorted(failures))}")
        elif role == "negative":
            _require(expected == oracle_controls.get(record["id"]), f"{record['id']} oracle mismatch")
            _require(
                sorted(failures) == sorted(expected) and expected,
                f"{record['id']} negative codes {sorted(failures)} != {sorted(expected)}",
            )
            rendered = ",".join(sorted(failures))
            lines.append(
                f"O3 NEGATIVE CONTROL FAILED AS DESIGNED: {record['id']} codes={rendered}"
            )
        else:
            raise CandidateError(f"{record['id']} unknown role")
    _require(set(oracle_controls) == set(seen_roles["negative"]), "oracle does not match negatives")
    _require(seen_roles["positive"] and seen_roles["negative"], "manifest is missing a role")
    return lines


def main() -> int:
    try:
        manifest = _load(ROOT / "fixtures" / "manifest.json")
        oracle = _load(ROOT / manifest["oracle"]["path"])
        lines = validate_manifest(manifest, oracle, _schema_validators())
    except CandidateError as exc:
        print(f"O3 candidate validator rejected the suite: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"O3 candidate validator could not read the suite: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    print("O3 candidate maturity remains researched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
