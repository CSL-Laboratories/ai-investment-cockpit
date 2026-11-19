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
     store lands in WP7; the mechanism + audit are real now). A cancel hook that RAISES is
     caught — including a ``BaseException`` (``asyncio.CancelledError`` / ``KeyboardInterrupt`` /
     ``SystemExit``), NOT just ``Exception`` (R2-S3) — with ``cancelled=-1``,
     ``cancel_failed=True`` in the audit row; the disconnect, disarm and EVENT_KILLSWITCH append
     STILL run — a botched cancel never leaves an auto mode armed or the kill unaudited (SEC-04
     fail closed). We never re-raise: a KeyboardInterrupt mid-kill must still let the kill finish.
  3. **disarm auto modes** — drop to ``review_live`` via the ladder (never a re-arm).

An ``AuditEntry`` is written (Inv 6). MCP DISCONNECT (the §4 L7 extension) is now performed
between cancel (2) and disarm (3) via an INJECTED ``disconnect_hook`` (W12 SEAM 4) — this
module never imports ``brokers/`` (Inv 1 boundary; the live disconnect is wired by the WP7/W17
composition root). A ``disconnect_hook`` that RAISES must not prevent the mode drop: we catch a
``BaseException`` (an ``asyncio.CancelledError`` during the W17 live-loop teardown is EXACTLY the
killswitch crisis — a plain ``except Exception`` would let it escape ``st.lock``), record
``mcp_disconnected=False``, and STILL disarm to ``review_live`` (fail closed — a failed
disconnect never leaves an auto mode armed).

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
    mcp_disconnected: bool = False


def killswitch(st: OrchestratorState, *, actor: str, reason: str = "manual killswitch",
               clock=None, halt_path: Path | str | None = None,
               cancel_hook: Callable[[], int] | None = None,
               disconnect_hook: Callable[[], None] | None = None) -> KillResult:
    """Instant containment. Ordering is load-bearing: set-flag -> cancel-in-flight ->
    MCP-disconnect -> disarm (SEC-04). The whole sequence runs under ``st.lock`` so an
    in-flight arm cannot land an auto mode at a higher audit seq than this drop.

    ``disconnect_hook`` (W12 SEAM 4) is the INJECTED MCP/broker disconnect — called AFTER
    cancel and BEFORE disarm. It is optional (``None`` -> no live connection to drop, e.g. the
    paper/in-process path). A hook that RAISES is caught: ``mcp_disconnected`` is recorded
    ``False`` and the drop to ``review_live`` STILL runs (fail closed — a botched disconnect
    never leaves an auto mode armed). No ``brokers/`` import here: the live hook is wired by
    the composition root (Inv 1 boundary).

    EVERY STEP RUNS even when an earlier one fails (SEC-04 fail closed). A HALT write, disarm or
    audit that raises (``BaseException``) is recorded and does NOT skip the later steps; each
    such failure also installs the sticky in-process denial
    (``st.admit_integrity_failure``) so this process's gateway refuses every submit. The audit
    row records ``halt_set`` and ``failed_steps``. After every step was attempted, the FIRST such
    failure is re-raised to the caller (notes name the others); the hooks keep their
    record-and-continue contract above."""
    clock = clock or st.clock
    now = clock.now()
    path = Path(halt_path) if halt_path is not None else default_halt_path()
    failures: list[tuple[str, BaseException]] = []

    def _record(step: str, exc: BaseException, *, deny: bool) -> None:
        failures.append((step, exc))
        if deny:
            try:
                st.admit_integrity_failure(f"killswitch {step} failed: {type(exc).__name__}")
            except BaseException as deny_exc:   # noqa: BLE001 - the kill must still finish
                failures.append(("deny", deny_exc))

    with st.lock:
        # 1. durable HALT flag FIRST — the gateway refuses to submit + arm refuses to arm
        #    once it is set. A write that raises (unwritable parent, full disk) must NOT skip
        #    cancel/disconnect/disarm/audit: record it, deny in-process, continue, re-raise below.
        halt_set = False
        try:
            set_halt(path, reason=reason)
            halt_set = True
        except BaseException as exc:   # noqa: BLE001 - containment continues; re-raised below
            _record("halt", exc, deny=True)
        # 2. cancel unapproved / in-flight proposals (WP7 supplies the real hook). A raise here
        #    (the WP7 order-store DB hook is the likeliest to throw mid-crisis) must NOT abort the
        #    kill: catch, record cancelled=-1 + cancel_failed=True, and STILL disconnect, disarm and
        #    audit below (fail closed — a botched cancel never leaves an auto mode armed or the kill
        #    unaudited). Mirrors the disconnect_hook catch-and-continue pattern (SEC-04).
        cancel_failed = False
        if cancel_hook is not None:
            try:
                cancelled = int(cancel_hook())
            except BaseException:   # noqa: BLE001 - R2-S3: catch BaseException, NOT Exception
                # A CancelledError / KeyboardInterrupt / SystemExit raised by the hook (exactly the
                # W17 live-async-disconnect teardown case) must NOT escape st.lock and skip the
                # disarm + audit below. Record the failure and CONTINUE to complete containment;
                # do NOT re-raise — a KeyboardInterrupt mid-kill must still let the kill finish.
                cancelled = -1
                cancel_failed = True
        else:
            cancelled = 0
        # 3. MCP DISCONNECT (injected hook; §4 L7). AFTER cancel, BEFORE disarm. A raise here
        #    must NOT block the mode drop — catch, mark not-disconnected, still disarm.
        mcp_disconnected = False
        if disconnect_hook is not None:
            try:
                disconnect_hook()
                mcp_disconnected = True
            except BaseException:   # noqa: BLE001 - R2-S3: BaseException, NOT Exception
                # asyncio.CancelledError during loop teardown (the W17 live disconnect crisis) is a
                # BaseException and would otherwise ESCAPE st.lock, skipping the disarm + audit and
                # leaving auto armed + the kill unaudited. Fail closed: record not-disconnected and
                # proceed to disarm regardless; do NOT re-raise (the kill must complete).
                mcp_disconnected = False
        # 4. disarm auto modes -> review_live (never a re-arm). A raise must not skip the audit.
        mode_after = None
        try:
            mode_after = ModeLadder(st).drop_to_review_live(
                f"killswitch: {reason}", clock=clock, actor=actor, kind="killswitch"
            )
        except BaseException as exc:   # noqa: BLE001 - the audit still runs; re-raised below
            _record("disarm", exc, deny=True)
        # 5. audit the kill, including every step that failed.
        try:
            st.audit.append(
                EVENT_KILLSWITCH, actor=actor,
                payload={"reason": reason, "halt_path": str(path), "cancelled": cancelled,
                         "cancel_failed": cancel_failed,
                         "mcp_disconnected": mcp_disconnected,
                         "mode_after": mode_after.value if mode_after is not None else None,
                         "halt_set": halt_set,
                         "failed_steps": [step for step, _ in failures],
                         # type names only: an exception's text may carry a path or a
                         # connection string, and this row is the signed audit.
                         "errors": {step: type(e).__name__ for step, e in failures}},
            )
        except BaseException as exc:   # noqa: BLE001 - re-raised below with the others
            _record("audit", exc, deny=True)

    if failures:
        first = failures[0][1]
        first.add_note("killswitch attempted every step; failed: "
                       + ", ".join(f"{step} ({type(e).__name__})" for step, e in failures))
        raise first

    event = SystemEvent("#system", "killswitch", None, reason, mode_after.value, now)
    return KillResult(halt_path=path, cancelled_count=cancelled, mode_after=mode_after,
                      event=event, mcp_disconnected=mcp_disconnected)
