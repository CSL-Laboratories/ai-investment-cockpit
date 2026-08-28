"""Hermetic tests for the RW-19 one-shot autolaunch scheduler helper."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "tools" / "rw19_autolaunch.py"
SPEC = importlib.util.spec_from_file_location("rw19_autolaunch_tool", TOOL)
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)

SOURCE_COMMIT = "a" * 40
SCHEDULED = datetime(2026, 8, 27, 16, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def sleeper_for(clock: FakeClock):
    def _sleep(seconds: float) -> None:
        clock.advance(seconds)

    return _sleep


def write_plan(
    path: Path,
    *,
    source_commit: str = SOURCE_COMMIT,
    output_dir: Path | None = None,
    aborted_dir: Path | None = None,
) -> tuple[bytes, str]:
    base = path.parent
    payload = (
        json.dumps(
            {
                "schema_version": 1,
                "package": "RW-19",
                "source_commit": source_commit,
                "output_paths": {
                    "capture_directory": str(output_dir or base / "evidence" / "capture"),
                    "aborted_directory": str(aborted_dir or base / "evidence" / "aborted"),
                    "publication_rule": "isolated write-once",
                },
                "note": "autolaunch-test-plan-stub",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    path.write_bytes(payload)
    return payload, hashlib.sha256(payload).hexdigest()


def make_layout(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "app").mkdir()
    plan_path = tmp_path / "plan.json"
    plan_bytes, plan_sha = write_plan(plan_path)
    python_exe = tmp_path / "python.exe"
    python_exe.write_text("#!/bin/sh\n", encoding="utf-8")
    preflight_tool = repo / "tools" / "rw19_preflight.py"
    preflight_tool.write_text("# stub\n", encoding="utf-8")
    collector_tool = repo / "tools" / "rw19_news_reconnect_plan_bound.py"
    collector_tool.write_text("# stub\n", encoding="utf-8")
    env_file = repo / "app" / ".env"
    env_file.write_text("COCKPIT_MODE=read_only\n", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    output_dir = evidence / "capture"
    aborted_dir = evidence / "aborted"
    cfg = tool.LaunchConfig(
        plan_path=plan_path,
        expected_plan_sha256=plan_sha,
        python_exe=python_exe,
        repo_root=repo,
        preflight_tool=preflight_tool,
        collector_tool=collector_tool,
        output_dir=output_dir,
        aborted_dir=aborted_dir,
        scratch_dir=scratch,
        log_dir=log_dir,
        env_file=env_file,
        halt_path=tmp_path / "halt" / "HALT",
        scheduled_start=SCHEDULED,
        late_cutoff_seconds=240,
    )
    return cfg, plan_bytes, evidence


class FakeRunner:
    """Injected subprocess runner — never executes the real collector or preflight."""

    def __init__(
        self,
        *,
        source_commit: str = SOURCE_COMMIT,
        fetch_exit: int = 0,
        head: str | None = None,
        origin_main: str | None = None,
        porcelain: str = "",
        preflight_exit: int = 0,
        collect_exit: int | None = 0,
        verify_exit: int = 0,
        verify_aborted_exit: int = 0,
        on_collect=None,
        fail_git: bool = False,
        collect_raises_timeout: bool = False,
    ) -> None:
        self.source_commit = source_commit
        self.fetch_exit = fetch_exit
        self.head = head if head is not None else source_commit
        self.origin_main = origin_main if origin_main is not None else source_commit
        self.porcelain = porcelain
        self.preflight_exit = preflight_exit
        self.collect_exit = collect_exit
        self.verify_exit = verify_exit
        self.verify_aborted_exit = verify_aborted_exit
        self.on_collect = on_collect
        self.fail_git = fail_git
        self.collect_raises_timeout = collect_raises_timeout
        self.calls: list[list[str]] = []
        self.env_by_call: list[dict | None] = []

    def collect_commands(self) -> list[list[str]]:
        return [c for c in self.calls if len(c) >= 3 and c[2] == "collect"]

    def __call__(
        self,
        argv,
        *,
        cwd=None,
        env=None,
        timeout=None,
    ) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        self.calls.append(argv)
        self.env_by_call.append(dict(env) if env is not None else None)
        if len(argv) >= 3 and argv[2] == "collect" and self.collect_raises_timeout:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=5400)
        if argv and argv[0] == "git":
            if self.fail_git:
                return subprocess.CompletedProcess(argv, 1, "", "git failed")
            if argv[1:3] == ["fetch", "origin"]:
                return subprocess.CompletedProcess(argv, self.fetch_exit, "", "")
            if argv[1:3] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(argv, 0, self.head + "\n", "")
            if argv[1:3] == ["rev-parse", "origin/main"]:
                return subprocess.CompletedProcess(argv, 0, self.origin_main + "\n", "")
            if argv[1:3] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(argv, 0, self.porcelain, "")
            return subprocess.CompletedProcess(argv, 1, "", "unexpected git")

        if "--output" in argv and "collect" not in argv:
            out = Path(argv[argv.index("--output") + 1])
            if self.preflight_exit == 0:
                out.write_text('{"ok":true}\n', encoding="utf-8")
            return subprocess.CompletedProcess(argv, self.preflight_exit, "", "")

        if len(argv) >= 3 and argv[2] == "collect":
            assert self.collect_exit is not None
            if self.on_collect is not None:
                self.on_collect(argv, cwd=cwd, env=env)
            return subprocess.CompletedProcess(argv, self.collect_exit, "", "")

        if len(argv) >= 3 and argv[2] == "verify":
            return subprocess.CompletedProcess(argv, self.verify_exit, "{}", "")

        if len(argv) >= 3 and argv[2] == "verify-aborted":
            return subprocess.CompletedProcess(argv, self.verify_aborted_exit, "{}", "")

        return subprocess.CompletedProcess(argv, 1, "", "unexpected command")


def read_log(cfg: tool.LaunchConfig) -> str:
    logs = sorted(cfg.log_dir.glob("rw19_autolaunch_*.log"))
    assert logs, "expected a launcher log file"
    return logs[-1].read_text(encoding="utf-8")


def test_hash_mismatch_refusal(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    cfg = tool.LaunchConfig(**{**cfg.__dict__, "expected_plan_sha256": "0" * 64})
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner()
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert runner.collect_commands() == []
    assert "plan file hash differs" in read_log(cfg)
    assert "terminal_state=LAUNCHER_REFUSED" in read_log(cfg)


def test_relative_path_refusal(tmp_path: Path):
    cfg_path = tmp_path / "cfg.json"
    plan_path = tmp_path / "plan.json"
    _payload, plan_sha = write_plan(plan_path)
    cfg_path.write_text(
        json.dumps(
            {
                "plan_path": "relative/plan.json",
                "expected_plan_sha256": plan_sha,
                "python_exe": str(tmp_path / "python.exe"),
                "repo_root": str(tmp_path / "repo"),
                "preflight_tool": str(tmp_path / "pre.py"),
                "collector_tool": str(tmp_path / "col.py"),
                "output_dir": str(tmp_path / "out"),
                "aborted_dir": str(tmp_path / "aborted"),
                "scratch_dir": str(tmp_path / "scratch"),
                "log_dir": str(tmp_path / "logs"),
                "env_file": str(tmp_path / "app.env"),
                "halt_path": str(tmp_path / "HALT"),
                "scheduled_start": SCHEDULED.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(tool.AutolaunchError, match="absolute"):
        tool.load_config(cfg_path)


def test_existing_output_dir_refusal(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    cfg.output_dir.mkdir()
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner()
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert runner.collect_commands() == []
    assert "output_dir must not already exist" in read_log(cfg)


def test_config_dir_differs_from_plan_refuses(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    _plan_bytes, plan_sha = write_plan(
        cfg.plan_path,
        output_dir=tmp_path / "elsewhere" / "capture",
        aborted_dir=cfg.aborted_dir,
    )
    cfg = tool.LaunchConfig(**{**cfg.__dict__, "expected_plan_sha256": plan_sha})
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner()
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert runner.collect_commands() == []
    assert "config output_dir differs from the plan capture_directory" in read_log(cfg)


def test_late_arrival_refuses_without_collector(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    clock = FakeClock(SCHEDULED + timedelta(seconds=241))
    runner = FakeRunner()
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert runner.collect_commands() == []
    log = read_log(cfg)
    assert "past scheduled_start+late_cutoff" in log
    assert "terminal_state=LAUNCHER_REFUSED" in log


def test_fetch_failure_refuses_pre_collector(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(fetch_exit=1)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert runner.collect_commands() == []
    assert "git fetch origin failed" in read_log(cfg)


def test_source_mismatch_refuses_pre_collector(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(head="b" * 40, origin_main="b" * 40)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert runner.collect_commands() == []
    assert "HEAD/origin/main must equal" in read_log(cfg)


def test_dirty_tree_refuses_pre_collector(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(porcelain=" M tools/rw19_autolaunch.py\n")
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert runner.collect_commands() == []
    assert "clean worktree" in read_log(cfg)


def test_preflight_nonzero_refuses(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(preflight_exit=7)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert runner.collect_commands() == []
    assert "preflight generator exited 7" in read_log(cfg)


def test_outcome_complete_verified(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)

    def on_collect(_argv, **_kwargs):
        cfg.output_dir.mkdir()
        (cfg.output_dir / "marker").write_text("ok\n", encoding="utf-8")

    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=0, on_collect=on_collect)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 0
    assert len(runner.collect_commands()) == 1
    assert "terminal_state=COMPLETE_VERIFIED" in read_log(cfg)
    assert any(c[2] == "verify" for c in runner.calls if len(c) >= 3)


def test_outcome_tombstone_verified(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)

    def on_collect(_argv, **_kwargs):
        cfg.aborted_dir.mkdir()
        (cfg.aborted_dir / "marker").write_text("tombstone\n", encoding="utf-8")

    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=3, on_collect=on_collect)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 0
    assert len(runner.collect_commands()) == 1
    assert "terminal_state=TOMBSTONE_VERIFIED" in read_log(cfg)
    assert any(c[2] == "verify-aborted" for c in runner.calls if len(c) >= 3)


def test_outcome_refused_prestart(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=2)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 0
    assert len(runner.collect_commands()) == 1
    assert "terminal_state=REFUSED_PRESTART" in read_log(cfg)


def test_outcome_recovery_sibling_integrity_error(tmp_path: Path):
    cfg, _plan_bytes, evidence = make_layout(tmp_path)
    (evidence / f"{cfg.aborted_dir.name}.recovery.attempt1").mkdir()

    def on_collect(_argv, **_kwargs):
        cfg.output_dir.mkdir()

    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=0, on_collect=on_collect)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert len(runner.collect_commands()) == 1
    assert "terminal_state=INTEGRITY_ERROR" in read_log(cfg)
    assert "manual inspection" in read_log(cfg)


def test_outcome_exit0_missing_dir_integrity_error(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=0)  # no on_collect → output_dir absent
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert len(runner.collect_commands()) == 1
    assert "terminal_state=INTEGRITY_ERROR" in read_log(cfg)


def test_outcome_exit3_both_dirs_integrity_error(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)

    def on_collect(_argv, **_kwargs):
        cfg.output_dir.mkdir()
        cfg.aborted_dir.mkdir()

    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=3, on_collect=on_collect)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert len(runner.collect_commands()) == 1
    assert "terminal_state=INTEGRITY_ERROR" in read_log(cfg)


def test_single_invocation_across_paths(tmp_path: Path):
    """Across failure and success paths, at most one collect command is recorded."""
    cases = []

    # late
    cfg, _, _ = make_layout(tmp_path / "late")
    clock = FakeClock(SCHEDULED + timedelta(seconds=300))
    runner = FakeRunner()
    tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    cases.append(runner.collect_commands())

    # fetch fail
    cfg, _, _ = make_layout(tmp_path / "fetch")
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(fetch_exit=1)
    tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    cases.append(runner.collect_commands())

    # success then verify fail — still only one collect
    cfg, _, _ = make_layout(tmp_path / "verify_fail")

    def on_collect(_argv, **_kwargs):
        cfg.output_dir.mkdir()

    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=0, verify_exit=9, on_collect=on_collect)
    tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    cases.append(runner.collect_commands())

    # unexpected exit — one collect, no second
    cfg, _, _ = make_layout(tmp_path / "weird")
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=99)
    tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    cases.append(runner.collect_commands())

    for collects in cases:
        assert len(collects) <= 1
    assert len(cases[2]) == 1
    assert len(cases[3]) == 1


def test_rehearsal_never_collects_and_builder_unreachable(tmp_path: Path, monkeypatch):
    cfg, plan_bytes, _evidence = make_layout(tmp_path)
    clock = FakeClock(SCHEDULED - timedelta(days=2))  # far from window; still OK
    runner = FakeRunner()

    def boom(*_a, **_k):
        raise AssertionError("collect argv builder must not run in rehearsal")

    monkeypatch.setattr(tool.run_live, "build_collect_argv", boom, raising=False)

    def live_guard(*_a, **_k):
        raise AssertionError("run_live must not be entered in rehearsal")

    monkeypatch.setattr(tool, "run_live", live_guard)

    rc = tool.run(
        cfg,
        rehearsal=True,
        clock=clock,
        sleeper=sleeper_for(clock),
        runner=runner,
    )
    assert rc == 0
    assert runner.collect_commands() == []
    log = read_log(cfg)
    assert "mode: REHEARSAL" in log
    assert "terminal_state=REHEARSAL_OK" in log
    assert "window_facts:" in log
    assert "tolerance logic skipped" in log
    # Log must not contain raw plan-file bytes.
    assert plan_bytes.decode("utf-8") not in log
    assert '"note":"autolaunch-test-plan-stub"' not in log


def test_rehearsal_structural_containment_ast():
    source = TOOL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    live_fn = None
    rehearsal_fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "run_live":
            live_fn = node
        if isinstance(node, ast.FunctionDef) and node.name == "run_rehearsal":
            rehearsal_fn = node
    assert live_fn is not None
    assert rehearsal_fn is not None

    nested_builders = [
        n
        for n in ast.walk(live_fn)
        if isinstance(n, ast.FunctionDef) and n.name == "build_collect_argv"
    ]
    assert len(nested_builders) == 1

    rehearsal_names = {
        n.id for n in ast.walk(rehearsal_fn) if isinstance(n, ast.Name)
    }
    assert "build_collect_argv" not in rehearsal_names
    # Rehearsal must not construct a collect subcommand literal in its body.
    rehearsal_consts = {
        n.value
        for n in ast.walk(rehearsal_fn)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert "collect" not in rehearsal_consts


def test_collect_timeout_is_integrity_error_with_no_retry(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_raises_timeout=True)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert len(runner.collect_commands()) == 1
    log = read_log(cfg)
    assert "exceeded the launcher timeout" in log
    assert "terminal_state=INTEGRITY_ERROR" in log


def test_verify_failure_is_not_reported_as_success(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)

    def on_collect(_argv, **_kwargs):
        cfg.output_dir.mkdir()

    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=0, verify_exit=9, on_collect=on_collect)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert "terminal_state=INTEGRITY_ERROR" in read_log(cfg)


def test_verify_aborted_failure_is_not_reported_as_success(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)

    def on_collect(_argv, **_kwargs):
        cfg.aborted_dir.mkdir()

    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=3, verify_aborted_exit=5, on_collect=on_collect)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert "terminal_state=INTEGRITY_ERROR" in read_log(cfg)


def test_slept_through_window_refuses_at_t0_without_collect(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))

    def oversleeper(seconds: float) -> None:
        # The machine sleeps through the hold and wakes far past the cutoff.
        clock.advance(seconds + 600)

    runner = FakeRunner()
    rc = tool.run(cfg, clock=clock, sleeper=oversleeper, runner=runner)
    assert rc == 1
    assert runner.collect_commands() == []
    log = read_log(cfg)
    assert "past scheduled_start+late_cutoff" in log
    assert "terminal_state=LAUNCHER_REFUSED" in log


def test_evidence_dir_appearing_during_hold_refuses(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))

    def racing_sleeper(seconds: float) -> None:
        clock.advance(seconds)
        if not cfg.output_dir.exists():
            cfg.output_dir.mkdir()

    runner = FakeRunner()
    rc = tool.run(cfg, clock=clock, sleeper=racing_sleeper, runner=runner)
    assert rc == 1
    assert runner.collect_commands() == []
    assert "evidence dirs appeared before collector invocation" in read_log(cfg)


def test_exit2_with_evidence_dir_is_integrity_error(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)

    def on_collect(_argv, **_kwargs):
        cfg.aborted_dir.mkdir()

    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=2, on_collect=on_collect)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert "terminal_state=INTEGRITY_ERROR" in read_log(cfg)


def test_collector_staging_residue_is_integrity_error(tmp_path: Path):
    cfg, _plan_bytes, evidence = make_layout(tmp_path)

    def on_collect(_argv, **_kwargs):
        # A locked file / failed recovery rename leaves the staging dir behind while
        # the collector exits 2 with neither planned dir present.
        (evidence / ".rw19-news-reconnect.k3j2h1").mkdir()

    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=2, on_collect=on_collect)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    log = read_log(cfg)
    assert "terminal_state=INTEGRITY_ERROR" in log
    assert "REFUSED_PRESTART" not in log


def test_cross_process_window_lock_refuses_second_launcher(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    lock = cfg.scratch_dir / f".rw19_autolaunch_live_{cfg.expected_plan_sha256[:16]}.lock"
    lock.write_text("", encoding="utf-8")
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner()
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert runner.collect_commands() == []
    assert "already claimed this window" in read_log(cfg)


def test_rehearsal_and_live_locks_are_independent(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    clock = FakeClock(SCHEDULED - timedelta(days=1))
    rc = tool.run(
        cfg, rehearsal=True, clock=clock, sleeper=sleeper_for(clock), runner=FakeRunner()
    )
    assert rc == 0
    # The rehearsal lock must not consume the live window's lock.
    def on_collect(_argv, **_kwargs):
        cfg.output_dir.mkdir()

    clock2 = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=0, on_collect=on_collect)
    rc2 = tool.run(cfg, clock=clock2, sleeper=sleeper_for(clock2), runner=runner)
    assert rc2 == 0
    assert len(runner.collect_commands()) == 1


def test_tool_outside_repo_root_refuses(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)
    outside = tmp_path / "outside_preflight.py"
    outside.write_text("# stub\n", encoding="utf-8")
    cfg = tool.LaunchConfig(**{**cfg.__dict__, "preflight_tool": outside})
    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner()
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 1
    assert runner.collect_commands() == []
    assert "preflight_tool is not inside repo_root" in read_log(cfg)


def test_preflight_and_collect_get_pinned_cockpit_environment(tmp_path: Path):
    cfg, _plan_bytes, _evidence = make_layout(tmp_path)

    def on_collect(_argv, **_kwargs):
        cfg.output_dir.mkdir()

    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=0, on_collect=on_collect)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 0
    pinned = [
        env
        for argv, env in zip(runner.calls, runner.env_by_call)
        if env is not None and (("--output" in argv) or ("collect" in argv))
    ]
    assert len(pinned) >= 2  # the preflight generator and the collector
    for env in pinned:
        assert env["COCKPIT_ENV_FILE"] == str(cfg.env_file)
        assert env["COCKPIT_HALT_PATH"] == str(cfg.halt_path)
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_log_contains_steps_and_no_plan_bytes(tmp_path: Path):
    cfg, plan_bytes, _evidence = make_layout(tmp_path)

    def on_collect(argv, *, cwd, env):
        assert env.get("PYTHONDONTWRITEBYTECODE") == "1"
        assert cwd == str(cfg.repo_root)
        assert "collect" in argv
        cfg.output_dir.mkdir()

    clock = FakeClock(SCHEDULED - timedelta(minutes=15))
    runner = FakeRunner(collect_exit=0, on_collect=on_collect)
    rc = tool.run(cfg, clock=clock, sleeper=sleeper_for(clock), runner=runner)
    assert rc == 0
    log = read_log(cfg)
    for fragment in (
        "bootstrap: begin",
        "bootstrap: complete",
        "source: git fetch origin",
        "preflight: complete",
        "collect: invoking collector exactly once",
        "terminal_state=COMPLETE_VERIFIED",
    ):
        assert fragment in log
    assert plan_bytes.decode("utf-8") not in log
