"""One-shot, no-retry, fail-closed RW-19 plan-bound news collector autolaunch.

Invoked by Windows Task Scheduler at T-15 minutes. Performs bootstrap, a bounded
source refresh at T-2, a fresh safety preflight, then exactly one collector
invocation at the planned UTC start. Never retries. Never touches credentials.

Rehearsal mode (--rehearsal) validates bootstrap, source state, and preflight
generation only. The live collector argv builder is defined and called exclusively
inside the live-path function so rehearsal cannot construct a collect command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

FETCH_TIMEOUT_SECONDS = 60
DEFAULT_LATE_CUTOFF_SECONDS = 240
# Collector wall cap is 4200s plus REST/verify slack; the scheduler task limit (2h)
# sits above this so the launcher, not the scheduler, reports a hang.
COLLECT_TIMEOUT_SECONDS = 5400
VERIFY_TIMEOUT_SECONDS = 600
T_MINUS_2 = timedelta(minutes=2)

TERMINAL_COMPLETE = "COMPLETE_VERIFIED"
TERMINAL_TOMBSTONE = "TOMBSTONE_VERIFIED"
TERMINAL_REFUSED_PRESTART = "REFUSED_PRESTART"
TERMINAL_INTEGRITY = "INTEGRITY_ERROR"
TERMINAL_LAUNCHER_REFUSED = "LAUNCHER_REFUSED"


class AutolaunchError(Exception):
    """Fail-closed launcher refusal or integrity failure."""

    def __init__(self, message: str, *, terminal: str = TERMINAL_LAUNCHER_REFUSED) -> None:
        super().__init__(message)
        self.terminal = terminal


RunnerResult = subprocess.CompletedProcess[str]
Runner = Callable[..., RunnerResult]
Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


def _fs_exists(path: Path) -> bool:
    return Path(path).exists()


def _fs_is_file(path: Path) -> bool:
    return Path(path).is_file()


def _fs_is_dir(path: Path) -> bool:
    return Path(path).is_dir()


def _fs_is_absolute(path: Path) -> bool:
    return Path(path).is_absolute()


def _fs_read_bytes(path: Path) -> bytes:
    return Path(path).read_bytes()


def _fs_write_text(path: Path, text: str) -> int:
    return Path(path).write_text(text, encoding="utf-8")


def _fs_mkdir(path: Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def _fs_iterdir(path: Path) -> list[Path]:
    return list(Path(path).iterdir())


def _fs_resolve(path: Path) -> Path:
    return Path(path).resolve()


@dataclass(frozen=True)
class FsProbes:
    """Injectable filesystem probes (production defaults use Path methods)."""

    exists: Callable[[Path], bool] = _fs_exists
    is_file: Callable[[Path], bool] = _fs_is_file
    is_dir: Callable[[Path], bool] = _fs_is_dir
    is_absolute: Callable[[Path], bool] = _fs_is_absolute
    read_bytes: Callable[[Path], bytes] = _fs_read_bytes
    write_text: Callable[[Path, str], int] = _fs_write_text
    mkdir: Callable[[Path], None] = _fs_mkdir
    iterdir: Callable[[Path], list[Path]] = _fs_iterdir
    resolve: Callable[[Path], Path] = _fs_resolve


@dataclass(frozen=True)
class LaunchConfig:
    plan_path: Path
    expected_plan_sha256: str
    python_exe: Path
    repo_root: Path
    preflight_tool: Path
    collector_tool: Path
    output_dir: Path
    aborted_dir: Path
    scratch_dir: Path
    log_dir: Path
    env_file: Path
    halt_path: Path
    scheduled_start: datetime
    late_cutoff_seconds: int


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _default_runner(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> RunnerResult:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        timeout=timeout,
        capture_output=True,
        text=True,
        check=False,
    )


def _parse_utc(value: str, label: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AutolaunchError(f"{label} is not RFC3339 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise AutolaunchError(f"{label} must be timezone-qualified UTC")
    return parsed.astimezone(UTC)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_absolute(path: Path, label: str, fs: FsProbes) -> Path:
    if not fs.is_absolute(path):
        raise AutolaunchError(f"{label} must be an absolute path")
    return path


def load_config(path: Path, *, fs: FsProbes | None = None) -> LaunchConfig:
    probes = fs or FsProbes()
    raw = json.loads(probes.read_bytes(path).decode("utf-8"))
    if not isinstance(raw, dict):
        raise AutolaunchError("config root must be a JSON object")
    required = {
        "plan_path",
        "expected_plan_sha256",
        "python_exe",
        "repo_root",
        "preflight_tool",
        "collector_tool",
        "output_dir",
        "aborted_dir",
        "scratch_dir",
        "log_dir",
        "env_file",
        "halt_path",
        "scheduled_start",
    }
    missing = sorted(required - set(raw))
    if missing:
        raise AutolaunchError(f"config missing keys: {', '.join(missing)}")
    late = raw.get("late_cutoff_seconds", DEFAULT_LATE_CUTOFF_SECONDS)
    if not isinstance(late, int) or isinstance(late, bool) or late < 0:
        raise AutolaunchError("late_cutoff_seconds must be a non-negative integer")
    expected = raw["expected_plan_sha256"]
    if not isinstance(expected, str) or len(expected) != 64 or any(
        ch not in "0123456789abcdef" for ch in expected
    ):
        raise AutolaunchError("expected_plan_sha256 must be lowercase SHA-256")

    def abs_path(key: str) -> Path:
        value = raw[key]
        if not isinstance(value, str) or not value:
            raise AutolaunchError(f"{key} must be a non-empty string path")
        return _require_absolute(Path(value), key, probes)

    return LaunchConfig(
        plan_path=abs_path("plan_path"),
        expected_plan_sha256=expected,
        python_exe=abs_path("python_exe"),
        repo_root=abs_path("repo_root"),
        preflight_tool=abs_path("preflight_tool"),
        collector_tool=abs_path("collector_tool"),
        output_dir=abs_path("output_dir"),
        aborted_dir=abs_path("aborted_dir"),
        scratch_dir=abs_path("scratch_dir"),
        log_dir=abs_path("log_dir"),
        env_file=abs_path("env_file"),
        halt_path=abs_path("halt_path"),
        scheduled_start=_parse_utc(str(raw["scheduled_start"]), "scheduled_start"),
        late_cutoff_seconds=late,
    )


class LaunchLog:
    def __init__(self, path: Path, *, fs: FsProbes, clock: Clock) -> None:
        self.path = path
        self._fs = fs
        self._clock = clock
        self.lines: list[str] = []
        self._fs.write_text(path, "")

    def write(self, message: str) -> None:
        stamp = self._clock().astimezone(UTC).isoformat()
        line = f"{stamp} {message}"
        self.lines.append(line)
        existing = ""
        if self._fs.exists(self.path):
            existing = self._fs.read_bytes(self.path).decode("utf-8")
        self._fs.write_text(self.path, existing + line + "\n")


def _plan_bindings(plan_bytes: bytes) -> tuple[str, Path, Path]:
    """The hash-verified plan's source_commit and output directories."""
    try:
        plan = json.loads(plan_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutolaunchError("plan file is not valid UTF-8 JSON") from exc
    if not isinstance(plan, dict):
        raise AutolaunchError("plan root must be a JSON object")
    commit = plan.get("source_commit")
    if not isinstance(commit, str) or len(commit) != 40 or any(
        ch not in "0123456789abcdef" for ch in commit
    ):
        raise AutolaunchError("plan source_commit is not a canonical lowercase SHA-1")
    output_paths = plan.get("output_paths")
    if not isinstance(output_paths, dict):
        raise AutolaunchError("plan output_paths must be an object")
    capture = output_paths.get("capture_directory")
    aborted = output_paths.get("aborted_directory")
    if not isinstance(capture, str) or not isinstance(aborted, str):
        raise AutolaunchError("plan output_paths directories must be strings")
    return commit, Path(capture), Path(aborted)


def _open_log(
    cfg: LaunchConfig,
    *,
    fs: FsProbes,
    clock: Clock,
) -> LaunchLog:
    if not fs.exists(cfg.log_dir):
        fs.mkdir(cfg.log_dir)
    if not fs.is_dir(cfg.log_dir):
        raise AutolaunchError("log_dir is not a writable directory")
    stamp = clock().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = cfg.log_dir / f"rw19_autolaunch_{stamp}.log"
    # Prove writability before proceeding.
    probe = cfg.log_dir / f".rw19_autolaunch_write_probe_{stamp}"
    fs.write_text(probe, "ok\n")
    return LaunchLog(path, fs=fs, clock=clock)


def bootstrap(
    cfg: LaunchConfig,
    log: LaunchLog,
    *,
    fs: FsProbes,
    mode: str,
) -> str:
    """Validate config paths, plan hash, and absent evidence dirs. Returns source_commit."""
    log.write("bootstrap: begin")
    path_labels = (
        ("plan_path", cfg.plan_path, True),
        ("python_exe", cfg.python_exe, True),
        ("repo_root", cfg.repo_root, True),
        ("preflight_tool", cfg.preflight_tool, True),
        ("collector_tool", cfg.collector_tool, True),
        ("scratch_dir", cfg.scratch_dir, True),
        ("log_dir", cfg.log_dir, True),
        ("env_file", cfg.env_file, True),
    )
    for label, path, must_exist in path_labels:
        _require_absolute(path, label, fs)
        if must_exist and not fs.exists(path):
            raise AutolaunchError(f"{label} does not exist: {path}")
        log.write(f"bootstrap: path ok {label}")

    if not fs.is_file(cfg.plan_path):
        raise AutolaunchError("plan_path is not a file")
    if not fs.is_file(cfg.python_exe):
        raise AutolaunchError("python_exe is not a file")
    if not fs.is_dir(cfg.repo_root):
        raise AutolaunchError("repo_root is not a directory")
    if not fs.is_file(cfg.preflight_tool):
        raise AutolaunchError("preflight_tool is not a file")
    if not fs.is_file(cfg.collector_tool):
        raise AutolaunchError("collector_tool is not a file")
    if not fs.is_dir(cfg.scratch_dir):
        raise AutolaunchError("scratch_dir is not a directory")
    if not fs.is_file(cfg.env_file):
        raise AutolaunchError("env_file is not a file")
    # halt_path names the sentinel LOCATION; its absence is the normal safe state.

    # The verified-clean-and-pinned git state must describe the tree that executes:
    # both tools are required to live inside repo_root (adversarial P2-6).
    repo_resolved = fs.resolve(cfg.repo_root)
    for label, path in (
        ("preflight_tool", cfg.preflight_tool),
        ("collector_tool", cfg.collector_tool),
    ):
        try:
            fs.resolve(path).relative_to(repo_resolved)
        except ValueError:
            raise AutolaunchError(f"{label} is not inside repo_root") from None

    for label, path in (("output_dir", cfg.output_dir), ("aborted_dir", cfg.aborted_dir)):
        _require_absolute(path, label, fs)
        if fs.exists(path):
            raise AutolaunchError(f"{label} must not already exist")
        log.write(f"bootstrap: absent ok {label}")

    plan_bytes = fs.read_bytes(cfg.plan_path)
    digest = _sha256(plan_bytes)
    if digest != cfg.expected_plan_sha256:
        raise AutolaunchError("plan file hash differs from expected_plan_sha256")
    log.write("bootstrap: plan hash matched expected_plan_sha256")
    source_commit, plan_capture, plan_aborted = _plan_bindings(plan_bytes)
    # The config must point at exactly the sealed plan's write-once directories;
    # a mismatch would either mis-aim the capture or make the outcome machine
    # look for a tombstone in the wrong place.
    if fs.resolve(cfg.output_dir) != fs.resolve(plan_capture):
        raise AutolaunchError("config output_dir differs from the plan capture_directory")
    if fs.resolve(cfg.aborted_dir) != fs.resolve(plan_aborted):
        raise AutolaunchError("config aborted_dir differs from the plan aborted_directory")
    log.write("bootstrap: config output dirs match the plan output_paths")
    log.write(f"bootstrap: plan source_commit={source_commit}")

    # One launcher per window per mode, across PROCESSES (adversarial P2-3): the
    # scheduler's IgnoreNew covers only task-triggered starts. O_EXCL is atomic.
    # Claimed LAST (verification finding N3) so an ordinary refusal above does not
    # burn the window's lock; the lock deliberately persists for the one-shot window,
    # and the refusal message names the file an operator would have to remove.
    lock_path = cfg.scratch_dir / (
        f".rw19_autolaunch_{mode}_{cfg.expected_plan_sha256[:16]}.lock"
    )
    try:
        handle = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise AutolaunchError(
            f"another {mode} launcher already claimed this window "
            f"(lock exists: {lock_path})"
        ) from None
    os.write(handle, f"pid={os.getpid()}\n".encode("utf-8"))
    os.close(handle)
    log.write(f"bootstrap: claimed {mode} window lock {lock_path.name}")
    log.write("bootstrap: complete")
    return source_commit


def _refuse_if_late(
    cfg: LaunchConfig,
    log: LaunchLog,
    *,
    clock: Clock,
    stage: str,
) -> None:
    now = clock()
    deadline = cfg.scheduled_start + timedelta(seconds=cfg.late_cutoff_seconds)
    delta = (now - cfg.scheduled_start).total_seconds()
    log.write(
        f"timing: stage={stage} now={now.isoformat()} "
        f"scheduled_start={cfg.scheduled_start.isoformat()} "
        f"delta_seconds={delta:.3f} late_cutoff_seconds={cfg.late_cutoff_seconds}"
    )
    if now > deadline:
        raise AutolaunchError(
            f"clock past scheduled_start+late_cutoff at {stage}; refusing without collector"
        )


def _sleep_until(
    target: datetime,
    log: LaunchLog,
    *,
    clock: Clock,
    sleeper: Sleeper,
    label: str,
) -> None:
    now = clock()
    remaining = (target - now).total_seconds()
    log.write(
        f"hold: waiting for {label} target={target.isoformat()} "
        f"remaining_seconds={remaining:.3f}"
    )
    if remaining > 0:
        sleeper(remaining)
        log.write(f"hold: reached {label}")
    else:
        log.write(f"hold: already at/past {label}")


def _git(
    runner: Runner,
    repo_root: Path,
    *args: str,
    timeout: float | None = None,
) -> RunnerResult:
    return runner(["git", *args], cwd=str(repo_root), timeout=timeout)


def verify_source_state(
    cfg: LaunchConfig,
    source_commit: str,
    log: LaunchLog,
    *,
    runner: Runner,
    fetch: bool,
) -> dict[str, str]:
    if fetch:
        log.write("source: git fetch origin (bounded)")
        fetched = _git(runner, cfg.repo_root, "fetch", "origin", timeout=FETCH_TIMEOUT_SECONDS)
        if fetched.returncode != 0:
            raise AutolaunchError(
                f"git fetch origin failed with exit {fetched.returncode}"
            )
        log.write("source: fetch complete")

    head = _git(runner, cfg.repo_root, "rev-parse", "HEAD")
    origin = _git(runner, cfg.repo_root, "rev-parse", "origin/main")
    porcelain = _git(runner, cfg.repo_root, "status", "--porcelain")
    if head.returncode != 0 or origin.returncode != 0 or porcelain.returncode != 0:
        raise AutolaunchError("git source-state probes failed")
    head_sha = head.stdout.strip()
    origin_sha = origin.stdout.strip()
    dirty = bool(porcelain.stdout.strip())
    log.write(
        f"source: HEAD={head_sha} origin/main={origin_sha} dirty={dirty} "
        f"expected={source_commit}"
    )
    if head_sha != source_commit or origin_sha != source_commit or dirty:
        raise AutolaunchError(
            "HEAD/origin/main must equal plan source_commit with a clean worktree"
        )
    log.write("source: verified clean and pinned")
    return {"head": head_sha, "origin_main": origin_sha, "worktree_clean": "true"}


def _pinned_env(cfg: LaunchConfig) -> dict[str, str]:
    """Subprocess environment with the cockpit's config/HALT locations pinned.

    Settings and the HALT sentinel resolve from the child's environment, not the
    cockpit's (adversarial P1-4); pin both so the preflight probes the same .env and
    HALT path the cockpit service uses, never a silent default.
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["COCKPIT_ENV_FILE"] = str(cfg.env_file)
    env["COCKPIT_HALT_PATH"] = str(cfg.halt_path)
    return env


def run_preflight(
    cfg: LaunchConfig,
    log: LaunchLog,
    *,
    runner: Runner,
    clock: Clock,
    fs: FsProbes,
) -> Path:
    stamp = clock().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = cfg.scratch_dir / f"rw19_safety_preflight_{stamp}.json"
    if fs.exists(output):
        raise AutolaunchError("preflight output path already exists")
    argv = [
        str(cfg.python_exe),
        str(cfg.preflight_tool),
        "--plan",
        str(cfg.plan_path),
        "--expected-plan-sha256",
        cfg.expected_plan_sha256,
        "--output",
        str(output),
    ]
    log.write(f"preflight: invoking generator output={output.name}")
    result = runner(argv, cwd=str(cfg.repo_root), env=_pinned_env(cfg), timeout=120)
    if result.returncode != 0:
        raise AutolaunchError(f"preflight generator exited {result.returncode}")
    if not fs.exists(output):
        raise AutolaunchError("preflight generator reported success but output is missing")
    log.write("preflight: complete")
    return output


def _recovery_siblings(cfg: LaunchConfig, fs: FsProbes) -> list[str]:
    parents = {cfg.output_dir.parent, cfg.aborted_dir.parent}
    found: list[str] = []
    for parent in parents:
        if not fs.exists(parent) or not fs.is_dir(parent):
            continue
        for entry in fs.iterdir(parent):
            name = entry.name
            # Also match the collector's staging prefix (adversarial P2-2): a locked
            # file or failed recovery rename leaves ".rw19-news-reconnect.*" residue,
            # and mislabeling that as a clean refusal would hide a partial capture.
            if ".recovery." in name or name.startswith(".rw19-news-reconnect."):
                found.append(name)
    return sorted(found)


def _dirs_present(cfg: LaunchConfig, fs: FsProbes) -> tuple[bool, bool]:
    return fs.exists(cfg.output_dir), fs.exists(cfg.aborted_dir)


def evaluate_collector_outcome(
    cfg: LaunchConfig,
    collect_exit: int,
    log: LaunchLog,
    *,
    runner: Runner,
    fs: FsProbes,
) -> tuple[str, int]:
    """Map collector exit + directory layout to a terminal state. Never retries collect."""
    output_present, aborted_present = _dirs_present(cfg, fs)
    recovery = _recovery_siblings(cfg, fs)
    log.write(
        f"outcome: collect_exit={collect_exit} output_dir={output_present} "
        f"aborted_dir={aborted_present} recovery_siblings={recovery}"
    )
    if recovery:
        log.write("INTEGRITY_ERROR: .recovery.* sibling present; manual inspection required")
        return TERMINAL_INTEGRITY, 1

    if collect_exit == 0:
        if not output_present or aborted_present:
            log.write("INTEGRITY_ERROR: exit 0 requires output_dir present and aborted_dir absent")
            return TERMINAL_INTEGRITY, 1
        verify_argv = [
            str(cfg.python_exe),
            str(cfg.collector_tool),
            "verify",
            "--package-dir",
            str(cfg.output_dir),
            "--expected-plan-sha256",
            cfg.expected_plan_sha256,
        ]
        log.write("outcome: running verify on output_dir")
        verified = runner(
            verify_argv,
            cwd=str(cfg.repo_root),
            env=_pinned_env(cfg),
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
        log.write(f"outcome: verify_exit={verified.returncode}")
        if verified.returncode != 0:
            return TERMINAL_INTEGRITY, 1
        return TERMINAL_COMPLETE, 0

    if collect_exit == 3:
        if not aborted_present or output_present:
            log.write("INTEGRITY_ERROR: exit 3 requires aborted_dir present and output_dir absent")
            return TERMINAL_INTEGRITY, 1
        verify_argv = [
            str(cfg.python_exe),
            str(cfg.collector_tool),
            "verify-aborted",
            "--package-dir",
            str(cfg.aborted_dir),
            "--expected-plan-sha256",
            cfg.expected_plan_sha256,
        ]
        log.write("outcome: running verify-aborted on aborted_dir")
        verified = runner(
            verify_argv,
            cwd=str(cfg.repo_root),
            env=_pinned_env(cfg),
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
        log.write(f"outcome: verify_aborted_exit={verified.returncode}")
        if verified.returncode != 0:
            return TERMINAL_INTEGRITY, 1
        return TERMINAL_TOMBSTONE, 0

    if collect_exit == 2:
        if output_present or aborted_present:
            log.write("INTEGRITY_ERROR: exit 2 requires neither planned evidence dir")
            return TERMINAL_INTEGRITY, 1
        log.write("outcome: pre-start refusal recorded")
        return TERMINAL_REFUSED_PRESTART, 0

    log.write(f"INTEGRITY_ERROR: unexpected collector exit {collect_exit}")
    return TERMINAL_INTEGRITY, 1


def run_rehearsal(
    cfg: LaunchConfig,
    *,
    clock: Clock,
    sleeper: Sleeper,
    runner: Runner,
    fs: FsProbes,
) -> int:
    """Bootstrap + source verify + preflight only.

    Structurally isolated from collector argv construction: this function must not
    reference ``build_collect_argv`` (AST-enforced). ``run()`` never enters
    ``run_live`` when ``rehearsal=True``, and ``build_collect_argv`` is defined
    only inside ``run_live``.
    """
    del sleeper  # rehearsal never holds to the window
    log: LaunchLog | None = None
    terminal = TERMINAL_LAUNCHER_REFUSED
    started = clock()
    try:
        log = _open_log(cfg, fs=fs, clock=clock)
        log.write("mode: REHEARSAL")
        source_commit = bootstrap(cfg, log, fs=fs, mode="rehearsal")
        now = clock()
        delta = (cfg.scheduled_start - now).total_seconds()
        log.write(
            f"window_facts: scheduled_start={cfg.scheduled_start.isoformat()} "
            f"late_cutoff_seconds={cfg.late_cutoff_seconds} "
            f"delta_to_start_seconds={delta:.3f} "
            f"(tolerance logic skipped in rehearsal)"
        )
        verify_source_state(cfg, source_commit, log, runner=runner, fetch=True)
        run_preflight(cfg, log, runner=runner, clock=clock, fs=fs)
        terminal = "REHEARSAL_OK"
        log.write(
            f"terminal_state={terminal} started_at={started.isoformat()} "
            f"finished_at={clock().isoformat()}"
        )
        return 0
    except AutolaunchError as exc:
        terminal = exc.terminal
        if log is not None:
            log.write(f"refused: {exc}")
            log.write(
                f"terminal_state={terminal} started_at={started.isoformat()} "
                f"finished_at={clock().isoformat()}"
            )
        else:
            print(f"RW-19 autolaunch refused: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — fail closed, log, nonzero
        if log is not None:
            log.write(f"refused: {type(exc).__name__}: {exc}")
            log.write(
                f"terminal_state={TERMINAL_LAUNCHER_REFUSED} "
                f"started_at={started.isoformat()} finished_at={clock().isoformat()}"
            )
        else:
            print(
                f"RW-19 autolaunch refused: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        return 1


def run_live(
    cfg: LaunchConfig,
    *,
    clock: Clock,
    sleeper: Sleeper,
    runner: Runner,
    fs: FsProbes,
) -> int:
    """Full live sequence including exactly one collector invocation."""

    def build_collect_argv(preflight_path: Path) -> list[str]:
        # Defined and called ONLY inside the live path. Rehearsal has no reference.
        return [
            str(cfg.python_exe),
            str(cfg.collector_tool),
            "collect",
            "--plan",
            str(cfg.plan_path),
            "--expected-plan-sha256",
            cfg.expected_plan_sha256,
            "--safety-preflight",
            str(preflight_path),
            "--output-dir",
            str(cfg.output_dir),
        ]

    log: LaunchLog | None = None
    terminal = TERMINAL_LAUNCHER_REFUSED
    exit_code = 1
    started = clock()
    try:
        log = _open_log(cfg, fs=fs, clock=clock)
        log.write("mode: LIVE")
        source_commit = bootstrap(cfg, log, fs=fs, mode="live")
        _refuse_if_late(cfg, log, clock=clock, stage="post_bootstrap")

        t_minus_2 = cfg.scheduled_start - T_MINUS_2
        _sleep_until(t_minus_2, log, clock=clock, sleeper=sleeper, label="T-2")
        _refuse_if_late(cfg, log, clock=clock, stage="at_T-2")

        verify_source_state(cfg, source_commit, log, runner=runner, fetch=True)
        preflight_path = run_preflight(cfg, log, runner=runner, clock=clock, fs=fs)

        _refuse_if_late(cfg, log, clock=clock, stage="pre_hold_to_T0")
        _sleep_until(cfg.scheduled_start, log, clock=clock, sleeper=sleeper, label="T0")
        _refuse_if_late(cfg, log, clock=clock, stage="at_T0")

        output_present, aborted_present = _dirs_present(cfg, fs)
        if output_present or aborted_present:
            raise AutolaunchError("evidence dirs appeared before collector invocation")
        verify_source_state(cfg, source_commit, log, runner=runner, fetch=False)

        collect_argv = build_collect_argv(preflight_path)
        env = _pinned_env(cfg)
        log.write("collect: invoking collector exactly once")
        try:
            # Bound under the scheduler's 2h task limit so a hung collector is
            # logged HERE as an integrity error, not silently killed above us.
            collect_result = runner(
                collect_argv,
                cwd=str(cfg.repo_root),
                env=env,
                timeout=COLLECT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            log.write("INTEGRITY_ERROR: collector exceeded the launcher timeout; killed")
            log.write(
                f"terminal_state={TERMINAL_INTEGRITY} started_at={started.isoformat()} "
                f"finished_at={clock().isoformat()}"
            )
            return 1
        log.write(f"collect: exit={collect_result.returncode}")
        # No retry path exists after this point.
        terminal, exit_code = evaluate_collector_outcome(
            cfg,
            collect_result.returncode,
            log,
            runner=runner,
            fs=fs,
        )
        log.write(
            f"terminal_state={terminal} started_at={started.isoformat()} "
            f"finished_at={clock().isoformat()} collect_exit={collect_result.returncode}"
        )
        return exit_code
    except AutolaunchError as exc:
        terminal = exc.terminal
        if log is not None:
            log.write(f"refused: {exc}")
            log.write(
                f"terminal_state={terminal} started_at={started.isoformat()} "
                f"finished_at={clock().isoformat()}"
            )
        else:
            print(f"RW-19 autolaunch refused: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — fail closed, log, nonzero
        if log is not None:
            log.write(f"refused: {type(exc).__name__}: {exc}")
            log.write(
                f"terminal_state={TERMINAL_LAUNCHER_REFUSED} "
                f"started_at={started.isoformat()} finished_at={clock().isoformat()}"
            )
        else:
            print(
                f"RW-19 autolaunch refused: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        return 1


def run(
    cfg: LaunchConfig,
    *,
    rehearsal: bool = False,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
    runner: Runner | None = None,
    fs: FsProbes | None = None,
) -> int:
    clock_fn = clock or _utc_now
    sleeper_fn = sleeper or time.sleep
    runner_fn = runner or _default_runner
    probes = fs or FsProbes()
    # Separate call graphs: rehearsal never enters run_live (where argv is built).
    if rehearsal:
        return run_rehearsal(
            cfg,
            clock=clock_fn,
            sleeper=sleeper_fn,
            runner=runner_fn,
            fs=probes,
        )
    return run_live(
        cfg,
        clock=clock_fn,
        sleeper=sleeper_fn,
        runner=runner_fn,
        fs=probes,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--rehearsal",
        action="store_true",
        help="Validate bootstrap/source/preflight only; never invoke the collector",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except AutolaunchError as exc:
        print(f"RW-19 autolaunch refused: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"RW-19 autolaunch refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return run(cfg, rehearsal=bool(args.rehearsal))


if __name__ == "__main__":
    raise SystemExit(main())
