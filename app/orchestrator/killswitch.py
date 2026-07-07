"""WP5 — the global kill switch (sec-arch §4 L7; SEC-04; Inv 1).

``killswitch`` performs instant containment in a fixed, fail-closed order, HELD UNDER the
shared state lock so a concurrent ``arm_auto_mode`` cannot interleave between the HALT flag
and the mode drop (SEC-04 race fix):
  1. **set the filesystem HALT sentinel** (SEC-04) — a durable flag at a CONFIGURABLE path
     (``default_halt_path`` / injectable) that the future WP7 ``OrderGateway`` checks
     SYNCHRONOUSLY immediately before every broker submit, AND that ``arm_auto_mode``
     refuses to arm while set. Setting it first means even a wedged event loop cannot slip an
     order out after the switch is pulled.
  2. **cancel unapproved / in-flight proposals** (via an injected cancel hook — the order
     store lands in WP7; the mechanism + audit are real now).
  3. **disarm auto modes** — drop to ``review_live`` via the ladder (never a re-arm).

An ``AuditEntry`` is written (Inv 6). MCP disconnect (the §4 L7 extension) is a WP7 broker
concern and is intentionally out of this deterministic core.

The HALT primitives live in ``halt.py`` (re-exported here for the WP7 gateway + tests) so
this module and ``mode_ladder`` can share them without a circular import.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from config.settings import Mode

from .halt import clear_halt, default_halt_path, halt_is_set, set_halt
from .mode_ladder import ModeLadder
from .state import EVENT_KILLSWITCH, OrchestratorState, SystemEvent

__all__ = [
    "KillResult", "killswitch",
    "default_halt_path", "set_halt", "halt_is_set", "clear_halt",
]


@dataclass
class KillResult:
    halt_path: Path
    cancelled_count: int
    mode_after: Mode
    event: SystemEvent


def killswitch(st: OrchestratorState, *, actor: str, reason: str = "manual killswitch",
               clock=None, halt_path: Path | str | None = None,
               cancel_hook: Callable[[], int] | None = None) -> KillResult:
    """Instant containment. Ordering is load-bearing: set-flag -> cancel-in-flight ->
    disarm (SEC-04). The whole sequence runs under ``st.lock`` so an in-flight arm cannot
    land an auto mode at a higher audit seq than this drop."""
    clock = clock or st.clock
    now = clock.now()
    path = Path(halt_path) if halt_path is not None else default_halt_path()

    with st.lock:
        # 1. durable HALT flag FIRST — the gateway refuses to submit + arm refuses to arm
        #    once it is set.
        set_halt(path, reason=reason)
        # 2. cancel unapproved / in-flight proposals (WP7 supplies the real hook).
        cancelled = int(cancel_hook() if cancel_hook is not None else 0)
        # 3. disarm auto modes -> review_live (never a re-arm).
        mode_after = ModeLadder(st).drop_to_review_live(
            f"killswitch: {reason}", clock=clock, actor=actor, kind="killswitch"
        )
        st.audit.append(
            EVENT_KILLSWITCH, actor=actor,
            payload={"reason": reason, "halt_path": str(path), "cancelled": cancelled,
                     "mode_after": mode_after.value},
        )

    event = SystemEvent("#system", "killswitch", None, reason, mode_after.value, now)
    return KillResult(halt_path=path, cancelled_count=cancelled, mode_after=mode_after, event=event)
