"""Provider-free validator for the CSL-214 O2 candidate evidence contract.

This module is a candidate artifact. It does not ingest, trade, or call a provider.
Exit 0 means the frozen corpus replayed and every negative control failed as designed.
That is not contract_frozen, not an empirical PASS, and not feed qualification.
"""
from __future__ import annotations

import hashlib
import json
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
    "fixtures/qualification_matrix.json",
    "fixtures/negative_controls.json",
    "schemas/identity.schema.json",
    "schemas/raw_record.schema.json",
    "schemas/observation.schema.json",
    "schemas/lineage.schema.json",
    "schemas/replay_manifest.schema.json",
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


def _action_tuple(record: dict) -> tuple:
    payload = record["payload"]
    return (
        payload["action_type"],
        payload["ratio_numerator"],
        payload["ratio_denominator"],
        payload["ex_date"],
        record["raw_record_id"],
    )


def _action_applies(bundle: Bundle, record: dict, listing: str) -> bool:
    payload = record["payload"]
    if payload.get("kind") != "corporate_action" or record["duplicate_of"] is not None:
        return False
    before = _listing_key(bundle.identities, payload["listing_identity_before"])
    after = _listing_key(bundle.identities, payload["listing_identity_after"])
    return listing in (before, after)


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
    # at the decision moves that chain onto this listing. A later correction
    # cannot authorize the ancestor.
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


def _observed_actions(observation: dict) -> set[tuple]:
    actions = observation.get("corporate_actions")
    if actions is None:
        return set()
    return {
        (
            action["action_type"],
            action["ratio_numerator"],
            action["ratio_denominator"],
            action["ex_date"],
            action["raw_record_id"],
        )
        for action in actions
    }


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


def _is_genuine_duplicate(bundle: Bundle, raw_id: str) -> bool:
    """A claimed duplicate must be a recorded duplicate with identical bytes."""
    record = bundle.records.get(raw_id)
    if record is None or record["duplicate_of"] is None:
        return False
    target = bundle.records.get(record["duplicate_of"])
    return target is not None and target["raw_sha256"] == record["raw_sha256"]


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


def _require_equity_natural_key(record: dict, identity: dict) -> None:
    """The CIK, listing, accession, and fact embedded in an equity key must match the record."""
    key = record["natural_key"]
    equity = identity["equity"]
    cik = equity["issuer_cik"]
    share = equity["share_class"]
    payload = record["payload"]
    kind = payload.get("kind")
    raw_id = record["raw_record_id"]
    if key.startswith("sec:fact:"):
        parts = key.split(":")
        if len(parts) < 5 or parts[2] != cik or parts[3] != payload.get("concept"):
            raise ContractError(f"{raw_id} natural key does not name its equity fact")
        return
    if key.startswith("sec:accession:"):
        accession = key[len("sec:accession:") :]
        if accession != payload.get("accession") or not accession.startswith(f"{cik}-"):
            raise ContractError(f"{raw_id} natural key does not name its accession")
        return
    listing_prefix = f"us-equity:listing:{cik}:{share}:"
    if key.startswith(listing_prefix):
        rest = key[len(listing_prefix) :]
        fact = rest.split(":", 1)[0]
        if fact != kind:
            raise ContractError(f"{raw_id} natural key does not name its equity fact")
        if rest.startswith("corporate_action:ex-") and rest[len("corporate_action:ex-") :] != payload.get("ex_date"):
            raise ContractError(f"{raw_id} natural key does not name its ex-date")
        return
    raise ContractError(f"{raw_id} natural key does not name its equity identity")


def _predecessor_resolution(bundle: Bundle, record: dict):
    """Resolution supplied by an earlier record in this correction chain."""
    seen: set[str] = set()
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
        if record["duplicate_of"]:
            target = bundle.records.get(record["duplicate_of"])
            if (
                target is None
                or target["natural_key"] != record["natural_key"]
                or target["raw_sha256"] != record["raw_sha256"]
            ):
                raise ContractError(f"{record['raw_record_id']} duplicate does not match its target")
            if parse_time(record["accepted_at"]) < parse_time(target["accepted_at"]):
                raise ContractError(f"{record['raw_record_id']} duplicate precedes its target")
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
            if kind == "filing_amendment":
                amended = target["payload"]
                amendment = record["payload"]
                if amendment.get("amends_accession") != amended.get("accession"):
                    raise ContractError(
                        f"{record['raw_record_id']} amends_accession does not name the superseded filing"
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
            if payload.get("settlement_value") is not None and payload["event_type"] != "determined":
                raise ContractError(f"{record['raw_record_id']} carries settlement_value outside a determined event")
            if payload.get("settled_ts") is not None and payload["event_type"] != "settled":
                raise ContractError(f"{record['raw_record_id']} carries settled_ts outside a settled event")
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
    by_key: dict[str, list[dict]] = {}
    for record in bundle.records.values():
        by_key.setdefault(record["natural_key"], []).append(record)
    for group in by_key.values():
        _require_version_chain(group)
    for identity in bundle.identities.values():
        if identity["market_family"] == "equity":
            equity = identity["equity"]
            expected = f"us-equity:cik:{equity['issuer_cik']}:class:{equity['share_class']}"
            if equity["listing_key"] != expected:
                raise ContractError(f"{identity['identity_id']} listing_key contradicts its issuer_cik or share_class")
        prior_id = identity["prior_identity_id"]
        if prior_id is None:
            continue
        prior = bundle.identities.get(prior_id)
        if prior is None or prior["market_family"] != identity["market_family"]:
            raise ContractError(f"{identity['identity_id']} prior identity crosses families")
        family = identity["market_family"]
        stable_key = "listing_key" if family == "equity" else "market_ticker"
        if identity[family][stable_key] != prior[family][stable_key]:
            raise ContractError(f"{identity['identity_id']} prior identity changes instrument")
        if identity["identity_version"] <= prior["identity_version"]:
            raise ContractError(f"{identity['identity_id']} version does not advance")


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


def validate_replay(bundle: Bundle) -> None:
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


def validate_qualification(bundle: Bundle) -> None:
    if not bundle.feeds:
        raise ContractError("qualification matrix is empty")
    for feed in bundle.feeds:
        if feed["qualification"] in {"qualified", "pass", "provider_free_verified"}:
            raise ContractError(f"{feed['feed_id']} claims qualification")
        if feed["admitted_for_acquisition"]:
            raise ContractError(f"{feed['feed_id']} admits acquisition")


def validate_candidate(root: Path | None = None) -> list[str]:
    root = ROOT if root is None else root
    documents = {rel: _load_json(root / rel) for rel in (*SCHEMA_FOR,)}
    verify_manifest(root)
    validate_schemas(root, documents)
    bundle = Bundle(documents)
    validate_records(bundle)
    validate_replay(bundle)
    validate_observations(bundle)
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
