"""Hermetic tests for the RW-19 safety-preflight generator."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "tools" / "rw19_preflight.py"
SPEC = importlib.util.spec_from_file_location("rw19_preflight_tool", TOOL)
preflight_mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preflight_mod
SPEC.loader.exec_module(preflight_mod)

COLLECTOR_TOOL = ROOT / "tools" / "rw19_news_reconnect_plan_bound.py"
COLLECTOR_SPEC = importlib.util.spec_from_file_location(
    "rw19_news_reconnect_plan_bound_for_preflight_tests", COLLECTOR_TOOL
)
collector = importlib.util.module_from_spec(COLLECTOR_SPEC)
sys.modules[COLLECTOR_SPEC.name] = collector
COLLECTOR_SPEC.loader.exec_module(collector)

SOURCE_COMMIT = "a" * 40
NOW = datetime(2026, 8, 25, 16, 0, tzinfo=UTC)
UNIVERSE = ["AAPL", "AVGO", "INTC", "META", "MRVL", "MSFT", "MU", "NVDA", "SNDK", "SPCX", "TSLA"]
SECRET_MATERIAL = "alpaca-super-secret-value-do-not-leak"


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def write_json(path: Path, value) -> str:
    payload = canonical(value) + b"\n"
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def plan_doc(**changes) -> dict:
    value = {
        "schema_version": 1,
        "package": "RW-19",
        "scope": "prospective-news-reconnect-plan-only",
        "source_commit": SOURCE_COMMIT,
        "universe_symbols": list(UNIVERSE),
    }
    value.update(changes)
    return value


def write_plan(tmp_path: Path, **changes) -> tuple[Path, str, dict]:
    value = plan_doc(**changes)
    path = tmp_path / "plan.json"
    return path, write_json(path, value), value


class Recording:
    def __init__(self) -> None:
        self.order: list[str] = []


def make_deps(tmp_path: Path, recording: Recording | None = None, **overrides):
    rec = recording or Recording()

    def mark(name, fn):
        def wrapped(*args, **kwargs):
            rec.order.append(name)
            return fn(*args, **kwargs)

        return wrapped

    settings = SimpleNamespace(
        mode=SimpleNamespace(value="read_only"),
        merger_watchlist_symbols=frozenset(),
        watchlist_symbols=frozenset(UNIVERSE),
        api_host="127.0.0.1",
        api_port=8787,
    )
    row_counts = {table: 0 for _, table in preflight_mod.COUNT_TABLES}
    secrets = {preflight_mod.ALPACA_KEY_NAME, preflight_mod.ALPACA_SECRET_NAME}
    git_state = {
        "HEAD": SOURCE_COMMIT,
        "origin/main": SOURCE_COMMIT,
        "porcelain": "",
    }
    clock_steps = {"n": 0}
    db_file = tmp_path / "fixture-cockpit.db"
    db_file.write_bytes(b"")

    def read_settings():
        return settings

    def database_path():
        return db_file

    def count_rows(_db, table):
        if table not in row_counts:
            raise preflight_mod.PreflightError(f"required table missing: {table}")
        return row_counts[table]

    def secret_present(name):
        return name in secrets

    def require_hmac_key():
        return b"fixture-hmac-key"

    def verify_audit_chain(_db, _key, _settings):
        return True

    def read_modes(_db, _key, _settings):
        return "read_only", None

    def halt_absent():
        return True

    def count_service_processes():
        return 0

    def count_api_port_listeners(host, port):
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise preflight_mod.PreflightError(f"api_host is not loopback: {host!r}")
        return 0

    def git(*args: str) -> str:
        if args == ("status", "--porcelain"):
            return git_state["porcelain"]
        if args == ("rev-parse", "HEAD"):
            return git_state["HEAD"] + "\n"
        if args == ("rev-parse", "origin/main"):
            return git_state["origin/main"] + "\n"
        raise AssertionError(f"unexpected git args: {args}")

    def clock():
        clock_steps["n"] += 1
        return NOW + timedelta(seconds=clock_steps["n"])

    base = dict(
        read_settings=mark("settings", read_settings),
        env_file_present=mark("env_file", lambda: True),
        halt_env_pinned=mark("halt_env", lambda: True),
        database_path=mark("database_path", database_path),
        count_rows=mark("count_rows", count_rows),
        secret_present=mark("secret_present", secret_present),
        require_hmac_key=mark("require_hmac_key", require_hmac_key),
        verify_audit_chain=mark("verify_audit_chain", verify_audit_chain),
        read_modes=mark("read_modes", read_modes),
        halt_absent=mark("halt_absent", halt_absent),
        count_service_processes=mark("processes", count_service_processes),
        count_api_port_listeners=mark("port", count_api_port_listeners),
        git=mark("git", git),
        clock=mark("clock", clock),
    )
    base.update(overrides)
    deps = preflight_mod.PreflightDependencies(**base)
    # The dependencies dataclass is frozen; stash test handles via object.__setattr__.
    for name, value in (
        ("_test_settings", settings),
        ("_test_row_counts", row_counts),
        ("_test_secrets", secrets),
        ("_test_git_state", git_state),
        ("_test_recording", rec),
        ("_test_db_file", db_file),
    ):
        object.__setattr__(deps, name, value)
    return deps


def test_success_writes_canonical_bytes_validated_by_collector(tmp_path):
    plan_path, digest, plan = write_plan(tmp_path)
    output = tmp_path / "safety_preflight.json"
    deps = make_deps(tmp_path)
    preflight_mod.run(
        plan_path=plan_path,
        expected_plan_sha256=digest,
        output_path=output,
        deps=deps,
    )
    payload = output.read_bytes()
    assert payload.endswith(b"\n")
    assert payload == canonical(json.loads(payload.decode("utf-8"))) + b"\n"
    loaded, loaded_bytes = collector._load_preflight(
        output,
        now=NOW + timedelta(minutes=1),
        collector_source_commit=plan["source_commit"],
        symbols=list(plan["universe_symbols"]),
    )
    assert loaded_bytes == payload
    assert loaded["schema_version"] == 1
    assert loaded["package"] == "RW-19"
    assert loaded["scope"] == "prospective-news-reconnect-safety-preflight"
    assert loaded["expected_edge_available"] is False
    assert loaded["persisted_mode"] is None


def test_cli_success_exit_zero(tmp_path, monkeypatch):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path)
    monkeypatch.setattr(
        preflight_mod,
        "PreflightDependencies",
        lambda: deps,
    )
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    assert preflight_mod.main([
        "--plan", str(plan_path),
        "--expected-plan-sha256", digest,
        "--output", str(output),
    ]) == 0
    assert output.is_file()
    assert SECRET_MATERIAL not in output.read_text(encoding="utf-8")
    assert SECRET_MATERIAL not in stderr.getvalue()


@pytest.mark.parametrize(
    "field",
    [
        "merger_edge_evidence",
        "merger_selection_commitment",
        "proposal_record",
        "risk_decision",
        "approval_record",
        "order_row",
    ],
)
def test_nonzero_row_count_refuses_and_writes_nothing(tmp_path, field):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path)
    deps._test_row_counts[field] = 1  # type: ignore[attr-defined]
    with pytest.raises(preflight_mod.PreflightError, match="nonzero"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_nonzero_merger_watchlist_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path)
    deps._test_settings.merger_watchlist_symbols = frozenset({"AAPL"})  # type: ignore[attr-defined]
    with pytest.raises(preflight_mod.PreflightError, match="merger_execution_watchlist"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_dirty_tree_before_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path)
    deps._test_git_state["porcelain"] = " M tools/rw19_preflight.py\n"  # type: ignore[attr-defined]
    with pytest.raises(preflight_mod.PreflightError, match="dirty"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_head_not_equal_origin_main_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path)
    deps._test_git_state["origin/main"] = "b" * 40  # type: ignore[attr-defined]
    with pytest.raises(preflight_mod.PreflightError, match="origin/main"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_halt_present_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path, halt_absent=lambda: False)
    with pytest.raises(preflight_mod.PreflightError, match="HALT"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_configured_mode_not_read_only_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path)
    deps._test_settings.mode.value = "paper"  # type: ignore[attr-defined]
    with pytest.raises(preflight_mod.PreflightError, match="configured_mode"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_effective_mode_not_read_only_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path, read_modes=lambda *_a, **_k: ("paper", None))
    with pytest.raises(preflight_mod.PreflightError, match="effective_mode"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_persisted_mode_invalid_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path, read_modes=lambda *_a, **_k: ("read_only", "paper"))
    with pytest.raises(preflight_mod.PreflightError, match="persisted_mode"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_missing_keyring_entry_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path)
    deps._test_secrets.remove(preflight_mod.ALPACA_SECRET_NAME)  # type: ignore[attr-defined]
    with pytest.raises(preflight_mod.PreflightError, match="alpaca credentials"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_wrong_universe_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path)
    deps._test_settings.watchlist_symbols = frozenset({"AAPL"})  # type: ignore[attr-defined]
    with pytest.raises(preflight_mod.PreflightError, match="universe"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_missing_db_file_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"

    def missing_db():
        return tmp_path / "absent.db"

    deps = make_deps(tmp_path, database_path=missing_db)
    with pytest.raises(preflight_mod.PreflightError, match="database file is missing"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_missing_table_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"

    def count_rows(_db, table):
        raise preflight_mod.PreflightError(f"required table missing: {table}")

    deps = make_deps(tmp_path, count_rows=count_rows)
    with pytest.raises(preflight_mod.PreflightError, match="required table missing"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_audit_chain_invalid_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path, verify_audit_chain=lambda *_a, **_k: False)
    with pytest.raises(preflight_mod.PreflightError, match="audit chain"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_non_loopback_api_host_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path)
    deps._test_settings.api_host = "0.0.0.0"  # type: ignore[attr-defined]
    with pytest.raises(preflight_mod.PreflightError, match="loopback"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_busy_port_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path, count_api_port_listeners=lambda host, port: 1)
    with pytest.raises(preflight_mod.PreflightError, match="api_port_listeners"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_process_found_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path, count_service_processes=lambda: 2)
    with pytest.raises(preflight_mod.PreflightError, match="cockpit_service_processes"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_plan_hash_mismatch_refuses(tmp_path):
    plan_path, _digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path)
    with pytest.raises(preflight_mod.PreflightError, match="hash differs"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256="0" * 64,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_cli_refusal_deletes_partial_and_prints_one_line(tmp_path, monkeypatch):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    output.write_text("partial\n", encoding="utf-8")
    deps = make_deps(tmp_path, halt_absent=lambda: False)
    monkeypatch.setattr(preflight_mod, "PreflightDependencies", lambda: deps)
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    assert preflight_mod.main([
        "--plan", str(plan_path),
        "--expected-plan-sha256", digest,
        "--output", str(output),
    ]) == 1
    assert not output.exists()
    err = stderr.getvalue().strip()
    assert err
    assert "\n" not in err
    assert SECRET_MATERIAL not in err


def test_checked_at_set_after_other_checks(tmp_path):
    recording = Recording()
    deps = make_deps(tmp_path, recording=recording)
    result = preflight_mod.build_safety_preflight(
        plan_doc(),
        deps=deps,
    )
    assert recording.order[-1] == "clock"
    assert "settings" in recording.order
    assert "count_rows" in recording.order
    assert "verify_audit_chain" in recording.order
    assert "halt_absent" in recording.order
    assert recording.order.index("clock") > recording.order.index("settings")
    assert recording.order.index("clock") > max(
        i for i, name in enumerate(recording.order) if name != "clock"
    )
    checked = datetime.fromisoformat(result["checked_at"])
    assert checked > NOW


def test_missing_env_file_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path, env_file_present=lambda: False)
    with pytest.raises(preflight_mod.PreflightError, match=r"\.env file is absent"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


def test_unpinned_halt_env_refuses(tmp_path):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"
    deps = make_deps(tmp_path, halt_env_pinned=lambda: False)
    with pytest.raises(preflight_mod.PreflightError, match="COCKPIT_HALT_PATH"):
        preflight_mod.run(
            plan_path=plan_path,
            expected_plan_sha256=digest,
            output_path=output,
            deps=deps,
        )
    assert not output.exists()


@pytest.mark.parametrize(
    ("cmdline", "expected"),
    [
        # Quoted Windows spellings must be detected (adversarial P1-1).
        (r'python "C:\repo with space\app\main.py"', True),
        (r'"C:\repo\app\main.py"', True),
        (r'python "main.py" --x', True),
        ("python -m main", True),
        ("python -mmain", True),
        ("python -m app.main", True),
        ("py main.py", True),
        ("uvicorn orchestrator.app:app", True),
        # "pytest" in an ARGUMENT must not hide a service (self markers are token-bound).
        (r"python -m main --log C:\logs\pytest\out.txt", True),
        (r'"C:\Users\x\.venv-pytest\Scripts\python.exe" -m main', True),
        # Non-services and this tool family itself.
        ("python -m maintenance", False),
        (r'python "C:\repo\tools\rw19_preflight.py" --plan x', False),
        (r'python "C:\repo\tools\rw19_autolaunch.py" --config x', False),
        (r'python "C:\repo\tools\rw19_news_reconnect_plan_bound.py" collect', False),
        ("python -m pytest app/tests", False),
        (r"C:\venv\Scripts\pytest.exe -q", False),
        ("explorer.exe", False),
    ],
)
def test_cmdline_service_classifier(cmdline, expected):
    assert preflight_mod._cmdline_is_cockpit_service(cmdline) is expected


def _wmic_list(*records: tuple[str, str, str]) -> str:
    """Real WMIC /FORMAT:LIST framing: every LINE is followed by a blank line
    (``\\r\\r\\n`` after universal-newline translation), fields alphabetical, records
    closed by ProcessId. Verification finding N1: a blank-line split separates
    FIELDS, not records — fixtures must reproduce the real shape."""
    parts = ["\n\n"]
    for cmdline, name, pid in records:
        parts.append(f"CommandLine={cmdline}\n\nName={name}\n\nProcessId={pid}\n\n\n\n")
    return "".join(parts)


def test_windows_scan_refuses_opaque_python_process():
    stdout = _wmic_list(
        ("C:\\Windows\\explorer.exe", "explorer.exe", "555"),
        ("", "python.exe", "444"),
    )
    with pytest.raises(preflight_mod.PreflightError, match="unreadable command line"):
        preflight_mod._scan_windows_processes(stdout)


def test_windows_scan_ignores_opaque_non_python_and_counts_services():
    stdout = _wmic_list(
        ("", "System Idle Process", "0"),
        ('python "C:\\repo\\app\\main.py"', "python.exe", "123"),
        ("explorer.exe", "explorer.exe", "321"),
    )
    assert preflight_mod._scan_windows_processes(stdout) == 1


def test_windows_scan_positive_control_refuses_when_nothing_readable():
    stdout = _wmic_list(("", "svchost.exe", "9"), ("", "System", "4"))
    with pytest.raises(preflight_mod.PreflightError, match="positive control"):
        preflight_mod._scan_windows_processes(stdout)


@pytest.mark.skipif(sys.platform != "win32", reason="WMIC scan is Windows-only")
def test_windows_scan_live_smoke():
    """The real WMIC output must parse: at least this test's own python process pairs
    a name with a readable command line (guards the N1 record-framing regression)."""
    count = preflight_mod.default_count_service_processes()
    assert isinstance(count, int) and count == 0


def test_port_probe_detects_ipv4_listener_and_free_port():
    import socket as socket_mod

    listener = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert preflight_mod.default_count_api_port_listeners("127.0.0.1", port) == 1
    finally:
        listener.close()
    assert preflight_mod.default_count_api_port_listeners("127.0.0.1", port) == 0


def test_port_probe_detects_ipv6_listener_for_v6_and_localhost_hosts():
    import socket as socket_mod

    try:
        listener = socket_mod.socket(socket_mod.AF_INET6, socket_mod.SOCK_STREAM)
    except OSError:
        pytest.skip("host has no IPv6 stack")
    try:
        listener.bind(("::1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        # An IPv6-only loopback listener must be visible for "::1" and "localhost"
        # (adversarial P1-3: an AF_INET-only probe reported it as absent).
        assert preflight_mod.default_count_api_port_listeners("::1", port) == 1
        assert preflight_mod.default_count_api_port_listeners("localhost", port) >= 1
    finally:
        listener.close()


class _FakeModeSession:
    def __init__(self, entry):
        self._entry = entry

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, _stmt):
        entry = self._entry
        return SimpleNamespace(scalar_one_or_none=lambda: entry)


def _fake_mode_state(entry, *, verify_ok=True):
    return SimpleNamespace(
        session_factory=lambda: _FakeModeSession(entry),
        audit=SimpleNamespace(verify_entry=lambda _e: verify_ok),
    )


def test_latest_mode_change_absent_or_read_only_passes():
    assert preflight_mod._latest_mode_change_error(_fake_mode_state(None)) is None
    entry = SimpleNamespace(payload={"to": "read_only"})
    assert preflight_mod._latest_mode_change_error(_fake_mode_state(entry)) is None


def test_latest_mode_change_auto_arm_refuses_even_if_row_collapsed():
    entry = SimpleNamespace(payload={"to": "auto_full"})
    detail = preflight_mod._latest_mode_change_error(_fake_mode_state(entry))
    assert detail is not None and "not read_only" in detail


def test_latest_mode_change_bad_hmac_refuses():
    entry = SimpleNamespace(payload={"to": "read_only"})
    detail = preflight_mod._latest_mode_change_error(
        _fake_mode_state(entry, verify_ok=False)
    )
    assert detail is not None and "HMAC" in detail


def test_no_secret_material_in_output_or_stderr(tmp_path, monkeypatch):
    plan_path, digest, _plan = write_plan(tmp_path)
    output = tmp_path / "out.json"

    def secret_present(name):
        # Probe may *know* a secret exists but must never return or log it.
        assert SECRET_MATERIAL
        return name in {preflight_mod.ALPACA_KEY_NAME, preflight_mod.ALPACA_SECRET_NAME}

    def require_hmac_key():
        return SECRET_MATERIAL.encode("utf-8")

    deps = make_deps(
        tmp_path, secret_present=secret_present, require_hmac_key=require_hmac_key
    )
    monkeypatch.setattr(preflight_mod, "PreflightDependencies", lambda: deps)
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    assert preflight_mod.main([
        "--plan", str(plan_path),
        "--expected-plan-sha256", digest,
        "--output", str(output),
    ]) == 0
    out_bytes = output.read_bytes()
    assert SECRET_MATERIAL.encode() not in out_bytes
    assert SECRET_MATERIAL not in stderr.getvalue()
