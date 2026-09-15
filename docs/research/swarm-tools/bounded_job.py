#!/usr/bin/env python3
"""RW-34 bounded development-job controller.

Admits one no-tools JSON child profile on the Cursor Cloud Linux host. Cursor-native
Task, Cursor CLI, Codex, OpenCode and Devin remain unqualified. This module does not
import production risk, broker, audit, mode or eligibility code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterator

try:
    import jsonschema
except ImportError:  # pragma: no cover - smoke environment
    sys.stderr.write("jsonschema is required\n")
    raise SystemExit(2)

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parents[2]
BRIEFS_DIR = ROOT / "docs/research/swarm-briefs"
SCHEMAS = {
    "leaf": ROOT / "docs/research/swarm-schemas/leaf-filing.schema.json",
    "review": ROOT / "docs/research/swarm-schemas/review-filing.schema.json",
    "synthesis": ROOT / "docs/research/swarm-schemas/synthesis.schema.json",
}
JSON_CHILD = TOOLS_DIR / "json_child.py"
PROFILE_ID = "cursor-cloud-json-child"
ROUTE_FAMILY = "cursor-cloud-json-child"
ROUTE_MODEL = "json-child"
ALLOWED_ROLES = {"leaf": "leaf", "first-gate-review": "review"}
FORBIDDEN_FLAGS = {
    "--auto", "--resume", "--continue", "--sandbox", "--force", "--trust",
    "--approve-mcps", "--mcp", "--mcp-config", "--api-key", "--bypass",
    "--yolo", "--dangerous", "--inherit",
}
MAX_BRIEF_BYTES = 64 * 1024
MAX_EXCERPT_BYTES = 16 * 1024
MAX_EVENTS = 256
DEFAULT_CONCURRENCY = 1
DEFAULT_OUTPUT_LIMIT = 32 * 1024
DEFAULT_TIMEOUT = 20.0
REAP_GRACE_SECONDS = 5.0
KILL_GRACE_SECONDS = 10.0
_ENV_KEEP = (
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC",
    "TMP", "TEMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL", "LC_CTYPE",
)


class AdmissionRefused(Exception):
    """Job is not admitted; no child may run."""


class ProfileError(Exception):
    """Actual argv/config is not the admitted no-tools profile."""


@dataclass
class ProcResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    cancelled: bool = False


class SubprocessTransport:
    """Spawn the inspected argv with a sanitized environment and kill on timeout/cancel."""

    def run(
        self,
        argv: list[str],
        input_bytes: bytes,
        timeout_s: float,
        cwd: Path,
        env: dict[str, str],
        *,
        output_limit: int,
        cancel_event: threading.Event | None = None,
    ) -> ProcResult:
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": str(cwd),
            "env": env,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(argv, **popen_kwargs)
        stdout = bytearray()
        stderr = bytearray()
        lock = threading.Lock()
        over_limit = threading.Event()
        cancelled = False
        timed_out = False

        def _pump(stream, buf: bytearray) -> None:
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        return
                    with lock:
                        buf.extend(chunk)
                        if len(buf) > output_limit:
                            over_limit.set()
                            return
            except Exception:
                return

        try:
            t_out = threading.Thread(target=_pump, args=(proc.stdout, stdout), daemon=True)
            t_err = threading.Thread(target=_pump, args=(proc.stderr, stderr), daemon=True)
            t_out.start()
            t_err.start()
            assert proc.stdin is not None
            proc.stdin.write(input_bytes)
            proc.stdin.close()
            deadline = time.monotonic() + max(0.01, float(timeout_s))
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    _kill_tree(proc)
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    _kill_tree(proc)
                    break
                if over_limit.is_set():
                    _kill_tree(proc)
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.02)
            t_out.join(timeout=REAP_GRACE_SECONDS)
            t_err.join(timeout=REAP_GRACE_SECONDS)
            try:
                proc.wait(timeout=REAP_GRACE_SECONDS)
            except Exception:
                _kill_tree(proc)
            with lock:
                out = bytes(stdout)
                err = bytes(stderr)
            return ProcResult(proc.returncode, out, err, timed_out, cancelled)
        except Exception:
            _kill_tree(proc)
            raise


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=KILL_GRACE_SECONDS,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=REAP_GRACE_SECONDS)
    except Exception:
        pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def profile_digest() -> str:
    return sha256_file(JSON_CHILD)


def child_env(source: dict[str, str] | None = None) -> dict[str, str]:
    env_src = source if source is not None else os.environ
    keep = {name: env_src[name] for name in _ENV_KEEP if name in env_src}
    keep["PYTHONIOENCODING"] = "utf-8"
    keep["PYTHONHASHSEED"] = "0"
    keep["PYTHONDONTWRITEBYTECODE"] = "1"
    return keep


def _within(path: Path, base: Path) -> bool:
    return path != base and path.is_relative_to(base)


def _parse_deadline(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise AdmissionRefused("deadline must be timezone-aware")
    return stamp.astimezone(timezone.utc)


def _money(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise AdmissionRefused("invalid budget") from exc
    if amount < 0 or amount.as_tuple().exponent < -4:
        raise AdmissionRefused("invalid budget")
    return amount


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def inspect_argv(argv: list[str], *, role: str, output_limit: int) -> None:
    if len(argv) != 6:
        raise ProfileError("admitted argv must be exactly python json_child --role --output-limit")
    executable = Path(argv[0]).name.lower()
    if not executable.startswith("python"):
        raise ProfileError("admitted child executable must be the pinned Python")
    if Path(argv[1]).resolve() != JSON_CHILD.resolve():
        raise ProfileError("admitted child script must be json_child.py")
    if argv[2] != "--role" or argv[3] != role:
        raise ProfileError("admitted role flag mismatch")
    if argv[4] != "--output-limit" or argv[5] != str(output_limit):
        raise ProfileError("admitted output limit mismatch")
    joined = " ".join(argv).lower()
    for flag in FORBIDDEN_FLAGS:
        if flag in joined:
            raise ProfileError(f"forbidden capability flag {flag}")


def build_argv(*, python: str, role: str, output_limit: int) -> list[str]:
    argv = [python, str(JSON_CHILD.resolve()), "--role", role, "--output-limit", str(output_limit)]
    inspect_argv(argv, role=role, output_limit=output_limit)
    return argv


@dataclass
class Envelope:
    job_id: str
    claim_id: str
    attempt: int
    role: str
    principal: str
    disclosure: str
    brief_path: Path
    input_paths: tuple[Path, ...]
    input_sha256: dict[str, str]
    policy_hash: str
    schema_kind: str
    deadline_utc: datetime
    timeout_seconds: float
    output_byte_limit: int
    reservation_id: str
    budget_scope: str
    remaining_usd: Decimal
    reserved_usd: Decimal
    profile_id: str
    subject_filing_path: Path | None = None
    source_families: tuple[str, ...] = ()
    leaf_ids: tuple[str, ...] = ()


@dataclass
class Receipt:
    job_id: str
    claim_id: str
    attempt: int
    status: str
    route_family: str
    route_model: str
    profile_id: str
    executable: str
    argv: list[str]
    raw_sha256: str | None
    filing_sha256: str | None
    receipt_sha256: str | None
    usage_class: str
    reserved_usd: str
    observed_usd: str
    hold: bool
    empirical_pass_claimed: bool
    reason: str
    paths: dict[str, str] = field(default_factory=dict)


def load_envelope(payload: dict[str, Any], *, now: datetime) -> Envelope:
    required = (
        "job_id", "claim_id", "attempt", "role", "principal", "disclosure",
        "brief_path", "input_paths", "input_sha256", "policy_hash", "schema_kind",
        "deadline_utc", "timeout_seconds", "output_byte_limit", "reservation_id",
        "budget_scope", "remaining_usd", "reserved_usd", "profile_id",
    )
    missing = [name for name in required if name not in payload]
    if missing:
        raise AdmissionRefused("missing admission fields")
    if payload["profile_id"] != PROFILE_ID:
        raise AdmissionRefused("unsupported profile")
    role = payload["role"]
    if role not in ALLOWED_ROLES:
        if role in {"muse-second-gate", "openrouter-second-gate", "hive-second-gate"}:
            raise AdmissionRefused("no qualified distinct-family synthesizer profile")
        raise AdmissionRefused("unsupported role")
    if payload["disclosure"] != "public-tracked":
        raise AdmissionRefused("disclosure not admitted for this profile")
    if payload.get("acquisition_allowlist"):
        raise AdmissionRefused("acquisitions are not admitted")
    if not str(payload["claim_id"]).startswith("RW-34"):
        raise AdmissionRefused("claim is not RW-34")
    attempt = payload["attempt"]
    if not isinstance(attempt, int) or attempt < 1:
        raise AdmissionRefused("attempt must be a positive integer")
    timeout_s = float(payload["timeout_seconds"])
    output_limit = int(payload["output_byte_limit"])
    if timeout_s <= 0 or timeout_s > 120:
        raise AdmissionRefused("timeout outside admitted bound")
    if output_limit <= 0 or output_limit > 128 * 1024:
        raise AdmissionRefused("output limit outside admitted bound")
    reserved = _money(payload["reserved_usd"])
    remaining = _money(payload["remaining_usd"])
    if reserved != 0:
        raise AdmissionRefused("this profile admits zero inference spend")
    if remaining < reserved:
        raise AdmissionRefused("reservation exceeds remaining budget")
    deadline = _parse_deadline(str(payload["deadline_utc"]))
    if deadline <= now:
        raise AdmissionRefused("admission expired")
    brief = Path(payload["brief_path"])
    inputs = tuple(Path(item) for item in payload["input_paths"])
    digest_map = {str(k): str(v) for k, v in dict(payload["input_sha256"]).items()}
    subject = payload.get("subject_filing_path")
    schema_kind = payload["schema_kind"]
    if schema_kind != ALLOWED_ROLES[role]:
        raise AdmissionRefused("schema does not match role")
    return Envelope(
        job_id=str(payload["job_id"]),
        claim_id=str(payload["claim_id"]),
        attempt=attempt,
        role=role,
        principal=str(payload["principal"]),
        disclosure=str(payload["disclosure"]),
        brief_path=brief,
        input_paths=inputs,
        input_sha256=digest_map,
        policy_hash=str(payload["policy_hash"]),
        schema_kind=schema_kind,
        deadline_utc=deadline,
        timeout_seconds=timeout_s,
        output_byte_limit=output_limit,
        reservation_id=str(payload["reservation_id"]),
        budget_scope=str(payload["budget_scope"]),
        remaining_usd=remaining,
        reserved_usd=reserved,
        profile_id=str(payload["profile_id"]),
        subject_filing_path=Path(subject) if subject else None,
        source_families=tuple(payload.get("source_families") or ()),
        leaf_ids=tuple(payload.get("leaf_ids") or ()),
    )


def freeze_inputs(paths: list[Path]) -> dict[str, str]:
    return {str(path): sha256_file(path) for path in paths}


class Ledger:
    def __init__(self, run_dir: Path, *, max_concurrent: int = DEFAULT_CONCURRENCY):
        self.run_dir = run_dir
        self.max_concurrent = max_concurrent
        self.path = run_dir / "ledger.json"
        self.lock_path = run_dir / "ledger.lock"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"active": [], "settled": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        fh = open(self.lock_path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                locked = False
                for _ in range(200):
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                        break
                    except OSError:
                        time.sleep(0.01)
                if not locked:
                    raise AdmissionRefused("reservation lock unavailable")
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            fh.close()

    def reserve(self, envelope: Envelope) -> None:
        with self._locked():
            data = self._read()
            identity = [envelope.job_id, envelope.attempt]
            for row in data["active"] + data["settled"]:
                if [row["job_id"], row["attempt"]] == identity:
                    raise AdmissionRefused("attempt already reserved or settled")
            if len(data["active"]) >= self.max_concurrent:
                raise AdmissionRefused("concurrent cap reached")
            data["active"].append({
                "job_id": envelope.job_id,
                "attempt": envelope.attempt,
                "reservation_id": envelope.reservation_id,
                "reserved_usd": str(envelope.reserved_usd),
                "status": "reserved",
            })
            self._write(data)

    def settle(self, envelope: Envelope, *, status: str, hold: bool, usage_class: str) -> None:
        with self._locked():
            data = self._read()
            remaining = []
            found = None
            for row in data["active"]:
                if row["job_id"] == envelope.job_id and row["attempt"] == envelope.attempt:
                    found = row
                else:
                    remaining.append(row)
            if found is None:
                raise AdmissionRefused("reservation missing at settlement")
            data["active"] = remaining
            record = {
                **found,
                "status": "held" if hold else status,
                "usage_class": usage_class,
                "hold": hold,
            }
            if hold:
                data["active"].append(record)
            else:
                data["settled"].append(record)
            self._write(data)


def resolve_and_hash(envelope: Envelope) -> tuple[Path, list[tuple[Path, bytes]]]:
    brief = envelope.brief_path
    if not brief.is_absolute():
        brief = (ROOT / brief).resolve()
    else:
        brief = brief.resolve()
    if not _within(brief, BRIEFS_DIR.resolve()) or not brief.is_file():
        raise AdmissionRefused("brief outside approved directory")
    raw = brief.read_bytes()
    if not raw or len(raw) > MAX_BRIEF_BYTES:
        raise AdmissionRefused("empty or oversized brief")
    files: list[tuple[Path, bytes]] = [(brief, raw)]
    expected = dict(envelope.input_sha256)
    observed = {str(envelope.brief_path): sha256_bytes(raw)}
    for item in envelope.input_paths:
        path = item if item.is_absolute() else (ROOT / item)
        path = path.resolve()
        if path == brief:
            continue
        if not path.is_file() or not _within(path, ROOT.resolve()):
            raise AdmissionRefused("input outside repository or missing")
        data = path.read_bytes()
        if len(data) > MAX_EXCERPT_BYTES:
            raise AdmissionRefused("excerpt exceeds admitted size")
        files.append((path, data))
        observed[str(item)] = sha256_bytes(data)
    if observed != expected:
        raise AdmissionRefused("input hashes do not match the frozen admission")
    if envelope.policy_hash != profile_digest():
        raise AdmissionRefused("profile hash changed")
    if envelope.subject_filing_path is not None:
        subject = envelope.subject_filing_path
        if not subject.is_absolute():
            subject = (ROOT / subject).resolve()
        else:
            subject = subject.resolve()
        if not subject.is_file():
            raise AdmissionRefused("subject filing missing")
    return brief, files


def attempt_dir(run_dir: Path, envelope: Envelope) -> Path:
    return run_dir / envelope.job_id / str(envelope.attempt)


def bind_filing(role: str, envelope: Envelope, payload: dict[str, Any]) -> dict[str, Any]:
    bound = dict(payload)
    bound["job_id"] = envelope.job_id
    bound["claim_id"] = envelope.claim_id
    bound["attempt"] = envelope.attempt
    bound["role"] = role if role != "first-gate-review" else "first-gate-review"
    bound["route_family"] = ROUTE_FAMILY
    bound["route_model"] = ROUTE_MODEL
    bound["empirical_pass_claimed"] = False
    if role == "leaf":
        bound["brief_path"] = str(envelope.brief_path).replace("\\", "/")
    if role == "first-gate-review" and envelope.subject_filing_path is not None:
        bound["subject_filing_path"] = str(envelope.subject_filing_path).replace("\\", "/")
    return bound


def parse_transport(raw: bytes, *, output_limit: int) -> tuple[str, dict[str, Any] | None, str]:
    if len(raw) > output_limit:
        return "refusal", None, "oversized event stream"
    if not raw.strip():
        return "unknown", None, "empty transport"
    text = raw.decode("utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_EVENTS:
        return "refusal", None, "too many events"
    final: dict[str, Any] | None = None
    try:
        if len(lines) == 1 and not lines[0].lstrip().startswith('{"type"'):
            payload = json.loads(lines[0], object_pairs_hook=_unique_object)
            if isinstance(payload, dict):
                final = payload
        else:
            for line in lines:
                event = json.loads(line, object_pairs_hook=_unique_object)
                if not isinstance(event, dict):
                    return "refusal", None, "malformed event stream"
                if event.get("type") == "final":
                    payload = event.get("payload")
                    if not isinstance(payload, dict):
                        return "refusal", None, "malformed final payload"
                    final = payload
    except ValueError:
        return "refusal", None, "malformed event stream"
    if final is None:
        return "unknown", None, "no final object"
    if final.get("empirical_pass_claimed") is True:
        return "refusal", None, "empirical PASS quarantined"
    return "success", final, "ok"


def validate_filing(kind: str, payload: dict[str, Any]) -> None:
    schema = json.loads(SCHEMAS[kind].read_text(encoding="utf-8"))
    jsonschema.validate(instance=payload, schema=schema)


class Controller:
    def __init__(
        self,
        run_dir: Path,
        *,
        python: str | None = None,
        transport: SubprocessTransport | None = None,
        now_fn: Callable[[], datetime] | None = None,
        max_concurrent: int = DEFAULT_CONCURRENCY,
    ):
        self.run_dir = run_dir
        self.python = python or sys.executable
        self.transport = transport or SubprocessTransport()
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.ledger = Ledger(run_dir, max_concurrent=max_concurrent)

    def admit(self, payload: dict[str, Any]) -> Envelope:
        envelope = load_envelope(payload, now=self.now_fn())
        resolve_and_hash(envelope)
        return envelope

    def run(
        self,
        payload: dict[str, Any],
        *,
        cancel_event: threading.Event | None = None,
    ) -> Receipt:
        envelope = self.admit(payload)
        argv = build_argv(
            python=self.python,
            role=envelope.role,
            output_limit=envelope.output_byte_limit,
        )
        target = attempt_dir(self.run_dir, envelope)
        if target.exists():
            raise AdmissionRefused("exclusive output already exists")
        sandbox = target / "sandbox"
        raw_path = target / "raw.jsonl"
        filing_path = target / "filing.json"
        receipt_path = target / "receipt.json"
        self.ledger.reserve(envelope)
        try:
            target.mkdir(parents=True)
            sandbox.mkdir()
        except OSError as exc:
            self.ledger.settle(envelope, status="unknown", hold=True, usage_class="unavailable")
            raise AdmissionRefused("exclusive output unavailable") from exc
        brief, files = resolve_and_hash(envelope)
        excerpts = []
        for path, data in files:
            rel = str(path.relative_to(ROOT)).replace("\\", "/") if _within(path, ROOT) else str(path)
            excerpts.append({
                "path": rel,
                "sha256": sha256_bytes(data),
                "text": data.decode("utf-8"),
            })
        stdin_obj = {
            "job_id": envelope.job_id,
            "claim_id": envelope.claim_id,
            "attempt": envelope.attempt,
            "brief_path": str(envelope.brief_path).replace("\\", "/"),
            "excerpts": excerpts,
        }
        if envelope.subject_filing_path is not None:
            stdin_obj["subject_filing_path"] = str(envelope.subject_filing_path).replace("\\", "/")
        stdin_bytes = json.dumps(stdin_obj).encode("utf-8")
        try:
            result = self.transport.run(
                argv,
                stdin_bytes,
                envelope.timeout_seconds,
                sandbox,
                child_env(),
                output_limit=envelope.output_byte_limit,
                cancel_event=cancel_event,
            )
        except Exception:
            self.ledger.settle(envelope, status="unknown", hold=True, usage_class="unavailable")
            raise
        with raw_path.open("xb") as handle:
            handle.write(result.stdout)
        raw_digest = sha256_bytes(result.stdout)
        status, payload_obj, reason = parse_transport(
            result.stdout, output_limit=envelope.output_byte_limit
        )
        if result.cancelled:
            status, payload_obj, reason = "cancelled", None, "cancelled"
        elif result.timed_out:
            status, payload_obj, reason = "timeout", None, "timeout"
        elif status == "success" and result.returncode not in (0, None) and not result.timed_out:
            status, payload_obj, reason = "unknown", None, "nonzero exit without timeout"
        hold = status == "unknown"
        usage_class = "unavailable" if hold else "measured"
        observed = "0"
        filing_digest = None
        if status == "success" and payload_obj is not None:
            bound = bind_filing(envelope.role, envelope, payload_obj)
            try:
                validate_filing(envelope.schema_kind, bound)
                encoded = (json.dumps(bound, indent=2) + "\n").encode("utf-8")
                with filing_path.open("xb") as handle:
                    handle.write(encoded)
                filing_digest = sha256_bytes(encoded)
            except (jsonschema.ValidationError, ValueError, OSError):
                status, reason = "refusal", "invalid or duplicate filing"
                hold = False
        elif status == "success":
            status, reason = "refusal", "missing payload"
        self.ledger.settle(envelope, status=status, hold=hold, usage_class=usage_class)
        receipt = Receipt(
            job_id=envelope.job_id,
            claim_id=envelope.claim_id,
            attempt=envelope.attempt,
            status=status,
            route_family=ROUTE_FAMILY,
            route_model=ROUTE_MODEL,
            profile_id=PROFILE_ID,
            executable=self.python,
            argv=argv,
            raw_sha256=raw_digest,
            filing_sha256=filing_digest,
            receipt_sha256=None,
            usage_class=usage_class,
            reserved_usd=str(envelope.reserved_usd),
            observed_usd=observed,
            hold=hold,
            empirical_pass_claimed=False,
            reason=reason,
            paths={
                "raw": str(raw_path),
                "filing": str(filing_path) if filing_digest else "",
                "receipt": str(receipt_path),
                "brief": str(brief),
            },
        )
        encoded_receipt = (json.dumps(receipt.__dict__, indent=2) + "\n").encode("utf-8")
        receipt.receipt_sha256 = sha256_bytes(encoded_receipt)
        payload_out = dict(receipt.__dict__)
        payload_out["receipt_sha256"] = receipt.receipt_sha256
        with receipt_path.open("xb") as handle:
            handle.write((json.dumps(payload_out, indent=2) + "\n").encode("utf-8"))
        receipt.paths["receipt"] = str(receipt_path)
        return receipt


class RefusalParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(2, "refusing invalid arguments; use --help for the admitted interface\n")


def _load_payload(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return json.loads(raw, object_pairs_hook=_unique_object)


def main(argv: list[str] | None = None) -> int:
    parser = RefusalParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=("preview", "run"))
    parser.add_argument("--envelope", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        payload = _load_payload(args.envelope)
        run_dir = args.run_dir.resolve()
        if run_dir.exists() and not run_dir.is_dir():
            raise AdmissionRefused("run dir is not a directory")
        controller = Controller(run_dir)
        if args.action == "preview":
            envelope = controller.admit(payload)
            argv_cmd = build_argv(
                python=controller.python,
                role=envelope.role,
                output_limit=envelope.output_byte_limit,
            )
            print(json.dumps({
                "kind": "admitted-preview",
                "profile_id": PROFILE_ID,
                "route_family": ROUTE_FAMILY,
                "dispatch_enabled": True,
                "child_spawned": False,
                "empirical_pass_claimed": False,
                "argv": argv_cmd,
                "policy_hash": profile_digest(),
            }, indent=2))
            return 0
        receipt = controller.run(payload)
        print(json.dumps(receipt.__dict__, indent=2))
        return 0 if receipt.status == "success" else 1
    except (AdmissionRefused, ProfileError, OSError, ValueError) as exc:
        sys.stderr.write(f"refusing: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
