#!/usr/bin/env python3
"""CSL-392 open-audit: which tracked files the linux job suites actually open.

This module is two things:

1. A ``sitecustomize`` hook. When copied or symlinked onto ``PYTHONPATH`` as
   ``sitecustomize.py`` and ``CSL392_AUDIT_LOG`` is set, it wraps ``open`` /
   ``io.open`` / ``os.open`` / ``io.open_code`` and records every *tracked*
   repo file a Python process opens, with the current pytest/unittest name.
   It also records each child process a test starts, so the report can size
   the blind spot (non-Python readers, and Python children that cannot load
   the hook).
2. A runner. ``python tools/csl_392_docs_read_audit.py`` builds its command
   list from the parsed ``linux`` job steps in ``ci.yml`` and the
   ``run-gates`` composite action (neither file is modified), runs every
   test/check command there under the hook, and classifies each opened path
   with the ``filter`` job's decide patterns. A Python command in the job that
   is neither a recognised test/check nor a reviewed ``EXCLUDED`` entry stops
   the runner, as does an ``EXCLUDED`` entry the job no longer has. Exit codes:
   0 clean, 1 incomplete (a suite exited non-zero), 2 mismatches.

It does not edit ``.github/`` or the filter. Any filter change still needs its
own human-assigned issue.

Re-run::

    python tools/csl_392_docs_read_audit.py
    python tools/csl_392_docs_read_audit.py classify /path/to/opens.jsonl
"""
from __future__ import annotations

import builtins
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
RUN_GATES = ROOT / ".github" / "actions" / "run-gates" / "action.yml"

# Python commands in the linux job (or the run-gates action it calls) that this
# runner deliberately does not run, keyed by (working directory, fullmatch
# regex on the command line) with the reason. Every other python/pytest/unittest
# line there must be a test/check the runner can execute, or planning fails.
# An entry that no longer matches any job line also fails, so this list cannot
# go stale. Do not "fix" a failure by editing the workflow from this issue.
EXCLUDED: dict[tuple[str, str], str] = {
    (".", r'python -I -X utf8 -B "\$stage/merge_candidate\.py" stage-policy "\$stage"'): (
        "candidate policy staging; needs the Actions env (GITHUB_SHA, RUNNER_TEMP)"
    ),
    (".", r'python -I -X utf8 -B "\$CODEX_POLICY_VALIDATOR" validate-dispatch'): (
        "candidate dispatch validation; needs the Actions env and a staged validator"
    ),
    (".", r"python -P \.github/actions/run-gates/install\.py"): (
        "dependency installer, not a test; the audit venv uses the same pins"
    ),
    ("${{ runner.temp }}", r"python -P -m pip install -U maturin"): "package install, not a test",
    (".", r"python -P -m maturin build .*"): "Rust wheel build, not a test",
    (".", r"python -P -c .*"): "installs the built wheel, not a test",
    ("app", r"python -P -m pytest -q tests/quant/book/test_book_batch\.py -ra \| tee .*"): (
        "batch parity re-run of tests/quant/book/test_book_batch.py against the "
        "Rust wheel; the same file runs in `python -m pytest -q`"
    ),
}

RUN_GATES_USES = "./.github/actions/run-gates"
_PYTHONISH = re.compile(r"(?:^|[\s;&|(])(?:python3?|pytest)(?:\s|$)|\bunittest\b")
_SHELL_META = re.compile(r"[|;&$<>`()]")
_RUNNABLE_SCRIPT = re.compile(r"tools/(?:test|check)_\w+\.py")
_RUNNABLE_MODULES = ("pytest", "unittest")
# -I / -E / -s drop PYTHONPATH, so the sitecustomize hook would not load.
_HOOK_BLOCKING_FLAGS = ("-I", "-E", "-s")


_ORIG_OPEN = builtins.open
_ORIG_IO_OPEN = io.open
_ORIG_OS_OPEN = os.open
_ORIG_OPEN_CODE = getattr(io, "open_code", None)
_ORIG_UNITTEST_RUN = unittest.case.TestCase.run
_ORIG_POPEN_INIT = subprocess.Popen.__init__
_LOCK = threading.Lock()
# Per-thread, not process-wide. A global flag drops opens on other threads
# while this thread is inside _record / _record_exec (the app suite runs
# threaded tests). The lock below still covers the shared log.
_hook_local = threading.local()
_TRACKED: set[str] | None = None
_SEEN: set[tuple[str, ...]] = set()


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    return Path(raw) if raw else None


def _repo() -> Path:
    raw = os.environ.get("CSL392_REPO")
    return Path(raw) if raw else ROOT


def _tracked() -> set[str]:
    global _TRACKED
    if _TRACKED is None:
        listed = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=_repo(), text=True
        )
        _TRACKED = {path for path in listed.split("\0") if path}
    return _TRACKED


def _current_test() -> str:
    env = os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("CSL392_CURRENT_TEST")
    if env:
        return env.split(" (")[0]
    repo = str(_repo())
    for frame in traceback.extract_stack()[:-2]:
        filename = frame.filename.replace("\\", "/")
        if not filename.startswith(repo.replace("\\", "/")):
            continue
        rel = filename[len(repo) :].lstrip("/")
        if rel.startswith(("app/tests/", "tools/test_")):
            return f"{rel}:{frame.lineno}"
        if "/tests/" in rel and rel.endswith(".py"):
            return f"{rel}:{frame.lineno}"
    return "<import-or-session>"


def _relpath(fileobj: object) -> str | None:
    if isinstance(fileobj, int):
        return None
    if isinstance(fileobj, bytes):
        try:
            fileobj = os.fsdecode(fileobj)
        except UnicodeError:
            return None
    if not isinstance(fileobj, (str, os.PathLike)):
        return None
    path = Path(fileobj)
    repo = _repo().resolve()
    try:
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        rel = path.relative_to(repo).as_posix()
    except (OSError, RuntimeError, ValueError):
        return None
    if rel in _tracked():
        return rel
    return None


def _enter_hook() -> bool:
    """True when this thread may record. Same-thread re-entry stays skipped."""
    if getattr(_hook_local, "busy", False):
        return False
    _hook_local.busy = True
    return True


def _leave_hook() -> None:
    _hook_local.busy = False


def _record(fileobj: object, op: str) -> None:
    log_path = os.environ.get("CSL392_AUDIT_LOG")
    if not log_path or not _enter_hook():
        return
    try:
        rel = _relpath(fileobj)
        if rel is None:
            return
        test = _current_test()
        _write_row(log_path, (test, rel), {"test": test, "path": rel, "op": op})
    except Exception:  # noqa: BLE001, S110 - the hook must never break the audited process
        pass
    finally:
        _leave_hook()


def _write_row(log_path: str, key: tuple[str, ...], row: dict) -> None:
    with _LOCK:
        if key in _SEEN:
            return
        _SEEN.add(key)
        with _ORIG_OPEN(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _child_is_hooked(argv: list[str], env: object) -> bool:
    """Whether a Python child would load this hook (same sitecustomize dir)."""
    if any(arg in _HOOK_BLOCKING_FLAGS for arg in argv[1:]):
        return False
    if env is None:
        return True
    if not isinstance(env, dict) or not env.get("CSL392_AUDIT_LOG"):
        return False
    here = os.path.dirname(os.path.abspath(__file__))
    return here in str(env.get("PYTHONPATH", "")).split(os.pathsep)


def _record_exec(args: object, kwargs: dict) -> None:
    """Log a child process: the open hook cannot see what it reads."""
    log_path = os.environ.get("CSL392_AUDIT_LOG")
    if not log_path or not _enter_hook():
        return
    try:
        if isinstance(args, (str, bytes, os.PathLike)):
            argv = [os.fsdecode(args)]
        else:
            argv = [os.fsdecode(arg) for arg in args]
        if kwargs.get("shell"):
            words = argv[0].split()
            argv = ["sh", "-c", words[0] if words else ""]
        name = os.path.basename(argv[0]) if argv else "?"
        if kwargs.get("shell"):
            name = f"sh -c {argv[2]}"
        python = re.fullmatch(r"python[0-9.]*(?:\.exe)?", name) is not None or (
            argv[0] == sys.executable
        )
        hooked = python and _child_is_hooked(argv, kwargs.get("env"))
        test = _current_test()
        _write_row(
            log_path,
            (test, "exec", name, str(hooked)),
            {"test": test, "exec": name, "python": python, "hooked": hooked},
        )
    except Exception:  # noqa: BLE001, S110 - the hook must never break the audited process
        pass
    finally:
        _leave_hook()


def _wrapped_open(file, *args, **kwargs):
    _record(file, "open")
    return _ORIG_OPEN(file, *args, **kwargs)


def _wrapped_io_open(file, *args, **kwargs):
    # pathlib.Path.read_text/read_bytes call io.open, which is bound at
    # import of io and is NOT updated when builtins.open is rebound.
    _record(file, "io.open")
    return _ORIG_IO_OPEN(file, *args, **kwargs)


def _wrapped_os_open(path, *args, **kwargs):
    if kwargs.get("dir_fd") is None:
        _record(path, "os.open")
    return _ORIG_OS_OPEN(path, *args, **kwargs)


def _wrapped_open_code(path):
    _record(path, "open_code")
    return _ORIG_OPEN_CODE(path)


def _wrapped_popen_init(self, args, *rest, **kwargs):
    _record_exec(args, kwargs)
    return _ORIG_POPEN_INIT(self, args, *rest, **kwargs)


def _wrapped_unittest_run(self, result=None):
    token = self.id()
    if token.startswith("__main__.") and sys.argv and sys.argv[0].endswith(".py"):
        # `python tools/test_x.py` names tests __main__.Class.test; keep the file.
        script = Path(sys.argv[0]).resolve()
        try:
            script_name = script.relative_to(_repo().resolve()).as_posix()
        except ValueError:
            script_name = script.name
        token = f"{script_name}::{token[len('__main__.'):]}"
    previous = os.environ.get("CSL392_CURRENT_TEST")
    os.environ["CSL392_CURRENT_TEST"] = token
    try:
        return _ORIG_UNITTEST_RUN(self, result)
    finally:
        if previous is None:
            os.environ.pop("CSL392_CURRENT_TEST", None)
        else:
            os.environ["CSL392_CURRENT_TEST"] = previous


def install_hooks() -> None:
    """Install the open/os.open/open_code, child-process and unittest name hooks."""
    builtins.open = _wrapped_open
    io.open = _wrapped_io_open
    os.open = _wrapped_os_open
    if _ORIG_OPEN_CODE is not None:
        io.open_code = _wrapped_open_code
    unittest.case.TestCase.run = _wrapped_unittest_run
    subprocess.Popen.__init__ = _wrapped_popen_init


def filter_patterns() -> tuple[str, ...]:
    """The decide step's grep -Eq patterns, in order: run, run, skip."""
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("PyYAML is required to parse ci.yml") from exc
    ci = yaml.load(CI.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    step = next(
        item for item in ci["jobs"]["filter"]["steps"] if item.get("id") == "decide"
    )
    patterns = tuple(re.findall(r"grep -Eq(?: \\\n\s*)? '([^']+)'", step["run"]))
    if len(patterns) != 3:
        raise SystemExit(f"expected 3 decide grep -Eq patterns, got {len(patterns)}")
    return patterns


def skipped_by_filter(path: str, patterns: tuple[str, ...] | None = None) -> bool:
    patterns = patterns or filter_patterns()
    run_patterns, skip_pattern = patterns[:-1], patterns[-1]
    if any(re.search(pattern, path) for pattern in run_patterns):
        return False
    return re.search(skip_pattern, path) is not None


def _test_file(test: str) -> str:
    return re.split(r"::|:\d+$", test, maxsplit=1)[0]


def classify(log_path: Path, patterns: tuple[str, ...] | None = None) -> dict:
    patterns = patterns or filter_patterns()
    by_path: dict[str, list[str]] = {}
    blind: dict[str, set[str]] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "exec" in row:
            if not row["hooked"]:
                label = row["exec"] + (" (python, hook not loaded)" if row["python"] else "")
                blind.setdefault(label, set()).add(_test_file(row["test"]))
            continue
        by_path.setdefault(row["path"], [])
        if row["test"] not in by_path[row["path"]]:
            by_path[row["path"]].append(row["test"])
    mismatches = []
    opened_run = []
    opened_other = []
    for path, tests in sorted(by_path.items()):
        entry = {"path": path, "tests": tests}
        if skipped_by_filter(path, patterns):
            mismatches.append(entry)
        elif any(re.search(pattern, path) for pattern in patterns[:-1]):
            opened_run.append(entry)
        else:
            opened_other.append(entry)
    return {
        "log": str(log_path),
        "patterns": list(patterns),
        "opened_tracked": len(by_path),
        "mismatches": mismatches,
        "opened_run_tests": opened_run,
        "opened_other": opened_other,
        "unhooked_children": [
            {"exec": name, "test_files": sorted(files)}
            for name, files in sorted(blind.items())
        ],
    }


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("PyYAML is required to parse the workflow files") from exc
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _python_target(argv: list[str]) -> tuple[str, str | None, list[str]]:
    """Split ``python [flags] (-m mod | -c code | script) ...``."""
    flags: list[str] = []
    i = 1
    while i < len(argv) and argv[i].startswith("-") and argv[i] not in ("-m", "-c"):
        if argv[i] in ("-X", "-W"):
            flags.extend(argv[i : i + 2])
            i += 2
        else:
            flags.append(argv[i])
            i += 1
    if i >= len(argv):
        return "none", None, flags
    if argv[i] == "-m":
        return "module", argv[i + 1] if i + 1 < len(argv) else None, flags
    if argv[i] == "-c":
        return "code", None, flags
    return "script", argv[i], flags


def _job_lines(ci: dict, action: dict) -> list[tuple[str, str]]:
    """(working directory, command line) for every run line of the linux job."""
    steps: list[dict] = []
    expanded = False
    for step in ci["jobs"]["linux"]["steps"]:
        uses = step.get("uses", "")
        if uses == RUN_GATES_USES:
            steps.extend(action["runs"]["steps"])
            expanded = True
        elif uses.startswith("./"):
            raise SystemExit(f"linux job uses an unaudited local action: {uses}")
        elif "run" in step:
            steps.append(step)
    if not expanded:
        raise SystemExit(f"linux job no longer calls {RUN_GATES_USES}; update this audit")
    lines: list[tuple[str, str]] = []
    for step in steps:
        if "run" not in step:
            continue
        cwd = step.get("working-directory", ".")
        pending = ""
        for raw in step["run"].splitlines():
            line = raw.strip()
            if pending:
                line = pending + " " + line
                pending = ""
            if line.endswith("\\"):
                pending = line[:-1].strip()
                continue
            if not line or line.startswith("#"):
                continue
            if line.startswith("cd "):
                cwd = os.path.normpath(os.path.join(cwd, line[3:].strip()))
                continue
            lines.append((cwd, line))
    return lines


def plan_linux_commands(
    ci: dict | None = None, action: dict | None = None
) -> tuple[list[tuple[str, list[str]]], list[dict]]:
    """Commands to run, plus the reviewed exclusions that matched.

    Fails (SystemExit) when a python/pytest/unittest line in the linux job is
    neither runnable here nor in ``EXCLUDED``, when an ``EXCLUDED`` entry no
    longer matches a job line, or when no app pytest run is found.
    """
    import shlex

    ci = ci if ci is not None else _load_yaml(CI)
    action = action if action is not None else _load_yaml(RUN_GATES)
    commands: list[tuple[str, list[str]]] = []
    excluded: list[dict] = []
    used: set[tuple[str, str]] = set()
    uncovered: list[str] = []
    for cwd, line in _job_lines(ci, action):
        if not _PYTHONISH.search(line):
            continue
        hit = next(
            (key for key in EXCLUDED if key[0] == cwd and re.fullmatch(key[1], line)),
            None,
        )
        if hit is not None:
            used.add(hit)
            excluded.append({"cwd": cwd, "command": line, "reason": EXCLUDED[hit]})
            continue
        argv = None if _SHELL_META.search(line) else shlex.split(line)
        if argv and argv[0] == "python":
            kind, target, flags = _python_target(argv)
            runnable = (
                (kind == "module" and target in _RUNNABLE_MODULES)
                or (kind == "script" and target and _RUNNABLE_SCRIPT.fullmatch(target))
            ) and not any(flag in _HOOK_BLOCKING_FLAGS for flag in flags)
            if runnable:
                commands.append((cwd, argv))
                continue
        uncovered.append(f"({cwd}) {line}")
    stale = [f"({cwd}) {pattern}" for cwd, pattern in EXCLUDED if (cwd, pattern) not in used]
    if uncovered or stale:
        raise SystemExit(
            "linux job commands drifted from this audit; update EXCLUDED or the "
            f"runnable forms after review. uncovered={uncovered} stale_exclusions={stale}"
        )
    if not any(cwd == "app" and argv[1:3] == ["-m", "pytest"] for cwd, argv in commands):
        raise SystemExit("no `python -m pytest` run in app/ found in the linux job")
    return commands, excluded


def _python() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    if venv.is_file():
        return str(venv)
    return sys.executable


def _run(command: list[str], env: dict[str, str], cwd: Path) -> int:
    print("+", " ".join(command), f"(cwd={cwd})", flush=True)
    completed = subprocess.run(command, check=False, cwd=cwd, env=env)
    print(f"exit {completed.returncode}: {' '.join(command)}", flush=True)
    return completed.returncode


def run_linux_suites(log_path: Path) -> tuple[dict[str, int], list[dict]]:
    # Suites run with different working directories. A relative log path would
    # make the app suite append to app/<log> while classify reads the root file.
    log_path = log_path.resolve()
    commands, excluded = plan_linux_commands()
    python = _python()
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("COCKPIT_"):
            env.pop(key, None)
    env["CSL392_AUDIT_LOG"] = str(log_path)
    env["CSL392_REPO"] = str(ROOT)
    codes: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="csl392-audit-") as raw:
        audit_dir = Path(raw)
        (audit_dir / "sitecustomize.py").write_bytes(Path(__file__).read_bytes())
        env["PYTHONPATH"] = str(audit_dir) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        log_path.write_text("", encoding="utf-8")
        for cwd, argv in commands:
            name = f"({cwd}) {' '.join(argv)}"
            codes[name] = _run([python, *argv[1:]], env, (ROOT / cwd).resolve())
    return codes, excluded


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "classify":
        log_path = Path(argv[1])
        result = classify(log_path)
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if not result["mismatches"] else 2
    if argv and argv[0] == "plan":
        commands, excluded = plan_linux_commands()
        for cwd, command in commands:
            print(f"run      ({cwd}) {' '.join(command)}")
        for item in excluded:
            print(f"excluded ({item['cwd']}) {item['command']}  # {item['reason']}")
        return 0
    log_path = Path(argv[0]) if argv else Path(tempfile.gettempdir()) / "csl392-opens.jsonl"
    codes, excluded = run_linux_suites(log_path)
    result = classify(log_path)
    failed = sorted(name for name, code in codes.items() if code != 0)
    result["exit_codes"] = codes
    result["excluded_commands"] = excluded
    result["complete"] = not failed
    result["failed_suites"] = failed
    out = Path(os.environ.get("CSL392_AUDIT_SUMMARY", str(log_path) + ".summary.json"))
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"summary: {out}", flush=True)
    print(f"mismatches: {len(result['mismatches'])}", flush=True)
    for item in result["mismatches"]:
        print(f"  {item['path']} <- {item['tests'][:5]}", flush=True)
    if result["mismatches"]:
        return 2
    if failed:
        # A suite that did not finish opened less than the job would; a missing
        # result must not read as a clean one.
        print("INCOMPLETE: suites exited non-zero:", flush=True)
        for name in failed:
            print(f"  {name}", flush=True)
        return 1
    return 0


if __name__ == "sitecustomize" and os.environ.get("CSL392_AUDIT_LOG"):
    install_hooks()

if __name__ == "__main__":
    sys.exit(main())
