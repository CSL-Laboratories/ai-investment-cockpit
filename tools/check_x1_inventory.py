"""Offline guard for the X1 Cockpit legacy-test disposition inventory (WP14, CSL-511).

Every legacy test file under app/ must appear in exactly one row of the inventory
table with a disposition (port, drop or keep), an oracle from the catalog and a
reason. Counts are pinned as floors so that deleting or thinning an oracle turns
the job red until the floor and the table are edited in the same reviewed change.
Nothing here deletes or moves a file.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
DEFAULT_TABLE = ROOT / "docs" / "evidence" / "x1-cockpit-legacy-test-inventory-2026-10-26.md"

# Floors, exact at the head that introduced them. A deliberate move or deletion
# edits these in the same reviewed change as the table row it affects.
X1_MIN_ROWS = 224
X1_MIN_KEEP_ROWS = 136
X1_MIN_PORT_ROWS = 77
X1_MIN_ORACLE_TESTS = 2711

X1_TAG = "pre-x1-cockpit-20261026"
X1_TAG_COMMIT = "3122cac4780d776cbd487dd2a671466151a465ba"

DISPOSITIONS = ("port", "drop", "keep")
ROWS_BEGIN = "<!-- x1-inventory:rows:begin -->"
ROWS_END = "<!-- x1-inventory:rows:end -->"
ORACLES_BEGIN = "<!-- x1-inventory:oracles:begin -->"
ORACLES_END = "<!-- x1-inventory:oracles:end -->"
PATH_RE = re.compile(r"^app/tests/[A-Za-z0-9_./-]+\.py$")
ORACLE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")


def _block(text: str, begin: str, end: str) -> list[str]:
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError(f"expected exactly one {begin} and one {end} marker")
    start = text.index(begin) + len(begin)
    stop = text.index(end)
    if stop < start:
        raise ValueError(f"{end} precedes {begin}")
    return text[start:stop].splitlines()


def _cells(line: str) -> list[str] | None:
    line = line.strip()
    if not line.startswith("|") or not line.endswith("|"):
        return None
    cells = [c.strip() for c in line[1:-1].split("|")]
    if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
        return None
    return cells


def parse_oracles(text: str) -> dict[str, str]:
    """Return oracle id -> class (keep, port or drop) from the catalog table."""
    oracles: dict[str, str] = {}
    for line in _block(text, ORACLES_BEGIN, ORACLES_END):
        cells = _cells(line)
        if cells is None or cells[0].lower() == "oracle":
            continue
        if len(cells) != 4:
            raise ValueError(f"oracle row has {len(cells)} cells, expected 4: {line[:80]}")
        oid = cells[0].strip("`")
        if not ORACLE_ID_RE.match(oid):
            raise ValueError(f"bad oracle id {oid!r}")
        if oid in oracles:
            raise ValueError(f"duplicate oracle id {oid}")
        if cells[1] not in DISPOSITIONS:
            raise ValueError(f"oracle {oid} has unknown class {cells[1]!r}")
        if not cells[2] or not cells[3]:
            raise ValueError(f"oracle {oid} has an empty successor oracle or rationale")
        oracles[oid] = cells[1]
    return oracles


def parse_rows(text: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in _block(text, ROWS_BEGIN, ROWS_END):
        cells = _cells(line)
        if cells is None or cells[0].lower() == "path":
            continue
        if len(cells) != 6:
            raise ValueError(f"row has {len(cells)} cells, expected 6: {line[:80]}")
        path = cells[0].strip("`")
        if not PATH_RE.match(path):
            raise ValueError(f"bad path {path!r}")
        if not cells[1].isdigit():
            raise ValueError(f"{path}: test count {cells[1]!r} is not a number")
        rows.append(
            {
                "path": path,
                "tests": int(cells[1]),
                "disposition": cells[2],
                "oracle": cells[3].strip("`"),
                "reason": cells[4],
                "subject": cells[5],
            }
        )
    return rows


def static_test_count(source: str) -> int:
    """Count test functions statically; parametrized cases count once."""
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    )


def discover(app: Path = APP) -> tuple[dict[str, int], list[str]]:
    """Return {repo-relative path: static test count} for every legacy test file, plus errors."""
    found: dict[str, int] = {}
    errors: list[str] = []
    root = app.parent
    for path in sorted(app.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = path.name
        if not (name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"):
            continue
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            errors.append(f"{rel} is a symlink")
            continue
        try:
            found[rel] = static_test_count(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
            errors.append(f"{rel} is uncollectable: {type(exc).__name__}")
    return found, errors


def test_imports(app: Path = APP) -> dict[str, set[str]]:
    """Return {test file: legacy test files it imports through the tests package}."""
    root = app.parent
    known = {
        path.relative_to(root).as_posix()
        for path in app.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    result: dict[str, set[str]] = {}
    for rel in sorted(known):
        name = Path(rel).name
        if not (name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"):
            continue
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module == "tests" or node.module.startswith("tests."):
                    modules.add(node.module)
                    modules.update(f"{node.module}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Import):
                modules.update(a.name for a in node.names if a.name.startswith("tests."))
        hits = set()
        for module in modules:
            base = "app/" + module.replace(".", "/")
            for candidate in (base + ".py", base + "/conftest.py"):
                if candidate in known and candidate != rel:
                    hits.add(candidate)
        if hits:
            result[rel] = hits
    return result


def check(
    table_text: str,
    actual: dict[str, int],
    *,
    imports: dict[str, set[str]] | None = None,
    min_rows: int = X1_MIN_ROWS,
    min_keep_rows: int = X1_MIN_KEEP_ROWS,
    min_port_rows: int = X1_MIN_PORT_ROWS,
    min_oracle_tests: int = X1_MIN_ORACLE_TESTS,
) -> list[str]:
    errors: list[str] = []
    try:
        oracles = parse_oracles(table_text)
        rows = parse_rows(table_text)
    except ValueError as exc:
        return [f"inventory table is malformed: {exc}"]

    seen: dict[str, int] = {}
    for row in rows:
        path = str(row["path"])
        seen[path] = seen.get(path, 0) + 1
    for path, n in sorted(seen.items()):
        if n > 1:
            errors.append(f"{path} appears in {n} rows; it must appear in exactly one")
    for path in sorted(actual):
        if path not in seen:
            errors.append(f"{path} has no disposition row")
    for path in sorted(seen):
        if path not in actual:
            errors.append(f"{path} has a row but the file is missing; deletion is not allowed before the Nov 10 rehearsal")

    keep_rows = port_rows = oracle_tests = 0
    for row in rows:
        path = str(row["path"])
        disposition = str(row["disposition"])
        oracle = str(row["oracle"])
        if disposition not in DISPOSITIONS:
            errors.append(f"{path}: unknown disposition {disposition!r}")
            continue
        if oracle not in oracles:
            errors.append(f"{path}: oracle {oracle!r} is not in the oracle catalog")
        elif oracles[oracle] != disposition:
            errors.append(f"{path}: disposition {disposition} conflicts with oracle {oracle} class {oracles[oracle]}")
        if not str(row["reason"]).strip():
            errors.append(f"{path}: empty reason")
        if disposition in ("keep", "port"):
            tests = int(row["tests"])  # type: ignore[call-overload]
            if path in actual and actual[path] < tests:
                errors.append(f"{path}: collects {actual[path]} test functions, floor {tests}")
            oracle_tests += tests
            if disposition == "keep":
                keep_rows += 1
            else:
                port_rows += 1

    by_path = {str(r["path"]): str(r["disposition"]) for r in rows}
    for importer, targets in sorted((imports or {}).items()):
        if by_path.get(importer) == "drop":
            continue
        for target in sorted(targets):
            if by_path.get(target) == "drop":
                errors.append(f"{importer} imports {target}, which is marked drop")

    if len(rows) < min_rows:
        errors.append(f"inventory has {len(rows)} rows, floor {min_rows}")
    if keep_rows < min_keep_rows:
        errors.append(f"inventory has {keep_rows} keep rows, floor {min_keep_rows}")
    if port_rows < min_port_rows:
        errors.append(f"inventory has {port_rows} port rows, floor {min_port_rows}")
    if oracle_tests < min_oracle_tests:
        errors.append(f"keep and port rows total {oracle_tests} test functions, floor {min_oracle_tests}")
    return errors


def check_tag() -> list[str]:
    result = subprocess.run(
        ("git", "rev-parse", "-q", "--verify", f"refs/tags/{X1_TAG}^{{commit}}"),
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return [f"tag {X1_TAG} is not present locally; run git fetch origin tag {X1_TAG}"]
    if result.stdout.strip() != X1_TAG_COMMIT:
        return [f"tag {X1_TAG} points at {result.stdout.strip()}, expected {X1_TAG_COMMIT}"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument("--require-tag", action="store_true", help="also verify the pre-x1 tag locally")
    args = parser.parse_args(argv)

    errors: list[str] = []
    if args.table.is_symlink() or not args.table.is_file():
        errors.append(f"{args.table} is missing or a symlink")
        text = ""
    else:
        text = args.table.read_text(encoding="utf-8")
    actual, discover_errors = discover()
    errors.extend(discover_errors)
    if text:
        errors.extend(check(text, actual, imports=test_imports()))
    if args.require_tag:
        errors.extend(check_tag())
    if errors:
        for err in errors:
            print(f"x1-cockpit-inventory: FAIL: {err}", file=sys.stderr)
        return 1
    rows = parse_rows(text)
    print(
        f"x1-cockpit-inventory: PASS: {len(rows)} rows cover {len(actual)} legacy test files "
        f"(floor {X1_MIN_ROWS})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
