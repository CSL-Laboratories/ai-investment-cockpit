"""Provider-free validator for the CSL-214 O2 candidate evidence contract.

This module is a candidate artifact. It does not ingest, trade, or call a provider.
Exit 0 means the frozen corpus replayed and every negative control failed as designed.
That is not contract_frozen, not an empirical PASS, and not feed qualification.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

EXPECTED_FILES = (
    "CONTRACT.md",
    "validate_o2.py",
    "fixtures/identities.json",
    "fixtures/raw_records.json",
    "fixtures/observations.json",
    "fixtures/lineage.json",
    "fixtures/replay_checks.json",
    "fixtures/coverage.json",
    "fixtures/qualification_matrix.json",
    "fixtures/negative_controls.json",
    "schemas/identity.schema.json",
    "schemas/raw_record.schema.json",
    "schemas/observation.schema.json",
    "schemas/lineage.schema.json",
    "schemas/replay_manifest.schema.json",
    "schemas/coverage.schema.json",
    "schemas/qualification_matrix.schema.json",
    "schemas/negative_control.schema.json",
    "schemas/manifest.schema.json",
)

SCHEMA_FOR = {
    "fixtures/identities.json": "schemas/identity.schema.json",
    "fixtures/raw_records.json": "schemas/raw_record.schema.json",
    "fixtures/observations.json": "schemas/observation.schema.json",
    "fixtures/lineage.json": "schemas/lineage.schema.json",
    "fixtures/replay_checks.json": "schemas/replay_manifest.schema.json",
    "fixtures/coverage.json": "schemas/coverage.schema.json",
    "fixtures/qualification_matrix.json": "schemas/qualification_matrix.schema.json",
    "fixtures/negative_controls.json": "schemas/negative_control.schema.json",
    "fixtures/manifest.json": "schemas/manifest.schema.json",
}

EQUITY_KINDS = {"filing", "filing_amendment", "corporate_action", "quote"}
EVENT_KIND = "lifecycle"
# A source certifies provenance: each provider feed may only carry the payload
# kinds it actually emits (a quote cannot arrive on the SEC submissions feed).
SOURCE_KINDS = {
    "synthetic_sec_submission": {"filing", "filing_amendment"},
    "synthetic_corporate_action": {"corporate_action"},
    "synthetic_equity_quote": {"quote"},
    "synthetic_kalshi_demo_lifecycle": {"lifecycle"},
}
UNRESOLVED_STATUSES = {"initialized", "active", "inactive", "closed"}
RESOLVED_STATUSES = {"determined", "disputed", "amended", "finalized"}
# These are admissible snapshot states, not transitions inferred from replay
# order. REST can observe any official state; synthetic void is determined-only.
LIFECYCLE_STATUSES = {
    "created": {"initialized"},
    "close_date_updated": UNRESOLVED_STATUSES,
    "determined": {"determined", "voided"},
    "settled": {"finalized"},
    "status_observed": UNRESOLVED_STATUSES | RESOLVED_STATUSES,
}
# Consumers query these suffixes. A lifecycle record may not invent another one.
LIFECYCLE_KEY_SUFFIX = {
    "created": "created",
    "close_date_updated": "close_date",
    "determined": "resolution",
    "settled": "resolution",
    "status_observed": "resolution",
}
CONTRACT_RESOLUTIONS = {"binary": {"yes", "no"}, "scalar": {"scalar"}}
NEGATIVE_CODES = (
    "future_data_leak",
    "raw_not_reconstructable",
    "lost_resolution_semantics",
    "lost_corporate_action_semantics",
    "no_evidence",
    "dedup_collapses_correction",
    "stock_treated_as_event",
)


class ContractError(ValueError):
    """The candidate corpus or a negative control did not meet its rule."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def payload_sha256(payload: object) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def parse_time(value: object) -> datetime:
    if type(value) is not str or len(value) != 20 or not value.endswith("Z"):
        raise ContractError(f"timestamp must be YYYY-MM-DDTHH:MM:SSZ, got {value!r}")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ContractError(f"timestamp is not a real UTC instant: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ContractError(f"timestamp is not UTC: {value}")
    return parsed


def parse_unix_time(value: object) -> datetime:
    if type(value) is not int or value < 0:
        raise ContractError(f"unix timestamp must be a non-negative integer, got {value!r}")
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OSError, OverflowError, ValueError) as exc:
        raise ContractError(f"unix timestamp is not a real instant: {value}") from exc


def parse_date(value: object) -> date:
    if type(value) is not str:
        raise ContractError(f"date must be YYYY-MM-DD, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"date is not a real calendar date: {value}") from exc


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid JSON input: {path.name}: {exc}") from None


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest(root: Path) -> dict:
    manifest_path = root / "fixtures" / "manifest.json"
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ContractError("manifest root must be an object")
    files = manifest.get("files")
    if not isinstance(files, dict) or tuple(files) != EXPECTED_FILES:
        raise ContractError("frozen manifest file set changed")
    for rel in EXPECTED_FILES:
        digest = files[rel]
        if type(digest) is not str or _sha_file(root / rel) != digest:
            raise ContractError(f"frozen hash mismatch: {rel}")
    return manifest


def validate_schemas(root: Path, documents: dict[str, object]) -> None:
    try:
        import jsonschema
    except ImportError as exc:
        raise ContractError("jsonschema is required for the candidate schema check") from exc
    for doc_rel, schema_rel in SCHEMA_FOR.items():
        schema = _load_json(root / schema_rel)
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
            jsonschema.Draft202012Validator(schema).validate(documents[doc_rel])
        except jsonschema.exceptions.SchemaError as exc:
            raise ContractError(f"schema error in {schema_rel}: {exc.message}") from None
        except jsonschema.exceptions.ValidationError as exc:
            raise ContractError(f"schema: {doc_rel}: {exc.message}") from None


def _index(items: list, field: str, label: str) -> dict:
    out = {}
    for item in items:
        key = item[field]
        if key in out:
            raise ContractError(f"duplicate {label}: {key}")
        out[key] = item
    return out


class Bundle:
    def __init__(self, documents: dict[str, object]):
        self.identities = _index(documents["fixtures/identities.json"]["identities"], "identity_id", "identity")
        self.records = _index(documents["fixtures/raw_records.json"]["records"], "raw_record_id", "raw record")
        self.observations = documents["fixtures/observations.json"]["observations"]
        self.edges = documents["fixtures/lineage.json"]["edges"]
        self.checks = documents["fixtures/replay_checks.json"]["checks"]
        self.controls = documents["fixtures/negative_controls.json"]["controls"]
        self.feeds = documents["fixtures/qualification_matrix.json"]["feeds"]


def visible_versions(records: dict, natural_key: str, decision: datetime) -> list:
    rows = []
    for record in records.values():
        if record["natural_key"] != natural_key or record["duplicate_of"] is not None:
            continue
        if parse_time(record["accepted_at"]) <= decision:
            rows.append(record)
    rows.sort(key=lambda record: (parse_time(record["accepted_at"]), record["raw_record_id"]))
    return rows


def effective_record(records: dict, natural_key: str, decision: datetime):
    rows = visible_versions(records, natural_key, decision)
    return rows[-1] if rows else None


def _listing_key(identities: dict, identity_id: str):
    identity = identities.get(identity_id)
    if identity is None or identity["market_family"] != "equity":
        return None
    return identity["equity"]["listing_key"]


def _cik_in_listing_key(listing_key: object) -> str | None:
    if type(listing_key) is not str:
        return None
    parts = listing_key.split(":")
    if len(parts) < 5 or parts[:2] != ["us-equity", "cik"] or parts[3] != "class":
        return None
    cik = parts[2]
    if len(cik) != 10 or not cik.isdigit():
        return None
    return cik


def _issuer_cik_in_natural_key(natural_key: object) -> str | None:
    """Issuer CIK embedded in a key. An accession key names the filer, not the issuer."""
    if type(natural_key) is not str:
        return None
    if natural_key.startswith("sec:fact:"):
        parts = natural_key.split(":")
        cik = parts[2] if len(parts) > 2 else ""
    elif natural_key.startswith("us-equity:listing:"):
        parts = natural_key.split(":")
        cik = parts[2] if len(parts) > 2 else ""
    else:
        return None
    if len(cik) != 10 or not cik.isdigit():
        return None
    return cik


def _listing_key_in_natural_key(natural_key: object) -> str | None:
    if type(natural_key) is not str or not natural_key.startswith("us-equity:listing:"):
        return None
    parts = natural_key.split(":")
    if len(parts) < 4:
        return None
    cik, share = parts[2], parts[3]
    if len(cik) != 10 or not cik.isdigit() or not share:
        return None
    return f"us-equity:cik:{cik}:class:{share}"


# A form may supersede only its own family: the base form, or that base form's amendment.
FORM_FAMILY = {
    "10-K": frozenset({"10-K"}),
    "10-K/A": frozenset({"10-K", "10-K/A"}),
    "8-K": frozenset({"8-K"}),
    "8-K/A": frozenset({"8-K", "8-K/A"}),
}


def _action_tuple(record: dict) -> tuple:
    payload = record["payload"]
    return (
        payload["action_type"],
        payload["ratio_numerator"],
        payload["ratio_denominator"],
        payload["ex_date"],
        record["raw_record_id"],
    )


def _split_listing_key(bundle: Bundle, record: dict) -> str | None:
    """The one listing key shared by the record, its natural key, and both endpoints."""
    payload = record["payload"]
    keys = (
        _listing_key(bundle.identities, record.get("identity_id")),
        _listing_key(bundle.identities, payload.get("listing_identity_before")),
        _listing_key(bundle.identities, payload.get("listing_identity_after")),
        _listing_key_in_natural_key(record.get("natural_key")),
    )
    if any(key is None for key in keys) or len(set(keys)) != 1:
        return None
    return keys[0]


def _action_applies(bundle: Bundle, record: dict, listing: str) -> bool:
    payload = record["payload"]
    if payload.get("kind") != "corporate_action" or record["duplicate_of"] is not None:
        return False
    return _split_listing_key(bundle, record) == listing


def _input_identity_ok(bundle: Bundle, observation: dict, record: dict, decision: datetime) -> bool:
    if record["identity_id"] == observation["identity_id"]:
        return True
    if observation["market_family"] != "equity":
        return False
    listing = _listing_key(bundle.identities, observation["identity_id"])
    if listing is None:
        return False
    if _action_applies(bundle, record, listing):
        return True
    # A superseded ancestor stays lineage-only when a correction already visible
    # at the decision is on this same listing. A later correction cannot
    # authorize the ancestor.
    if record["payload"].get("kind") != "corporate_action":
        return False
    return any(
        other["natural_key"] == record["natural_key"]
        and other["duplicate_of"] is None
        and parse_time(other["accepted_at"]) <= decision
        and _action_applies(bundle, other, listing)
        for other in bundle.records.values()
    )


def _chain_leaves(records: list[dict]) -> list[dict]:
    """Latest record of each supersedes chain. Independent roots both remain."""
    ids = {record["raw_record_id"] for record in records}
    superseded = {
        record["supersedes"]
        for record in records
        if record["supersedes"] in ids
    }
    return [record for record in records if record["raw_record_id"] not in superseded]


def required_corporate_actions(bundle: Bundle, identity_id: str, decision: datetime) -> set[tuple]:
    """Effective action of every visible chain. Listing is judged on the leaf only."""
    listing = _listing_key(bundle.identities, identity_id)
    if listing is None:
        return set()
    grouped: dict[str, list[dict]] = {}
    for record in bundle.records.values():
        payload = record["payload"]
        if payload.get("kind") != "corporate_action" or record["duplicate_of"] is not None:
            continue
        if parse_time(record["accepted_at"]) > decision:
            continue
        grouped.setdefault(record["natural_key"], []).append(record)
    found = set()
    for group in grouped.values():
        for record in _chain_leaves(group):
            if _action_applies(bundle, record, listing):
                found.add(_action_tuple(record))
    return found


def _action_identity(action: dict) -> tuple:
    return (
        action["action_type"],
        action["ratio_numerator"],
        action["ratio_denominator"],
        action["ex_date"],
        action["raw_record_id"],
    )


def _require_unique_actions(observation: dict) -> None:
    """An observation lists each corporate action once. A set must not hide a repeat."""
    actions = observation.get("corporate_actions")
    if not actions:
        return
    seen: set[tuple] = set()
    for action in actions:
        identity = _action_identity(action)
        if identity in seen:
            raise ContractError(
                f"{observation['observation_id']} repeats corporate action {action['raw_record_id']}"
            )
        seen.add(identity)


def _observed_actions(observation: dict) -> set[tuple]:
    actions = observation.get("corporate_actions")
    if actions is None:
        return set()
    return {_action_identity(action) for action in actions}


def _unique(codes: list[str]) -> list[str]:
    seen = []
    for code in codes:
        if code not in seen:
            seen.append(code)
    return seen


def reconstruction_codes(bundle: Bundle, raw_ids: list, decision: datetime) -> list[str]:
    codes = []
    for raw_id in raw_ids:
        record = bundle.records.get(raw_id)
        if record is None or payload_sha256(record["payload"]) != record["raw_sha256"]:
            codes.append("raw_not_reconstructable")
            continue
        if parse_time(record["accepted_at"]) > decision:
            codes.append("future_data_leak")
    return _unique(codes)


def semantic_codes(bundle: Bundle, observation: dict, decision: datetime) -> list[str]:
    family = observation.get("market_family")
    natural_key = str(observation.get("natural_key") or "")
    if family == "event":
        identity = bundle.identities.get(observation.get("identity_id"))
        if identity is not None and identity["market_family"] != "event":
            return []
        if not natural_key.endswith(":resolution"):
            # Event observations exist only on resolution keys; a close_date or
            # other record's nullable fields must not surface as resolution data.
            return ["lost_resolution_semantics"]
        effective = effective_record(bundle.records, natural_key, decision)
        payload = effective["payload"] if effective is not None else None
        if (
            payload is None
            or observation.get("resolution") != payload.get("resolution")
            or observation.get("lifecycle_status") != payload.get("status_rest")
            or observation.get("resolution") is None
        ):
            return ["lost_resolution_semantics"]
        return []
    if family == "equity":
        required = required_corporate_actions(bundle, observation.get("identity_id"), decision)
        if _observed_actions(observation) != required:
            return ["lost_corporate_action_semantics"]
    return []


def _resolve_duplicate(bundle: Bundle, record: dict) -> dict | None:
    """The original this row copies, or None when the row is not a duplicate.

    Every duplicate_of lookup comes through here. The link must name a different
    record that exists, was accepted no later than this row, and is not itself a
    duplicate. A self-link, a missing or later target, and any chain or cycle fail.
    """
    target_id = record.get("duplicate_of")
    if not target_id:
        return None
    raw_id = record["raw_record_id"]
    if target_id == raw_id:
        raise ContractError(f"{raw_id} duplicate refers to itself")
    target = bundle.records.get(target_id)
    if target is None:
        raise ContractError(f"{raw_id} duplicate names a missing record")
    if parse_time(record["accepted_at"]) < parse_time(target["accepted_at"]):
        raise ContractError(f"{raw_id} duplicate precedes its target")
    if target.get("duplicate_of"):
        seen = {raw_id, target_id}
        current_id = target.get("duplicate_of")
        while type(current_id) is str:
            if current_id in seen:
                raise ContractError(f"{raw_id} duplicate cycle")
            seen.add(current_id)
            current = bundle.records.get(current_id)
            if current is None or not current.get("duplicate_of"):
                break
            current_id = current.get("duplicate_of")
        raise ContractError(f"{raw_id} duplicate chains through another duplicate")
    if target["natural_key"] != record["natural_key"] or target["raw_sha256"] != record["raw_sha256"]:
        raise ContractError(f"{raw_id} duplicate does not match its target")
    return target


def _is_genuine_duplicate(bundle: Bundle, raw_id: str) -> bool:
    """A claimed duplicate must resolve to an original with identical bytes."""
    record = bundle.records.get(raw_id)
    if record is None or not record.get("duplicate_of"):
        return False
    return _resolve_duplicate(bundle, record) is not None


def _family_collapse_code(bundle: Bundle, observation: dict) -> list[str]:
    """Silent semantic equivalence: an equity fact claimed under the event family."""
    family = observation.get("market_family")
    natural_key = str(observation.get("natural_key") or "")
    identity = bundle.identities.get(observation.get("identity_id"))
    if identity is not None and identity["market_family"] != family:
        return ["stock_treated_as_event"]
    if family == "event" and not natural_key.startswith("kalshi_demo:"):
        return ["stock_treated_as_event"]
    if family == "equity" and natural_key.startswith("kalshi_demo:"):
        return ["stock_treated_as_event"]
    return []


def assess_control(bundle: Bundle, control: dict) -> list[str]:
    decision = parse_time(control["decision_time"])
    codes = []
    claimed = control.get("claimed_visible_raw_record_ids")
    if claimed is not None:
        codes.extend(reconstruction_codes(bundle, claimed, decision))
    window_key = control.get("natural_key")
    if window_key is not None and not visible_versions(bundle.records, window_key, decision):
        codes.append("no_evidence")
    for raw_id in control.get("claimed_duplicate_raw_record_ids") or []:
        if not _is_genuine_duplicate(bundle, raw_id):
            codes.append("dedup_collapses_correction")
    observation = control.get("observation")
    if observation is not None:
        raw_codes = reconstruction_codes(bundle, observation.get("raw_record_ids") or [], decision)
        codes.extend(raw_codes)
        codes.extend(_family_collapse_code(bundle, observation))
        if "raw_not_reconstructable" not in raw_codes:
            codes.extend(semantic_codes(bundle, observation, decision))
    return _unique(codes)


def _require_family_clock(record: dict) -> None:
    calendar = record["calendar"]["calendar_id"]
    if record["market_family"] == "equity":
        if calendar != "XNYS" or record["payload"]["kind"] not in EQUITY_KINDS:
            raise ContractError(f"{record['raw_record_id']} mixes equity with a non-equity clock or payload")
        parse_date(record["calendar"]["session_date"])
    elif record["market_family"] == "event":
        if calendar != "kalshi_demo_exchange" or record["payload"]["kind"] != EVENT_KIND:
            raise ContractError(f"{record['raw_record_id']} mixes an event market with an equity clock or payload")
    else:
        raise ContractError(f"unknown market family on {record['raw_record_id']}")


def _require_event_natural_key(record: dict, identity: dict) -> None:
    ticker = identity["event"]["market_ticker"].lower()
    prefix = f"{identity['venue']}:market:{ticker}:"
    if not record["natural_key"].startswith(prefix):
        raise ContractError(f"{record['raw_record_id']} natural key does not name its market identity")
    payload = record["payload"]
    if payload.get("kind") != "lifecycle":
        return
    suffix = LIFECYCLE_KEY_SUFFIX.get(payload.get("event_type"))
    if suffix is None or record["natural_key"] != prefix + suffix:
        raise ContractError(
            f"{record['raw_record_id']} natural key suffix does not match its event_type"
        )


def _filer_cik(accession: object) -> str | None:
    if type(accession) is not str or len(accession) < 11 or accession[10] != "-":
        return None
    prefix = accession[:10]
    if not prefix.isdigit():
        return None
    return prefix


def _identity_issuer(identity: dict) -> str:
    """Issuer CIK bound to this equity identity's listing key and share class."""
    equity = identity["equity"]
    issuer = equity["issuer_cik"]
    expected = f"us-equity:cik:{issuer}:class:{equity['share_class']}"
    if equity.get("listing_key") != expected or _cik_in_listing_key(equity.get("listing_key")) != issuer:
        raise ContractError(
            f"{identity['identity_id']} listing_key contradicts its issuer_cik or share_class"
        )
    return issuer


def _bind_record_issuer(record: dict, identity: dict) -> str:
    """One issuer CIK for every equity record, whatever natural-key shape it uses.

    The identity, its listing key, and any issuer CIK embedded in the natural key
    must agree. A filing or amendment then binds that same CIK to its accession,
    on a sec:fact key, an accession key, or a listing key.
    """
    raw_id = record["raw_record_id"]
    issuer = _identity_issuer(identity)
    key_cik = _issuer_cik_in_natural_key(record.get("natural_key"))
    if key_cik is not None and key_cik != issuer:
        raise ContractError(f"{raw_id} issuer CIK is not bound")
    payload = record["payload"]
    explicit = payload.get("issuer_cik")
    if explicit is not None and explicit != issuer:
        raise ContractError(f"{raw_id} issuer CIK is not bound")
    if payload.get("kind") in {"filing", "filing_amendment"}:
        _require_accession_issuer(raw_id, payload.get("accession"), issuer, payload)
    return issuer


def _require_accession_issuer(raw_id: str, accession: object, issuer_cik: str, payload: dict) -> None:
    """The filer prefix may be the issuer or a filing agent.

    The record must carry the issuer CIK, and that CIK must be the fact's issuer.
    An issuer-filed accession carries the issuer in its prefix. An agent-filed
    accession is allowed only when payload.issuer_cik is that same issuer.
    """
    filer = _filer_cik(accession)
    if filer is None:
        raise ContractError(f"{raw_id} accession does not name a filer")
    carried = payload.get("issuer_cik")
    if filer == issuer_cik:
        if carried is not None and carried != issuer_cik:
            raise ContractError(f"{raw_id} accession issuer does not match its fact")
        return
    if carried != issuer_cik:
        raise ContractError(f"{raw_id} agent-filed accession lacks its issuer CIK")


def _require_equity_natural_key(record: dict, identity: dict) -> None:
    """The CIK, listing, accession, and fact embedded in an equity key must match the record."""
    key = record["natural_key"]
    equity = identity["equity"]
    share = equity["share_class"]
    payload = record["payload"]
    kind = payload.get("kind")
    raw_id = record["raw_record_id"]
    cik = _bind_record_issuer(record, identity)
    if key.startswith("sec:fact:"):
        parts = key.split(":")
        if len(parts) < 5 or parts[2] != cik or parts[3] != payload.get("concept"):
            raise ContractError(f"{raw_id} natural key does not name its equity fact")
        return
    if key.startswith("sec:accession:"):
        accession = key[len("sec:accession:") :]
        if accession != payload.get("accession"):
            raise ContractError(f"{raw_id} natural key does not name its accession")
        return
    listing_prefix = f"us-equity:listing:{cik}:{share}:"
    if key.startswith(listing_prefix):
        _require_listing_tail(record, key[len(listing_prefix) :], kind, payload)
        return
    raise ContractError(f"{raw_id} natural key does not name its equity identity")


def _require_listing_tail(record: dict, rest: str, kind: object, payload: dict) -> None:
    """A listing key ends in its payload kind, or in corporate_action:ex-<date>."""
    raw_id = record["raw_record_id"]
    if kind == "corporate_action":
        if rest == "corporate_action":
            return
        prefix = "corporate_action:ex-"
        if rest.startswith(prefix) and rest.count(":") == 1:
            ex_date = rest[len(prefix) :]
            parse_date(ex_date)
            # The suffix names the chain's original ex-date. A correction may move it.
            if not record["supersedes"] and ex_date != payload.get("ex_date"):
                raise ContractError(f"{raw_id} natural key does not name its ex-date")
            return
        raise ContractError(f"{raw_id} natural key suffix is not a corporate action")
    if rest != kind:
        raise ContractError(f"{raw_id} natural key does not name its equity fact")


def _chain_accessions(bundle: Bundle, record: dict) -> set[str]:
    """Every accession named by an earlier record in this correction chain."""
    found: set[str] = set()
    seen: set[str] = set()
    parent_id = record.get("supersedes")
    while type(parent_id) is str and parent_id not in seen:
        seen.add(parent_id)
        parent = bundle.records.get(parent_id)
        if parent is None or parent["natural_key"] != record["natural_key"]:
            break
        payload = parent["payload"]
        for field in ("accession", "amends_accession"):
            value = payload.get(field)
            if type(value) is str:
                found.add(value)
        parent_id = parent.get("supersedes")
    return found


def _predecessor_resolution(bundle: Bundle, record: dict):
    """Resolution supplied by an earlier record in this correction chain."""
    seen: set[str] = set()
    if record.get("duplicate_of"):
        original = _resolve_duplicate(bundle, record)
        parent_id = None if original is None else original["raw_record_id"]
    else:
        parent_id = record.get("supersedes")
    while type(parent_id) is str and parent_id not in seen:
        seen.add(parent_id)
        parent = bundle.records.get(parent_id)
        if parent is None or parent["natural_key"] != record["natural_key"]:
            return None
        if parse_time(parent["accepted_at"]) > parse_time(record["accepted_at"]):
            return None
        resolution = parent["payload"].get("resolution")
        if resolution is not None:
            return resolution
        parent_id = parent.get("supersedes")
    return None


def validate_records(bundle: Bundle) -> None:
    families: dict[str, str] = {}
    for record in bundle.records.values():
        identity = bundle.identities.get(record["identity_id"])
        if identity is None or identity["market_family"] != record["market_family"]:
            raise ContractError(f"{record['raw_record_id']} identity family does not match")
        if record["market_family"] == "event":
            _require_event_natural_key(record, identity)
        elif record["market_family"] == "equity":
            _require_equity_natural_key(record, identity)
        _require_family_clock(record)
        if record["payload"]["kind"] not in SOURCE_KINDS[record["source"]]:
            raise ContractError(f"{record['raw_record_id']} source does not emit {record['payload']['kind']} payloads")
        event_time = parse_time(record["event_time"])
        accepted_at = parse_time(record["accepted_at"])
        if event_time > accepted_at:
            raise ContractError(f"{record['raw_record_id']} event_time is after accepted_at")
        if payload_sha256(record["payload"]) != record["raw_sha256"]:
            raise ContractError(f"{record['raw_record_id']} raw bytes do not reconstruct")
        prior = families.setdefault(record["natural_key"], record["market_family"])
        if prior != record["market_family"]:
            raise ContractError(f"natural key crosses market families: {record['natural_key']}")
        if record["duplicate_of"] and record["supersedes"]:
            raise ContractError(f"{record['raw_record_id']} is both a duplicate and a correction")
        _resolve_duplicate(bundle, record)
        if record["supersedes"]:
            target = bundle.records.get(record["supersedes"])
            if target is None or target["natural_key"] != record["natural_key"]:
                raise ContractError(f"{record['raw_record_id']} supersedes a different fact")
            if record["raw_sha256"] == target["raw_sha256"]:
                raise ContractError(f"{record['raw_record_id']} correction repeats its predecessor's bytes")
            if target["source"] != record["source"]:
                raise ContractError(f"{record['raw_record_id']} correction changes its source")
            kind = record["payload"].get("kind")
            compatible = kind == target["payload"].get("kind") or (
                target["payload"].get("kind") == "filing" and kind == "filing_amendment"
            )
            if not compatible:
                raise ContractError(f"{record['raw_record_id']} correction changes payload kind")
            if record["payload"].get("kind") in {"filing", "filing_amendment"}:
                allowed = FORM_FAMILY.get(record["payload"].get("form"))
                if allowed is None or target["payload"].get("form") not in allowed:
                    raise ContractError(
                        f"{record['raw_record_id']} form family does not match its predecessor"
                    )
            if kind == "filing_amendment":
                amended = target["payload"]
                amendment = record["payload"]
                # A later 10-K/A may name the original filing, not only its predecessor.
                chain_accessions = _chain_accessions(bundle, record)
                if amendment.get("amends_accession") not in chain_accessions:
                    raise ContractError(
                        f"{record['raw_record_id']} amends_accession does not name the superseded filing"
                    )
                if amendment.get("accession") in chain_accessions:
                    raise ContractError(
                        f"{record['raw_record_id']} amendment reuses a predecessor accession"
                    )
                for field in ("concept", "unit"):
                    if amendment.get(field) != amended.get(field):
                        raise ContractError(
                            f"{record['raw_record_id']} amendment changes stable fact field {field}"
                        )
            if parse_time(target["accepted_at"]) >= parse_time(record["accepted_at"]):
                raise ContractError(f"{record['raw_record_id']} correction is not later knowledge")
        payload = record["payload"]
        if payload.get("kind") == "lifecycle":
            voided = payload["status_rest"] == "voided" or payload["resolution"] == "voided"
            if voided != (payload["status_rest"] == "voided" and payload["resolution"] == "voided"):
                raise ContractError(f"{record['raw_record_id']} splits void across status and resolution")
            if voided and (payload["official_lifecycle_status_observed"] or payload["result"] is not None):
                raise ContractError(f"{record['raw_record_id']} claims void was an observed official result")
            if not voided and not payload["official_lifecycle_status_observed"]:
                raise ContractError(f"{record['raw_record_id']} drops an observed lifecycle status")
            if payload["result"] is not None and payload["result"] != payload["resolution"]:
                raise ContractError(f"{record['raw_record_id']} result contradicts its resolution")
            resolutions = CONTRACT_RESOLUTIONS[identity["event"]["contract_family"]]
            for field in ("resolution", "result"):
                value = payload[field]
                if value not in (None, "voided") and value not in resolutions:
                    raise ContractError(f"{record['raw_record_id']} {field} contradicts its contract family")
            status = payload["status_rest"]
            if status not in LIFECYCLE_STATUSES.get(payload["event_type"], set()):
                raise ContractError(f"{record['raw_record_id']} lifecycle state contradicts its event_type")
            if status in UNRESOLVED_STATUSES:
                if payload["resolution"] is not None or payload["result"] is not None:
                    raise ContractError(f"{record['raw_record_id']} unresolved lifecycle state carries a result")
            elif not voided:
                # Socket result exists only when determined; a settled message may omit it.
                result_optional = payload["event_type"] == "settled" and payload["channel"] != "rest_get_markets"
                if payload["resolution"] not in resolutions or (
                    payload["result"] != payload["resolution"] and not (result_optional and payload["result"] is None)
                ):
                    raise ContractError(f"{record['raw_record_id']} resolved lifecycle state has no matching result")
                if result_optional and payload["result"] is None:
                    supplied = _predecessor_resolution(bundle, record)
                    if supplied != payload["resolution"]:
                        raise ContractError(
                            f"{record['raw_record_id']} settled resolution has no predecessor"
                        )
            if payload["event_type"] == "close_date_updated":
                if payload.get("close_time") is None:
                    raise ContractError(f"{record['raw_record_id']} close-date update has no close_time")
                parse_time(payload["close_time"])
            elif payload.get("close_time") is not None:
                raise ContractError(f"{record['raw_record_id']} carries a close_time without a close-date update")
            # Documented optionals: determination_ts/result/settlement_value exist
            # only when determined; settled_ts only when settled; settlement_ts
            # is the REST market field, never a market_lifecycle_v2 field.
            if payload.get("determination_ts") is not None and payload["event_type"] != "determined":
                raise ContractError(f"{record['raw_record_id']} carries determination_ts outside a determined event")
            if payload.get("determination_ts") is not None:
                determination_ts = parse_unix_time(payload["determination_ts"])
                if determination_ts > accepted_at:
                    raise ContractError(f"{record['raw_record_id']} determination_ts is after accepted_at")
            if payload.get("settlement_value") is not None and payload["event_type"] != "determined":
                raise ContractError(f"{record['raw_record_id']} carries settlement_value outside a determined event")
            if payload.get("settled_ts") is not None and payload["event_type"] != "settled":
                raise ContractError(f"{record['raw_record_id']} carries settled_ts outside a settled event")
            if payload.get("settled_ts") is not None:
                settled_ts = parse_unix_time(payload["settled_ts"])
                if settled_ts > accepted_at:
                    raise ContractError(f"{record['raw_record_id']} settled_ts is after accepted_at")
            if payload.get("settlement_ts") is not None and payload["channel"] != "rest_get_markets":
                raise ContractError(f"{record['raw_record_id']} carries REST settlement_ts on {payload['channel']}")
            if payload.get("settlement_ts") is not None:
                settlement_ts = parse_time(payload["settlement_ts"])
                if payload["status_rest"] != "finalized":
                    raise ContractError(f"{record['raw_record_id']} carries settlement_ts before finalized")
                if settlement_ts > accepted_at:
                    raise ContractError(f"{record['raw_record_id']} settlement_ts is after accepted_at")
            # status_observed is a REST poll; the socket never emits it (disputed/amended).
            if (payload["event_type"] == "status_observed") != (payload["channel"] == "rest_get_markets"):
                raise ContractError(f"{record['raw_record_id']} pairs {payload['event_type']} with {payload['channel']}")
        if payload.get("kind") == "corporate_action":
            parse_date(payload["ex_date"])
            for field in ("listing_identity_before", "listing_identity_after"):
                ref = bundle.identities.get(payload[field])
                if ref is None or ref["market_family"] != "equity":
                    raise ContractError(f"{record['raw_record_id']} has an unresolved listing reference")
            if _split_listing_key(bundle, record) is None:
                raise ContractError(f"{record['raw_record_id']} split endpoints are not one listing")
    by_key: dict[str, list[dict]] = {}
    for record in bundle.records.values():
        by_key.setdefault(record["natural_key"], []).append(record)
    for group in by_key.values():
        _require_version_chain(group)
    for identity in bundle.identities.values():
        if identity["market_family"] == "equity":
            _identity_issuer(identity)
    _require_identity_lineage(bundle)


def _instrument_key(identity: dict) -> tuple:
    family = identity["market_family"]
    stable = "listing_key" if family == "equity" else "market_ticker"
    return (family, identity[family][stable])


def _require_identity_lineage(bundle: Bundle) -> None:
    """Version 1 is a root. Every later version names the previous version of its instrument.

    This is the only identity-version check. A null prior on a later version does not
    skip the lineage. The prior must be the same instrument at exactly the previous version.
    """
    claimed: dict[tuple, str] = {}
    for identity in bundle.identities.values():
        identity_id = identity["identity_id"]
        version = identity["identity_version"]
        instrument = _instrument_key(identity)
        slot = (*instrument, version)
        if slot in claimed:
            raise ContractError(f"{identity_id} repeats identity version {version}")
        claimed[slot] = identity_id
        prior_id = identity.get("prior_identity_id")
        if version == 1:
            if prior_id is not None:
                raise ContractError(f"{identity_id} first identity version names a prior")
            continue
        if not prior_id:
            raise ContractError(f"{identity_id} identity version does not name its prior")
        prior = bundle.identities.get(prior_id)
        if prior is None or prior["market_family"] != identity["market_family"]:
            raise ContractError(f"{identity_id} prior identity crosses families")
        if _instrument_key(prior) != instrument:
            raise ContractError(f"{identity_id} prior identity changes instrument")
        if prior.get("identity_version") != version - 1:
            raise ContractError(f"{identity_id} identity version does not follow its prior")


def _require_version_chain(records: list[dict]) -> None:
    """One natural key is one correction chain. Independent actions need their own keys."""
    versions = [record for record in records if record["duplicate_of"] is None]
    if not versions:
        return
    by_id = {record["raw_record_id"]: record for record in versions}
    children: dict[str, list[str]] = {record["raw_record_id"]: [] for record in versions}
    roots = []
    key = versions[0]["natural_key"]
    for record in versions:
        parent = record["supersedes"]
        if parent is None:
            roots.append(record["raw_record_id"])
            continue
        if parent not in by_id:
            raise ContractError(
                f"{record['raw_record_id']} correction does not continue the natural-key chain"
            )
        children[parent].append(record["raw_record_id"])
    if len(roots) != 1:
        raise ContractError(
            f"natural key {key} has independent actions; each action needs its own natural key"
        )
    seen = []
    current: str | None = roots[0]
    while current is not None:
        seen.append(current)
        nxt = children[current]
        if len(nxt) > 1:
            raise ContractError(f"natural key {key} branches; a correction replaces one predecessor")
        current = nxt[0] if nxt else None
    if len(seen) != len(versions):
        raise ContractError(f"natural key {key} is not a single correction chain")


def coverage_scenario(check: dict) -> dict:
    """The scenario a replay check id promises: key, decision time, and outcome."""
    outcome = {
        "visible_raw_record_ids": list(check["visible_raw_record_ids"]),
        "hidden_raw_record_ids": list(check["hidden_raw_record_ids"]),
        "effective_raw_record_id": check["effective_raw_record_id"],
    }
    if "expected_close_time" in check:
        outcome["expected_close_time"] = check["expected_close_time"]
    return {
        "check_id": check["check_id"],
        "natural_key": check["natural_key"],
        "decision_time": check["decision_time"],
        "outcome": outcome,
    }


_COVERAGE_DIGEST = re.compile(r"Coverage manifest SHA-256: `([0-9a-f]{64})`")


# Minimum replay scenarios, fixed here so the regenerable coverage fixture cannot drop one.
REQUIRED_CHECK_IDS = frozenset(
    {
        "late_correction_before_amendment",
        "duplicate_does_not_supersede",
        "late_correction_after_amendment",
        "out_of_order_acceptance_hidden",
        "out_of_order_acceptance_visible_after_accept",
        "corporate_action_retained",
        "corporate_action_correction_is_effective",
        "independent_corporate_action_hidden",
        "independent_corporate_action_retained",
        "independent_action_does_not_replace_correction",
        "unadjusted_quote_not_rewritten",
        "event_resolution_before_late_message",
        "event_close_out_of_order_hidden",
        "event_close_visible_only_after_accept",
        "event_settled_keeps_determination_raw",
        "voided_event_stays_distinct",
        "event_dispute_chain_determined_only",
        "event_dispute_correction_is_effective",
        "event_amendment_supersedes_dispute",
        "event_finalized_keeps_dispute_chain",
    }
)


def validate_replay(bundle: Bundle) -> None:
    check_ids = [check["check_id"] for check in bundle.checks]
    if len(check_ids) != len(set(check_ids)):
        raise ContractError("duplicate replay check")
    if not REQUIRED_CHECK_IDS <= set(check_ids):
        raise ContractError("required replay scenario is missing")
    for check in bundle.checks:
        decision = parse_time(check["decision_time"])
        visible = visible_versions(bundle.records, check["natural_key"], decision)
        visible_ids = [record["raw_record_id"] for record in visible]
        hidden_ids = sorted(
            record["raw_record_id"]
            for record in bundle.records.values()
            if record["natural_key"] == check["natural_key"] and record["raw_record_id"] not in visible_ids
        )
        if visible_ids != sorted(check["visible_raw_record_ids"], key=lambda raw_id: (
            parse_time(bundle.records[raw_id]["accepted_at"]), raw_id
        )):
            raise ContractError(f"{check['check_id']} visible set mismatch: {visible_ids}")
        if hidden_ids != sorted(check["hidden_raw_record_ids"]):
            raise ContractError(f"{check['check_id']} hidden set mismatch: {hidden_ids}")
        effective = visible[-1]["raw_record_id"] if visible else None
        if effective != check["effective_raw_record_id"]:
            raise ContractError(f"{check['check_id']} effective record mismatch: {effective}")
        if "expected_close_time" in check:
            payload = bundle.records[effective]["payload"] if effective is not None else {}
            if payload.get("close_time") != check["expected_close_time"]:
                raise ContractError(f"{check['check_id']} close_time was not preserved")


def _missing_raw_parents(bundle: Bundle, raw_ids: list, decision: datetime) -> list[str]:
    """Visible supersedes ancestors that the observation did not retain."""
    missing = []
    seen = set()
    stack = list(raw_ids)
    while stack:
        raw_id = stack.pop()
        if raw_id in seen:
            continue
        seen.add(raw_id)
        record = bundle.records.get(raw_id)
        if record is None:
            continue
        parent_id = record["supersedes"]
        if not parent_id or parent_id in seen:
            continue
        parent = bundle.records.get(parent_id)
        if parent is None or parse_time(parent["accepted_at"]) > decision:
            continue
        if parent_id not in raw_ids and parent_id not in missing:
            missing.append(parent_id)
        stack.append(parent_id)
    return missing


def _fact_value(record: dict):
    payload = record["payload"]
    return payload["value"] if "value" in payload else None


def validate_observations(bundle: Bundle) -> None:
    observed = _index(bundle.observations, "observation_id", "observation")
    edges = _index(bundle.edges, "observation_id", "lineage observation")
    if set(observed) != set(edges):
        raise ContractError("lineage does not cover each observation once")
    for observation in bundle.observations:
        _require_unique_actions(observation)
        decision = parse_time(observation["decision_time"])
        codes = reconstruction_codes(bundle, observation["raw_record_ids"], decision)
        codes.extend(semantic_codes(bundle, observation, decision))
        if codes:
            raise ContractError(
                f"{observation['observation_id']} tripped {codes}; the positive corpus must not"
            )
        effective = effective_record(bundle.records, observation["natural_key"], decision)
        if effective is None or effective["raw_record_id"] != observation["effective_raw_record_id"]:
            raise ContractError(f"{observation['observation_id']} effective raw id mismatch")
        if observation["effective_raw_record_id"] not in observation["raw_record_ids"]:
            raise ContractError(f"{observation['observation_id']} effective raw id is not retained")
        missing_parents = _missing_raw_parents(bundle, observation["raw_record_ids"], decision)
        if missing_parents:
            raise ContractError(
                f"{observation['observation_id']} dropped raw parent {','.join(missing_parents)}"
            )
        if _fact_value(effective) != observation["value"]:
            raise ContractError(f"{observation['observation_id']} value does not match raw")
        identity = bundle.identities[observation["identity_id"]]
        if identity["market_family"] != observation["market_family"]:
            raise ContractError(f"{observation['observation_id']} collapses market families")
        if observation["identity_id"] != effective["identity_id"]:
            raise ContractError(
                f"{observation['observation_id']} identity does not match its effective raw record"
            )
        for raw_id in observation["raw_record_ids"]:
            if not _input_identity_ok(bundle, observation, bundle.records[raw_id], decision):
                raise ContractError(f"{observation['observation_id']} input {raw_id} uses another identity")
        for action in observation["corporate_actions"] or []:
            raw_id = action["raw_record_id"]
            if raw_id not in observation["raw_record_ids"]:
                raise ContractError(f"{observation['observation_id']} action {raw_id} has no lineage input")
            source = bundle.records[raw_id]
            if source["payload"].get("kind") != "corporate_action" or _action_tuple(source) != (
                action["action_type"],
                action["ratio_numerator"],
                action["ratio_denominator"],
                action["ex_date"],
                raw_id,
            ):
                raise ContractError(f"{observation['observation_id']} action {raw_id} does not match its raw record")
        edge = edges[observation["observation_id"]]
        if edge["input_raw_record_ids"] != observation["raw_record_ids"]:
            raise ContractError(f"{observation['observation_id']} lineage inputs differ from raw ids")
        for raw_id in edge["input_raw_record_ids"]:
            if payload_sha256(bundle.records[raw_id]["payload"]) != bundle.records[raw_id]["raw_sha256"]:
                raise ContractError(f"{observation['observation_id']} lineage raw does not reconstruct")


def validate_controls(bundle: Bundle) -> list[str]:
    if [control["control_id"] for control in bundle.controls] != list(NEGATIVE_CODES):
        raise ContractError("negative-control ids drifted from the required falsifiers")
    lines = []
    for control in bundle.controls:
        if control["expect"] != [control["control_id"]]:
            raise ContractError(f"{control['control_id']} expect list drifted")
        codes = assess_control(bundle, control)
        if codes != control["expect"]:
            raise ContractError(
                f"{control['control_id']} did not fail as designed: expected {control['expect']} got {codes}"
            )
        lines.append(
            "O2 NEGATIVE CONTROL FAIL AS DESIGNED: "
            f"{control['control_id']} codes={','.join(codes)}"
        )
    return lines


# Decided feed rows: market family, qualification, credentials required.
# Admitted acquisition is false for every feed. This is the only qualification check.
FEED_DECISIONS = {
    "o2_synthetic_fixture_corpus": ("cross_market", "not_qualified", False),
    "sec_edgar_submissions": ("equity", "public_documented", False),
    "sec_edgar_companyfacts": ("equity", "public_documented", False),
    "sec_edgar_frames": ("equity", "disqualified_as_asof_source", False),
    "kalshi_demo_rest_trade_api": ("event", "public_documented", True),
    "kalshi_demo_ws_market_lifecycle_v2": ("event", "public_documented", True),
    "local_edgar_pipeline": ("equity", "local_observed", False),
    "local_filings_raw_store": ("equity", "local_observed", False),
    "local_parquet_snapshot": ("equity", "local_observed", False),
    "local_quant_replay": ("equity", "local_observed", False),
    "local_ingest_dedup": ("equity", "local_observed", False),
    "alpaca_benzinga_and_finnhub": ("equity", "not_qualified", True),
}


def validate_qualification(bundle: Bundle) -> None:
    """Every feed must match the decided qualification table. No other feed is admitted."""
    if not bundle.feeds:
        raise ContractError("qualification matrix is empty")
    seen: list[str] = []
    for feed in bundle.feeds:
        feed_id = feed["feed_id"]
        if feed_id in seen:
            raise ContractError(f"duplicate feed {feed_id}")
        seen.append(feed_id)
        expected = FEED_DECISIONS.get(feed_id)
        if expected is None:
            raise ContractError(f"{feed_id} is not a decided feed")
        family, qualification, credentials = expected
        if (
            feed["market_family"] != family
            or feed["qualification"] != qualification
            or feed["credentials_required"] is not credentials
        ):
            raise ContractError(f"{feed_id} qualification decision does not match")
        if feed["admitted_for_acquisition"]:
            raise ContractError(f"{feed_id} admits acquisition")
    if set(seen) != set(FEED_DECISIONS):
        raise ContractError("qualification decisions are not the required set")


def validate_coverage(root: Path, bundle: Bundle) -> None:
    """Replay scenarios and observation ids must equal the coverage manifest.

    The manifest digest recorded in the contract is the pin. A swapped scenario
    or a deleted observation fails this equality even when the remaining corpus
    is internally consistent.
    """
    path = root / "fixtures" / "coverage.json"
    digest = _sha_file(path)
    recorded = _COVERAGE_DIGEST.findall((root / "CONTRACT.md").read_text(encoding="utf-8"))
    if recorded != [digest]:
        raise ContractError("coverage manifest digest does not match the contract")
    coverage = _load_json(path)
    if not isinstance(coverage, dict):
        raise ContractError("coverage manifest root must be an object")
    expected_checks = coverage.get("checks")
    if not isinstance(expected_checks, list) or not all(
        isinstance(item, dict) and "check_id" in item for item in expected_checks
    ):
        raise ContractError("coverage manifest checks are malformed")
    by_id = {item["check_id"]: item for item in expected_checks}
    if len(by_id) != len(expected_checks):
        raise ContractError("coverage manifest repeats a check")
    actual = {check["check_id"]: coverage_scenario(check) for check in bundle.checks}
    if actual != by_id:
        raise ContractError("replay scenario does not match the coverage manifest")
    observation_ids = [observation["observation_id"] for observation in bundle.observations]
    expected_ids = coverage.get("observation_ids")
    if (
        not isinstance(expected_ids, list)
        or len(set(observation_ids)) != len(observation_ids)
        or sorted(observation_ids) != sorted(expected_ids)
    ):
        raise ContractError("observation set does not match the coverage manifest")


def validate_candidate(root: Path | None = None) -> list[str]:
    root = ROOT if root is None else root
    documents = {rel: _load_json(root / rel) for rel in (*SCHEMA_FOR,)}
    verify_manifest(root)
    validate_schemas(root, documents)
    bundle = Bundle(documents)
    validate_records(bundle)
    validate_replay(bundle)
    validate_observations(bundle)
    validate_coverage(root, bundle)
    validate_qualification(bundle)
    lines = [
        "O2 candidate corpus: ok",
        "O2 maturity: researched",
        *validate_controls(bundle),
    ]
    return lines


def main() -> int:
    try:
        lines = validate_candidate()
    except ContractError as exc:
        print(f"O2 candidate: FAIL: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    print(
        "O2 candidate command exit 0 means the corpus replayed and every negative "
        "control failed as designed. Not claimed: contract_frozen, PASS, qualification, "
        "provider_free_verified."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
