"""WP5 — the autonomy-ladder state machine (security-architecture §4 L5; Inv 2/7/8).

The ladder is the lever Discord pulls. This module is the ONLY sanctioned way to move it,
and every move persists a new ``ModeState`` row + its ``AuditEntry`` atomically through
``AuditLog.record_state_change`` (Inv 6). All transitions use the single audit event type
``EVENT_MODE_CHANGE`` (payload ``kind`` distinguishes manual / arm / breaker_drop /
daily_disarm / recover) so the "current mode" is unambiguously the highest-seq mode_change
in the tamper-evident chain, and the performance gate can read the full mode timeline from
that one event type (SEC-20).

Legal adjacency (any other move raises ``IllegalTransition``):
  * ``read_only <-> paper <-> review_live`` — single-rung steps up, reversible.
  * ``review_live -> auto_veto -> auto_full`` — single-rung steps up (auto entry ONLY via
    ``arm_auto_mode``, never a bare ``set_mode``).
  * a **drop DOWN** to any lower rung is always legal (breakers/killswitch/recovery).

An upward move NEVER skips a rung; a downward move may skip. So a breaker drop and recovery
can only ever *reduce* autonomy — never silently grant it (SEC-23 fail-closed).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select

from config.models import runtime_for
from config.settings import Mode
from core.ids import new_id
from schemas.models.audit_entry import AuditEntry
from schemas.models.mode_state import ModeState

from .halt import default_halt_path, halt_is_set
from .market_calendar import session_close
from .state import EVENT_MODE_CHANGE, OrchestratorState

# ladder order (index == rung height)
_LADDER: list[Mode] = [
    Mode.read_only, Mode.paper, Mode.review_live, Mode.auto_veto, Mode.auto_full,
]
_RUNG: dict[Mode, int] = {m: i for i, m in enumerate(_LADDER)}
_AUTO_MODES: frozenset[Mode] = frozenset({Mode.auto_veto, Mode.auto_full})
_LIVE_MODES: frozenset[Mode] = frozenset({Mode.review_live, Mode.auto_veto, Mode.auto_full})
_DECISION_STAGE_ALIASES = ("triage", "deep")


class IllegalTransition(Exception):
    """A ladder move outside the legal adjacency."""


class ExecutionRuntimeError(Exception):
    """SEC-08 — a live/auto mode refused a non-Claude runtime at execution time."""


@dataclass
class ArmResult:
    """Outcome of an ``arm_auto_mode`` attempt. ``armed=False`` means the arm was REFUSED
    (gate not passed, non-Claude decision stage, or illegal rung) and NO state changed."""

    armed: bool
    mode: Mode
    reasons: list[str] = field(default_factory=list)
    row: ModeState | None = None


def _as_mode(m) -> Mode:
    return m if isinstance(m, Mode) else Mode(m)


def legal_transition(src: Mode, dst: Mode) -> bool:
    """Pure adjacency predicate. Equal modes are NOT a transition (returns False)."""
    src, dst = _as_mode(src), _as_mode(dst)
    delta = _RUNG[dst] - _RUNG[src]
    if delta == 1:
        return True          # single-rung step up
    if delta < 0:
        return True          # any drop down
    return False             # equal (0) or skip-up (>=2)


def default_decision_stage_runtimes(settings=None) -> list[str]:
    """Resolve decision roles for this deployment.

    The auto gate below still requires both to be Claude. Passing Settings makes a configured
    Codex paper deployment visibly fail that gate instead of inspecting the legacy YAML default.
    """
    from config.models import configured_decision_runtime

    selected = configured_decision_runtime(settings) if settings is not None else None
    return [
        runtime_for(a) if selected is None else runtime_for(a, selected)
        for a in _DECISION_STAGE_ALIASES
    ]


def assert_execution_runtime(mode, producing_runtime: str) -> None:
    """SEC-08 — refuse non-Claude provenance at every live broker submit.

    Codex parity is intentionally paper-only. This final submit-time backstop means a persisted
    review-live mode or stale proposal cannot turn the provider switch into live access.
    """
    if _as_mode(mode) in _LIVE_MODES and producing_runtime != "claude":
        raise ExecutionRuntimeError(
            f"live/auto mode refuses non-claude execution runtime {producing_runtime!r} "
            "(SEC-08 / Inv 2)"
        )


class ModeLadder:
    def __init__(self, st: OrchestratorState) -> None:
        self.st = st

    # ---- read current mode from the verified chain ------------------------------------
    def _latest_entry(self, s):
        return s.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == EVENT_MODE_CHANGE)
            .order_by(AuditEntry.seq.desc())
            .limit(1)
        ).scalar_one_or_none()

    def current(self) -> Mode:
        """The live autonomy mode is the HMAC-SIGNED ``payload['to']`` of the latest
        mode_change entry — NEVER the mutable ``mode_state.mode`` column, and only from a
        FULLY-VERIFIED chain. A local DB writer without the HMAC key must not be able to flip
        or resurrect a mode. Three tamper classes, all fail CLOSED to ``read_only`` (the safe
        floor, no order authority):
          * mutable ``mode_state.mode`` edit -> caught by the row<->signed-payload reconcile;
          * forged/edited signing payload -> caught by ``verify_entry`` (and ``verify_chain``);
          * TRUNCATION — deleting newer disarm/drop rows so an old auto-arm reads as 'latest'
            -> caught by ``verify_chain`` (seq-gap + the SEC-11 signed watermark), which the
            mode oracle MUST consult for parity with the performance gate (the reproduced
            bypass exploited exactly this asymmetry). Deferred defense-in-depth: an out-of-DB
            anchor (``AuditLog.verify_against_anchor``, git/#audit-log) closes truncation by a
            KEY-HOLDER too (SEC-11/SEC-20); it lands with the WP6 external witness."""
        # Verify the whole chain first: catches truncation/rollback the per-entry HMAC cannot.
        v = self.st.audit.verify_chain()
        if not v.ok:
            return Mode.read_only
        with self.st.session_factory() as s:
            # Gate-parity rebind (closes the verify->read TOCTOU): a concurrent truncation in
            # the window between verify_chain and this read would move the head; require the
            # read session's head to match the verified head, else fail closed to read_only.
            head = s.execute(select(func.max(AuditEntry.seq))).scalar_one() or 0
            if head != v.head_seq:
                return Mode.read_only
            entry = self._latest_entry(s)
            if entry is None:
                return self.st.settings.mode
            if not self.st.audit.verify_entry(entry):
                return Mode.read_only                 # tampered signing entry
            to = (entry.payload or {}).get("to")
            if not to:
                return Mode.read_only
            row_id = (entry.refs or {}).get("row_id")
            row = s.get(ModeState, row_id) if row_id else None
            if row is not None and row.mode != to:
                return Mode.read_only                 # mutable row diverges from signed record
            m = _as_mode(to)
            # SEC-11: an AUTO mode-read must ALSO clear the external witness. A replayed
            # watermark can resurrect an old (validly-signed) auto-arm that verify_chain()
            # accepts; only the out-of-DB anchor catches that rollback. When an anchor store is
            # wired (production; compute_gate makes it a hard prerequisite to ever arm auto),
            # require the verified head to still cover the last anchor, else fail closed to
            # read_only. Non-auto rungs carry no order authority, so they do not need the witness.
            if m in _AUTO_MODES and self.st.anchor_store is not None:
                anchor = self.st.anchor_store.latest()
                if anchor is None or not self.st.audit.verify_against_anchor(anchor[0], anchor[1]).ok:
                    return Mode.read_only
            return m

    def current_row(self) -> ModeState | None:
        """The latest ModeState row (for armed_at/until/by display only). The authoritative
        MODE is ``current()`` (the signed payload); this row is not trusted for the mode."""
        with self.st.session_factory() as s:
            entry = self._latest_entry(s)
            if entry is None:
                return None
            row_id = (entry.refs or {}).get("row_id")
            row = s.get(ModeState, row_id) if row_id else None
            if row is not None:
                s.expunge(row)
            return row

    # ---- persistence -------------------------------------------------------------------
    def _persist(self, src, target, kind, actor, reason, clock, *,
                 armed_at=None, armed_until=None, armed_by=None, extra=None) -> ModeState:
        now = clock.now()
        row = ModeState(
            id=new_id("mode"), mode=target.value,
            armed_at=armed_at, armed_until=armed_until, armed_by=armed_by,
            reason=reason, created_at=now,
        )
        payload = {"kind": kind, "from": src.value, "to": target.value, "reason": reason}
        if armed_until is not None:
            payload["armed_until"] = armed_until.isoformat()
        if extra:
            payload.update(extra)
        self.st.audit.record_state_change(row, EVENT_MODE_CHANGE, actor=actor, payload=payload)
        return row

    # ---- manual (non-auto) transitions -------------------------------------------------
    def set_mode(self, target, *, actor, reason=None, clock=None) -> Mode:
        """Move on the non-auto part of the ladder (read_only/paper/review_live). Entering
        an auto mode is refused here — use ``arm_auto_mode`` (it enforces the gate + pin)."""
        clock = clock or self.st.clock
        target = _as_mode(target)
        with self.st.lock:
            src = self.current()
            if target == src:
                return src                                   # idempotent no-op
            if target in _AUTO_MODES:
                raise IllegalTransition(f"enter {target.value} via arm_auto_mode, not set_mode")
            if target is Mode.review_live:
                from config.models import configured_decision_runtime

                runtime = configured_decision_runtime(self.st.settings)
                if runtime != "claude":
                    raise IllegalTransition(
                        "review_live requires COCKPIT_DECISION_RUNTIME=claude; "
                        f"configured runtime is {runtime!r}"
                    )
            if not legal_transition(src, target):
                raise IllegalTransition(f"{src.value} -> {target.value} is not a legal ladder move")
            self._persist(src, target, "manual", actor, reason or "manual mode change", clock)
        return target

    # ---- gated auto arm ----------------------------------------------------------------
    def arm_auto_mode(self, target, decision_stage_runtimes=None, gate_result=None,
                      operator=None, clock=None, decision_stage_specs=None) -> ArmResult:
        """Arm ``auto_veto`` / ``auto_full``. REFUSES (returns ``armed=False``, nothing
        persisted) unless: target is an auto mode reachable by a single-rung step up, the
        performance gate passed for exactly this target (SEC-20), every decision-stage runtime
        is ``claude`` (Inv 2), AND every decision-stage job resolves to a tool/MCP-STARVED argv
        (SEC-06 / W11 — deny-by-default on the actual constructed argv). On success, stamps
        armed_at / armed_until (disarm at market close) / armed_by and persists via
        record_state_change."""
        clock = clock or self.st.clock
        target = _as_mode(target)
        # Whole read-modify-write under the lock so a concurrent killswitch cannot interleave
        # between our checks and the persist and leave an auto mode armed while HALT is set.
        with self.st.lock:
            src = self.current()
            reasons: list[str] = []

            # SEC-04: refuse to arm while the killswitch HALT sentinel exists. Re-checked here
            # under the lock so the killswitch->arm race is closed (arm never wins the mode
            # while containment is active); the operator must clear HALT before re-arming.
            if halt_is_set(default_halt_path()):
                reasons.append("killswitch HALT sentinel is set; clear it before arming (SEC-04)")

            if target not in _AUTO_MODES:
                reasons.append(f"{target.value} is not an auto mode")
            elif not legal_transition(src, target):
                reasons.append(f"{src.value} -> {target.value} is not a legal ladder move")

            if gate_result is None or not getattr(gate_result, "unlockable", False):
                reasons.append("performance gate did not pass")
            else:
                gt = getattr(gate_result, "target", None)
                if gt not in (target.value, target):
                    reasons.append(f"performance gate target {gt!r} != requested {target.value}")

            runtimes = (default_decision_stage_runtimes(self.st.settings)
                        if decision_stage_runtimes is None else list(decision_stage_runtimes))
            non_claude = sorted({r for r in runtimes if r != "claude"})
            if non_claude:
                reasons.append(f"decision-stage runtime(s) not claude: {non_claude} (Inv 2)")

            # W11 SEC-06: refuse to arm if any decision-stage job's ACTUAL argv grants a tool or
            # attaches an MCP server (deny-by-default). Defaults to the canonical decision aliases
            # so a wiring that made a decision job tool-bearing is caught at ARM time, not first run.
            from agent_runner import tool_inspector
            specs = (decision_stage_specs if decision_stage_specs is not None
                     else tool_inspector.default_decision_stage_specs(self.st.settings))
            try:
                tool_inspector.assert_all_decision_specs_tool_starved(specs)
            except tool_inspector.ToolStarvationError as exc:
                reasons.append(f"decision-stage job is not tool-starved (SEC-06): {exc}")

            if reasons:
                return ArmResult(armed=False, mode=src, reasons=reasons, row=None)

            armed_at = clock.now()
            armed_until = session_close(armed_at)
            row = self._persist(
                src, target, "arm", operator, f"armed {target.value}", clock,
                armed_at=armed_at, armed_until=armed_until, armed_by=operator,
            )
        return ArmResult(armed=True, mode=target, reasons=[], row=row)

    # ---- downgrades / recovery (never increase autonomy) -------------------------------
    def drop_to_review_live(self, reason, clock=None, *, actor="system",
                            kind="breaker_drop") -> Mode:
        """Drop to ``review_live`` (used by breakers + killswitch). Never raises autonomy:
        from a rung at/below review_live it is a no-op (the caller records its own audit)."""
        clock = clock or self.st.clock
        with self.st.lock:
            src = self.current()
            floor = Mode.review_live
            target = floor if _RUNG[src] > _RUNG[floor] else src
            if target == src:
                return src
            self._persist(src, target, kind, actor, reason, clock)
            return target

    def daily_disarm(self, clock=None, *, actor="scheduler") -> Mode:
        """Disarm auto modes at market close (deliberate friction: a one-tap re-arm is
        required next morning). No-op outside the auto modes."""
        clock = clock or self.st.clock
        with self.st.lock:
            if self.current() not in _AUTO_MODES:
                return self.current()
            return self.drop_to_review_live(
                "daily disarm at market close", clock=clock, actor=actor, kind="daily_disarm"
            )

    def recover(self, prev_state, clock=None, *, actor="system") -> Mode:
        """SEC-23 — recovery from ANY degradation (breaker / usage exhaustion / killswitch)
        lands FAIL-CLOSED on ``review_live`` and requires an explicit daily re-arm. NEVER
        silently restores ``prev_state``; it is recorded only as the mode we refused to
        restore. Never increases autonomy (a no-op if already at/below review_live)."""
        clock = clock or self.st.clock
        prev = _as_mode(prev_state).value if isinstance(prev_state, (Mode, str)) else str(prev_state)
        with self.st.lock:
            src = self.current()
            floor = Mode.review_live
            target = floor if _RUNG[src] > _RUNG[floor] else src
            if target == src:
                return src
            self._persist(
                src, target, "recover", actor,
                f"fail-closed recovery; refused to restore {prev}", clock,
                extra={"refused_restore": prev},
            )
            return target

    # convenience delegate (SEC-08)
    def assert_execution_runtime(self, mode, producing_runtime: str) -> None:
        assert_execution_runtime(mode, producing_runtime)
