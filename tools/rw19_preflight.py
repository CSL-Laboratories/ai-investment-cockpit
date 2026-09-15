"""RW-19 prospective news-reconnect safety-preflight generator.

Deterministic, strictly read-only producer for the 24-field safety preflight consumed by
``tools/rw19_news_reconnect_plan_bound.py``. It never starts the collector, never opens a
network socket to a vendor, never mutates the repository, and never creates/migrates a
database, HALT directory, or keyring entry. One caveat for WAL databases: a ``mode=ro``
SQLite connection still writes read-marks into the ``-shm`` sidecar (database content is
never modified), and an uncheckpointed ``-wal`` after an unclean shutdown makes the
read-only open fail — which refuses the launch (fail closed) rather than repairing it.

Field sourcing (authoritative repo APIs):
  schema_version / package / scope — constants matching the collector contract
  checked_at — injected clock, sampled only after every other check succeeds
  configured_mode — ``config.settings.get_settings().mode.value``
  effective_mode — ``orchestrator.mode_ladder.ModeLadder.current()`` over a mode=ro store
  persisted_mode — ``ModeLadder.current_row().mode`` when a ModeState is referenced, else null
  merger_execution_watchlist_count — ``len(Settings.merger_watchlist_symbols)``
  expected_edge_available — ``False`` only after proving that watchlist is empty
  row counts — ``SELECT COUNT(*)`` on merger_edge_evidence / merger_selection_commitment /
    proposal_record / risk_decision / approval_record / order_row via sqlite ``mode=ro``
  audit_chain_valid — ``storage.audit.AuditLog.verify_chain()`` with
    ``core.secrets.require_secret("audit-hmac-key")`` (``main._HMAC_SECRET_NAME``); never
    ``main._hmac_key()``
  halt_absent — ``not orchestrator.halt.halt_is_set(default_halt_path())``
  alpaca_credentials_configured — presence via ``core.secrets.get_secret`` for
    ``alpaca_key`` / ``alpaca_secret`` only (never reveal)
  cockpit_service_processes — scan of running process command lines for cockpit service
    entrypoints (``main`` / ``orchestrator.app``)
  api_port_listeners — ``orchestrator.auth.is_loopback(settings.api_host)`` and a free
    ``settings.api_port`` bind probe
  source_commit / worktree_clean / main_equals_origin_main — ``git rev-parse HEAD``,
    ``git rev-parse origin/main``, ``git status --porcelain`` (no fetch)
  awareness_universe_symbols — ``sorted(Settings.watchlist_symbols)``; must equal the plan
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Before any repo-module import: this process must never write bytecode caches.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from config.settings import ENV_FILE, get_settings  # noqa: E402
from core.secrets import get_secret, require_secret  # noqa: E402
from ingest.edgar_brew import read_only_uri  # noqa: E402
from orchestrator.auth import is_loopback  # noqa: E402
from orchestrator.halt import default_halt_path, halt_is_set  # noqa: E402
from orchestrator.mode_ladder import ModeLadder  # noqa: E402
from orchestrator.state import OrchestratorState  # noqa: E402

SCHEMA_VERSION = 1
PACKAGE = "RW-19"
SCOPE = "prospective-news-reconnect-safety-preflight"
# main._HMAC_SECRET_NAME — presence-only require; never main._hmac_key() (that can mint).
HMAC_SECRET_NAME = "audit-hmac-key"
ALPACA_KEY_NAME = "alpaca_key"
ALPACA_SECRET_NAME = "alpaca_secret"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

COUNT_TABLES: tuple[tuple[str, str], ...] = (
    ("merger_edge_rows", "merger_edge_evidence"),
    ("merger_selection_rows", "merger_selection_commitment"),
    ("proposal_rows", "proposal_record"),
    ("risk_decision_rows", "risk_decision"),
    ("approval_rows", "approval_record"),
    ("order_rows", "order_row"),
)
# Suffix boundaries include quotes: Windows spells arguments with spaces as "...\main.py",
# and a marker anchored on whitespace-or-EOS misses the closing quote (adversarial P1-1).
_SERVICE_MARKERS = (
    re.compile(r"(?:^|[\s/\\\"'])main\.py(?:[\s\"']|$)", re.IGNORECASE),
    re.compile(r"(?:^|[\s\"'])-m\s*(?:app\.)?main(?:[\s\"']|$)", re.IGNORECASE),
    re.compile(r"orchestrator\.app", re.IGNORECASE),
)
# Self-exclusion must match only the executing-script/module token, never a free substring:
# "pytest" appearing in a venv name or log-path argument must not hide a real service.
_SELF_MARKERS = (
    re.compile(r"(?:^|[\s/\\\"'])rw19_preflight\.py(?:[\s\"']|$)", re.IGNORECASE),
    re.compile(
        r"(?:^|[\s/\\\"'])rw19_news_reconnect_plan_bound\.py(?:[\s\"']|$)", re.IGNORECASE
    ),
    re.compile(r"(?:^|[\s/\\\"'])rw19_autolaunch\.py(?:[\s\"']|$)", re.IGNORECASE),
    re.compile(r"(?:^|[\s\"'])-m\s*pytest(?:[\s\"']|$)", re.IGNORECASE),
    re.compile(r"(?:^|[\s/\\\"'])pytest(?:\.exe)?(?:[\s\"']|$)", re.IGNORECASE),
)
_PYTHON_IMAGE_RE = re.compile(r"^(?:py|python)\w*(?:\.\d+)*\.exe$", re.IGNORECASE)


class PreflightError(RuntimeError):
    """A single failed safety check; the CLI prints ``str(self)`` and exits nonzero."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_collector():
    path = ROOT / "tools" / "rw19_news_reconnect_plan_bound.py"
    name = "rw19_news_reconnect_plan_bound_preflight_consumer"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PreflightError("cannot load plan-bound collector module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_plan_hash_verified(path: Path, expected_sha256: str) -> dict[str, Any]:
    """Load a plan after SHA-256 verification without the collector's start-tolerance gate."""
    if SHA_RE.fullmatch(str(expected_sha256)) is None:
        raise PreflightError("expected plan hash must be lowercase SHA-256")
    payload = Path(path).read_bytes()
    if _sha256(payload) != expected_sha256:
        raise PreflightError("collection plan hash differs from expected plan hash")
    try:
        plan = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreflightError("collection plan is not valid UTF-8 JSON") from exc
    if not isinstance(plan, dict):
        raise PreflightError("collection plan must be a JSON object")
    symbols = plan.get("universe_symbols")
    if not isinstance(symbols, list) or not all(isinstance(s, str) for s in symbols):
        raise PreflightError("collection plan universe_symbols must be a string list")
    source_commit = plan.get("source_commit")
    if not isinstance(source_commit, str) or COMMIT_RE.fullmatch(source_commit) is None:
        raise PreflightError("collection plan source_commit is not canonical")
    return plan


def default_database_path() -> Path:
    """Operational cockpit SQLite file (compose default ``sqlite:///cockpit.db`` under ``app/``)."""
    return APP / "cockpit.db"


def default_count_rows(db_path: Path, table: str) -> int:
    if not Path(db_path).is_file():
        raise PreflightError("configured SQLite database file is missing")
    try:
        conn = sqlite3.connect(read_only_uri(db_path), uri=True)
    except sqlite3.Error as exc:
        raise PreflightError(f"cannot open SQLite database read-only: {exc}") from exc
    try:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if present is None:
            raise PreflightError(f"required table missing: {table}")
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return int(count)
    except PreflightError:
        raise
    except sqlite3.Error as exc:
        raise PreflightError(f"failed reading table {table}: {exc}") from exc
    finally:
        conn.close()


def default_secret_present(name: str) -> bool:
    return get_secret(name) is not None


def default_require_hmac_key() -> bytes:
    """Presence-only HMAC material for verify_chain; never mints a missing credential."""
    return require_secret(HMAC_SECRET_NAME).reveal().encode("utf-8")


def _open_readonly_state(db_path: Path, settings, hmac_key: bytes) -> OrchestratorState:
    """Build OrchestratorState over an existing DB without make_engine/create_all/compose."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    if not Path(db_path).is_file():
        raise PreflightError("configured SQLite database file is missing")
    uri = read_only_uri(db_path)

    def _connect():
        return sqlite3.connect(uri, uri=True)

    try:
        engine = create_engine("sqlite://", creator=_connect, future=True)
        session_factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
        return OrchestratorState.build(
            session_factory, hmac_key=hmac_key, settings=settings
        )
    except Exception as exc:
        raise PreflightError(f"cannot open read-only orchestrator store: {exc}") from exc


def default_verify_audit_chain(db_path: Path, hmac_key: bytes, settings) -> bool:
    state = _open_readonly_state(db_path, settings, hmac_key)
    try:
        return bool(state.audit.verify_chain().ok)
    except Exception as exc:
        raise PreflightError(f"audit chain verification failed: {exc}") from exc


def _latest_mode_change_error(state) -> str | None:
    """The latest SIGNED mode_change must be absent or ``read_only``.

    ``ModeLadder.current()`` fails closed TO ``read_only`` — which in this generator's
    polarity reads as safe (adversarial P2-1). Assert on the signed record itself so a
    valid chain whose latest signed transition armed an auto rung refuses here even if
    the mutable row was edited back and ``current()`` collapsed to ``read_only``.
    """
    ladder = ModeLadder(state)
    with state.session_factory() as s:
        entry = ladder._latest_entry(s)
        if entry is None:
            return None
        if not state.audit.verify_entry(entry):
            return "latest mode_change entry fails HMAC verification"
        to = (entry.payload or {}).get("to")
        if to != "read_only":
            return f"latest signed mode_change is {to!r}, not read_only"
    return None


def default_read_modes(db_path: Path, hmac_key: bytes, settings) -> tuple[str, str | None]:
    state = _open_readonly_state(db_path, settings, hmac_key)
    try:
        ladder = ModeLadder(state)
        effective = ladder.current().value
        row = ladder.current_row()
        persisted = row.mode if row is not None else None
        detail = _latest_mode_change_error(state)
        if detail is not None:
            raise PreflightError(detail)
        return effective, persisted
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError(f"mode ladder read failed: {exc}") from exc


def default_halt_absent() -> bool:
    try:
        path = default_halt_path()
        return not halt_is_set(path)
    except Exception as exc:
        raise PreflightError(f"HALT probe failed: {exc}") from exc


def default_env_file_present() -> bool:
    """Settings must come from the cockpit's real .env, never from silent defaults.

    ``config.settings.ENV_FILE`` resolves from THIS process's environment (adversarial
    P1-4): if the file is absent, ``Settings`` falls back to all-defaults and the
    generator would assert ``read_only`` / empty-merger-watchlist without ever having
    read the cockpit's actual configuration. The launcher pins ``COCKPIT_ENV_FILE``.
    """
    return Path(ENV_FILE).is_file()


def default_halt_env_pinned() -> bool:
    """``COCKPIT_HALT_PATH`` must be explicitly pinned in this environment.

    ``default_halt_path`` deliberately reads the environment, not Settings; probing a
    fallback location while the cockpit service reads a pinned one is exactly the
    R5-S2 divergence class ``halt.py`` warns about (adversarial P1-4). The launcher
    exports the pinned path so the generator and the service converge.
    """
    return bool(os.environ.get("COCKPIT_HALT_PATH"))


def _cmdline_is_cockpit_service(cmdline: str) -> bool:
    if any(pattern.search(cmdline) for pattern in _SELF_MARKERS):
        return False
    return any(pattern.search(cmdline) for pattern in _SERVICE_MARKERS)


def _scan_windows_processes(stdout: str) -> int:
    """Count cockpit services in WMIC LIST output, fail closed on opacity.

    Record framing (verification finding N1): WMIC terminates every LINE with
    ``\\r\\r\\n``, which universal-newline translation turns into a blank line — so a
    blank-line split separates FIELDS, not records. Records are framed by field keys:
    WMIC emits requested properties alphabetically (CommandLine, Name, ProcessId), so
    ``ProcessId=`` closes a record.

    A limited token gets an EMPTY CommandLine for processes it cannot open (adversarial
    P1-2): "zero cockpit processes" and "zero processes visible to me" must not be the
    same answer, so any python-imaged process with an unreadable command line refuses,
    and a scan that pairs NO name with a readable command line refuses outright.
    """
    count = 0
    readable_pairs = 0
    record: dict[str, str] = {}

    def flush() -> None:
        nonlocal count, readable_pairs
        name = record.get("name", "")
        cmdline = record.get("commandline", "")
        record.clear()
        if not name:
            return
        if not cmdline:
            if _PYTHON_IMAGE_RE.fullmatch(name):
                raise PreflightError(
                    f"python process {name!r} has an unreadable command line; "
                    "cannot rule out a cockpit service"
                )
            return
        readable_pairs += 1
        if _cmdline_is_cockpit_service(cmdline):
            count += 1

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        if key not in {"commandline", "name", "processid"}:
            continue
        if key in record:
            flush()
        record[key] = value.strip()
        if key == "processid":
            flush()
    flush()
    if readable_pairs == 0:
        raise PreflightError(
            "process scan positive control failed: no process paired a name with a "
            "readable command line"
        )
    return count


def _scan_windows_cim_processes(stdout: str) -> int:
    """Read structured CIM records without treating command-line text as framing."""
    try:
        records = json.loads(stdout)
    except ValueError:
        raise PreflightError("process scan returned invalid JSON") from None
    if not isinstance(records, list):
        raise PreflightError("process scan returned invalid records")
    count = readable_pairs = 0
    seen_pids: set[int] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"Name", "CommandLine", "ProcessId"}:
            raise PreflightError("process scan returned invalid record fields")
        name, cmdline, pid = record["Name"], record["CommandLine"], record["ProcessId"]
        if (not isinstance(name, str) or not name.strip()
                or (cmdline is not None and not isinstance(cmdline, str))
                or type(pid) is not int or pid < 0 or pid in seen_pids):
            raise PreflightError("process scan returned invalid record values")
        seen_pids.add(pid)
        if not cmdline or not cmdline.strip():
            if _PYTHON_IMAGE_RE.fullmatch(name.strip()):
                raise PreflightError("python process has an unreadable command line")
            continue
        readable_pairs += 1
        count += int(_cmdline_is_cockpit_service(cmdline))
    if not readable_pairs:
        raise PreflightError("process scan positive control failed: no readable process")
    return count


def _count_windows_service_processes() -> int:
    options = dict(check=False, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=30)
    try:
        completed = subprocess.run(
            ["wmic", "process", "get", "ProcessId,Name,CommandLine", "/FORMAT:LIST"],
            **options,
        )
    except FileNotFoundError:
        # Current Windows releases remove WMIC. Query the same local WMI class;
        # JSON preserves multiline command lines and null (unreadable) values.
        script = (
            "$ErrorActionPreference='Stop'; "
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
            "$rows=@(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop | "
            "Select-Object Name,CommandLine,ProcessId); "
            "ConvertTo-Json -InputObject $rows -Compress"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            **options,
        )
        if completed.returncode != 0:
            raise PreflightError("CIM process scan failed")
        return _scan_windows_cim_processes(completed.stdout)
    if completed.returncode != 0:
        raise PreflightError("process scan failed")
    return _scan_windows_processes(completed.stdout)


def default_count_service_processes() -> int:
    """Count running cockpit service entrypoints (/proc or Windows WMI/CIM)."""
    try:
        if os.name == "nt":
            return _count_windows_service_processes()
        count = 0
        proc_root = Path("/proc")
        if not proc_root.is_dir():
            raise PreflightError("process scan failed: /proc unavailable")
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                # Same opacity rule as Windows: an unreadable python process refuses.
                try:
                    comm = (entry / "comm").read_text(errors="replace").strip()
                except OSError:
                    continue
                if comm.lower().startswith(("python", "py")):
                    raise PreflightError(
                        f"python process pid {entry.name} has an unreadable command line; "
                        "cannot rule out a cockpit service"
                    ) from None
                continue
            cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
            if cmdline and _cmdline_is_cockpit_service(cmdline):
                count += 1
        return count
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError(f"process scan failed: {exc}") from exc


def _bind_probe(family: int, address: str, port: int) -> int:
    """1 if the loopback port is taken, 0 if free. No SO_REUSEADDR: on Windows it
    lets a bind SUCCEED over an active listener (fail-open)."""
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((address, int(port)))
        except OSError:
            return 1
        return 0


def default_count_api_port_listeners(host: str, port: int) -> int:
    if not is_loopback(host):
        raise PreflightError(f"api_host is not loopback: {host!r}")
    # Probe the address families the configured host can actually resolve to
    # (adversarial P1-3): "::1" needs AF_INET6, and "localhost" resolves to ::1
    # first on Windows, so an IPv4-only probe reports a live v6 API as absent.
    if host == "127.0.0.1":
        targets = ((socket.AF_INET, "127.0.0.1"),)
    elif host == "::1":
        targets = ((socket.AF_INET6, "::1"),)
    else:  # "localhost" per orchestrator.auth._LOOPBACK_HOSTS
        targets = ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1"))
    count = 0
    probed = 0
    for family, address in targets:
        try:
            sock_probe = _bind_probe(family, address, port)
        except OSError as exc:
            # Only a MISSING STACK may skip a leg (no listener can exist on a stack
            # the host does not have); any other probe fault refuses (finding N2).
            no_stack = exc.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT) or (
                getattr(exc, "winerror", None) in (10043, 10047)
            )
            if no_stack:
                continue
            raise PreflightError(f"API port probe failed: {exc}") from exc
        except Exception as exc:
            raise PreflightError(f"API port probe failed: {exc}") from exc
        probed += 1
        count += sock_probe
    if probed == 0:
        raise PreflightError(
            f"API port probe could not probe any address family for {host!r}"
        )
    return count


def default_git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def default_utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class PreflightDependencies:
    """Injectable seams; production defaults bind the real read-only APIs."""

    read_settings: Callable[[], Any] = get_settings
    env_file_present: Callable[[], bool] = default_env_file_present
    halt_env_pinned: Callable[[], bool] = default_halt_env_pinned
    database_path: Callable[[], Path] = default_database_path
    count_rows: Callable[[Path, str], int] = default_count_rows
    secret_present: Callable[[str], bool] = default_secret_present
    require_hmac_key: Callable[[], bytes] = default_require_hmac_key
    verify_audit_chain: Callable[[Path, bytes, Any], bool] = default_verify_audit_chain
    read_modes: Callable[[Path, bytes, Any], tuple[str, str | None]] = default_read_modes
    halt_absent: Callable[[], bool] = default_halt_absent
    count_service_processes: Callable[[], int] = default_count_service_processes
    count_api_port_listeners: Callable[[str, int], int] = default_count_api_port_listeners
    git: Callable[..., str] = default_git
    clock: Callable[[], datetime] = default_utc_now


def _git_porcelain(git: Callable[..., str]) -> str:
    try:
        return git("status", "--porcelain")
    except Exception as exc:
        raise PreflightError(f"git status failed: {exc}") from exc


def _require_clean_worktree(git: Callable[..., str], *, when: str) -> None:
    porcelain = _git_porcelain(git)
    if porcelain.strip():
        raise PreflightError(f"worktree is dirty ({when})")


def build_safety_preflight(
    plan: dict[str, Any],
    *,
    deps: PreflightDependencies | None = None,
) -> dict[str, Any]:
    """Assemble and refuse-closed-validate the 24-field safety preflight dict."""
    deps = deps or PreflightDependencies()
    _require_clean_worktree(deps.git, when="before checks")

    if not deps.env_file_present():
        raise PreflightError(
            "cockpit .env file is absent; refusing to assert defaults as configuration"
        )
    if not deps.halt_env_pinned():
        raise PreflightError("COCKPIT_HALT_PATH is not pinned in this environment")

    settings = deps.read_settings()
    configured_mode = settings.mode.value
    if configured_mode != "read_only":
        raise PreflightError(f"configured_mode is not read_only: {configured_mode!r}")

    db_path = Path(deps.database_path())
    if not db_path.is_file():
        raise PreflightError("configured SQLite database file is missing")

    row_counts: dict[str, int] = {}
    for field_name, table in COUNT_TABLES:
        count = deps.count_rows(db_path, table)
        if count != 0:
            raise PreflightError(f"{field_name} is nonzero: {count}")
        row_counts[field_name] = count

    try:
        hmac_key = deps.require_hmac_key()
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError(f"audit HMAC secret is not provisioned: {exc}") from exc

    if not deps.verify_audit_chain(db_path, hmac_key, settings):
        raise PreflightError("audit chain is invalid")

    effective_mode, persisted_mode = deps.read_modes(db_path, hmac_key, settings)
    if effective_mode != "read_only":
        raise PreflightError(f"effective_mode is not read_only: {effective_mode!r}")
    if persisted_mode not in (None, "read_only"):
        raise PreflightError(f"persisted_mode exceeds read_only: {persisted_mode!r}")

    merger_count = len(settings.merger_watchlist_symbols)
    if merger_count != 0:
        raise PreflightError(
            f"merger_execution_watchlist_count is nonzero: {merger_count}"
        )
    # Prove emptiness before asserting expected edge unavailable (never hardcode).
    expected_edge_available = False

    if not deps.halt_absent():
        raise PreflightError("HALT sentinel is present")

    if not (
        deps.secret_present(ALPACA_KEY_NAME) and deps.secret_present(ALPACA_SECRET_NAME)
    ):
        raise PreflightError("alpaca credentials are not fully provisioned")

    process_count = deps.count_service_processes()
    if process_count != 0:
        raise PreflightError(f"cockpit_service_processes is nonzero: {process_count}")

    listener_count = deps.count_api_port_listeners(settings.api_host, settings.api_port)
    if listener_count != 0:
        raise PreflightError(f"api_port_listeners is nonzero: {listener_count}")

    try:
        head = deps.git("rev-parse", "HEAD").strip()
        origin_main = deps.git("rev-parse", "origin/main").strip()
    except Exception as exc:
        raise PreflightError(f"git rev-parse failed: {exc}") from exc
    if COMMIT_RE.fullmatch(head) is None or COMMIT_RE.fullmatch(origin_main) is None:
        raise PreflightError("git commit hashes are not canonical")
    if head != origin_main:
        raise PreflightError("HEAD does not equal origin/main")
    if head != plan["source_commit"]:
        raise PreflightError("HEAD does not equal plan source_commit")
    worktree_clean = not _git_porcelain(deps.git).strip()
    if not worktree_clean:
        raise PreflightError("worktree is dirty (during checks)")

    awareness = sorted(settings.watchlist_symbols)
    if awareness != list(plan["universe_symbols"]):
        raise PreflightError("awareness universe differs from the plan universe")

    _require_clean_worktree(deps.git, when="after checks")

    checked_at = deps.clock()
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise PreflightError("checked_at clock must be timezone-aware UTC")
    checked_at = checked_at.astimezone(UTC)

    preflight = {
        "schema_version": SCHEMA_VERSION,
        "package": PACKAGE,
        "scope": SCOPE,
        "checked_at": checked_at.isoformat(),
        "source_commit": head,
        "configured_mode": configured_mode,
        "effective_mode": effective_mode,
        "persisted_mode": persisted_mode,
        "expected_edge_available": expected_edge_available,
        "merger_execution_watchlist_count": merger_count,
        "merger_edge_rows": row_counts["merger_edge_rows"],
        "merger_selection_rows": row_counts["merger_selection_rows"],
        "proposal_rows": row_counts["proposal_rows"],
        "risk_decision_rows": row_counts["risk_decision_rows"],
        "approval_rows": row_counts["approval_rows"],
        "order_rows": row_counts["order_rows"],
        "audit_chain_valid": True,
        "halt_absent": True,
        "alpaca_credentials_configured": True,
        "cockpit_service_processes": process_count,
        "api_port_listeners": listener_count,
        "worktree_clean": True,
        "main_equals_origin_main": True,
        "awareness_universe_symbols": awareness,
    }

    payload = _canonical_bytes(preflight) + b"\n"
    collector = _load_collector()
    fd, tmp_name = tempfile.mkstemp(prefix="rw19-preflight-", suffix=".json")
    tmp_path = Path(tmp_name)
    try:
        os.close(fd)
        tmp_path.write_bytes(payload)
        collector._load_preflight(
            tmp_path,
            now=checked_at,
            collector_source_commit=plan["source_commit"],
            symbols=list(plan["universe_symbols"]),
        )
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError(f"collector preflight validation refused: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)
    return preflight


def write_preflight(path: Path, preflight: dict[str, Any]) -> bytes:
    payload = _canonical_bytes(preflight) + b"\n"
    path = Path(path)
    try:
        path.write_bytes(payload)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return payload


def run(
    *,
    plan_path: Path,
    expected_plan_sha256: str,
    output_path: Path,
    deps: PreflightDependencies | None = None,
) -> dict[str, Any]:
    plan = load_plan_hash_verified(plan_path, expected_plan_sha256)
    preflight = build_safety_preflight(plan, deps=deps)
    write_preflight(output_path, preflight)
    return preflight


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a fail-closed RW-19 safety preflight JSON (read-only)."
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = Path(args.output)
    try:
        run(
            plan_path=Path(args.plan),
            expected_plan_sha256=args.expected_plan_sha256,
            output_path=output,
        )
    except Exception as exc:  # noqa: BLE001 — any failed check: no output, one-line stderr
        if output.exists():
            output.unlink(missing_ok=True)
        print(str(exc).splitlines()[0], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
