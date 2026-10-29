#!/usr/bin/env python3
"""Test-inventory floor for the replacement-program and O7 checkers (CSL-511 WP4).

The two checkers are promoted to direct CI steps, but their regression tests
still live partly under app/. Deleting or thinning one of those files used to
leave the suite green. This script fails closed when a pinned test file is
missing, is a symlink, fails to collect, or collects fewer tests than its floor.

    python -X utf8 -B tools/check_checker_test_floor.py

A deliberate move (for example an app/ deletion that ports the oracles) must
edit FLOORS in the same reviewed change, so the loss is visible in the diff.
Counts are exact as of the pinned head; raise them when tests are added, and
lower one only with an explicit reviewer-visible reason.
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]


class Floor(NamedTuple):
    path: str
    runner: str
    minimum: int


FLOORS = (
    Floor("app/tests/test_replacement_program_registry.py", "pytest", 142),
    Floor("app/tests/test_o7_operator_contract.py", "pytest", 69),
    Floor("tools/test_replacement_program.py", "unittest", 65),
)

REQUIRED_FILES = (
    "tools/check_replacement_program.py",
    "tools/check_o7_operator_contract.py",
    "docs/replacement/program.json",
)

Counter = Callable[[Path, Floor], int]

_PYTEST_ITEM = re.compile(r"^\S+::\S")


def count_pytest(root: Path, floor: Floor) -> int:
    app = root / "app"
    target = (root / floor.path).relative_to(app).as_posix()
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", target],
        check=False, cwd=app, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pytest collection failed (exit {proc.returncode}):\n{proc.stdout}{proc.stderr}")
    return sum(1 for line in proc.stdout.splitlines() if _PYTEST_ITEM.match(line))


def count_unittest(root: Path, floor: Floor) -> int:
    module = floor.path.removesuffix(".py").replace("/", ".")
    code = (
        "import unittest, sys; "
        f"suite = unittest.defaultTestLoader.loadTestsFromName({module!r}); "
        "sys.stdout.write(str(suite.countTestCases()))"
    )
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", "-c", code],
        check=False, cwd=root, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0 or not proc.stdout.strip().isdigit():
        raise RuntimeError(f"unittest load failed (exit {proc.returncode}):\n{proc.stdout}{proc.stderr}")
    return int(proc.stdout.strip())


COUNTERS: dict[str, Counter] = {"pytest": count_pytest, "unittest": count_unittest}


def _plain_file_problem(root: Path, rel: str) -> str | None:
    path = root / rel
    if path.is_symlink():
        return f"{rel} is a symlink"
    if not path.is_file():
        return f"{rel} is missing"
    return None


def check(
    root: Path = ROOT,
    floors: tuple[Floor, ...] = FLOORS,
    required: tuple[str, ...] = REQUIRED_FILES,
    counters: dict[str, Counter] | None = None,
    report: Callable[[str], None] | None = None,
) -> list[str]:
    counters = COUNTERS if counters is None else counters
    problems: list[str] = []
    for rel in required:
        problem = _plain_file_problem(root, rel)
        if problem:
            problems.append(problem)
    for floor in floors:
        problem = _plain_file_problem(root, floor.path)
        if problem:
            problems.append(f"{problem}; floor {floor.minimum} tests")
            continue
        counter = counters.get(floor.runner)
        if counter is None:
            problems.append(f"{floor.path}: unknown runner {floor.runner!r}")
            continue
        try:
            found = counter(root, floor)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            problems.append(f"{floor.path}: {exc}")
            continue
        if found < floor.minimum:
            problems.append(f"{floor.path} collects {found} tests, below its floor of {floor.minimum}")
        else:
            if report is not None:
                report(f"floor ok: {floor.path} {found} >= {floor.minimum}")
    return problems


def main() -> int:
    problems = check(report=print)
    for problem in problems:
        print(f"::error::checker test floor: {problem}", file=sys.stderr)
    if problems:
        print("checker-test-floor: FAIL", file=sys.stderr)
        return 1
    print("checker-test-floor: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
