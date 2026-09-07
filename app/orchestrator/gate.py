"""WP5 — the performance-gate calculator (SEC-20; draft-2 §graduated autonomy).

Computes whether an auto mode may unlock SOLELY from the VERIFIED, HMAC-anchored audit
chain — never from mutable domain tables. The chain is verified first (``verify_chain()``);
if it does not verify, the gate FAILS CLOSED (``unlockable=False``) regardless of metrics.
A local writer who fabricates 30 "approved" proposals in the proposals table moves nothing
here, because the gate only reads audited events (SEC-20).

``auto_veto`` unlock: >=4 weeks in paper/review_live, >=30 adjudicated proposals, operator
approval rate >=80%, realized max drawdown within the configured limit, and ZERO
bypass-attempt events (a recorded ``hard_check_failed`` / ``resubmit_after_denied`` /
``approval_against_failed_risk`` blocks unlock).

``auto_full`` unlock: everything above PLUS >=4 weeks in ``auto_veto``, veto rate <=5%, and
no breaker-trip events.

Thresholds are a ``GateThresholds`` dataclass (config-tunable via ``from_settings``), not
magic numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from config.settings import Mode, Settings
from schemas.models.audit_entry import AuditEntry

from .state import (
    BYPASS_EVENTS,
    EVENT_ADJUDICATION,
    EVENT_BREAKER_TRIP,
    EVENT_DRAWDOWN_MARK,
    EVENT_MODE_CHANGE,
    OrchestratorState,
)

_WEEK_SECONDS = 7 * 24 * 3600
_LOWER_MODES = {Mode.paper.value, Mode.review_live.value}


@dataclass(frozen=True)
class GateThresholds:
    min_weeks_lower: float = 4.0
    min_adjudicated: int = 30
    min_approval_rate: float = 0.80
    max_drawdown_limit: Decimal = Decimal("0.20")   # 20% realized max drawdown ceiling
    min_weeks_auto_veto: float = 4.0
    max_veto_rate: float = 0.05

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "GateThresholds":
        """Read overrides from Settings where present; fall back to the defaults. (Settings
        carries no gate knobs yet — this is the tunable seam so they never become magic
        numbers buried in the calculator.)"""
        s = settings
        if s is None:
            return cls()
        return cls(
            min_weeks_lower=float(getattr(s, "gate_min_weeks_lower", cls.min_weeks_lower)),
            min_adjudicated=int(getattr(s, "gate_min_adjudicated", cls.min_adjudicated)),
            min_approval_rate=float(getattr(s, "gate_min_approval_rate", cls.min_approval_rate)),
            max_drawdown_limit=Decimal(str(getattr(s, "gate_max_drawdown", cls.max_drawdown_limit))),
            min_weeks_auto_veto=float(getattr(s, "gate_min_weeks_auto_veto", cls.min_weeks_auto_veto)),
            max_veto_rate=float(getattr(s, "gate_max_veto_rate", cls.max_veto_rate)),
        )


@dataclass
class GateResult:
    unlockable: bool
    target: str
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def _to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _dec(x) -> Decimal:
    """Parse a gate metric. BUG-129: garbage used to read as Decimal("0") — a malformed
    drawdown payload then read as ZERO drawdown against the max-drawdown ceiling (fail
    open). Unparseable input now reads as NaN, and every consumer treats NaN as
    fail-closed (refuse), never as a passing zero."""
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


def _weeks_in_modes(mode_events: list[tuple[datetime, str]], now: datetime, modes: set[str]) -> float:
    """Sum wall-time (in weeks) the ladder spent in ``modes``, from the mode_change timeline
    (each event active until the next one, the last until ``now``). Clock skew is clamped to
    non-negative durations."""
    total = 0.0
    for i, (start, mode) in enumerate(mode_events):
        end = mode_events[i + 1][0] if i + 1 < len(mode_events) else now
        if mode in modes:
            total += max(0.0, (end - start).total_seconds())
    return total / _WEEK_SECONDS


def compute_gate(st: OrchestratorState, target, *, now: datetime | None = None,
                 thresholds: GateThresholds | None = None) -> GateResult:
    target_mode = target if isinstance(target, Mode) else Mode(target)
    tgt = target_mode.value
    th = thresholds or GateThresholds.from_settings(st.settings)
    now = now or st.clock.now()
    now_naive = _to_naive_utc(now)

    if target_mode not in (Mode.auto_veto, Mode.auto_full):
        return GateResult(False, tgt, [f"{tgt} is not an auto mode"], {})

    # SEC-20: verify the chain FIRST; fail closed on any tamper/truncation.
    v = st.audit.verify_chain()
    if not v.ok:
        return GateResult(False, tgt, [f"audit chain failed verification: {v.error}"],
                          {"chain_ok": False})

    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
        # SEC-20: BIND the counted set to the verified head. ``verify_chain()`` ran in a
        # SEPARATE transaction, so rows COMMITTED in the verify->count window would otherwise
        # be counted unverified (the reproduced TOCTOU). Re-verify the exact rows we count —
        # contiguous seq, linked prev_hash, HMAC-valid — and confirm the head still matches;
        # a forged row (bad HMAC) or a moved head fails the gate CLOSED.
        prev = None
        for i, r in enumerate(rows):
            if r.seq != i + 1 or r.prev_hash != prev or not st.audit.verify_entry(r):
                return GateResult(False, tgt,
                                  [f"audit chain re-verification failed at seq {r.seq}"],
                                  {"chain_ok": False})
            prev = r.entry_hash
        if (rows[-1].seq if rows else 0) != v.head_seq:
            return GateResult(False, tgt, ["audit head moved during gate computation"],
                              {"chain_ok": False})

    # SEC-11 (P0-auto prerequisite): the in-DB signed watermark is REPLAYABLE — a keyless writer
    # who captured a past (seq, sig) can DELETE the tail and replay it, and verify_chain() cannot
    # distinguish that rollback from the genuine past. The ONLY closure is the EXTERNAL out-of-DB
    # witness. An auto unlock therefore REQUIRES a wired anchor store whose recorded head the
    # verified chain still covers: a missing store, an empty store, or a verified head BELOW the
    # anchor all FAIL CLOSED (no auto_veto/auto_full can arm without it).
    if st.anchor_store is None:
        return GateResult(False, tgt,
                          ["no external audit anchor configured (SEC-11 P0-auto prerequisite)"],
                          {"chain_ok": True, "anchor_ok": False})
    anchor = st.anchor_store.latest()
    if anchor is None:
        return GateResult(False, tgt,
                          ["external audit anchor is empty; post a head anchor before arming (SEC-11)"],
                          {"chain_ok": True, "anchor_ok": False})
    av = st.audit.verify_against_anchor(anchor[0], anchor[1])
    if not av.ok:
        return GateResult(False, tgt,
                          [f"external anchor rollback/truncation detected: {av.error}"],
                          {"chain_ok": True, "anchor_ok": False, "anchor_seq": anchor[0]})

    adjudications = [r for r in rows if r.event_type == EVENT_ADJUDICATION]
    decisions = [(r.payload or {}).get("decision") for r in adjudications]
    adjudicated = len(adjudications)
    approvals = sum(1 for d in decisions if d == "approve")
    vetoes = sum(1 for d in decisions if d == "veto")
    approval_rate = (approvals / adjudicated) if adjudicated else 0.0
    veto_rate = (vetoes / adjudicated) if adjudicated else 0.0

    bypass_count = sum(1 for r in rows if r.event_type in BYPASS_EVENTS)
    breaker_trip_count = sum(1 for r in rows if r.event_type == EVENT_BREAKER_TRIP)
    # BUG-129: a malformed drawdown payload must BLOCK the unlock, not read as zero.
    # NaN is not "no drawdown" — it is "we cannot prove the drawdown", and an auto
    # unlock requires proof.
    drawdown_vals: list[Decimal] = []
    for r in rows:
        if r.event_type != EVENT_DRAWDOWN_MARK:
            continue
        dd = _dec((r.payload or {}).get("drawdown", 0))
        if not dd.is_finite():
            return GateResult(False, tgt,
                              ["malformed drawdown payload on the verified chain "
                               "(fail closed: cannot prove drawdown is within limits)"],
                              {"chain_ok": True, "anchor_ok": True})
        drawdown_vals.append(dd)
    max_drawdown = max(drawdown_vals, default=Decimal("0"))

    mode_events: list[tuple[datetime, str]] = []
    for r in rows:
        if r.event_type != EVENT_MODE_CHANGE:
            continue
        p = r.payload or {}
        ts = p.get("_ts")
        to = p.get("to")
        if ts and to:
            try:
                mode_events.append((datetime.fromisoformat(ts), to))
            except (ValueError, TypeError):
                # BUG-129b: one malformed verified _ts must lock the gate, not crash it.
                return GateResult(False, tgt,
                                  ["malformed mode_change timestamp on the verified chain "
                                   "(fail closed)"],
                                  {"chain_ok": True, "anchor_ok": True})
    weeks_lower = _weeks_in_modes(mode_events, now_naive, _LOWER_MODES)
    weeks_auto_veto = _weeks_in_modes(mode_events, now_naive, {Mode.auto_veto.value})

    metrics = {
        "chain_ok": True,
        "anchor_ok": True,
        "anchor_seq": anchor[0],
        "chain_head_seq": v.head_seq,
        "adjudicated": adjudicated,
        "approvals": approvals,
        "approval_rate": approval_rate,
        "vetoes": vetoes,
        "veto_rate": veto_rate,
        "bypass_events": bypass_count,
        "breaker_trips": breaker_trip_count,
        "max_drawdown": str(max_drawdown),
        "weeks_in_lower": weeks_lower,
        "weeks_in_auto_veto": weeks_auto_veto,
    }

    reasons: list[str] = []
    if weeks_lower < th.min_weeks_lower:
        reasons.append(f"only {weeks_lower:.2f} weeks in paper/review_live (need {th.min_weeks_lower})")
    if adjudicated < th.min_adjudicated:
        reasons.append(f"only {adjudicated} adjudicated proposals (need {th.min_adjudicated})")
    if approval_rate < th.min_approval_rate:
        reasons.append(f"approval rate {approval_rate:.2%} < {th.min_approval_rate:.0%}")
    if max_drawdown > th.max_drawdown_limit:
        reasons.append(f"max drawdown {max_drawdown} > limit {th.max_drawdown_limit}")
    if bypass_count > 0:
        reasons.append(f"{bypass_count} bypass-attempt event(s) recorded (must be 0)")

    if target_mode is Mode.auto_full:
        if weeks_auto_veto < th.min_weeks_auto_veto:
            reasons.append(
                f"only {weeks_auto_veto:.2f} weeks in auto_veto (need {th.min_weeks_auto_veto})"
            )
        if veto_rate > th.max_veto_rate:
            reasons.append(f"veto rate {veto_rate:.2%} > {th.max_veto_rate:.0%}")
        if breaker_trip_count > 0:
            reasons.append(f"{breaker_trip_count} breaker-trip event(s) recorded (must be 0)")

    return GateResult(unlockable=not reasons, target=tgt, reasons=reasons, metrics=metrics)
