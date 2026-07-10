"""WP5 — the out-of-band HALT sentinel (SEC-04).

A durable filesystem flag that the future WP7 ``OrderGateway`` checks SYNCHRONOUSLY before
every broker submit, and that the mode ladder consults before arming an auto mode. Kept in
its OWN module (no orchestrator-internal imports) so both ``killswitch.py`` and
``mode_ladder.py`` can use it without a circular import.

Path precedence: an explicit ``COCKPIT_HALT_PATH`` env override (operator-configurable, and
the test-isolation seam) -> per-user app data (Windows ``LOCALAPPDATA`` / POSIX
``XDG_STATE_HOME``) -> the system temp dir.

W12 (SEC-04) — the OUT-OF-BAND trigger: ``python -m orchestrator.halt set`` writes the
sentinel from a SEPARATE OS process, so it lands even when the orchestrator's own event
loop is wedged, GIL-starved, or mid-``auto_full``-burst (a `/killswitch` request queued
*behind* the loop it should preempt is not out-of-band; a second process touching the same
file is). This is the file-drop-watcher equivalent — no watcher thread is needed because the
gateway already checks the sentinel synchronously before every submit (§4 L7): a plain
``set_halt`` from any process is immediately load-bearing on the NEXT submit attempt. The
hard stop remains **stopping the Windows service (NSSM)**, which additionally drops the MCP
connection outright, independent of this flag.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def default_halt_path() -> Path:
    """The configured HALT sentinel path. ``COCKPIT_HALT_PATH`` wins if set (SEC-04: the
    sentinel path is deliberately configurable)."""
    explicit = os.environ.get("COCKPIT_HALT_PATH")
    if explicit:
        return Path(explicit)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME") \
        or tempfile.gettempdir()
    return Path(base) / "ai-investment-cockpit" / "HALT"


def set_halt(path: Path | str, reason: str = "killswitch") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{datetime.now(timezone.utc).isoformat()} {reason}\n", encoding="utf-8")
    return path


def halt_is_set(path: Path | str) -> bool:
    """Whether the HALT sentinel is present. (The order gateway treats an UNreadable flag as
    set — fail closed — but that gateway-side policy lives in WP7; this is the plain probe.)"""
    return Path(path).exists()


def clear_halt(path: Path | str) -> None:
    Path(path).unlink(missing_ok=True)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.halt",
        description="Out-of-band HALT sentinel control (SEC-04). Run from a SEPARATE "
                    "process/terminal (or a file-drop trigger) — independent of the "
                    "orchestrator's own event loop, so it lands even if that loop is "
                    "wedged or mid-burst. The hard stop is still 'nssm stop <service>'.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    p_set = sub.add_parser("set", help="set the HALT sentinel")
    p_set.add_argument("--reason", default="manual out-of-band halt")
    p_set.add_argument("--path", default=None, help="override the sentinel path")
    p_clear = sub.add_parser("clear", help="clear the HALT sentinel")
    p_clear.add_argument("--path", default=None)
    p_status = sub.add_parser("status", help="report whether the sentinel is set")
    p_status.add_argument("--path", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    path = Path(args.path) if args.path else default_halt_path()
    if args.action == "set":
        set_halt(path, reason=args.reason)
        print(f"HALT set at {path}")
    elif args.action == "clear":
        clear_halt(path)
        print(f"HALT cleared at {path}")
    else:
        print("SET" if halt_is_set(path) else "CLEAR")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint, not under test
    sys.exit(main())
