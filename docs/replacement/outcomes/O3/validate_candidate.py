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


def _line_signature(entry: dict, *, reverse: bool) -> tuple:
    flipped = {"dr": "cr", "cr": "dr"}
    rows = []
    for line in entry.get("lines", ()):
        side = line.get("side")
        if reverse:
            side = flipped.get(side, side)
        rows.append((
            line.get("account"),
            side,
            line.get("amount_minor"),
            line.get("instrument_id"),
        ))
    return tuple(sorted(rows, key=lambda row: tuple(str(part) for part in row)))


def _signature_positive(signature: tuple) -> bool:
    return any(
        isinstance(amount, int) and amount > 0
        for _account, _side, amount, _instrument in signature
    )


def _posts_instrument(entry: dict, instrument_id: str) -> bool:
    return any(line.get("instrument_id") == instrument_id for line in entry.get("lines", ()))


def _only_instrument(entry: dict, instrument_id: str) -> bool:
    lines = entry.get("lines", ())
    return bool(lines) and all(line.get("instrument_id") == instrument_id for line in lines)


def _paired_settlement_ids(fixture: dict, settlements: list[dict]) -> set[str]:
    """One reversal covers one settlement. A second copy stays unpaired."""
    paired: set[str] = set()
    used: set[str] = set()
    for entry in settlements:
        entry_id = entry.get("entry_id")
        if not isinstance(entry_id, str) or entry_id in used:
            continue
        wanted = _line_signature(entry, reverse=True)
        partner_id = None
        for other in fixture["journal"]:
            other_id = other.get("entry_id")
            if not isinstance(other_id, str) or other_id == entry_id or other_id in used:
                continue
            if _line_signature(other, reverse=False) == wanted:
                partner_id = other_id
                break
        if partner_id is None:
            continue
        paired.add(entry_id)
        paired.add(partner_id)
        used.add(entry_id)
        used.add(partner_id)
    return paired


def _admitted_fill_matches(fixture: dict, item: dict, instrument_id: str) -> bool:
    fill_id = item.get("admits_fill_id")
    if not isinstance(fill_id, str) or not fill_id:
        return False
    for fill in fixture.get("fills", ()):
        if fill.get("fill_id") == fill_id:
            return fill.get("instrument_id") == instrument_id
    return False


def _correction_supersedes_void(
    fixture: dict,
    item: dict,
    instrument_id: str,
    source_id: object,
) -> bool:
    """A correction overrides void evidence only when it reverses that posting."""
    if item.get("supersedes_source_id") != source_id:
        return False
    if not isinstance(source_id, str) or not source_id:
        return False
    if item.get("instrument_id") != instrument_id or item.get("cause") != "settlement_payout":
        return False
    original = next(
        (
            entry
            for entry in fixture["journal"]
            if entry.get("entry_id") == item.get("reverses_entry_id")
        ),
        None,
    )
    if original is None or original.get("reason") != "settlement_payout":
        return False
    if not _only_instrument(original, instrument_id):
        return False
    if not _signature_positive(_line_signature(original, reverse=False)):
        return False
    settlements: list[dict] = []
    for entry in fixture["journal"]:
        if entry.get("reason") != "settlement_payout":
            continue
        if not _posts_instrument(entry, instrument_id):
            continue
        # A posting that mixes instruments is not a reversal of this void.
        if not _only_instrument(entry, instrument_id):
            return False
        if _signature_positive(_line_signature(entry, reverse=False)):
            settlements.append(entry)
    if not settlements:
        return False
    paired = _paired_settlement_ids(fixture, settlements)
    settlement_ids = {entry.get("entry_id") for entry in settlements}
    if not settlement_ids <= paired or original.get("entry_id") not in settlement_ids:
        return False
    return _admitted_fill_matches(fixture, item, instrument_id)


def _linked_reversal_ids(fixture: dict) -> set[str]:
    """Entry ids of a settlement and the correction-linked mirror that reverses it.

    The mirror is a positive ``settlement_payout`` on the same instrument with
    the sides swapped and the amounts unchanged. The correction names that
    settlement, with ``cause`` ``settlement_payout``. A zero pair, a partial
    swap, or a correction that names a missing entry does not qualify.
    """
    paired: set[str] = set()
    used: set[str] = set()
    journal = fixture.get("journal", ())
    for item in fixture.get("corrections", ()):
        if item.get("cause") != "settlement_payout":
            continue
        original_id = item.get("reverses_entry_id")
        instrument_id = item.get("instrument_id")
        if not isinstance(original_id, str) or original_id in used:
            continue
        if not isinstance(instrument_id, str):
            continue
        original = next(
            (entry for entry in journal if entry.get("entry_id") == original_id),
            None,
        )
        if original is None or original.get("reason") != "settlement_payout":
            continue
        if not _only_instrument(original, instrument_id):
            continue
        if not _signature_positive(_line_signature(original, reverse=False)):
            continue
        wanted = _line_signature(original, reverse=True)
        partner_id = None
        for entry in journal:
            entry_id = entry.get("entry_id")
            if not isinstance(entry_id, str) or entry_id == original_id or entry_id in used:
                continue
            if entry.get("reason") != "settlement_payout":
                continue
            if not _only_instrument(entry, instrument_id):
                continue
            if _line_signature(entry, reverse=False) == wanted:
                partner_id = entry_id
                break
        if partner_id is None:
            continue
        paired.add(original_id)
        paired.add(partner_id)
        used.add(original_id)
        used.add(partner_id)
    return paired


def _reject_void_unwind_shape(fixture: dict, failures: set[str]) -> None:
    """Direction and cause of a void_unwind, without reading market-state rows."""
    void_evidence = {
        item["instrument_id"]
        for item in fixture["evidence"]
        if item.get("kind") == "market_void"
    }
    for entry in fixture["journal"]:
        if entry.get("reason") != "void_unwind":
            continue
        if entry.get("fill_id"):
            failures.add("balance_mismatch")
        per_instrument: dict[str, list[int]] = {}
        other = False
        instruments: set[str] = set()
        for line in entry.get("lines", ()):
            instrument_id = line.get("instrument_id")
            if isinstance(instrument_id, str):
                instruments.add(instrument_id)
            else:
                other = True
                continue
            amount = _int(line.get("amount_minor"))
            if amount is None:
                other = True
                continue
            bucket = per_instrument.setdefault(instrument_id, [0, 0])
            if line.get("account") == "cash_available" and line.get("side") == "dr":
                bucket[0] += amount
            elif line.get("account") == "collateral_reserved" and line.get("side") == "cr":
                bucket[1] += amount
            else:
                other = True
        if (
            other
            or not per_instrument
            or any(cash != collateral or cash == 0 for cash, collateral in per_instrument.values())
        ):
            failures.add("balance_mismatch")
        owned_release = isinstance(entry.get("order_id"), str) and bool(entry.get("order_id"))
        if not owned_release and not instruments <= void_evidence:
            failures.add("balance_mismatch")


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
        instrument_id = instrument["instrument_id"]
        if instrument_id in known_instruments:
            # A later row must not replace the earlier one. Settlement reads one.
            failures.add("position_divergence")
        known_instruments.add(instrument_id)
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
        order_id = order["order_id"]
        if order_id in orders:
            # A later row must not replace the earlier one. Lifecycle checks
            # read the retained row only, so both cannot share one fill total.
            failures.add("position_divergence")
            continue
        orders[order_id] = order
        if order["instrument_id"] not in known_instruments:
            failures.add("unknown_instrument")
        if order["family"] != book_id:
            failures.add("family_crossed")
        allowed = EQUITY_STATES if book_id == "equity" else EVENT_STATES
        if order["state"] not in allowed:
            failures.add("family_crossed")
        terminal = order.get("terminal_state")
        if terminal is not None and terminal not in allowed:
            failures.add("family_crossed")

    action_instruments = set()
    for action in fixture["corporate_actions"]:
        if action["kind"] != "cash_dividend":
            failures.add("corporate_action_unsupported")
        if action["instrument_id"] not in known_instruments:
            failures.add("unknown_instrument")
        action_instruments.add(action["instrument_id"])

    for item in fixture["evidence"]:
        if item["instrument_id"] not in known_instruments:
            failures.add("unknown_instrument")

    journal_entry_ids = {entry["entry_id"] for entry in fixture["journal"]}
    checkpoint = fixture["checkpoint"]
    applied_entry_ids = set(checkpoint.get("applied_entry_ids", ()))
    journal_entry_ids |= applied_entry_ids
    admitted = set()
    for item in fixture["corrections"]:
        if item["reverses_entry_id"] in journal_entry_ids:
            admitted.add(item["admits_fill_id"])
    seen_fills: set[str] = set()
    accepted_fills: list[dict] = []
    checkpointed_fill_ids: set[str] = set()
    defect_instruments: set[str] = set()
    accepted_qty: dict[str, int] = {}
    order_fills_total: dict[str, int] = {}
    for row in checkpoint["positions"] + checkpoint["open_reservations"]:
        if row["instrument_id"] not in known_instruments:
            failures.add("unknown_instrument")
    checkpoint_reservations: dict[str, int] = {}
    for reservation in checkpoint["open_reservations"]:
        checkpoint_reservations[reservation["instrument_id"]] = (
            checkpoint_reservations.get(reservation["instrument_id"], 0)
            + reservation["amount_minor"]
        )
    if sum(checkpoint_reservations.values()) != checkpoint["collateral_reserved"]:
        failures.add("collateral_double_count")
    derived = {row["instrument_id"]: row["qty"] for row in checkpoint["positions"]}
    checkpoint_qty: dict[str, int] = {}
    for row in checkpoint["positions"]:
        checkpoint_qty[row["instrument_id"]] = (
            checkpoint_qty.get(row["instrument_id"], 0) + row["qty"]
        )
    checkpoint_order_qty: dict[str, int] = {}
    for order in orders.values():
        checkpoint_order_qty[order["instrument_id"]] = (
            checkpoint_order_qty.get(order["instrument_id"], 0)
            + order.get("checkpoint_filled_qty", 0)
        )
    for instrument_id in set(checkpoint_qty) | set(checkpoint_order_qty):
        if checkpoint_qty.get(instrument_id, 0) != checkpoint_order_qty.get(instrument_id, 0):
            failures.add("position_divergence")
    order_checkpoint_hold: dict[str, int] = {}
    for reservation in checkpoint["open_reservations"]:
        owner = reservation.get("order_id")
        if not isinstance(owner, str) or not owner:
            continue
        if owner not in orders:
            failures.add("balance_mismatch")
            continue
        if reservation["instrument_id"] != orders[owner]["instrument_id"]:
            # Crossed ownership must not fund the other order's release.
            failures.add("balance_mismatch")
            continue
        order_checkpoint_hold[owner] = (
            order_checkpoint_hold.get(owner, 0) + reservation["amount_minor"]
        )
    order_hold = dict(order_checkpoint_hold)
    applied_fill_fees = 0
    fill_ids = {fill["fill_id"] for fill in fixture["fills"]}
    fills_by_id = {fill["fill_id"]: fill for fill in fixture["fills"]}
    settlement_at: dict[str, int] = {}
    for entry in fixture["journal"]:
        if entry.get("reason") != "settlement_payout":
            continue
        sequence = entry.get("sequence")
        if type(sequence) is not int:
            continue
        for line in entry.get("lines", ()):
            instrument_id = line.get("instrument_id")
            if not isinstance(instrument_id, str):
                continue
            previous = settlement_at.get(instrument_id)
            if previous is None or sequence < previous:
                settlement_at[instrument_id] = sequence
    post_checkpoint_qty: dict[str, int] = {}
    posted_premium: dict[str, int] = {}
    posted_fees: dict[str, int] = {}
    order_first_fill_seq: dict[str, int] = {}
    order_reserve_total: dict[str, int] = {}
    order_created: dict[str, int] = {}
    order_collateral_events: dict[str, list[tuple[int, int]]] = {}

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
            defect_instruments.add(fill["instrument_id"])
            continue
        seen_fills.add(fill["fill_id"])
        terminal = order.get("terminal_state") or order["state"]
        terminal_sequence = order.get("terminal_sequence")
        if terminal in LATE_TERMINALS and terminal_sequence is None:
            failures.add("schema_invalid")
        late = (
            terminal in LATE_TERMINALS
            and terminal_sequence is not None
            and fill["sequence"] > terminal_sequence
            and fill["fill_id"] not in admitted
        )
        if late:
            failures.add("late_fill_divergence")
            defect_instruments.add(fill["instrument_id"])
            continue
        settle_at = settlement_at.get(fill["instrument_id"])
        if settle_at is not None and fill["sequence"] >= settle_at:
            # A shared sequence is not ordered before the payout, so the fill
            # is not part of the position that settlement closed.
            failures.add("settlement_unreconciled")
            continue
        filled = accepted_qty.get(order["order_id"], 0)
        if filled + fill["qty"] > order["qty"]:
            failures.add("partial_fill_divergence")
            defect_instruments.add(fill["instrument_id"])
            continue
        accepted_qty[order["order_id"]] = filled + fill["qty"]
        order_fills_total[order["order_id"]] = (
            order_fills_total.get(order["order_id"], 0)
            + fill["qty"] * fill["price_minor"]
            + fill["fee_minor"]
        )
        first_seq = order_first_fill_seq.get(fill["order_id"])
        if first_seq is None or fill["sequence"] < first_seq:
            order_first_fill_seq[fill["order_id"]] = fill["sequence"]
        if fill["sequence"] <= checkpoint["sequence"]:
            checkpointed_fill_ids.add(fill["fill_id"])
            continue
        post_checkpoint_qty[order["order_id"]] = (
            post_checkpoint_qty.get(order["order_id"], 0) + fill["qty"]
        )
        accepted_fills.append(fill)
        derived[fill["instrument_id"]] = derived.get(fill["instrument_id"], 0) + fill["qty"]
        applied_fill_fees += fill["fee_minor"]

    if not (failures & FILL_DEFECTS):
        seen_lifecycle: set[str] = set()
        for order in fixture["orders"]:
            if order["order_id"] in seen_lifecycle:
                continue
            seen_lifecycle.add(order["order_id"])
            terminal = order.get("terminal_state") or order["state"]
            filled_qty = (
                order.get("checkpoint_filled_qty", 0)
                + post_checkpoint_qty.get(order["order_id"], 0)
            )
            if terminal == "filled" and filled_qty != order["qty"]:
                failures.add("partial_fill_divergence")
            partial = 0 < filled_qty < order["qty"]
            if terminal == "partially_filled" and not partial:
                failures.add("partial_fill_divergence")
            if partial and terminal not in LATE_TERMINALS and terminal != "partially_filled":
                failures.add("partial_fill_divergence")

    pre_settlement = dict(derived)
    seen_market_instruments: set[str] = set()
    for market in fixture["markets"]:
        if market["instrument_id"] in seen_market_instruments:
            failures.add("settlement_unreconciled")
        seen_market_instruments.add(market["instrument_id"])
        if market["instrument_id"] not in known_instruments:
            failures.add("unknown_instrument")
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
        instrument = next(
            (i for i in fixture["instruments"] if i["instrument_id"] == instrument_id), None
        )
        if instrument is None:
            failures.add("settlement_unreconciled")
            continue
        if instrument.get("contract_style") == "binary" and scale is not None:
            winner = record["market_result"] == instrument.get("outcome_side")
            expected_gross = record["long_qty"] * 10**scale if winner else 0
            if record["market_result"] == "scalar" or record["pre_rounding_minor"] != expected_gross:
                failures.add("settlement_unreconciled")
            if record["payout_minor"] > expected_gross:
                failures.add("settlement_unreconciled")
        if record["misc_fee_minor"] == 0 and record["pre_rounding_minor"] != record["payout_minor"]:
            failures.add("settlement_unreconciled")
        if record["fee_already_deducted_from_payout"]:
            if record["payout_minor"] + record["misc_fee_minor"] != record["pre_rounding_minor"]:
                failures.add("fee_double_count")
        elif record["misc_fee_minor"] != 0 and record["pre_rounding_minor"] != record["payout_minor"]:
            failures.add("settlement_unreconciled")
        derived[instrument_id] = 0

    voided = {market["instrument_id"] for market in fixture["markets"] if market["state"] == "voided"}
    settled_ids = {market["instrument_id"] for market in fixture["markets"] if market["state"] == "settled"}
    reported_positions: dict[str, int] = {}
    seen_reported_positions: set[str] = set()
    for row in fixture["reported"]["positions"]:
        instrument_id = row["instrument_id"]
        if instrument_id in seen_reported_positions:
            # The last row must not replace an earlier reported quantity.
            failures.add("position_divergence")
        seen_reported_positions.add(instrument_id)
        reported_positions[instrument_id] = row["qty"]
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
        if instrument_id in defect_instruments:
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

    cash = checkpoint["cash_available"]
    collateral = checkpoint["collateral_reserved"]
    seen_keys = set(checkpoint["applied_keys"])
    floor = checkpoint["sequence"]
    attribution: dict[str, dict[str, int]] = {}
    replay_detected = False
    journal_fees = 0
    corporate_cash = 0
    corporate_cash_by: dict[str, int] = {}
    settlement_fee_cash_by: dict[str, int] = {}
    settlement_cash_dr = 0
    settlement_cash_by: dict[str, int] = {}
    settlement_collateral_by: dict[str, int] = {}
    settlement_fee_by: dict[str, int] = {}
    settlement_loss_by: dict[str, int] = {}
    settlement_gain_by: dict[str, int] = {}
    settlement_seq: dict[str, int] = {}
    reserved_presettle = dict(checkpoint_reservations)
    reserved_end = dict(checkpoint_reservations)
    linked_reversals = _linked_reversal_ids(fixture)

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
            if (book_id == "equity" and line["account"] == "collateral_reserved") or (
                book_id == "event" and line["account"] == "position_cost"
            ):
                failures.add("family_crossed")
        if debits != credits:
            failures.add("unbalanced_entry")
        replay = (
            entry["sequence"] <= floor
            or entry["idempotency_key"] in seen_keys
            or entry["entry_id"] in applied_entry_ids
        )
        if replay:
            replay_detected = True
            if book_id == "event":
                failures.add("collateral_double_count")
            else:
                failures.add("restart_replay")
            continue
        seen_keys.add(entry["idempotency_key"])
        strategy_id = entry["strategy_id"]
        if entry["reason"] == "settlement_payout":
            for line in entry["lines"]:
                prev_settle = settlement_seq.get(line["instrument_id"])
                if prev_settle is None or entry["sequence"] < prev_settle:
                    settlement_seq[line["instrument_id"]] = entry["sequence"]
        fill_ref = entry.get("fill_id")
        order_ref = entry.get("order_id")
        if fill_ref is not None and (fill_ref not in fill_ids or fill_ref in checkpointed_fill_ids):
            failures.add("position_divergence")
        referenced_fill = fills_by_id.get(fill_ref) if fill_ref is not None else None
        if referenced_fill is not None:
            settle_at = settlement_at.get(referenced_fill["instrument_id"])
            if (
                settle_at is not None
                and referenced_fill["sequence"] >= settle_at
                and entry["sequence"] < referenced_fill["sequence"]
            ):
                failures.add("position_divergence")
        if order_ref is not None and order_ref not in orders:
            failures.add("position_divergence")
        if fill_ref is not None and order_ref is not None:
            failures.add("position_divergence")
        first_fill_seq = order_first_fill_seq.get(order_ref) if order_ref is not None else None
        # Only the creation of a reservation is ordered before the first fill.
        # A later release (settlement or void unwind) may name the same order.
        if (
            entry["reason"] == "reserve_collateral"
            and fill_ref is None
            and order_ref is not None
            and first_fill_seq is not None
            and entry["sequence"] > first_fill_seq
        ):
            failures.add("position_divergence")
        if entry["reason"] == "fill" and fill_ref is None:
            failures.add("balance_mismatch")
        if entry["reason"] == "reserve_collateral" and fill_ref is None and order_ref is None:
            failures.add("balance_mismatch")
        if entry["reason"] == "trade_fee" and fill_ref is None:
            failures.add("balance_mismatch")
        if entry["reason"] in {"fill", "reserve_collateral", "trade_fee"}:
            allowed = {
                "fill": {("position_cost", "dr"), ("fees_expense", "dr"), ("cash_available", "cr")},
                "reserve_collateral": {("collateral_reserved", "dr"), ("cash_available", "cr")},
                "trade_fee": {("fees_expense", "dr"), ("cash_available", "cr")},
            }[entry["reason"]]
            cash_credit = 0
            funded = 0
            for line in entry["lines"]:
                pair = (line["account"], line["side"])
                if pair not in allowed:
                    failures.add("balance_mismatch")
                    continue
                amount = _line_amount(line, failures)
                if pair == ("cash_available", "cr"):
                    cash_credit += amount
                else:
                    funded += amount
            if cash_credit != funded:
                failures.add("balance_mismatch")
        ref_instrument = None
        if fill_ref in fills_by_id:
            ref_instrument = fills_by_id[fill_ref]["instrument_id"]
        elif order_ref in orders:
            ref_instrument = orders[order_ref]["instrument_id"]
        if entry["reason"] == "corporate_action":
            for line in entry["lines"]:
                if (line["account"], line["side"]) not in {
                    ("cash_available", "dr"),
                    ("realized_pnl", "cr"),
                } or line["instrument_id"] not in action_instruments:
                    failures.add("corporate_action_mismatch")
        if entry["reason"] == "settlement_payout":
            line_instruments = {line["instrument_id"] for line in entry["lines"]}
            if len(line_instruments) != 1 or not (
                line_instruments <= settled_ids or line_instruments <= voided
            ):
                failures.add("settlement_unreconciled")
            allowed_settlement = {
                ("cash_available", "dr"),
                ("cash_available", "cr"),
                ("collateral_reserved", "cr"),
                ("realized_pnl", "dr"),
                ("realized_pnl", "cr"),
                ("fees_expense", "dr"),
            }
            # The mirror of a real settlement debits the collateral the
            # original released. Only that correction-linked pair may do so.
            if entry.get("entry_id") in linked_reversals:
                allowed_settlement.add(("collateral_reserved", "dr"))
                allowed_settlement.add(("fees_expense", "cr"))
            for line in entry["lines"]:
                if (line["account"], line["side"]) not in allowed_settlement:
                    failures.add("settlement_unreconciled")
        premium_delta = 0
        fee_delta = 0
        for line in entry["lines"]:
            amount = _line_amount(line, failures)
            if line["instrument_id"] not in known_instruments:
                failures.add("unknown_instrument")
            if ref_instrument is not None and line["instrument_id"] != ref_instrument:
                failures.add("position_divergence")
            account = line["account"]
            side = line["side"]
            if fill_ref is not None and entry["reason"] in {"fill", "reserve_collateral", "trade_fee"}:
                signed = amount if side == "dr" else -amount
                if account in {"position_cost", "collateral_reserved"}:
                    premium_delta += signed
                elif account == "fees_expense":
                    fee_delta += signed
            if account == "cash_available":
                cash += amount if side == "dr" else -amount
                if entry["reason"] == "corporate_action" and side == "dr":
                    corporate_cash += amount
                    corporate_cash_by[line["instrument_id"]] = (
                        corporate_cash_by.get(line["instrument_id"], 0) + amount
                    )
                counts_settlement = (
                    entry["reason"] == "settlement_payout"
                    and entry.get("entry_id") not in linked_reversals
                )
                if counts_settlement and side == "cr":
                    settlement_fee_cash_by[line["instrument_id"]] = (
                        settlement_fee_cash_by.get(line["instrument_id"], 0) + amount
                    )
                if counts_settlement and side == "dr":
                    settlement_cash_dr += amount
                    settlement_cash_by[line["instrument_id"]] = (
                        settlement_cash_by.get(line["instrument_id"], 0) + amount
                    )
            elif account == "collateral_reserved":
                collateral += amount if side == "dr" else -amount
                collateral_delta = amount if side == "dr" else -amount
                if (
                    order_ref is not None
                    and fill_ref is None
                    and entry["reason"] in {"reserve_collateral", "void_unwind", "settlement_payout"}
                ):
                    order_reserve_total[order_ref] = (
                        order_reserve_total.get(order_ref, 0) + collateral_delta
                    )
                    if entry["reason"] == "reserve_collateral" and collateral_delta > 0:
                        order_created[order_ref] = (
                            order_created.get(order_ref, 0) + collateral_delta
                        )
                    available = order_hold.get(order_ref, 0)
                    if collateral_delta < 0 and -collateral_delta > available:
                        failures.add("balance_mismatch")
                    order_hold[order_ref] = available + collateral_delta
                    order_collateral_events.setdefault(order_ref, []).append(
                        (entry["sequence"], collateral_delta)
                    )
                reserved_end[line["instrument_id"]] = (
                    reserved_end.get(line["instrument_id"], 0) + collateral_delta
                )
                settled_seq = settlement_seq.get(line["instrument_id"])
                if entry["reason"] != "settlement_payout" and (
                    settled_seq is None or entry["sequence"] < settled_seq
                ):
                    reserved_presettle[line["instrument_id"]] = (
                        reserved_presettle.get(line["instrument_id"], 0) + collateral_delta
                    )
                counts_settlement = (
                    entry["reason"] == "settlement_payout"
                    and entry.get("entry_id") not in linked_reversals
                )
                if counts_settlement and side == "cr":
                    settlement_collateral_by[line["instrument_id"]] = (
                        settlement_collateral_by.get(line["instrument_id"], 0) + amount
                    )
                if entry["reason"] not in {"reserve_collateral", "settlement_payout", "void_unwind"}:
                    failures.add("balance_mismatch")
            elif account == "realized_pnl":
                delta = amount if side == "cr" else -amount
                bucket = attribution.setdefault(strategy_id, {"realized_pnl_minor": 0, "fees_minor": 0})
                bucket["realized_pnl_minor"] += delta
                if (
                    entry["reason"] == "settlement_payout"
                    and entry.get("entry_id") not in linked_reversals
                ):
                    if side == "dr":
                        settlement_loss_by[line["instrument_id"]] = (
                            settlement_loss_by.get(line["instrument_id"], 0) + amount
                        )
                    else:
                        settlement_gain_by[line["instrument_id"]] = (
                            settlement_gain_by.get(line["instrument_id"], 0) + amount
                        )
            elif account == "fees_expense":
                delta = amount if side == "dr" else -amount
                if entry["reason"] != "settlement_payout":
                    journal_fees += delta
                bucket = attribution.setdefault(strategy_id, {"realized_pnl_minor": 0, "fees_minor": 0})
                bucket["fees_minor"] += delta
                if (
                    entry["reason"] == "settlement_payout"
                    and entry.get("entry_id") not in linked_reversals
                ):
                    settlement_fee_by[line["instrument_id"]] = (
                        settlement_fee_by.get(line["instrument_id"], 0) + amount
                    )
            elif account == "position_cost":
                if entry["reason"] != "fill":
                    failures.add("balance_mismatch")
            else:
                failures.add("schema_invalid")
        if fill_ref is not None:
            posted_premium[fill_ref] = posted_premium.get(fill_ref, 0) + premium_delta
            posted_fees[fill_ref] = posted_fees.get(fill_ref, 0) + fee_delta

    if not (failures & FILL_DEFECTS):
        for fill in accepted_fills:
            posted = posted_premium.get(fill["fill_id"], 0)
            premium = fill["qty"] * fill["price_minor"]
            # Releases at or before the fill reduce what that sequence can fund.
            available = order_checkpoint_hold.get(fill["order_id"], 0)
            for seq, delta in order_collateral_events.get(fill["order_id"], ()):
                if seq <= fill["sequence"]:
                    available += delta
            if available >= premium:
                # Collateral still held at this sequence already funds the fill.
                if posted != 0:
                    failures.add("balance_mismatch")
            elif posted != premium:
                failures.add("balance_mismatch")
            if posted_fees.get(fill["fill_id"], 0) != fill["fee_minor"]:
                failures.add("fee_mismatch")

    if not (failures & FILL_DEFECTS):
        reported_by_key: dict[tuple[str, str], int] = {}
        named_keys: set[tuple[str, str]] = set()
        for row in fixture["reported"]["open_reservations"]:
            owner = row.get("order_id")
            if not isinstance(owner, str) or not owner:
                continue
            order = orders.get(owner)
            if order is None or row["instrument_id"] != order["instrument_id"]:
                # A forged owner, or a hold moved onto another instrument.
                failures.add("balance_mismatch")
                continue
            key = (owner, row["instrument_id"])
            named_keys.add(key)
            reported_by_key[key] = reported_by_key.get(key, 0) + row["amount_minor"]
        order_ids = set(order_reserve_total) | set(order_checkpoint_hold) | {
            order_id for order_id, _instrument_id in named_keys
        }
        for order_id in order_ids:
            order = orders.get(order_id)
            if order is None:
                continue
            opening = order_checkpoint_hold.get(order_id, 0)
            created = order_created.get(order_id, 0)
            # Releases reduce the ending hold. They do not reduce the amount
            # that was reserved for the fills.
            pre_release = opening + created
            ending = order_hold.get(order_id, opening)
            filled_notional = order_fills_total.get(order_id, 0)
            checkpoint_qty = order.get("checkpoint_filled_qty", 0)
            terminal = order.get("terminal_state") or order["state"]
            if filled_notional > 0:
                if pre_release != filled_notional:
                    failures.add("balance_mismatch")
            elif checkpoint_qty > 0:
                # No fill row carries the notional. Compare a post-checkpoint
                # reserve with the checkpoint hold, the same way fill rows
                # are compared with premium plus fees.
                if pre_release != opening:
                    failures.add("balance_mismatch")
            elif terminal in LATE_TERMINALS and ending != 0:
                failures.add("balance_mismatch")
            key = (order_id, order["instrument_id"])
            if key in named_keys and reported_by_key.get(key, 0) != ending:
                failures.add("balance_mismatch")

    if not (failures & FILL_DEFECTS) and journal_fees != applied_fill_fees:
        failures.add("fee_mismatch")

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
        has_payout = any(
            entry["reason"] == "settlement_payout"
            and any(line["instrument_id"] == instrument_id for line in entry["lines"])
            for entry in fixture["journal"]
        )
        unwind_cash = 0
        unwind_collateral = 0
        unwind_other = False
        for entry in fixture["journal"]:
            if entry["reason"] != "void_unwind":
                continue
            for line in entry["lines"]:
                if line["instrument_id"] != instrument_id:
                    continue
                if line["account"] == "cash_available" and line["side"] == "dr":
                    unwind_cash += line["amount_minor"]
                elif line["account"] == "collateral_reserved" and line["side"] == "cr":
                    unwind_collateral += line["amount_minor"]
                else:
                    unwind_other = True
        if (
            not has_void
            or has_settlement
            or has_payout
            or unwind_other
            or unwind_cash != unwind_collateral
            or reported_positions.get(instrument_id, 0) != 0
            or reserved_end.get(instrument_id, 0) != 0
            or any(
                row["instrument_id"] == instrument_id
                for row in fixture["reported"]["open_reservations"]
            )
        ):
            failures.add("void_unreconciled")

    _reject_void_unwind_shape(fixture, failures)
    settlement_instruments = {
        item["instrument_id"]
        for item in fixture["evidence"]
        if item["kind"] == "market_settlement"
    }
    settlement_instruments.update(
        market["instrument_id"]
        for market in fixture["markets"]
        if market["state"] == "settled"
    )
    for entry in fixture["journal"]:
        if entry["reason"] != "settlement_payout":
            continue
        for line in entry["lines"]:
            settlement_instruments.add(line["instrument_id"])
    for item in fixture["evidence"]:
        if item["kind"] != "market_void":
            continue
        if item["instrument_id"] not in settlement_instruments:
            continue
        source_id = item.get("source_id")
        superseded = any(
            _correction_supersedes_void(fixture, correction, item["instrument_id"], source_id)
            for correction in fixture["corrections"]
        )
        if not superseded:
            failures.add("void_unreconciled")

    expected_dividend = sum(
        action["cash_minor"] for action in fixture["corporate_actions"] if action["kind"] == "cash_dividend"
    )
    if corporate_cash != expected_dividend:
        failures.add("corporate_action_mismatch")
    expected_dividend_by: dict[str, int] = {}
    for action in fixture["corporate_actions"]:
        if action["kind"] == "cash_dividend":
            expected_dividend_by[action["instrument_id"]] = (
                expected_dividend_by.get(action["instrument_id"], 0) + action["cash_minor"]
            )
    if {k: v for k, v in corporate_cash_by.items() if v} != {
        k: v for k, v in expected_dividend_by.items() if v
    }:
        failures.add("corporate_action_mismatch")

    settled = [market for market in fixture["markets"] if market["state"] == "settled"]
    if settled:
        instruments_by_id = {i["instrument_id"]: i for i in fixture["instruments"]}
        for market in settled:
            instrument_id = market["instrument_id"]
            evidence = [
                item
                for item in fixture["evidence"]
                if item["kind"] == "market_settlement" and item["instrument_id"] == instrument_id
            ]
            if len(evidence) != 1:
                continue
            record = evidence[0]
            payout = settlement_cash_by.get(instrument_id, 0)
            reserved = reserved_presettle.get(instrument_id, 0)
            if payout != record["payout_minor"]:
                failures.add("settlement_unreconciled")
            if record["fee_already_deducted_from_payout"]:
                if instrument_id in settlement_fee_by:
                    failures.add("fee_double_count")
                if settlement_fee_cash_by.get(instrument_id, 0) != 0:
                    failures.add("settlement_unreconciled")
            elif (
                settlement_fee_by.get(instrument_id, 0) != record["misc_fee_minor"]
                or settlement_fee_cash_by.get(instrument_id, 0) != record["misc_fee_minor"]
            ):
                failures.add("settlement_unreconciled")
            if settlement_collateral_by.get(instrument_id, 0) != reserved:
                failures.add("settlement_unreconciled")
            instrument = instruments_by_id.get(instrument_id) or {}
            if instrument.get("contract_style") == "binary":
                if settlement_loss_by.get(instrument_id, 0) != max(0, reserved - payout):
                    failures.add("settlement_unreconciled")
                if settlement_gain_by.get(instrument_id, 0) != max(0, payout - reserved):
                    failures.add("settlement_unreconciled")

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
        reported_reservations: dict[str, int] = {}
        for row in reported["open_reservations"]:
            if row["instrument_id"] not in known_instruments:
                failures.add("unknown_instrument")
            reported_reservations[row["instrument_id"]] = (
                reported_reservations.get(row["instrument_id"], 0) + row["amount_minor"]
            )
        for instrument_id in set(reported_reservations) | set(reserved_end):
            if instrument_id in voided:
                continue
            if reported_reservations.get(instrument_id, 0) != reserved_end.get(instrument_id, 0):
                failures.add("collateral_double_count")

    reported_attribution: dict[str, dict] = {}
    seen_attribution: set[str] = set()
    duplicate_attribution = False
    for row in reported["attribution"]:
        strategy_id = row["strategy_id"]
        if strategy_id in seen_attribution:
            # The last row must not replace an earlier strategy rollup.
            duplicate_attribution = True
        seen_attribution.add(strategy_id)
        reported_attribution[strategy_id] = row
    if duplicate_attribution or set(reported_attribution) != set(attribution):
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
