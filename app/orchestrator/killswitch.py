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
  3. **disconnect the MCP session** (via an injected disconnect hook — W12/SEC-04; no
     ``brokers/`` import here, the hook is supplied by the caller that owns the broker).
  4. **disarm auto modes** — drop to ``review_live`` via the ladder (never a re-arm).

An ``AuditEntry`` is written (Inv 6). Both hooks default to a no-op so existing callers
(and every pre-W12 test) are unaffected; disconnect failures are swallowed (best-effort —
containment must not be blocked by a broker-side disconnect error) but recorded in the
audit payload.

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
               cancel_hook: Callable[[], int] | None = None,
               disconnect_hook: Callable[[], None] | None = None) -> KillResult:
    """Instant containment. Ordering is load-bearing: set-flag -> cancel-in-flight ->
    disconnect-MCP -> disarm (SEC-04). The whole sequence runs under ``st.lock`` so an
    in-flight arm cannot land an auto mode at a higher audit seq than this drop.

    ``disconnect_hook`` (W12) is supplied by the caller that owns the broker/MCP session —
    this module never imports ``brokers/`` (keeps the deterministic core dependency-light).
    A disconnect failure is swallowed (containment must not stall on a broker-side error)
    but recorded in the audit payload; it never blocks the mode drop."""
    clock = clock or st.clock
    now = clock.now()
    path = Path(halt_path) if halt_path is not None else default_halt_path()

    with st.lock:
        # 1. durable HALT flag FIRST — the gateway refuses to submit + arm refuses to arm
        #    once it is set.
        set_halt(path, reason=reason)
        # 2. cancel unapproved / in-flight proposals (WP7 supplies the real hook).
        cancelled = int(cancel_hook() if cancel_hook is not None else 0)
        # 3. disconnect the MCP session (W12/SEC-04) — best-effort, never blocks containment.
        disconnected, disconnect_error = False, None
        if disconnect_hook is not None:
            try:
                disconnect_hook()
                disconnected = True
            except Exception as exc:  # noqa: BLE001 - containment must not stall on this
                disconnect_error = str(exc)
        # 4. disarm auto modes -> review_live (never a re-arm).
        mode_after = ModeLadder(st).drop_to_review_live(
            f"killswitch: {reason}", clock=clock, actor=actor, kind="killswitch"
        )
        st.audit.append(
            EVENT_KILLSWITCH, actor=actor,
            payload={"reason": reason, "halt_path": str(path), "cancelled": cancelled,
                     "mcp_disconnected": disconnected, "mcp_disconnect_error": disconnect_error,
                     "mode_after": mode_after.value},
        )

    event = SystemEvent("#system", "killswitch", None, reason, mode_after.value, now)
    return KillResult(halt_path=path, cancelled_count=cancelled, mode_after=mode_after, event=event)
