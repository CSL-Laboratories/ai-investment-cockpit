"""WP5 — the out-of-band HALT sentinel (SEC-04).

A durable filesystem flag that the future WP7 ``OrderGateway`` checks SYNCHRONOUSLY before
every broker submit, and that the mode ladder consults before arming an auto mode. Kept in
its OWN module (no orchestrator-internal imports) so both ``killswitch.py`` and
``mode_ladder.py`` can use it without a circular import.

Path precedence: an explicit ``COCKPIT_HALT_PATH`` env override (operator-configurable, and
the test-isolation seam) -> per-user app data (Windows ``LOCALAPPDATA`` / POSIX
``XDG_STATE_HOME``) -> the system temp dir.
"""
from __future__ import annotations

import os
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
