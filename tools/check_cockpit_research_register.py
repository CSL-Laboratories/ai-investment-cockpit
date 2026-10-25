"""Offline checker for the Cockpit living research register.

Does not read csl-research, credentials, or the network. Does not change
digest pins. Exit 0 on PASS.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTER = ROOT / "docs" / "replacement" / "amendments" / "RESEARCH-REGISTER.md"
BEGIN = "<!-- cockpit-research-register:begin -->"
END = "<!-- cockpit-research-register:end -->"
REQUIRED_PARENTS = tuple(f"CSL-{n}" for n in range(511, 523))
STATUSES = frozenset({"open", "findings-published", "proposed", "accepted"})
HEADER = ("parent", "linear", "research_record", "status")
LINK = re.compile(r"\[([^\]]+)\]\((https://linear\.app/csl-laboratories/issue/(CSL-\d+)(?:/[^)]*)?)\)")
RECORD = re.compile(r"^submissions/[A-Za-z0-9._-]+/$")
ROW = re.compile(r"^\| (.+) \|$")


class RegisterError(Exception):
    """Living register failed a structural check."""


def _cells(line: str) -> tuple[str, ...]:
    match = ROW.match(line.strip())
    if match is None:
        raise RegisterError(f"not a table row: {line!r}")
    return tuple(part.strip() for part in match.group(1).split("|"))


def parse_table(text: str) -> list[tuple[str, str, str, str]]:
    start = text.find(BEGIN)
    stop = text.find(END)
    if start < 0 or stop < 0 or stop <= start:
        raise RegisterError("missing cockpit-research-register markers")
    block = text[start + len(BEGIN) : stop].strip()
    lines = [line for line in block.splitlines() if line.strip()]
    if len(lines) < 2:
        raise RegisterError("register table is empty")
    header = tuple(cell.lower() for cell in _cells(lines[0]))
    if header != HEADER:
        raise RegisterError(f"register columns must be {HEADER}, got {header}")
    divider = _cells(lines[1])
    if len(divider) != len(HEADER) or any(
        not re.fullmatch(r"-{3,}", cell) for cell in divider
    ):
        raise RegisterError("second table row must be a four-column markdown divider")
    rows: list[tuple[str, str, str, str]] = []
    for line in lines[2:]:
        cells = _cells(line)
        if len(cells) != 4:
            raise RegisterError(f"row must have 4 cells: {line!r}")
        rows.append((cells[0], cells[1], cells[2], cells[3]))
    return rows


def validate(text: str) -> None:
    rows = parse_table(text)
    parents = tuple(row[0] for row in rows)
    if parents != REQUIRED_PARENTS:
        raise RegisterError(
            "parents must be CSL-511..CSL-522 in order; "
            f"got {parents}"
        )
    for parent, linear, record, status in rows:
        if status not in STATUSES:
            raise RegisterError(f"{parent}: status {status!r} is not allowed")
        link = LINK.fullmatch(linear)
        if link is None:
            raise RegisterError(f"{parent}: linear must be a Linear issue markdown link")
        if link.group(3) != parent:
            raise RegisterError(f"{parent}: linear URL parent is {link.group(3)}")
        if record != "none" and RECORD.fullmatch(record) is None:
            raise RegisterError(
                f"{parent}: research_record must be 'none' or submissions/<record_id>/"
            )
        if status == "findings-published" and record == "none":
            raise RegisterError(f"{parent}: findings-published requires a research_record")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", type=Path, default=DEFAULT_REGISTER)
    args = parser.parse_args(argv)
    try:
        validate(args.register.read_text(encoding="utf-8"))
    except (OSError, RegisterError) as exc:
        sys.stderr.write(f"cockpit-research-register: FAIL: {exc}\n")
        return 1
    sys.stdout.write("cockpit-research-register: PASS\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
