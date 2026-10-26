"""WP14 X1 inventory guard: every legacy test file has exactly one disposition row.

Runs under the unchanged ``linux-gates`` pytest step. The checker itself lives in
``tools/check_x1_inventory.py`` so it survives a later ``app/`` deletion.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "tools" / "check_x1_inventory.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_x1_inventory", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


x1 = _load()
TABLE_TEXT = x1.DEFAULT_TABLE.read_text(encoding="utf-8")


def _actual() -> dict[str, int]:
    found, errors = x1.discover()
    assert errors == []
    return found


def _row_for(path: str) -> str:
    for line in TABLE_TEXT.splitlines():
        if line.startswith(f"| `{path}` |"):
            return line
    raise AssertionError(f"no row for {path}")


def _keep_path() -> str:
    return next(r["path"] for r in x1.parse_rows(TABLE_TEXT) if r["disposition"] == "keep")


def test_inventory_covers_every_legacy_test_file_once() -> None:
    assert x1.check(TABLE_TEXT, _actual()) == []


def test_checker_cli_passes_on_this_tree() -> None:
    result = subprocess.run(
        (sys.executable, "tools/check_x1_inventory.py"), cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "x1-cockpit-inventory: PASS" in result.stdout


def test_every_disposition_is_used_and_nothing_is_deleted_yet() -> None:
    rows = x1.parse_rows(TABLE_TEXT)
    assert {r["disposition"] for r in rows} == {"keep", "port", "drop"}
    for row in rows:
        assert (ROOT / str(row["path"])).is_file(), row["path"]


def test_a_test_file_without_a_row_is_red() -> None:
    actual = _actual()
    actual["app/tests/test_unlisted_new_file.py"] = 1
    errors = x1.check(TABLE_TEXT, actual)
    assert any("test_unlisted_new_file.py has no disposition row" in e for e in errors)


def test_a_deleted_oracle_file_is_red() -> None:
    actual = _actual()
    del actual[_keep_path()]
    errors = x1.check(TABLE_TEXT, actual)
    assert any("the file is missing" in e for e in errors)


def test_a_thinned_oracle_file_is_red() -> None:
    actual = _actual()
    path = next(r["path"] for r in x1.parse_rows(TABLE_TEXT) if r["disposition"] == "keep" and r["tests"] > 0)
    actual[path] = 0
    errors = x1.check(TABLE_TEXT, actual)
    assert any(path in e and "floor" in e for e in errors)


def test_a_duplicate_row_is_red() -> None:
    line = _row_for(_keep_path())
    errors = x1.check(TABLE_TEXT.replace(line, line + "\n" + line, 1), _actual())
    assert any("appears in 2 rows" in e for e in errors)


def test_a_kept_file_importing_a_drop_file_is_red() -> None:
    rows = x1.parse_rows(TABLE_TEXT)
    dropped = next(r["path"] for r in rows if r["disposition"] == "drop")
    imports = {_keep_path(): {dropped}}
    errors = x1.check(TABLE_TEXT, _actual(), imports=imports)
    assert any("which is marked drop" in e for e in errors)
    assert x1.check(TABLE_TEXT, _actual(), imports=x1.test_imports()) == []


def test_a_dropped_row_is_red() -> None:
    line = _row_for(_keep_path())
    errors = x1.check(TABLE_TEXT.replace(line + "\n", "", 1), _actual())
    assert any("has no disposition row" in e for e in errors)
    assert any("floor" in e for e in errors)


def test_unknown_disposition_and_oracle_class_conflict_are_red() -> None:
    path = _keep_path()
    line = _row_for(path)
    bad_disposition = line.replace("| keep |", "| maybe |", 1)
    assert any("unknown disposition" in e for e in x1.check(TABLE_TEXT.replace(line, bad_disposition, 1), _actual()))
    conflicted = line.replace("| keep |", "| drop |", 1)
    assert any("conflicts with oracle" in e for e in x1.check(TABLE_TEXT.replace(line, conflicted, 1), _actual()))


def test_an_unknown_oracle_and_empty_reason_are_red() -> None:
    path = _keep_path()
    line = _row_for(path)
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    unknown = "| " + " | ".join(cells[:3] + ["`NO-SUCH-ORACLE`"] + cells[4:]) + " |"
    assert any("not in the oracle catalog" in e for e in x1.check(TABLE_TEXT.replace(line, unknown, 1), _actual()))
    blank = "| " + " | ".join(cells[:4] + [""] + cells[5:]) + " |"
    assert any("empty reason" in e for e in x1.check(TABLE_TEXT.replace(line, blank, 1), _actual()))


@pytest.mark.parametrize("drop", ["rows", "oracles"])
def test_missing_markers_are_red(drop: str) -> None:
    marker = x1.ROWS_BEGIN if drop == "rows" else x1.ORACLES_BEGIN
    errors = x1.check(TABLE_TEXT.replace(marker, ""), _actual())
    assert errors and "malformed" in errors[0]


def test_raising_a_floor_above_the_table_is_red() -> None:
    rows = x1.parse_rows(TABLE_TEXT)
    assert any("floor" in e for e in x1.check(TABLE_TEXT, _actual(), min_rows=len(rows) + 1))
    assert any("floor" in e for e in x1.check(TABLE_TEXT, _actual(), min_oracle_tests=10**9))
