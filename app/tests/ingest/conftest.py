"""Surface CSL-236 results in quiet pytest output.

Blacksmith runs ``python -m pytest -q``, which prints dots rather than node ids.
The acceptance check for these tests is that each one is visible as passing in
that job log.
"""
from __future__ import annotations

_PREFIX = "tests/ingest/test_strict_freshness_messages.py::"


def pytest_terminal_summary(terminalreporter):
    rows = []
    for outcome in ("passed", "failed", "skipped", "error"):
        for report in terminalreporter.stats.get(outcome, []):
            if getattr(report, "when", None) != "call":
                continue
            if not str(report.nodeid).startswith(_PREFIX):
                continue
            rows.append((outcome.upper(), report.nodeid))
    if not rows:
        return
    terminalreporter.write_sep("-", "CSL-236 strict-freshness tests")
    for outcome, nodeid in rows:
        terminalreporter.write_line(f"{outcome} {nodeid}")
