"""RW-11 prospective SEC Company Facts evidence capture.

The command captures exact Company Facts response bytes for a predeclared CIK census through
the repository's fair-access SEC client.  It publishes an isolated, write-once package only
after every response passes identity and integrity checks.  The package never reads the
operational database and never grants strategy, paper, live, or autonomous authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from filings.edgar.companyfacts import iter_facts  # noqa: E402
from filings.edgar.fetch import (  # noqa: E402
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_MAX_RUN_BYTES,
    SecFetchError,
    SecFetcher,
    companyfacts_url,
    pad_cik,
)
from filings.raw_store import RawStore, RawStoreError  # noqa: E402

SCHEMA_VERSION = 1
PACKAGE = "RW-11"
SCOPE = "prospective-companyfacts-capture-only"
RAW_PREFIX = "companyfacts-"
MANIFEST_NAME = "capture_manifest.json"
MANIFEST_HASH_NAME = "capture_manifest.sha256"
PLAN_NAME = "collection_plan.json"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
CIK_RE = re.compile(r"^\d{10}$")
ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
ELIGIBLE_FORMS = ("10-K", "10-Q")
KNOWN_BLOCKERS = (
    "a supported prospective periodic accession and numeric replay are absent",
    "a meaningful independently labelled population at >=95% is absent",
    "a meaningful real frozen delta population is absent",
    "a cited prospective live material numeric delta is absent",
    "the local package has no independent external witness",
)
IMPLEMENTATION_PATHS = (
    "app/filings/edgar/companyfacts.py",
    "app/filings/edgar/fetch.py",
    "app/filings/raw_store.py",
    "tools/rw11_companyfacts.py",
)


class EvidenceError(RuntimeError):
    """The proposed evidence package is incomplete, ambiguous, or tampered."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _exact_keys(value: dict, expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise EvidenceError(
            f"{label} keys mismatch: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError(f"{label} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(f"{label} must be an RFC3339 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise EvidenceError(f"{label} must be timezone-qualified UTC")
    return parsed


def _strict_json(payload: bytes, label: str) -> Any:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise EvidenceError(f"{label} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    def no_constant(value):
        raise EvidenceError(f"{label} contains non-finite JSON number {value!r}")

    try:
        text = payload.decode("utf-8")
        return json.loads(text, object_pairs_hook=no_duplicates, parse_constant=no_constant)
    except UnicodeDecodeError as exc:
        raise EvidenceError(f"{label} is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{label} is invalid JSON: {exc}") from exc


def _load_plan(path: Path, *, now: datetime) -> tuple[dict, bytes]:
    payload = Path(path).read_bytes()
    plan = _strict_json(payload, "collection plan")
    if not isinstance(plan, dict):
        raise EvidenceError("collection plan must be an object")
    _exact_keys(
        plan,
        {
            "schema_version",
            "package",
            "source_commit",
            "authored_at",
            "selected_by",
            "cutoff_date",
            "selection_rule",
            "universe_ciks",
            "eligible_forms",
        },
        "collection plan",
    )
    if plan["schema_version"] != SCHEMA_VERSION or plan["package"] != PACKAGE:
        raise EvidenceError("unsupported collection plan schema/package")
    if not isinstance(plan["source_commit"], str) or not COMMIT_RE.fullmatch(
        plan["source_commit"]
    ):
        raise EvidenceError("source_commit must be a lowercase 40-hex commit")
    if _parse_utc(plan["authored_at"], "authored_at") > now:
        raise EvidenceError("authored_at cannot be after capture time")
    if not isinstance(plan["selected_by"], str) or not plan["selected_by"].strip():
        raise EvidenceError("selected_by must identify the population selector")
    try:
        cutoff = date.fromisoformat(plan["cutoff_date"])
    except (TypeError, ValueError) as exc:
        raise EvidenceError("cutoff_date must be YYYY-MM-DD") from exc
    if cutoff >= now.date():
        raise EvidenceError("cutoff_date must precede the capture UTC date")
    rule = plan["selection_rule"]
    if not isinstance(rule, str) or len(rule.strip()) < 20:
        raise EvidenceError("selection_rule must predeclare a substantive census rule")
    ciks = plan["universe_ciks"]
    if not isinstance(ciks, list) or not ciks or len(ciks) > 50:
        raise EvidenceError("universe_ciks must contain 1..50 CIKs")
    if any(not isinstance(cik, str) or not CIK_RE.fullmatch(cik) for cik in ciks):
        raise EvidenceError("universe_ciks contains a non-canonical CIK")
    if ciks != sorted(set(ciks)):
        raise EvidenceError("universe_ciks must be sorted and unique")
    if plan["eligible_forms"] != list(ELIGIBLE_FORMS):
        raise EvidenceError(f"eligible_forms must be exactly {list(ELIGIBLE_FORMS)!r}")
    return plan, payload


def _payload_summary(payload: bytes, cik: str, cutoff: date) -> tuple[dict, dict]:
    document = _strict_json(payload, f"Company Facts payload for {cik}")
    if not isinstance(document, dict):
        raise EvidenceError(f"Company Facts payload for {cik} must be an object")
    try:
        response_cik = pad_cik(document.get("cik", ""))
    except (TypeError, ValueError) as exc:
        raise EvidenceError(f"Company Facts payload for {cik} has malformed CIK") from exc
    if response_cik != cik:
        raise EvidenceError(
            f"Company Facts response CIK {response_cik} does not match request CIK {cik}"
        )
    if not isinstance(document.get("facts"), dict):
        raise EvidenceError(f"Company Facts payload for {cik} has no facts object")

    rows = list(iter_facts(document))
    if not rows:
        raise EvidenceError(f"Company Facts payload for {cik} contains no iterable facts")
    by_accession: dict[str, dict[str, Any]] = {}
    malformed_accession_rows = 0
    incomplete_period_rows = 0
    for row in rows:
        accession = row.accession
        if not isinstance(accession, str) or ACCESSION_RE.fullmatch(accession) is None:
            malformed_accession_rows += 1
            continue
        entry = by_accession.setdefault(
            accession,
            {"forms": set(), "filed_dates": set(), "fact_rows": 0, "usable_numeric_rows": 0},
        )
        entry["fact_rows"] += 1
        if isinstance(row.form, str) and row.form:
            entry["forms"].add(row.form)
        if isinstance(row.filed, str) and row.filed:
            try:
                entry["filed_dates"].add(date.fromisoformat(row.filed))
            except ValueError:
                incomplete_period_rows += 1
        if row.val is not None and row.end is not None:
            try:
                date.fromisoformat(row.end)
                numeric = Decimal(str(row.val))
                if not numeric.is_finite():
                    raise InvalidOperation
            except (TypeError, ValueError, InvalidOperation):
                incomplete_period_rows += 1
            else:
                entry["usable_numeric_rows"] += 1

    eligible = []
    for accession, entry in sorted(by_accession.items()):
        forms = sorted(entry["forms"])
        filed_dates = sorted(entry["filed_dates"])
        supported = [form for form in forms if form in ELIGIBLE_FORMS]
        post_cutoff = [filed for filed in filed_dates if filed > cutoff]
        if not supported or not post_cutoff:
            continue
        if len(forms) != 1 or len(filed_dates) != 1:
            raise EvidenceError(
                f"Company Facts accession {accession} has ambiguous form/filed metadata"
            )
        if entry["usable_numeric_rows"] == 0:
            raise EvidenceError(
                f"eligible Company Facts accession {accession} has no usable numeric rows"
            )
        eligible.append(
            {
                "accession_no": accession,
                "form_type": forms[0],
                "filed": filed_dates[0].isoformat(),
                "fact_rows": entry["fact_rows"],
                "usable_numeric_rows": entry["usable_numeric_rows"],
            }
        )
    return document, {
        "cik": cik,
        "url": companyfacts_url(cik),
        "raw_key": f"{RAW_PREFIX}{cik}",
        "payload_sha256": _sha256(payload),
        "payload_bytes": len(payload),
        "iterated_fact_rows": len(rows),
        "accession_count": len(by_accession),
        "malformed_accession_rows": malformed_accession_rows,
        "incomplete_period_rows": incomplete_period_rows,
        "eligible_accessions": eligible,
    }


def _implementation_hashes() -> dict[str, str]:
    return {name: _sha256((ROOT / name).read_bytes()) for name in IMPLEMENTATION_PATHS}


def _collection_hash_value(manifest: dict) -> dict:
    return {
        "plan_sha256": manifest["plan_sha256"],
        "source_commit": manifest["source_commit"],
        "capture_started_at": manifest["capture_started_at"],
        "captured_at": manifest["captured_at"],
        "universe_ciks": manifest["universe_ciks"],
        "eligible_forms": manifest["eligible_forms"],
        "implementation_hashes": manifest["implementation_hashes"],
        "captures": manifest["captures"],
    }


def _safe_output(path: Path) -> Path:
    candidate = Path(path)
    if candidate.name in {"", ".", ".."}:
        raise EvidenceError("output directory must name one bounded package")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    resolved = candidate.resolve(strict=False)
    if candidate.exists() or candidate.is_symlink() or resolved.exists():
        raise EvidenceError("output directory already exists; evidence is write-once")
    return resolved


def collect(
    plan_path: Path,
    output_dir: Path,
    *,
    fetcher: Any,
    now: datetime | None = None,
) -> dict:
    """Fetch and atomically publish one complete predeclared Company Facts census."""
    fixed_time = now
    last_time: datetime | None = None

    def sample_time(label: str) -> datetime:
        nonlocal last_time
        sampled = fixed_time or datetime.now(UTC)
        if sampled.tzinfo is None or sampled.utcoffset() != UTC.utcoffset(sampled):
            raise EvidenceError(f"{label} must be timezone-qualified UTC")
        if last_time is not None and sampled < last_time:
            raise EvidenceError("capture clock moved backwards")
        last_time = sampled
        return sampled

    capture_started_at = sample_time("capture start")
    plan, plan_bytes = _load_plan(Path(plan_path), now=capture_started_at)
    target = _safe_output(Path(output_dir))
    staging = Path(tempfile.mkdtemp(prefix=".rw11-companyfacts.", dir=target.parent))
    try:
        (staging / PLAN_NAME).write_bytes(plan_bytes)
        raw_store = RawStore(staging / "raw", min_free_bytes=0)
        captures = []
        cutoff = date.fromisoformat(plan["cutoff_date"])
        for cik in plan["universe_ciks"]:
            payload = fetcher.get_bytes(companyfacts_url(cik))
            fetched_at = sample_time(f"fetch time for {cik}")
            _document, summary = _payload_summary(payload, cik, cutoff)
            summary["fetched_at"] = fetched_at.isoformat()
            ref = raw_store.write(summary["raw_key"], payload)
            if ref.sha256 != summary["payload_sha256"]:
                raise EvidenceError(f"raw-store hash mismatch after capture for CIK {cik}")
            captures.append(summary)

        captured_at = sample_time("capture completion")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "package": PACKAGE,
            "scope": SCOPE,
            "authority": "none",
            "rw11_gate": "OPEN",
            "gate_closed": False,
            "source_commit": plan["source_commit"],
            "capture_started_at": capture_started_at.isoformat(),
            "captured_at": captured_at.isoformat(),
            "selected_by": plan["selected_by"],
            "cutoff_date": plan["cutoff_date"],
            "selection_rule": plan["selection_rule"],
            "universe_ciks": plan["universe_ciks"],
            "eligible_forms": plan["eligible_forms"],
            "plan_sha256": _sha256(plan_bytes),
            "implementation_hashes": _implementation_hashes(),
            "captures": captures,
            "eligible_accession_count": sum(
                len(capture["eligible_accessions"]) for capture in captures
            ),
            "fetch_bytes_received": int(getattr(fetcher, "bytes_received", 0)),
            "known_blockers": list(KNOWN_BLOCKERS),
        }
        manifest["collection_hash"] = _sha256(_canonical_bytes(_collection_hash_value(manifest)))
        manifest_bytes = _canonical_bytes(manifest) + b"\n"
        (staging / MANIFEST_NAME).write_bytes(manifest_bytes)
        (staging / MANIFEST_HASH_NAME).write_text(
            _sha256(manifest_bytes) + "\n", encoding="ascii"
        )
        # Claim the final path with atomically-exclusive mkdir on every supported platform.
        # A crash while moving the already-complete staged children can leave a partial package,
        # but verification refuses it and the claimed path can never be overwritten.
        try:
            target.mkdir()
        except FileExistsError as exc:
            raise EvidenceError("output directory already exists; evidence is write-once") from exc
        for child in staging.iterdir():
            child.rename(target / child.name)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _expected_paths(manifest: dict) -> set[str]:
    paths = {PLAN_NAME, MANIFEST_NAME, MANIFEST_HASH_NAME, "raw"}
    for capture in manifest["captures"]:
        prefix = f"raw/{capture['raw_key']}"
        paths.update({prefix, f"{prefix}/raw.bin", f"{prefix}/sha256"})
    return paths


def _is_linklike(path: Path) -> bool:
    """Reject symlinks and Windows reparse points (including NTFS junctions)."""
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise EvidenceError(f"cannot inspect capture artifact {path}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(
        reparse_flag and getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def verify(package_dir: Path) -> dict:
    """Recompute every package binding and refuse extra, missing, or linked artifacts."""
    requested = Path(package_dir)
    if _is_linklike(requested):
        raise EvidenceError("capture package root cannot be a link or reparse point")
    root = requested.resolve(strict=True)
    if not root.is_dir():
        raise EvidenceError("capture package must be a directory")
    present = set()
    for path in root.rglob("*"):
        if _is_linklike(path):
            raise EvidenceError(
                f"capture package contains link or reparse point: {path.relative_to(root)}"
            )
        present.add(path.relative_to(root).as_posix())

    manifest_bytes = (root / MANIFEST_NAME).read_bytes()
    expected_manifest_hash = (root / MANIFEST_HASH_NAME).read_text(encoding="ascii").strip()
    if SHA_RE.fullmatch(expected_manifest_hash) is None:
        raise EvidenceError("manifest hash sidecar is malformed")
    if _sha256(manifest_bytes) != expected_manifest_hash:
        raise EvidenceError("capture manifest hash mismatch")
    manifest = _strict_json(manifest_bytes, "capture manifest")
    if not isinstance(manifest, dict):
        raise EvidenceError("capture manifest must be an object")
    _exact_keys(
        manifest,
        {
            "schema_version", "package", "scope", "authority", "rw11_gate", "gate_closed",
            "source_commit", "capture_started_at", "captured_at", "selected_by", "cutoff_date", "selection_rule",
            "universe_ciks", "eligible_forms", "plan_sha256", "implementation_hashes",
            "captures", "eligible_accession_count", "fetch_bytes_received", "known_blockers",
            "collection_hash",
        },
        "capture manifest",
    )
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["package"] != PACKAGE
        or manifest["scope"] != SCOPE
        or manifest["authority"] != "none"
        or manifest["rw11_gate"] != "OPEN"
        or manifest["gate_closed"] is not False
    ):
        raise EvidenceError("capture manifest safety boundary changed")
    if manifest["known_blockers"] != list(KNOWN_BLOCKERS):
        raise EvidenceError("capture manifest blocker set changed")
    captured_at = _parse_utc(manifest["captured_at"], "captured_at")
    capture_started_at = _parse_utc(manifest["capture_started_at"], "capture_started_at")
    if capture_started_at > captured_at:
        raise EvidenceError("capture completion precedes capture start")
    plan, plan_bytes = _load_plan(root / PLAN_NAME, now=captured_at)
    if manifest["plan_sha256"] != _sha256(plan_bytes):
        raise EvidenceError("collection plan hash mismatch")
    for key in (
        "source_commit", "selected_by", "cutoff_date", "selection_rule",
        "universe_ciks", "eligible_forms",
    ):
        if manifest[key] != plan[key]:
            raise EvidenceError(f"capture manifest diverges from collection plan: {key}")
    if manifest["implementation_hashes"] != _implementation_hashes():
        raise EvidenceError("capture implementation hashes changed")
    if not isinstance(manifest["captures"], list):
        raise EvidenceError("captures must be a list")
    if [capture.get("cik") for capture in manifest["captures"]] != plan["universe_ciks"]:
        raise EvidenceError("capture set/order does not match the predeclared universe")
    if present != _expected_paths(manifest):
        raise EvidenceError(
            f"capture artifact set changed: missing={sorted(_expected_paths(manifest) - present)}, "
            f"extra={sorted(present - _expected_paths(manifest))}"
        )

    store = RawStore(root / "raw", min_free_bytes=0)
    recomputed = []
    cutoff = date.fromisoformat(plan["cutoff_date"])
    for recorded in manifest["captures"]:
        _exact_keys(
            recorded,
            {
                "cik", "url", "raw_key", "payload_sha256", "payload_bytes", "fetched_at",
                "iterated_fact_rows", "accession_count", "malformed_accession_rows",
                "incomplete_period_rows", "eligible_accessions",
            },
            "capture row",
        )
        raw_key = recorded["raw_key"]
        fetched_at = _parse_utc(recorded["fetched_at"], "fetched_at")
        if not capture_started_at <= fetched_at <= captured_at:
            raise EvidenceError(f"fetch timestamp is outside capture window for {raw_key}")
        try:
            payload = store.resolve(raw_key)
        except (FileNotFoundError, RawStoreError) as exc:
            raise EvidenceError(f"raw Company Facts integrity failure for {raw_key}") from exc
        _document, summary = _payload_summary(payload, recorded["cik"], cutoff)
        summary["fetched_at"] = recorded["fetched_at"]
        if summary != recorded:
            raise EvidenceError(f"capture summary mismatch for CIK {recorded['cik']}")
        recomputed.append(summary)
    if sum(len(row["eligible_accessions"]) for row in recomputed) != manifest[
        "eligible_accession_count"
    ]:
        raise EvidenceError("eligible accession count mismatch")
    successful_bytes = sum(row["payload_bytes"] for row in recomputed)
    if (
        not isinstance(manifest["fetch_bytes_received"], int)
        or isinstance(manifest["fetch_bytes_received"], bool)
        or manifest["fetch_bytes_received"] < successful_bytes
    ):
        raise EvidenceError("fetch byte accounting is incomplete")
    if manifest["collection_hash"] != _sha256(
        _canonical_bytes(_collection_hash_value(manifest))
    ):
        raise EvidenceError("collection hash mismatch")
    return {
        "ok": True,
        "collection_hash": manifest["collection_hash"],
        "ciks": len(recomputed),
        "eligible_accessions": manifest["eligible_accession_count"],
        "authority": "none",
        "rw11_gate": "OPEN_NOT_CLOSED",
    }


def _live_fetcher(args) -> SecFetcher:
    user_agent = os.environ.get("COCKPIT_SEC_USER_AGENT", "")
    if not user_agent.strip():
        raise EvidenceError(
            "COCKPIT_SEC_USER_AGENT is required for SEC fair access; its value is never printed"
        )
    return SecFetcher(
        user_agent=user_agent,
        rate_per_sec=args.rate_per_second,
        max_response_bytes=args.max_response_bytes,
        max_total_bytes=args.max_total_bytes,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect_cmd = commands.add_parser("collect")
    collect_cmd.add_argument("--plan", type=Path, required=True)
    collect_cmd.add_argument("--output-dir", type=Path, required=True)
    collect_cmd.add_argument("--rate-per-second", type=float, default=2.0)
    collect_cmd.add_argument("--max-response-bytes", type=int, default=DEFAULT_MAX_RESPONSE_BYTES)
    collect_cmd.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_RUN_BYTES)
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("--package-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "collect":
            result = collect(args.plan, args.output_dir, fetcher=_live_fetcher(args))
            print(json.dumps({
                "collection_hash": result["collection_hash"],
                "ciks": len(result["captures"]),
                "eligible_accessions": result["eligible_accession_count"],
                "authority": "none",
                "rw11_gate": "OPEN_NOT_CLOSED",
            }))
            return 0
        print(json.dumps(verify(args.package_dir)))
        return 0
    except (EvidenceError, SecFetchError, OSError, ValueError) as exc:
        print(f"RW-11 Company Facts capture refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
