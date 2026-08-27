"""Prospective, plan-bound RW-19 Alpaca news reconnect evidence collector.

This operator-only command performs two bounded read-only news WebSocket observations around
one deliberate disconnect, then one bounded historical-news GET census. It cannot place an
order, mutate a watchlist, start the cockpit, or grant paper/live/autonomous authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core.secrets import require_secret  # noqa: E402
from ingest.characterization import characterize_news_reconnect  # noqa: E402

SCHEMA_VERSION = 1
PACKAGE = "RW-19"
PLAN_SCOPE = "prospective-news-reconnect-plan-only"
PREFLIGHT_SCOPE = "prospective-news-reconnect-safety-preflight"
CAPTURE_SCOPE = "prospective-news-reconnect-capture-only"
PLAN_NAME = "collection_plan.json"
PREFLIGHT_NAME = "safety_preflight.json"
SUMMARY_NAME = "summary.json"
MANIFEST_NAME = "capture_manifest.json"
MANIFEST_HASH_NAME = "capture_manifest.sha256"
ABORT_NAME = "aborted_manifest.json"
ABORT_HASH_NAME = "aborted_manifest.sha256"
FAILURE_NAME = "failure.json"
NEWS_REST_URL = "https://data.alpaca.markets/v1beta1/news"
NEWS_WS_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SYMBOL_RE = re.compile(r"^[A-Z0-9]+(?:[.-][A-Z0-9]+)*$")
ATTEMPT_RE = re.compile(r"^\d{8}T\d{6}\.\d{6}Z$")
PREFLIGHT_MAX_AGE = timedelta(minutes=15)
IMPLEMENTATION_PATHS = (
    "app/ingest/characterization.py",
    "tools/rw19_news_reconnect_plan_bound.py",
)
RAW_PATHS = {
    "websocket_before": "raw/ws-before.jsonl",
    "websocket_after": "raw/ws-after.jsonl",
    "rest": "raw/rest.jsonl",
    "control_before": "raw/control-before.jsonl",
    "control_after": "raw/control-after.jsonl",
    "rest_pages": "raw/rest-pages.jsonl",
}
KNOWN_BLOCKERS = (
    "a non-empty news reconnect observation remains required",
    "correction and update behavior across reconnect remains unproven",
    "broader market regimes remain required",
    "a bounded filtering and coalescing decision remains required before RW-13",
    "DEC-07 remains open",
)
CLASSIFICATION_CONTRACT = {
    "empty": "INSUFFICIENT_SAMPLE_NOT_QUIET_DAY",
    "nonempty": "OBSERVATION_ONLY_DEC07_OPEN",
    "failed": "FAILED_CAPTURE",
}


class EvidenceError(RuntimeError):
    """A malformed, incomplete, ambiguous, unsafe, or tampered evidence input."""


class CaptureFailure(EvidenceError):
    """A sanitized failure after the evidence attempt has been reserved."""

    def __init__(self, failure_class: str, detail: str):
        self.failure_class = failure_class
        self.detail = detail[:500]
        super().__init__(f"{failure_class}: {self.detail}")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: Any) -> bytes:
    payload = _canonical_bytes(value) + b"\n"
    path.write_bytes(payload)
    return payload


def _exact_keys(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    if set(value) != expected:
        raise EvidenceError(
            f"{label} keys mismatch: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )
    return value


def _strict_json(payload: bytes, label: str) -> Any:
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise EvidenceError(f"{label} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_constant(value):
        raise EvidenceError(f"{label} contains non-finite JSON number {value!r}")

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise EvidenceError(f"{label} is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{label} is invalid JSON") from exc


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError(f"{label} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(f"{label} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise EvidenceError(f"{label} must be timezone-qualified UTC")
    return parsed.astimezone(UTC)


def _positive_int(value: object, label: str, *, maximum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EvidenceError(f"{label} must be a positive integer")
    if maximum is not None and value > maximum:
        raise EvidenceError(f"{label} exceeds its hard maximum")
    return value


def _positive_number(value: object, label: str, *, maximum: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EvidenceError(f"{label} must be a positive number")
    number = float(value)
    if not 0 < number <= maximum:
        raise EvidenceError(f"{label} is outside its hard bounds")
    return number


def _sample_clock(
    clock: Callable[[], datetime], previous: datetime | None, label: str
) -> datetime:
    sampled = clock()
    if sampled.tzinfo is None or sampled.utcoffset() != timedelta(0):
        raise CaptureFailure("clock", f"{label} was not timezone-qualified UTC")
    sampled = sampled.astimezone(UTC)
    if previous is not None and sampled < previous:
        raise CaptureFailure("clock_backwards", "capture clock moved backwards")
    return sampled


def _load_plan(
    path: Path, *, now: datetime, expected_sha256: str
) -> tuple[dict, bytes]:
    if SHA_RE.fullmatch(str(expected_sha256)) is None:
        raise EvidenceError("expected plan hash must be lowercase SHA-256")
    payload = Path(path).read_bytes()
    if _sha256(payload) != expected_sha256:
        raise EvidenceError("collection plan hash differs from expected plan hash")
    plan = _strict_json(payload, "collection plan")
    plan = _exact_keys(
        plan,
        {
            "schema_version", "package", "scope", "authority", "rw19_gate",
            "dec07_status", "source_commit", "authored_at", "selected_by",
            "selection_rule", "observation_window", "universe_symbols",
            "resource_limits", "output_paths", "safety_preflight",
            "classification_contract", "known_blockers",
        },
        "collection plan",
    )
    if (
        plan["schema_version"] != SCHEMA_VERSION
        or plan["package"] != PACKAGE
        or plan["scope"] != PLAN_SCOPE
        or plan["authority"] != "none"
        or plan["rw19_gate"] != "OPEN_NOT_CLOSED"
        or plan["dec07_status"] != "open"
    ):
        raise EvidenceError("collection plan scope or safety boundary changed")
    if not isinstance(plan["source_commit"], str) or COMMIT_RE.fullmatch(
        plan["source_commit"]
    ) is None:
        raise EvidenceError("collection plan source_commit is not canonical")
    authored_at = _parse_utc(plan["authored_at"], "authored_at")
    if authored_at > now:
        raise EvidenceError("collection plan is future-authored")
    if not isinstance(plan["selected_by"], str) or len(plan["selected_by"].strip()) < 20:
        raise EvidenceError("collection plan selector identity is missing")
    if not isinstance(plan["selection_rule"], str) or len(plan["selection_rule"].strip()) < 200:
        raise EvidenceError("collection plan selection rule is not substantive")

    window = _exact_keys(
        plan["observation_window"],
        {
            "scheduled_start", "start_tolerance_seconds", "segment_seconds",
            "gap_seconds", "maximum_wall_seconds",
        },
        "observation window",
    )
    scheduled_start = _parse_utc(window["scheduled_start"], "scheduled_start")
    if authored_at >= scheduled_start:
        raise EvidenceError("collection plan was not authored before the observation window")
    tolerance = _positive_int(
        window["start_tolerance_seconds"], "start_tolerance_seconds", maximum=300
    )
    segment_seconds = _positive_number(
        window["segment_seconds"], "segment_seconds", maximum=1800
    )
    if segment_seconds < 5:
        raise EvidenceError("segment_seconds must be at least 5")
    gap_seconds = _positive_number(window["gap_seconds"], "gap_seconds", maximum=300)
    maximum_wall = _positive_number(
        window["maximum_wall_seconds"], "maximum_wall_seconds", maximum=7200
    )
    if abs((now - scheduled_start).total_seconds()) > tolerance:
        raise EvidenceError("capture start is outside the prospective plan tolerance")

    symbols = plan["universe_symbols"]
    if (
        not isinstance(symbols, list)
        or len(symbols) != 11
        or symbols != sorted(set(symbols))
        or any(not isinstance(symbol, str) or SYMBOL_RE.fullmatch(symbol) is None for symbol in symbols)
    ):
        raise EvidenceError("universe must contain exactly 11 canonical sorted unique symbols")

    limits = _exact_keys(
        plan["resource_limits"],
        {
            "maximum_frames_per_segment", "maximum_frame_bytes",
            "maximum_control_frames_per_segment", "maximum_rest_pages",
            "maximum_rest_rows", "maximum_rest_page_bytes", "maximum_rest_total_bytes",
            "maximum_rest_seconds", "request_timeout_seconds",
        },
        "resource limits",
    )
    _positive_int(limits["maximum_frames_per_segment"], "maximum_frames_per_segment", maximum=1_000_000)
    _positive_int(limits["maximum_frame_bytes"], "maximum_frame_bytes", maximum=10_000_000)
    _positive_int(
        limits["maximum_control_frames_per_segment"],
        "maximum_control_frames_per_segment",
        maximum=10_000,
    )
    _positive_int(limits["maximum_rest_pages"], "maximum_rest_pages", maximum=100)
    _positive_int(limits["maximum_rest_rows"], "maximum_rest_rows", maximum=100_000)
    _positive_int(limits["maximum_rest_page_bytes"], "maximum_rest_page_bytes", maximum=25_000_000)
    _positive_int(limits["maximum_rest_total_bytes"], "maximum_rest_total_bytes", maximum=100_000_000)
    rest_seconds = _positive_number(
        limits["maximum_rest_seconds"], "maximum_rest_seconds", maximum=600
    )
    _positive_number(limits["request_timeout_seconds"], "request_timeout_seconds", maximum=120)
    if limits["maximum_rest_total_bytes"] < limits["maximum_rest_page_bytes"]:
        raise EvidenceError("REST total-byte cap cannot be smaller than the page-byte cap")
    if maximum_wall < segment_seconds * 2 + gap_seconds + rest_seconds:
        raise EvidenceError("maximum_wall_seconds cannot cover the declared bounded attempt")

    output = _exact_keys(
        plan["output_paths"],
        {"capture_directory", "aborted_directory", "publication_rule"},
        "output paths",
    )
    if output["publication_rule"] != "isolated write-once":
        raise EvidenceError("output publication rule changed")
    if not Path(output["capture_directory"]).is_absolute() or not Path(
        output["aborted_directory"]
    ).is_absolute():
        raise EvidenceError("planned evidence output paths must be absolute")
    safety = _exact_keys(
        plan["safety_preflight"], {"required", "maximum_age_minutes"}, "safety preflight"
    )
    if safety != {"required": True, "maximum_age_minutes": 15}:
        raise EvidenceError("safety preflight contract changed")
    if plan["classification_contract"] != CLASSIFICATION_CONTRACT:
        raise EvidenceError("classification contract changed")
    if plan["known_blockers"] != list(KNOWN_BLOCKERS):
        raise EvidenceError("known blocker set changed")
    return plan, payload


def _load_preflight(
    path: Path, *, now: datetime, collector_source_commit: str, symbols: list[str]
) -> tuple[dict, bytes]:
    payload = Path(path).read_bytes()
    preflight = _strict_json(payload, "safety preflight")
    preflight = _exact_keys(
        preflight,
        {
            "schema_version", "package", "scope", "checked_at", "source_commit",
            "configured_mode", "effective_mode", "persisted_mode",
            "expected_edge_available", "merger_execution_watchlist_count",
            "merger_edge_rows", "merger_selection_rows", "proposal_rows",
            "risk_decision_rows", "approval_rows", "order_rows", "audit_chain_valid",
            "halt_absent", "alpaca_credentials_configured", "cockpit_service_processes",
            "api_port_listeners", "worktree_clean", "main_equals_origin_main",
            "awareness_universe_symbols",
        },
        "safety preflight",
    )
    if (
        preflight["schema_version"] != SCHEMA_VERSION
        or preflight["package"] != PACKAGE
        or preflight["scope"] != PREFLIGHT_SCOPE
        or preflight["source_commit"] != collector_source_commit
    ):
        raise EvidenceError("safety preflight package or source commit mismatch")
    checked_at = _parse_utc(preflight["checked_at"], "safety preflight checked_at")
    if checked_at > now or now - checked_at > PREFLIGHT_MAX_AGE:
        raise EvidenceError("safety preflight is future-dated or older than 15 minutes")
    if preflight["configured_mode"] != "read_only" or preflight["effective_mode"] != "read_only":
        raise EvidenceError("safety preflight is not read_only")
    if preflight["persisted_mode"] not in (None, "read_only"):
        raise EvidenceError("persisted mode exceeds read_only")
    zeroes = (
        "merger_execution_watchlist_count", "merger_edge_rows", "merger_selection_rows",
        "proposal_rows", "risk_decision_rows", "approval_rows", "order_rows",
        "cockpit_service_processes", "api_port_listeners",
    )
    if any(preflight[key] != 0 for key in zeroes):
        raise EvidenceError("safety preflight has a nonzero execution-state count")
    trues = (
        "audit_chain_valid", "halt_absent", "alpaca_credentials_configured",
        "worktree_clean", "main_equals_origin_main",
    )
    if preflight["expected_edge_available"] is not False or any(
        preflight[key] is not True for key in trues
    ):
        raise EvidenceError("safety preflight fail-closed conditions are not satisfied")
    if preflight["awareness_universe_symbols"] != symbols:
        raise EvidenceError("safety preflight awareness universe differs from the plan universe")
    return preflight, payload


def _source_state() -> dict[str, Any]:
    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
        )
        return completed.stdout.strip()

    return {
        "head": git("rev-parse", "HEAD"),
        "origin_main": git("rev-parse", "origin/main"),
        "worktree_clean": not git("status", "--porcelain"),
    }


def _validate_source_state(value: dict[str, Any], expected_commit: str) -> dict[str, Any]:
    state = _exact_keys(value, {"head", "origin_main", "worktree_clean"}, "source state")
    if not isinstance(state["head"], str) or COMMIT_RE.fullmatch(state["head"]) is None:
        raise EvidenceError("collector HEAD is not canonical")
    if (
        state["head"] != expected_commit
        or state["origin_main"] != expected_commit
        or state["worktree_clean"] is not True
    ):
        raise EvidenceError("collector requires the plan's clean HEAD equal to origin/main")
    return dict(state)


def _is_linklike(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise EvidenceError(f"cannot inspect evidence path {path}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(
        reparse_flag and getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _validate_external_paths(plan: dict, requested_output: Path) -> tuple[Path, Path]:
    capture = Path(plan["output_paths"]["capture_directory"])
    aborted = Path(plan["output_paths"]["aborted_directory"])
    if capture.name in {"", ".", ".."} or aborted.name in {"", ".", ".."}:
        raise EvidenceError("output paths must name bounded package directories")
    capture = capture.resolve(strict=False)
    aborted = aborted.resolve(strict=False)
    if Path(requested_output).resolve(strict=False) != capture:
        raise EvidenceError("output directory differs from the prospective plan")
    if capture == aborted:
        raise EvidenceError("capture and aborted directories must be distinct")
    repository = ROOT.resolve(strict=True)
    for candidate in (capture, aborted):
        if candidate == repository or repository in candidate.parents:
            raise EvidenceError("raw RW-19 evidence must remain outside the tracked repository")
        if candidate.exists() or candidate.is_symlink():
            raise EvidenceError("planned evidence output already exists; the attempt is write-once")
        parent = candidate.parent.resolve(strict=True)
        if not parent.is_dir() or _is_linklike(parent):
            raise EvidenceError("planned evidence parent must be a real external directory")
    if capture.parent != aborted.parent:
        raise EvidenceError("capture and aborted directories must share one external parent")
    return capture, aborted


def _credentials() -> tuple[str, str]:
    return require_secret("alpaca_key").reveal(), require_secret("alpaca_secret").reveal()


def _contains_secret(payload: bytes, secrets: tuple[str, str]) -> bool:
    return any(secret and secret.encode("utf-8") in payload for secret in secrets)


class _JsonlJournal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = path.open("ab", buffering=0)
        self.count = 0

    def append(self, value: Any) -> None:
        self._file.write(_canonical_bytes(value) + b"\n")
        self._file.flush()
        os.fsync(self._file.fileno())
        self.count += 1

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()


def _read_jsonl(path: Path, label: str) -> list[dict]:
    payload = path.read_bytes()
    if payload and not payload.endswith(b"\n"):
        raise EvidenceError(f"{label} JSONL is truncated")
    rows = []
    for index, line in enumerate(payload.splitlines(), start=1):
        if not line:
            raise EvidenceError(f"{label} JSONL has an empty row")
        row = _strict_json(line, f"{label} row {index}")
        if not isinstance(row, dict):
            raise EvidenceError(f"{label} row {index} must be an object")
        rows.append(row)
    return rows


def _frame_messages(
    socket: Any,
    *,
    deadline: float,
    limits: dict[str, Any],
    state: dict[str, int],
    secrets: tuple[str, str],
    monotonic: Callable[[], float],
) -> list[dict]:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    raw = socket.recv(timeout=remaining)
    if isinstance(raw, str):
        payload = raw.encode("utf-8")
    elif isinstance(raw, bytes):
        payload = raw
    else:
        raise CaptureFailure("socket_protocol", "news frame was not text or bytes")
    if len(payload) > limits["maximum_frame_bytes"]:
        raise CaptureFailure("frame_bytes", "news frame exceeded the declared byte cap")
    if _contains_secret(payload, secrets):
        raise CaptureFailure("secret_reflection", "vendor frame reflected a credential")
    state["frames"] += 1
    if state["frames"] > limits["maximum_frames_per_segment"]:
        raise CaptureFailure("frame_cap", "news segment exceeded the declared frame cap")
    decoded = _strict_json(payload, "Alpaca news WebSocket frame")
    if not isinstance(decoded, list) or not decoded or any(
        not isinstance(message, dict) for message in decoded
    ):
        raise CaptureFailure("socket_protocol", "news WebSocket frames must be non-empty arrays of objects")
    return decoded


def _safe_control(message: dict) -> dict:
    control_type = message.get("T")
    if control_type == "success":
        return {"T": "success", "msg": message.get("msg")}
    if control_type == "subscription":
        return {"T": "subscription", "news": message.get("news")}
    if control_type == "error":
        return {"T": "error", "code": message.get("code")}
    return {"T": control_type}


def _subscription_ack_error(ack: object, symbols: tuple[str, ...]) -> str | None:
    if not isinstance(ack, list):
        return "subscription acknowledgement news is not a list"
    if any(not isinstance(entry, str) for entry in ack):
        return "subscription acknowledgement contains a non-string entry"
    if len(set(ack)) != len(ack):
        return "subscription acknowledgement contains duplicate symbols"
    if set(ack) != set(symbols):
        return "subscription acknowledgement changed the symbol universe"
    return None


def _expect_control(
    socket: Any,
    *,
    expected_type: str,
    expected_message: str | None,
    symbols: tuple[str, ...],
    deadline: float,
    limits: dict[str, Any],
    state: dict[str, int],
    secrets: tuple[str, str],
    control_sink: Callable[[dict], None],
    clock: Callable[[], datetime],
    previous: datetime,
    monotonic: Callable[[], float],
    failure_class: str,
    row_sink: Callable[[dict], None] | None = None,
) -> datetime:
    while True:
        try:
            messages = _frame_messages(
                socket,
                deadline=deadline,
                limits=limits,
                state=state,
                secrets=secrets,
                monotonic=monotonic,
            )
        except TimeoutError as exc:
            raise CaptureFailure(failure_class, "socket handshake timed out") from exc
        received_at = _sample_clock(clock, previous, "socket control receive")
        previous = received_at
        matched = []
        for message in messages:
            if message.get("T") == "n":
                if row_sink is None:
                    raise CaptureFailure(
                        "socket_protocol", "news frame arrived before subscription acknowledgement"
                    )
                if message.get("id") is None:
                    raise CaptureFailure("socket_protocol", "news frame has no stable article id")
                row_sink({"received_at": received_at.isoformat(), "message": message})
                continue
            state["controls"] += 1
            if state["controls"] > limits["maximum_control_frames_per_segment"]:
                raise CaptureFailure("control_frame_cap", "socket control-frame cap exceeded")
            control_sink({"received_at": received_at.isoformat(), "message": _safe_control(message)})
            if message.get("T") == "error":
                raise CaptureFailure(
                    failure_class, f"vendor returned error code {message.get('code')!r}"
                )
            if message.get("T") != expected_type:
                raise CaptureFailure(failure_class, f"expected {expected_type} control")
            matched.append(message)
        if not matched:
            continue
        if len(matched) != 1:
            raise CaptureFailure(failure_class, "socket control frame contained duplicate controls")
        message = matched[0]
        if expected_message is not None and message.get("msg") != expected_message:
            raise CaptureFailure(failure_class, f"expected {expected_message} control")
        if expected_type == "subscription":
            detail = _subscription_ack_error(message.get("news"), symbols)
            if detail is not None:
                raise CaptureFailure(failure_class, detail)
        return received_at


def _capture_news_segment(
    *,
    connector: Any,
    key: str,
    secret: str,
    symbols: tuple[str, ...],
    duration_seconds: float,
    limits: dict[str, Any],
    row_sink: Callable[[dict], None],
    control_sink: Callable[[dict], None],
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
) -> tuple[datetime, datetime]:
    if connector is None:
        from websockets.sync.client import connect

        connector = connect
    started = _sample_clock(clock, None, "news segment start")
    deadline = monotonic() + duration_seconds
    state = {"frames": 0, "controls": 0}
    stage = "socket_connect"
    try:
        with connector(
            NEWS_WS_URL,
            open_timeout=15.0,
            close_timeout=5.0,
            max_size=limits["maximum_frame_bytes"],
        ) as socket:
            previous = _expect_control(
                socket,
                expected_type="success",
                expected_message="connected",
                symbols=symbols,
                deadline=deadline,
                limits=limits,
                state=state,
                secrets=(key, secret),
                control_sink=control_sink,
                clock=clock,
                previous=started,
                monotonic=monotonic,
                failure_class="socket_connect",
            )
            stage = "socket_auth"
            socket.send(json.dumps({"action": "auth", "key": key, "secret": secret}))
            previous = _expect_control(
                socket,
                expected_type="success",
                expected_message="authenticated",
                symbols=symbols,
                deadline=deadline,
                limits=limits,
                state=state,
                secrets=(key, secret),
                control_sink=control_sink,
                clock=clock,
                previous=previous,
                monotonic=monotonic,
                failure_class="socket_auth",
            )
            stage = "socket_subscribe"
            socket.send(json.dumps({"action": "subscribe", "news": list(symbols)}))
            previous = _expect_control(
                socket,
                expected_type="subscription",
                expected_message=None,
                symbols=symbols,
                deadline=deadline,
                limits=limits,
                state=state,
                secrets=(key, secret),
                control_sink=control_sink,
                clock=clock,
                previous=previous,
                monotonic=monotonic,
                failure_class="socket_subscribe",
                row_sink=row_sink,
            )
            stage = "socket_transport"
            while True:
                try:
                    messages = _frame_messages(
                        socket,
                        deadline=deadline,
                        limits=limits,
                        state=state,
                        secrets=(key, secret),
                        monotonic=monotonic,
                    )
                except TimeoutError:
                    break
                received_at = _sample_clock(clock, previous, "news frame receive")
                previous = received_at
                for message in messages:
                    message_type = message.get("T")
                    if message_type == "error":
                        raise CaptureFailure(
                            "socket_error", f"vendor returned error code {message.get('code')!r}"
                        )
                    if message_type == "n":
                        if message.get("id") is None:
                            raise CaptureFailure("socket_protocol", "news frame has no stable article id")
                        row_sink({"received_at": received_at.isoformat(), "message": message})
                    elif message_type in {"success", "subscription"}:
                        state["controls"] += 1
                        if state["controls"] > limits["maximum_control_frames_per_segment"]:
                            raise CaptureFailure("control_frame_cap", "socket control-frame cap exceeded")
                        control_sink({
                            "received_at": received_at.isoformat(),
                            "message": _safe_control(message),
                        })
                        if message_type == "subscription":
                            detail = _subscription_ack_error(message.get("news"), symbols)
                            if detail is not None:
                                raise CaptureFailure("socket_subscribe", detail)
                    else:
                        raise CaptureFailure("socket_protocol", "unexpected news WebSocket message type")
    except CaptureFailure:
        raise
    except Exception as exc:
        raise CaptureFailure(stage, f"socket transport raised {type(exc).__name__}") from exc
    ended = _sample_clock(clock, previous, "news segment end")
    return started, ended


def _fetch_news_rest(
    client: Any,
    *,
    symbols: tuple[str, ...],
    start: datetime,
    end: datetime,
    limits: dict[str, Any],
    secrets: tuple[str, str],
    row_sink: Callable[[dict], None],
    page_sink: Callable[[dict], None],
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
) -> dict[str, int]:
    started = monotonic()
    page_token = None
    seen_tokens: set[str] = set()
    total_bytes = 0
    total_rows = 0
    pages = 0
    while True:
        if monotonic() - started > limits["maximum_rest_seconds"]:
            raise CaptureFailure("rest_deadline", "REST census exceeded its overall deadline")
        if pages >= limits["maximum_rest_pages"]:
            raise CaptureFailure("rest_page_cap", "REST census exceeded its page cap")
        params = {
            "symbols": ",".join(symbols),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "sort": "asc",
            "limit": 50,
            "include_content": "false",
        }
        if page_token is not None:
            params["page_token"] = page_token
        try:
            response_context = client.stream("GET", NEWS_REST_URL, params=params)
            with response_context as response:
                if response.status_code >= 400:
                    raise CaptureFailure(
                        "rest_http", f"REST census returned HTTP {response.status_code}"
                    )
                chunks = []
                page_bytes = 0
                for chunk in response.iter_bytes():
                    if not isinstance(chunk, bytes):
                        raise CaptureFailure("rest_protocol", "REST stream yielded non-byte data")
                    page_bytes += len(chunk)
                    if page_bytes > limits["maximum_rest_page_bytes"]:
                        raise CaptureFailure("rest_page_bytes", "REST page exceeded its byte cap")
                    chunks.append(chunk)
                payload = b"".join(chunks)
                if _contains_secret(payload, secrets):
                    raise CaptureFailure("secret_reflection", "REST response reflected a credential")
                total_bytes += page_bytes
                if total_bytes > limits["maximum_rest_total_bytes"]:
                    raise CaptureFailure("rest_total_bytes", "REST census exceeded its total-byte cap")
                request_id = response.headers.get("X-Request-ID")
        except CaptureFailure:
            raise
        except Exception as exc:
            raise CaptureFailure("rest_transport", f"REST transport raised {type(exc).__name__}") from exc
        if monotonic() - started > limits["maximum_rest_seconds"]:
            raise CaptureFailure("rest_deadline", "REST census exceeded its overall deadline")
        document = _strict_json(payload, "Alpaca historical-news response")
        if not isinstance(document, dict):
            raise CaptureFailure("rest_protocol", "REST response was not an object")
        page_rows = document.get("news")
        if not isinstance(page_rows, list) or len(page_rows) > 50:
            raise CaptureFailure("rest_protocol", "REST news page shape exceeded the requested contract")
        if any(not isinstance(row, dict) or row.get("id") is None for row in page_rows):
            raise CaptureFailure("rest_protocol", "REST news row lacks an object or stable article id")
        total_rows += len(page_rows)
        if total_rows > limits["maximum_rest_rows"]:
            raise CaptureFailure("rest_row_cap", "REST census exceeded its row cap")
        next_token = document.get("next_page_token")
        if next_token is not None:
            if not isinstance(next_token, str) or not next_token or len(next_token) > 256:
                raise CaptureFailure("rest_token", "REST next-page token is malformed")
            if next_token in seen_tokens:
                raise CaptureFailure("rest_token", "REST next-page token repeated")
        fetched_at = _sample_clock(clock, None, "REST page fetch")
        safe_request_id = None if request_id is None else str(request_id)[:256]
        if safe_request_id and _contains_secret(safe_request_id.encode(), secrets):
            raise CaptureFailure("secret_reflection", "REST request id reflected a credential")
        for row in page_rows:
            row_sink(row)
        page_sink({
            "page_number": pages + 1,
            "method": "GET",
            "url": NEWS_REST_URL,
            "status_code": response.status_code,
            "request_id": safe_request_id,
            "fetched_at": fetched_at.isoformat(),
            "payload_bytes": page_bytes,
            "payload_sha256": _sha256(payload),
            "row_count": len(page_rows),
            "page_token_in": page_token,
            "page_token_out": next_token,
        })
        pages += 1
        if next_token is None:
            return {"pages": pages, "rows": total_rows, "bytes": total_bytes}
        seen_tokens.add(next_token)
        page_token = next_token


def _classify(before: list[dict], after: list[dict], rest: list[dict]) -> str:
    return (
        CLASSIFICATION_CONTRACT["nonempty"]
        if before or after or rest
        else CLASSIFICATION_CONTRACT["empty"]
    )


def _summary(
    before: list[dict],
    after: list[dict],
    rest: list[dict],
    *,
    disconnected_at: datetime,
    reconnected_at: datetime,
) -> dict:
    return {
        "classification": _classify(before, after, rest),
        "reconnect": characterize_news_reconnect(
            before,
            after,
            rest,
            disconnected_at=disconnected_at,
            reconnected_at=reconnected_at,
        ),
    }


def _implementation_hashes() -> dict[str, str]:
    return {path: _sha256((ROOT / path).read_bytes()) for path in IMPLEMENTATION_PATHS}


def _collection_hash_value(manifest: dict) -> dict:
    return {key: value for key, value in manifest.items() if key != "collection_hash"}


def _validate_attempt_times(manifest: dict, *, aborted: bool) -> None:
    if ATTEMPT_RE.fullmatch(str(manifest.get("attempt_id"))) is None:
        raise EvidenceError("attempt_id is not canonical")
    started = _parse_utc(manifest["capture_started_at"], "capture_started_at")
    if aborted:
        if _parse_utc(manifest["failed_at"], "failed_at") < started:
            raise EvidenceError("aborted attempt timestamp ordering changed")
        return
    ordered = [
        started,
        _parse_utc(manifest["window_start"], "window_start"),
        _parse_utc(manifest["disconnected_at"], "disconnected_at"),
        _parse_utc(manifest["reconnected_at"], "reconnected_at"),
        _parse_utc(manifest["window_end"], "window_end"),
        _parse_utc(manifest["captured_at"], "captured_at"),
    ]
    if not (
        ordered[0] <= ordered[1] < ordered[2] <= ordered[3] < ordered[4] <= ordered[5]
    ):
        raise EvidenceError("capture timestamp ordering changed")


def _close_journals(journals: dict[str, _JsonlJournal]) -> None:
    for journal in journals.values():
        journal.close()


def _journal_snapshot(root: Path) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    rows = {name: _read_jsonl(root / path, name) for name, path in RAW_PATHS.items()}
    artifacts = {}
    for name, relative in RAW_PATHS.items():
        payload = (root / relative).read_bytes()
        artifacts[name] = {
            "path": relative,
            "sha256": _sha256(payload),
            "bytes": len(payload),
            "rows": len(rows[name]),
        }
    return rows, artifacts


def _manifest_base(
    plan: dict,
    *,
    status: str,
    pre_state: dict,
    attempt_id: str,
    capture_started: datetime,
    plan_sha256: str,
    preflight_bytes: bytes,
    raw: dict,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "package": PACKAGE,
        "scope": CAPTURE_SCOPE,
        "status": status,
        "authority": "none",
        "rw19_gate": "OPEN_NOT_CLOSED",
        "dec07_status": "open",
        "source_commit": plan["source_commit"],
        "collector_source_commit": pre_state["head"],
        "attempt_id": attempt_id,
        "capture_started_at": capture_started.isoformat(),
        "plan_sha256": plan_sha256,
        "safety_preflight_sha256": _sha256(preflight_bytes),
        "pre_source_state": pre_state,
        "universe_symbols": list(plan["universe_symbols"]),
        "raw": raw,
        "implementation_hashes": _implementation_hashes(),
        "known_blockers": list(KNOWN_BLOCKERS),
    }


def _publish(staging: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise EvidenceError("planned evidence output already exists; write-once publication refused")
    try:
        staging.rename(target)
    except (FileExistsError, OSError) as exc:
        raise EvidenceError("atomic write-once publication failed") from exc


def _expected_capture_paths() -> set[str]:
    return {
        PLAN_NAME,
        PREFLIGHT_NAME,
        SUMMARY_NAME,
        MANIFEST_NAME,
        MANIFEST_HASH_NAME,
        "raw",
        *RAW_PATHS.values(),
    }


def _expected_aborted_paths() -> set[str]:
    return {
        PLAN_NAME,
        PREFLIGHT_NAME,
        FAILURE_NAME,
        ABORT_NAME,
        ABORT_HASH_NAME,
        "raw",
        *RAW_PATHS.values(),
    }


def _present_paths(root: Path) -> set[str]:
    if _is_linklike(root):
        raise EvidenceError("evidence package root cannot be a link or reparse point")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise EvidenceError("evidence package must be a directory")
    present = set()
    for path in resolved.rglob("*"):
        if _is_linklike(path):
            raise EvidenceError(f"evidence package contains link or reparse point: {path.relative_to(resolved)}")
        present.add(path.relative_to(resolved).as_posix())
    return present


def _raw_records(root: Path, expected: dict[str, dict]) -> dict[str, list[dict]]:
    rows = {}
    for name, relative in RAW_PATHS.items():
        values = _read_jsonl(root / relative, name)
        payload = (root / relative).read_bytes()
        record = expected.get(name)
        if record != {
            "path": relative,
            "sha256": _sha256(payload),
            "bytes": len(payload),
            "rows": len(values),
        }:
            raise EvidenceError(f"raw artifact binding changed for {name}")
        rows[name] = values
    return rows


def _open_manifest(
    package_dir: Path,
    *,
    manifest_name: str,
    hash_name: str,
    expected_paths: set[str],
    label: str,
) -> tuple[Path, dict]:
    requested = Path(package_dir)
    present = _present_paths(requested)
    root = requested.resolve(strict=True)
    if present != expected_paths:
        raise EvidenceError(
            f"{label} artifact set changed: missing={sorted(expected_paths - present)}, "
            f"extra={sorted(present - expected_paths)}"
        )
    payload = (root / manifest_name).read_bytes()
    sidecar = (root / hash_name).read_text(encoding="ascii").strip()
    if SHA_RE.fullmatch(sidecar) is None or sidecar != _sha256(payload):
        raise EvidenceError(f"{label} manifest hash mismatch")
    return root, _strict_json(payload, f"{label} manifest")


def _verify_common(
    root: Path,
    manifest: dict,
    *,
    expected_plan_sha256: str,
    output_key: str,
) -> tuple[dict, dict[str, list[dict]]]:
    started = _parse_utc(manifest["capture_started_at"], "capture_started_at")
    plan, plan_bytes = _load_plan(
        root / PLAN_NAME, now=started, expected_sha256=expected_plan_sha256
    )
    if root != Path(plan["output_paths"][output_key]).resolve(strict=True):
        raise EvidenceError("evidence package moved from the prospective output path")
    if manifest["plan_sha256"] != _sha256(plan_bytes):
        raise EvidenceError("manifest plan hash mismatch")
    preflight_bytes = (root / PREFLIGHT_NAME).read_bytes()
    _load_preflight(
        root / PREFLIGHT_NAME,
        now=started,
        collector_source_commit=manifest["collector_source_commit"],
        symbols=plan["universe_symbols"],
    )
    if manifest["safety_preflight_sha256"] != _sha256(preflight_bytes):
        raise EvidenceError("manifest safety-preflight hash mismatch")
    if (
        manifest["source_commit"] != plan["source_commit"]
        or manifest["universe_symbols"] != plan["universe_symbols"]
    ):
        raise EvidenceError("manifest differs from the prospective plan")
    _validate_source_state(manifest["pre_source_state"], plan["source_commit"])
    if manifest["implementation_hashes"] != _implementation_hashes():
        raise EvidenceError("implementation hashes changed")
    return plan, _raw_records(root, manifest["raw"])


def verify(package_dir: Path, *, expected_plan_sha256: str) -> dict:
    root, manifest = _open_manifest(
        package_dir,
        manifest_name=MANIFEST_NAME,
        hash_name=MANIFEST_HASH_NAME,
        expected_paths=_expected_capture_paths(),
        label="capture",
    )
    manifest = _exact_keys(
        manifest,
        {
            "schema_version", "package", "scope", "status", "authority", "rw19_gate",
            "dec07_status", "source_commit", "collector_source_commit", "attempt_id",
            "capture_started_at", "window_start", "disconnected_at", "reconnected_at",
            "window_end", "captured_at", "plan_sha256", "safety_preflight_sha256",
            "pre_source_state", "post_source_state", "universe_symbols", "sample_size",
            "resource_usage", "classification", "summary", "summary_artifact", "raw",
            "implementation_hashes", "limitations", "known_blockers", "collection_hash",
        },
        "capture manifest",
    )
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["package"] != PACKAGE
        or manifest["scope"] != CAPTURE_SCOPE
        or manifest["status"] != "complete"
        or manifest["authority"] != "none"
        or manifest["rw19_gate"] != "OPEN_NOT_CLOSED"
        or manifest["dec07_status"] != "open"
        or manifest["known_blockers"] != list(KNOWN_BLOCKERS)
    ):
        raise EvidenceError("capture manifest scope or safety boundary changed")
    _validate_attempt_times(manifest, aborted=False)
    plan, rows = _verify_common(
        root,
        manifest,
        expected_plan_sha256=expected_plan_sha256,
        output_key="capture_directory",
    )
    # Complete packages must re-derive the ack invariant from journaled control rows.
    # verify_aborted is exempt: a tombstone legitimately preserves the offending ack.
    planned_universe = tuple(manifest["universe_symbols"])
    for journal_name in ("control_before", "control_after"):
        for row in rows[journal_name]:
            message = row["message"]
            if message.get("T") != "subscription":
                continue
            if _subscription_ack_error(message.get("news"), planned_universe) is not None:
                raise EvidenceError(
                    "journaled subscription acknowledgement violates the planned universe"
                )
    _validate_source_state(manifest["post_source_state"], plan["source_commit"])
    before = rows["websocket_before"]
    after = rows["websocket_after"]
    rest = rows["rest"]
    sample_size = {
        "websocket_before": len(before),
        "websocket_after": len(after),
        "rest": len(rest),
    }
    if manifest["sample_size"] != sample_size:
        raise EvidenceError("capture sample-size census changed")
    disconnected_at = _parse_utc(manifest["disconnected_at"], "disconnected_at")
    reconnected_at = _parse_utc(manifest["reconnected_at"], "reconnected_at")
    expected_summary = _summary(
        before,
        after,
        rest,
        disconnected_at=disconnected_at,
        reconnected_at=reconnected_at,
    )
    summary_bytes = (root / SUMMARY_NAME).read_bytes()
    if summary_bytes != _canonical_bytes(expected_summary) + b"\n":
        raise EvidenceError("capture summary no longer recomputes from raw evidence")
    if manifest["summary"] != expected_summary or manifest["classification"] != expected_summary["classification"]:
        raise EvidenceError("capture classification changed")
    if manifest["summary_artifact"] != {
        "path": SUMMARY_NAME,
        "sha256": _sha256(summary_bytes),
        "bytes": len(summary_bytes),
    }:
        raise EvidenceError("capture summary binding changed")
    rest_pages = rows["rest_pages"]
    if manifest["resource_usage"] != {
        "websocket_before_frames": len(before) + len(rows["control_before"]),
        "websocket_after_frames": len(after) + len(rows["control_after"]),
        "rest_pages": len(rest_pages),
        "rest_rows": len(rest),
        "rest_bytes": sum(row["payload_bytes"] for row in rest_pages),
    }:
        raise EvidenceError("capture resource accounting changed")
    if manifest["collection_hash"] != _sha256(_canonical_bytes(_collection_hash_value(manifest))):
        raise EvidenceError("capture collection hash changed")
    return {"ok": True, "classification": manifest["classification"], "collection_hash": manifest["collection_hash"]}


def verify_aborted(package_dir: Path, *, expected_plan_sha256: str) -> dict:
    root, manifest = _open_manifest(
        package_dir,
        manifest_name=ABORT_NAME,
        hash_name=ABORT_HASH_NAME,
        expected_paths=_expected_aborted_paths(),
        label="aborted",
    )
    manifest = _exact_keys(
        manifest,
        {
            "schema_version", "package", "scope", "status", "published", "authority",
            "rw19_gate", "dec07_status", "source_commit", "collector_source_commit",
            "attempt_id", "capture_started_at", "failed_at", "plan_sha256",
            "safety_preflight_sha256", "pre_source_state", "universe_symbols",
            "classification", "failure_class", "failure_artifact", "raw",
            "implementation_hashes", "known_blockers", "collection_hash",
        },
        "aborted manifest",
    )
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["package"] != PACKAGE
        or manifest["scope"] != CAPTURE_SCOPE
        or manifest["status"] != "aborted"
        or manifest["published"] is not False
        or manifest["authority"] != "none"
        or manifest["rw19_gate"] != "OPEN_NOT_CLOSED"
        or manifest["dec07_status"] != "open"
        or manifest["classification"] != CLASSIFICATION_CONTRACT["failed"]
        or manifest["known_blockers"] != list(KNOWN_BLOCKERS)
    ):
        raise EvidenceError("aborted manifest scope or safety boundary changed")
    _validate_attempt_times(manifest, aborted=True)
    _plan, _rows = _verify_common(
        root,
        manifest,
        expected_plan_sha256=expected_plan_sha256,
        output_key="aborted_directory",
    )
    failure_bytes = (root / FAILURE_NAME).read_bytes()
    failure = _strict_json(failure_bytes, "failure artifact")
    if failure != {"failure_class": manifest["failure_class"], "detail": manifest["failure_class"]}:
        raise EvidenceError("failure artifact changed")
    if manifest["failure_artifact"] != {
        "path": FAILURE_NAME,
        "sha256": _sha256(failure_bytes),
        "bytes": len(failure_bytes),
    }:
        raise EvidenceError("failure artifact binding changed")
    if manifest["collection_hash"] != _sha256(_canonical_bytes(_collection_hash_value(manifest))):
        raise EvidenceError("aborted collection hash changed")
    return {"ok": True, "classification": manifest["classification"], "failure_class": manifest["failure_class"]}


def _failure_class(exc: BaseException) -> str:
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return "interrupted"
    if isinstance(exc, CaptureFailure):
        return exc.failure_class
    if isinstance(exc, EvidenceError):
        return "evidence_failure"
    return f"unexpected_{type(exc).__name__}"[:100]


def collect(
    plan_path: Path,
    safety_preflight_path: Path,
    output_dir: Path,
    *,
    expected_plan_sha256: str,
    credential_loader: Callable[[], tuple[str, str]] = _credentials,
    connector: Any = None,
    client_factory: Callable[..., Any] = httpx.Client,
    source_state: dict[str, Any] | None = None,
    source_state_reader: Callable[[], dict[str, Any]] = _source_state,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict:
    """Execute one predeclared attempt and publish either success or a partial tombstone."""
    capture_started = _sample_clock(clock, None, "capture start")
    plan, plan_bytes = _load_plan(
        plan_path, now=capture_started, expected_sha256=expected_plan_sha256
    )
    pre_state = _validate_source_state(
        source_state or source_state_reader(), plan["source_commit"]
    )
    _preflight, preflight_bytes = _load_preflight(
        safety_preflight_path,
        now=capture_started,
        collector_source_commit=pre_state["head"],
        symbols=plan["universe_symbols"],
    )
    capture_target, aborted_target = _validate_external_paths(plan, output_dir)
    credentials = credential_loader()
    if (
        not isinstance(credentials, tuple)
        or len(credentials) != 2
        or any(not isinstance(value, str) or not value for value in credentials)
    ):
        raise EvidenceError("Alpaca credential loader did not return two configured secrets")
    key, secret = credentials
    staging = Path(tempfile.mkdtemp(prefix=".rw19-news-reconnect.", dir=capture_target.parent))
    try:
        attempt_id = capture_started.strftime("%Y%m%dT%H%M%S.%fZ")
        (staging / PLAN_NAME).write_bytes(plan_bytes)
        (staging / PREFLIGHT_NAME).write_bytes(preflight_bytes)
        journals = {
            name: _JsonlJournal(staging / relative)
            for name, relative in RAW_PATHS.items()
        }
        attempt_started_monotonic = monotonic()
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        limits = plan["resource_limits"]
        window = plan["observation_window"]
        symbols = tuple(plan["universe_symbols"])
        window_start, disconnected_at = _capture_news_segment(
            connector=connector,
            key=key,
            secret=secret,
            symbols=symbols,
            duration_seconds=float(window["segment_seconds"]),
            limits=limits,
            row_sink=journals["websocket_before"].append,
            control_sink=journals["control_before"].append,
            clock=clock,
            monotonic=monotonic,
        )
        sleeper(float(window["gap_seconds"]))
        reconnected_at, window_end = _capture_news_segment(
            connector=connector,
            key=key,
            secret=secret,
            symbols=symbols,
            duration_seconds=float(window["segment_seconds"]),
            limits=limits,
            row_sink=journals["websocket_after"].append,
            control_sink=journals["control_after"].append,
            clock=clock,
            monotonic=monotonic,
        )
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        with client_factory(
            headers=headers, timeout=float(limits["request_timeout_seconds"])
        ) as client:
            rest_usage = _fetch_news_rest(
                client,
                symbols=symbols,
                start=window_start,
                end=window_end,
                limits=limits,
                secrets=(key, secret),
                row_sink=journals["rest"].append,
                page_sink=journals["rest_pages"].append,
                clock=clock,
                monotonic=monotonic,
            )
        captured_at = _sample_clock(clock, window_end, "capture completion")
        elapsed = monotonic() - attempt_started_monotonic
        if elapsed > float(window["maximum_wall_seconds"]):
            raise CaptureFailure("wall_deadline", "capture exceeded maximum_wall_seconds")
        post_state = _validate_source_state(source_state_reader(), plan["source_commit"])
        _close_journals(journals)
        rows, raw = _journal_snapshot(staging)
        before = rows["websocket_before"]
        after = rows["websocket_after"]
        rest = rows["rest"]
        summary = _summary(
            before,
            after,
            rest,
            disconnected_at=disconnected_at,
            reconnected_at=reconnected_at,
        )
        summary_bytes = _write_json(staging / SUMMARY_NAME, summary)
        manifest = _manifest_base(
            plan,
            status="complete",
            pre_state=pre_state,
            attempt_id=attempt_id,
            capture_started=capture_started,
            plan_sha256=expected_plan_sha256,
            preflight_bytes=preflight_bytes,
            raw=raw,
        )
        manifest.update({
            "window_start": window_start.isoformat(),
            "disconnected_at": disconnected_at.isoformat(),
            "reconnected_at": reconnected_at.isoformat(),
            "window_end": window_end.isoformat(),
            "captured_at": captured_at.isoformat(),
            "post_source_state": post_state,
            "sample_size": {
                "websocket_before": len(before),
                "websocket_after": len(after),
                "rest": len(rest),
            },
            "resource_usage": {
                "websocket_before_frames": len(before) + len(rows["control_before"]),
                "websocket_after_frames": len(after) + len(rows["control_after"]),
                "rest_pages": rest_usage["pages"],
                "rest_rows": rest_usage["rows"],
                "rest_bytes": rest_usage["bytes"],
            },
            "classification": summary["classification"],
            "summary": summary,
            "summary_artifact": {
                "path": SUMMARY_NAME,
                "sha256": _sha256(summary_bytes),
                "bytes": len(summary_bytes),
            },
            "limitations": [
                "A zero-identity window is insufficient and is not a quiet-day claim.",
                "A non-empty window is observation-only and cannot close RW-19 or DEC-07.",
                "Historical news exposes latest representations and may not retain update history.",
                "Raw vendor evidence remains external and must not be committed or redistributed.",
            ],
        })
        manifest["collection_hash"] = _sha256(
            _canonical_bytes(_collection_hash_value(manifest))
        )
        manifest_bytes = _write_json(staging / MANIFEST_NAME, manifest)
        (staging / MANIFEST_HASH_NAME).write_text(
            _sha256(manifest_bytes) + "\n", encoding="ascii"
        )
        _publish(staging, capture_target)
        result = verify(capture_target, expected_plan_sha256=expected_plan_sha256)
        if not result["ok"]:
            raise EvidenceError("published capture failed immediate verification")
        return manifest
    except BaseException as exc:
        _close_journals(journals)
        if capture_target.exists():
            raise
        try:
            failure_class = _failure_class(exc)
            failed_at = clock()
            if failed_at.tzinfo is None or failed_at.utcoffset() != timedelta(0):
                failed_at = capture_started
            failure_bytes = _write_json(
                staging / FAILURE_NAME,
                {"failure_class": failure_class, "detail": failure_class},
            )
            _rows, raw = _journal_snapshot(staging)
            manifest = _manifest_base(
                plan,
                status="aborted",
                pre_state=pre_state,
                attempt_id=attempt_id,
                capture_started=capture_started,
                plan_sha256=expected_plan_sha256,
                preflight_bytes=preflight_bytes,
                raw=raw,
            )
            manifest.update({
                "published": False,
                "failed_at": failed_at.astimezone(UTC).isoformat(),
                "classification": CLASSIFICATION_CONTRACT["failed"],
                "failure_class": failure_class,
                "failure_artifact": {
                    "path": FAILURE_NAME,
                    "sha256": _sha256(failure_bytes),
                    "bytes": len(failure_bytes),
                },
            })
            manifest["collection_hash"] = _sha256(
                _canonical_bytes(_collection_hash_value(manifest))
            )
            manifest_bytes = _write_json(staging / ABORT_NAME, manifest)
            (staging / ABORT_HASH_NAME).write_text(
                _sha256(manifest_bytes) + "\n", encoding="ascii"
            )
            _publish(staging, aborted_target)
            verified = verify_aborted(
                aborted_target, expected_plan_sha256=expected_plan_sha256
            )
            if not verified["ok"]:
                raise EvidenceError("aborted package failed immediate verification") from exc
            return manifest
        except BaseException:
            if staging.exists():
                recovery = aborted_target.with_name(
                    f"{aborted_target.name}.recovery.{attempt_id}"
                )
                try:
                    staging.rename(recovery)
                except OSError:
                    pass
            raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect_cmd = commands.add_parser("collect")
    collect_cmd.add_argument("--plan", type=Path, required=True)
    collect_cmd.add_argument("--expected-plan-sha256", required=True)
    collect_cmd.add_argument("--safety-preflight", type=Path, required=True)
    collect_cmd.add_argument("--output-dir", type=Path, required=True)
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("--package-dir", type=Path, required=True)
    verify_cmd.add_argument("--expected-plan-sha256", required=True)
    aborted_cmd = commands.add_parser("verify-aborted")
    aborted_cmd.add_argument("--package-dir", type=Path, required=True)
    aborted_cmd.add_argument("--expected-plan-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "verify":
            print(json.dumps(verify(
                args.package_dir, expected_plan_sha256=args.expected_plan_sha256
            ), sort_keys=True))
            return 0
        if args.command == "verify-aborted":
            print(json.dumps(verify_aborted(
                args.package_dir, expected_plan_sha256=args.expected_plan_sha256
            ), sort_keys=True))
            return 0
        manifest = collect(
            args.plan,
            args.safety_preflight,
            args.output_dir,
            expected_plan_sha256=args.expected_plan_sha256,
        )
        print(json.dumps({
            "status": manifest["status"],
            "classification": manifest["classification"],
            "sample_size": manifest.get("sample_size"),
            "failure_class": manifest.get("failure_class"),
            "collection_hash": manifest["collection_hash"],
            "authority": "none",
            "rw19_gate": "OPEN_NOT_CLOSED",
            "dec07_status": "open",
        }, sort_keys=True))
        return 0 if manifest["status"] == "complete" else 3
    except Exception as exc:
        label = exc.failure_class if isinstance(exc, CaptureFailure) else type(exc).__name__
        print(f"RW-19 plan-bound news reconnect refused: {label}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
