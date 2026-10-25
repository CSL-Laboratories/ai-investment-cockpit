#!/usr/bin/env python3
"""CSL-392 open-audit: which tracked files the linux job suites actually open.

This module is two things:

1. A ``sitecustomize`` hook. When copied or symlinked onto ``PYTHONPATH`` as
   ``sitecustomize.py`` and ``CSL392_AUDIT_LOG`` is set, it wraps ``open`` /
   ``os.open`` / ``io.open_code`` and records every *tracked* repo file a
   Python process opens, with the current pytest/unittest name.
2. A runner. ``python tools/csl_392_docs_read_audit.py`` installs the hook,
   runs the same test commands the required ``linux`` job runs (read from
   ``ci.yml`` and ``run-gates/action.yml``; those files are not modified),
   and classifies each opened path with the ``filter`` job's decide script.

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

# Needles copied from the linux job / run-gates composite. The runner refuses
# to start if a needle is missing, so a later workflow edit cannot silently
# drift this audit. Do not "fix" that by editing the workflow from this issue.
LINUX_NEEDLES = (
    "python -X utf8 -B tools/test_run_gates_action.py",
    "python -m pytest -q",
    "python -X utf8 -B tools/test_ci_dispatch_scope.py",
    "python -X utf8 -B tools/test_ci_settle.py",
    "python -X utf8 -B -m unittest tools.test_review_gate tools.test_queue_gates",
    "python -X utf8 -B tools/test_ci_cancel_controller.py",
    "python -X utf8 -B tools/test_candidate_workflows.py",
    "python -X utf8 -B tools/test_ci_docs_filter.py",
)

# Not run here: ruff (not a file-open test), and the linux job's later
# maturin/batch-parity step, which re-runs tests/quant/book/test_book_batch.py
# after building a wheel. That file is already in `python -m pytest -q`.

_ORIG_OPEN = builtins.open
_ORIG_IO_OPEN = io.open
_ORIG_OS_OPEN = os.open
_ORIG_OPEN_CODE = getattr(io, "open_code", None)
_ORIG_UNITTEST_RUN = unittest.case.TestCase.run
_LOCK = threading.Lock()
_IN_HOOK = False
_TRACKED: set[str] | None = None
_SEEN: set[tuple[str, str]] = set()


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
        if rel.startswith("app/tests/") or rel.startswith("tools/test_"):
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
        except Exception:
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


def _record(fileobj: object, op: str) -> None:
    global _IN_HOOK
    log_path = os.environ.get("CSL392_AUDIT_LOG")
    if not log_path or _IN_HOOK:
        return
    _IN_HOOK = True
    try:
        rel = _relpath(fileobj)
        if rel is None:
            return
        test = _current_test()
        key = (test, rel)
        with _LOCK:
            if key in _SEEN:
                return
            _SEEN.add(key)
            line = json.dumps(
                {"test": test, "path": rel, "op": op},
                ensure_ascii=True,
            )
            with _ORIG_OPEN(log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        pass
    finally:
        _IN_HOOK = False


def _wrapped_open(file, *args, **kwargs):
    _record(file, "open")
    return _ORIG_OPEN(file, *args, **kwargs)


def _wrapped_io_open(file, *args, **kwargs):
    # pathlib.Path.read_text/read_bytes call io.open, which is bound at
    # import of io and is NOT updated when builtins.open is rebound.
    _record(file, "io.open")
    return _ORIG_IO_OPEN(file, *args, **kwargs)


def _wrapped_os_open(path, *args, **kwargs):
    _record(path, "os.open")
    return _ORIG_OS_OPEN(path, *args, **kwargs)


def _wrapped_open_code(path):
    _record(path, "open_code")
    return _ORIG_OPEN_CODE(path)


def _wrapped_unittest_run(self, result=None):
    token = self.id()
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
    """Install the open/os.open/open_code and unittest name hooks."""
    builtins.open = _wrapped_open
    io.open = _wrapped_io_open
    os.open = _wrapped_os_open
    if _ORIG_OPEN_CODE is not None:
        io.open_code = _wrapped_open_code
    unittest.case.TestCase.run = _wrapped_unittest_run


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


def classify(log_path: Path) -> dict:
    patterns = filter_patterns()
    by_path: dict[str, list[str]] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
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
    }


def _assert_needles() -> None:
    blob = CI.read_text(encoding="utf-8") + "\n" + RUN_GATES.read_text(encoding="utf-8")
    missing = [needle for needle in LINUX_NEEDLES if needle not in blob]
    if missing:
        raise SystemExit(
            "linux job commands drifted; update LINUX_NEEDLES after a "
            f"human-assigned workflow issue: {missing}"
        )


def _python() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    if venv.is_file():
        return str(venv)
    return sys.executable


def _run(command: list[str], env: dict[str, str], cwd: Path) -> int:
    print("+", " ".join(command), f"(cwd={cwd})", flush=True)
    completed = subprocess.run(command, cwd=cwd, env=env)
    print(f"exit {completed.returncode}: {' '.join(command)}", flush=True)
    return completed.returncode


def run_linux_suites(log_path: Path) -> dict[str, int]:
    _assert_needles()
    python = _python()
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("COCKPIT_"):
            env.pop(key, None)
    env["CSL392_AUDIT_LOG"] = str(log_path)
    env["CSL392_REPO"] = str(ROOT)
    with tempfile.TemporaryDirectory(prefix="csl392-audit-") as raw:
        audit_dir = Path(raw)
        (audit_dir / "sitecustomize.py").write_bytes(Path(__file__).read_bytes())
        env["PYTHONPATH"] = str(audit_dir) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        log_path.write_text("", encoding="utf-8")
        codes = {}
        codes["test_run_gates_action"] = _run(
            [python, "-X", "utf8", "-B", "tools/test_run_gates_action.py"],
            env,
            ROOT,
        )
        codes["pytest"] = _run([python, "-m", "pytest", "-q"], env, ROOT / "app")
        codes["test_ci_dispatch_scope"] = _run(
            [python, "-X", "utf8", "-B", "tools/test_ci_dispatch_scope.py"],
            env,
            ROOT,
        )
        codes["test_ci_settle"] = _run(
            [python, "-X", "utf8", "-B", "tools/test_ci_settle.py"], env, ROOT
        )
        codes["test_review_gate_queue_gates"] = _run(
            [
                python,
                "-X",
                "utf8",
                "-B",
                "-m",
                "unittest",
                "tools.test_review_gate",
                "tools.test_queue_gates",
            ],
            env,
            ROOT,
        )
        codes["test_ci_cancel_controller"] = _run(
            [python, "-X", "utf8", "-B", "tools/test_ci_cancel_controller.py"],
            env,
            ROOT,
        )
        codes["test_candidate_workflows"] = _run(
            [python, "-X", "utf8", "-B", "tools/test_candidate_workflows.py"],
            env,
            ROOT,
        )
        codes["test_ci_docs_filter"] = _run(
            [python, "-X", "utf8", "-B", "tools/test_ci_docs_filter.py"],
            env,
            ROOT,
        )
    return codes


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "classify":
        log_path = Path(argv[1])
        result = classify(log_path)
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if not result["mismatches"] else 2
    log_path = Path(argv[0]) if argv else Path(tempfile.gettempdir()) / "csl392-opens.jsonl"
    codes = run_linux_suites(log_path)
    result = classify(log_path)
    result["exit_codes"] = codes
    out = Path(os.environ.get("CSL392_AUDIT_SUMMARY", str(log_path) + ".summary.json"))
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"summary: {out}", flush=True)
    print(f"mismatches: {len(result['mismatches'])}", flush=True)
    for item in result["mismatches"]:
        print(f"  {item['path']} <- {item['tests'][:5]}", flush=True)
    # A red suite is still an audit: do not hide open results behind exit 1.
    if any(code != 0 for code in codes.values()):
        print("one or more suites exited non-zero; see exit_codes in the summary", flush=True)
    return 0


if __name__ == "sitecustomize":
    if os.environ.get("CSL392_AUDIT_LOG"):
        install_hooks()

if __name__ == "__main__":
    sys.exit(main())
