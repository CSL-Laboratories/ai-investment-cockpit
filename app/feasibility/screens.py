"""Record GET-only W-24 screens and a cohort/label plan. No LLM, no broker.

RW-31 ingested the empty 2026-09-12 board without inventing identities.
RW-32 pins the operator seal (32 tickers + summary-file hashes) without
lowering the 30-identity gate, authorizing labelling, seeding the merger
watchlist, or starting measurement. Raw SEC docs stay off-clone.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema

from feasibility.evaluate import EVALUATION_PATH, load_evaluation
from feasibility.inspect import CASES_PATH, load_cases
from feasibility.protocol import JUDGE_FIELDS, PROTOCOL_PATH, load_protocol
from feasibility.validate import FREEZE_PATH, FeasibilityError

SCREENS_VERSION = "feasibility.screens.v1"
SCREENS_CLAIM_ID = "RW-32"
RW27_FREEZE_SHA256 = "26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8"
RW28_EVALUATION_SHA256 = "665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31"
RW29_CASES_SHA256 = "1f4f90a8dd03bc0970f8526bc951f425e46d4a0393bb2a6f74ce2f365bf3be41"
RW30_PROTOCOL_SHA256 = "a7bb528863b0a43073f1fbfbb91b6145fa6b6d85018a4dd5828c79ef76b6c528"
SEP09_NOTE = "docs/evidence/w24-natural-head-rescreen-2026-09-09.md"
SEP12_NOTE = "docs/evidence/w24-natural-head-rescreen-2026-09-12.md"
SEP12_SEAL = "docs/evidence/w24-natural-head-rescreen-2026-09-12.seal.json"
SEP09_NOTE_SHA256 = "427f5b9cf63b8071ad000a77ad59108bec77f7010ee6f8dfb95fa5379abd315b"
SEP12_NOTE_SHA256 = "71c1f8b03cd673ced206e8e2c8bcfaca6d8b99985e73b9771d9cd67b05882226"
SEP12_SEAL_SHA256 = "de131f21fa7ed81f0ff258c9f6bc16ab4d35b47eb0ec35c93b133906125821bc"
SCREEN_IDS = ("w24_2026_09_09", "w24_2026_09_12")
LATEST_SCREEN_ID = "w24_2026_09_12"
COHORT_RULE = "30_unique_eligible_identities_same_selection_date"
NEXT_OWNER_ACTION = "continue_get_only_natural_head_screens"
HELD_OUT_RULE = (
    "This file records GET-only screen counts and a cohort/label plan. "
    "It is not a measurement, not labels, not a strategy cohort, and must "
    "not be reused as held-out outcomes."
)
SEP09_SUPPORTED_SYMBOLS = (
    "ACA",
    "ATAI",
    "BBBY",
    "BKR",
    "CCO",
    "CYCU",
    "CYCUW",
    "DIOD",
    "ESI",
    "FSEA",
    "GBCS",
    "GNL",
    "GRML",
    "GRMLW",
    "HUN",
    "JTAI",
    "KPLT",
    "KR",
    "OLN",
    "RMBI",
    "SEI",
    "SLP",
    "SOLS",
    "STRR",
    "STRRP",
    "THFF",
    "TXNM",
    "UHS",
    "UUUU",
    "VEEE",
    "ZSQR",
    "ZYME",
)
SEP12_SUPPORTED_SYMBOLS = (
    "ACA",
    "AIRE",
    "AROW",
    "BKR",
    "CCO",
    "CYCU",
    "CYCUW",
    "DIOD",
    "ESI",
    "FSEA",
    "GBCS",
    "GRML",
    "GRMLW",
    "HUN",
    "JTAI",
    "OLN",
    "RLYB",
    "RMBI",
    "SLP",
    "SOLS",
    "STRR",
    "STRRP",
    "TARS",
    "TBPH",
    "TWOD",
    "TXNM",
    "UHS",
    "UUUU",
    "VCTR",
    "VEEE",
    "ZSQR",
    "ZYME",
)
SEP09_MAIN_HEAD = "7a290b1198ab94732dc36753a7d807502c126f06"
SEP12_MAIN_HEAD = "885d33a76b3cb9ab7a507e56f4a1d3f993e59b83"
SEP09_SOAK_SHA = "5d50b25194c14beaac7bb979dcd559cc21a2fb95"
SEP12_SOAK_SHA = "5d50b25194c14beaac7bb979dcd559cc21a2fb95"
SEP12_PARSER_PATH = "/workspace/cockpit-main-tip/app/strategies/merger_semantics.py"
SEP12_PARSER_BLOB_SHA = "fda5017b1c87cd4d6e18e6f297e2ca291371d44d"
MARKDOWN_HASH_CAVEAT = (
    "MD hash computed after removing self-hash line; see seal file for md sha."
)

_HERE = Path(__file__).resolve().parent
SCREENS_PATH = _HERE / "screens.v1.json"
SCREENS_SCHEMA_PATH = _HERE.parent / "schemas" / "json" / "FeasibilityScreens.schema.json"
REPO_ROOT = _HERE.parents[1]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_pin(payload: dict[str, Any], field: str, path: Path, expected: str, label: str) -> None:
    digest = sha256_file(path)
    if digest != expected or payload[field] != expected:
        raise FeasibilityError(f"screens must pin the unchanged {label} digest")


def load_sep12_seal() -> dict[str, Any]:
    path = REPO_ROOT / SEP12_SEAL
    if not path.is_file():
        raise FeasibilityError(f"missing seal {SEP12_SEAL}")
    if sha256_file(path) != SEP12_SEAL_SHA256:
        raise FeasibilityError("2026-09-12 seal bytes drifted")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_sep09(item: dict[str, Any]) -> None:
    if item["evidence_note"] != SEP09_NOTE or item["evidence_note_sha256"] != SEP09_NOTE_SHA256:
        raise FeasibilityError("2026-09-09 must cite the checked-in evidence note digest")
    if item["provenance"] != "checked_in_evidence_note":
        raise FeasibilityError("2026-09-09 provenance is the checked-in evidence note")
    if item["claimant"] != "cockpit_admin_automation":
        raise FeasibilityError("2026-09-09 claimant is cockpit admin automation")
    if item["identity_list_status"] != "cited_in_evidence_note":
        raise FeasibilityError("2026-09-09 identity list is cited in the evidence note")
    if item["soak_sha_status"] != "cited_in_evidence_note":
        raise FeasibilityError("2026-09-09 soak SHA is cited in the evidence note")
    if item["raw_packet_status"] != "cited_summary_only":
        raise FeasibilityError("2026-09-09 raw packets remain cited summaries")
    if item.get("supported_symbols") != list(SEP09_SUPPORTED_SYMBOLS):
        raise FeasibilityError("2026-09-09 supported_symbols must match the evidence note")
    if item["operator_main_head"] != SEP09_MAIN_HEAD:
        raise FeasibilityError("2026-09-09 operator_main_head must match the evidence note")
    if item.get("soak_sha_observed_live") != SEP09_SOAK_SHA:
        raise FeasibilityError("2026-09-09 soak SHA must match the evidence note")
    if item["constraint_source"] != "checked_in_evidence_note":
        raise FeasibilityError("2026-09-09 constraints come from the evidence note")
    if item["counts"]["pool"] != 195:
        raise FeasibilityError("2026-09-09 pool must stay 195")
    if "seal_file" in item or "packet_sha256" in item:
        raise FeasibilityError("2026-09-09 must not carry the 2026-09-12 seal fields")


def _validate_sep12(item: dict[str, Any]) -> None:
    seal = load_sep12_seal()
    if item["evidence_note"] != SEP12_NOTE or item["evidence_note_sha256"] != SEP12_NOTE_SHA256:
        raise FeasibilityError("2026-09-12 must cite the checked-in evidence note digest")
    if item["provenance"] != "sealed_operator_packet":
        raise FeasibilityError("2026-09-12 provenance is the sealed operator packet")
    if item["claimant"] != "grok_bot_operator_board":
        raise FeasibilityError("2026-09-12 claimant is the Grok Bot operator board")
    if item["identity_list_status"] != "cited_in_sealed_packet":
        raise FeasibilityError("2026-09-12 identity list must come from the seal")
    if item["soak_sha_status"] != "cited_in_sealed_packet":
        raise FeasibilityError("2026-09-12 soak SHA must come from the seal")
    if item["raw_packet_status"] != "sealed_summary_hashes":
        raise FeasibilityError("2026-09-12 pins summary hashes, not raw SEC docs")
    if item.get("supported_symbols") != list(SEP12_SUPPORTED_SYMBOLS):
        raise FeasibilityError("2026-09-12 supported_symbols must match the seal")
    if item.get("supported_symbols") == list(SEP09_SUPPORTED_SYMBOLS):
        raise FeasibilityError("2026-09-12 must not copy the 2026-09-09 identity list")
    if item.get("supported_symbols") != seal["supported_symbols"]:
        raise FeasibilityError("2026-09-12 supported_symbols must equal the seal file")
    if item["operator_main_head"] != SEP12_MAIN_HEAD:
        raise FeasibilityError("2026-09-12 operator_main_head must match origin/main 885d33a")
    if item["operator_main_head"] != seal["operator_main_clone_sha"]:
        raise FeasibilityError("2026-09-12 operator_main_head must equal the seal")
    if item.get("soak_sha_observed_live") != SEP12_SOAK_SHA:
        raise FeasibilityError("2026-09-12 soak SHA must match the seal")
    if item.get("soak_sha_observed_live") != seal["soak_sha_observed_live"]:
        raise FeasibilityError("2026-09-12 soak SHA must equal the seal")
    if item["constraint_source"] != "sealed_operator_packet":
        raise FeasibilityError("2026-09-12 constraints come from the sealed packet")
    if item["counts"]["pool"] != 197 or seal["board"]["pool"] != 197:
        raise FeasibilityError("2026-09-12 pool must stay 197")
    if item.get("seal_file") != SEP12_SEAL or item.get("seal_sha256") != SEP12_SEAL_SHA256:
        raise FeasibilityError("2026-09-12 must pin the checked-in seal digest")
    if item.get("packet_sha256") != seal["sha256"]:
        raise FeasibilityError("2026-09-12 packet hashes must equal the seal")
    if item.get("parser_path") != SEP12_PARSER_PATH or item.get("parser_path") != seal["parser_path"]:
        raise FeasibilityError("2026-09-12 parser_path must match the seal")
    if (
        item.get("parser_blob_sha") != SEP12_PARSER_BLOB_SHA
        or item.get("parser_blob_sha") != seal["parser_blob_sha"]
    ):
        raise FeasibilityError("2026-09-12 parser_blob_sha must match the seal")
    if item.get("markdown_hash_caveat") != MARKDOWN_HASH_CAVEAT:
        raise FeasibilityError("2026-09-12 must keep the soak-markdown hash caveat")
    if seal["board"]["meets_min_30_gate"] is not False:
        raise FeasibilityError("seal must not claim the 30-identity gate")
    if seal["board"]["cash"] != 0 or seal["board"]["cohort"] != 0:
        raise FeasibilityError("seal cash and cohort must stay 0")


def _validate_screen_row(item: dict[str, Any]) -> None:
    note = REPO_ROOT / item["evidence_note"]
    if not note.is_file():
        raise FeasibilityError(f"missing evidence note {item['evidence_note']}")
    note_digest = sha256_file(note)
    if note_digest != item["evidence_note_sha256"]:
        raise FeasibilityError("evidence_note_sha256 must match the checked-in note")
    # Head steer of F-02: this schema version cannot mark any screen cohort-ready.
    if item["meets_cohort_gate"] is not False:
        raise FeasibilityError("this ledger cannot mark a screen cohort-ready")
    if item["id"] == "w24_2026_09_09":
        _validate_sep09(item)
    elif item["id"] == "w24_2026_09_12":
        _validate_sep12(item)
    else:
        raise FeasibilityError("unknown screen id")


def validate_screens(payload: dict[str, Any]) -> None:
    jsonschema.validate(
        instance=payload,
        schema=json.loads(SCREENS_SCHEMA_PATH.read_text(encoding="utf-8")),
    )
    if payload.get("empirical_pass_claimed") is not False:
        raise FeasibilityError("empirical_pass_claimed must be false")
    if payload.get("schema_version") != SCREENS_VERSION:
        raise FeasibilityError("schema_version must be feasibility.screens.v1")
    if payload.get("claim_id") != SCREENS_CLAIM_ID:
        raise FeasibilityError("claim_id must be RW-32")
    if payload.get("qualify_count") != 0:
        raise FeasibilityError("this corpus cannot qualify a launch strategy")
    if payload.get("measurement_status") != "not_started":
        raise FeasibilityError("this packet does not start measurement")
    if payload.get("labelling_authorized") is not False:
        raise FeasibilityError("RW-32 does not authorize labelling")
    if payload.get("watchlist_seed_authorized") is not False:
        raise FeasibilityError("RW-32 does not authorize a watchlist seed")
    if payload.get("activation_authorized") is not False:
        raise FeasibilityError("RW-32 does not authorize activation")
    if payload.get("cef_nav_authorized") is not False:
        raise FeasibilityError("RW-32 does not authorize a NAV feed")
    _require_pin(payload, "freeze_sha256", FREEZE_PATH, RW27_FREEZE_SHA256, "RW-27 freeze")
    _require_pin(
        payload, "evaluation_sha256", EVALUATION_PATH, RW28_EVALUATION_SHA256, "RW-28 evaluation"
    )
    _require_pin(payload, "cases_sha256", CASES_PATH, RW29_CASES_SHA256, "RW-29 cases")
    _require_pin(
        payload, "protocol_sha256", PROTOCOL_PATH, RW30_PROTOCOL_SHA256, "RW-30 protocol"
    )
    protocol = load_protocol()
    if protocol["measurement_status"] != "not_started":
        raise FeasibilityError("pinned protocol must still have measurement not_started")
    if protocol["qualify_count"] != 0:
        raise FeasibilityError("pinned protocol must still have qualify_count 0")
    if any(protocol["resource_allowance"][key] is not False for key in protocol["resource_allowance"]):
        raise FeasibilityError("pinned protocol still authorizes no resources")
    evaluation = load_evaluation()
    if evaluation["qualify_count"] != 0:
        raise FeasibilityError("pinned evaluation must still have qualify_count 0")
    cases = load_cases()
    if cases["qualify_count"] != 0:
        raise FeasibilityError("pinned cases ledger must still have qualify_count 0")
    if payload["held_out_rule"] != HELD_OUT_RULE:
        raise FeasibilityError("held_out_rule must match the frozen sentence")
    ids = tuple(item["id"] for item in payload["screens"])
    if ids != SCREEN_IDS:
        raise FeasibilityError("screen ids must match the frozen ledger order")
    dates = [item["screen_date"] for item in payload["screens"]]
    if dates != sorted(dates) or dates[-1] != "2026-09-12":
        raise FeasibilityError("screens must be ordered by date with 2026-09-12 last")
    if payload["latest_screen_id"] != LATEST_SCREEN_ID or payload["screens"][-1]["id"] != LATEST_SCREEN_ID:
        raise FeasibilityError("latest_screen_id must be the 2026-09-12 sealed screen")
    if payload["meets_cohort_gate"] is not False:
        raise FeasibilityError("cohort gate is not met")
    for item in payload["screens"]:
        _validate_screen_row(item)
    latest = payload["screens"][-1]
    plan = payload["cohort_plan"]
    if plan["rule"] != COHORT_RULE:
        raise FeasibilityError("cohort rule must stay the frozen 30-identity rule")
    if plan["next_owner_action"] != NEXT_OWNER_ACTION:
        raise FeasibilityError("next owner action is another GET-only screen")
    if plan["latest_selection_ready"] != latest["counts"]["selection_ready"]:
        raise FeasibilityError("cohort_plan must repeat the latest selection-ready count")
    if plan["latest_date_ready"] != latest["counts"]["date_ready"]:
        raise FeasibilityError("cohort_plan must repeat the latest date-ready count")
    if plan["latest_cohort"] != latest["counts"]["cohort"]:
        raise FeasibilityError("cohort_plan must repeat the latest cohort count")
    if plan["meets_gate"] is not False:
        raise FeasibilityError("cohort_plan.meets_gate must stay false")
    labels = payload["label_plan"]
    if labels["labelling_authorized"] is not False:
        raise FeasibilityError("label_plan does not authorize labelling")
    if tuple(labels["fields"]) != JUDGE_FIELDS:
        raise FeasibilityError("label_plan.fields must match the frozen judge set")
    if labels["candidate_extraction_refusal_n"] != latest["counts"]["supported_heads"]:
        raise FeasibilityError("extraction-refusal candidates are the latest supported heads")
    if labels["candidate_cash_merger_n"] != latest["counts"]["cash_parse_hits"]:
        raise FeasibilityError("cash-merger label candidates must equal latest cash parses")
    allowance = payload["resource_allowance"]
    if any(allowance[key] is not False for key in allowance):
        raise FeasibilityError("RW-32 authorizes no collection, feed, soak, labelling, or activation")


def load_screens(path: Path | None = None) -> dict[str, Any]:
    payload = json.loads((path or SCREENS_PATH).read_text(encoding="utf-8"))
    validate_screens(payload)
    return payload
