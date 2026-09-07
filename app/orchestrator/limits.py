"""WP8 — audited runtime-limits reader (SEC-15), extracted from ``orchestrator/app.py``.

Implements the WP8 'orchestrator/limits.py' section of the frozen contract
(docs/build-scaffold-ultracode.md WP8) and SEC-15 / SEC-21 of
docs/security-hardening-backlog.md: the source of truth for runtime limits is the latest
``EVENT_LIMITS_CHANGE`` audit entry — never a mutable table, never raw Settings. WP8
workflows read ``triage_threshold`` and ``deep_model_daily_budget`` from
``current_limits(state)`` so operator ``/limits`` changes are honored (the SEC-21 deep
budget derives its cap from here). ``orchestrator/app.py`` re-imports these names as thin
aliases, keeping its behavior and all existing WP5 tests intact.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from schemas.models.audit_entry import AuditEntry

# Limits are audited (source of truth = latest EVENT_LIMITS_CHANGE), never a mutable table.
EVENT_LIMITS_CHANGE = "limits_change"

# Which direction LOOSENS a limit: +1 == a larger value loosens; -1 == a smaller value does.
LIMIT_LOOSEN_DIR = {
    "max_notional": 1,
    "daily_loss_limit": 1,
    "max_positions": 1,
    "concentration_pct": 1,
    "triage_threshold": -1,          # a LOWER triage threshold widens what escalates (SEC-15)
    "deep_model_daily_budget": 1,
}

DEFAULT_FUNDED_BALANCE = Decimal("25000")   # [est.] outer bound; overridable via settings

# The Settings-default baseline (== default_limits(Settings())), kept as a plain constant so
# callers/tests can reference the shipped defaults without instantiating Settings.
DEFAULT_LIMITS = {
    "max_notional": "1000",
    "daily_loss_limit": "500",
    "max_positions": 10,
    "concentration_pct": "0.25",
    "triage_threshold": 60,
    "deep_model_daily_budget": 5,
}


def dec(x) -> Decimal:
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


def funded_balance(settings) -> Decimal:
    return dec(getattr(settings, "funded_balance", DEFAULT_FUNDED_BALANCE))


def default_limits(settings) -> dict:
    fb = funded_balance(settings)
    return {
        "max_notional": str(getattr(settings, "max_notional", min(Decimal("1000"), fb))),
        "daily_loss_limit": str(getattr(settings, "daily_loss_limit", min(Decimal("500"), fb))),
        "max_positions": int(getattr(settings, "max_positions", 10)),
        "concentration_pct": str(getattr(settings, "concentration_pct", Decimal("0.25"))),
        "triage_threshold": int(settings.triage_threshold),
        "deep_model_daily_budget": int(settings.deep_model_daily_budget),
    }


def fail_closed_limits(settings) -> dict:
    """The TIGHTEST value each field may take (SEC-21 round-3 fail-closed floor). Used when the
    audit chain does NOT verify and NO verified ``EVENT_LIMITS_CHANGE`` row survives: a keyless
    writer that DELETEs (seq gap) or byte-flips (HMAC break) a genuine SUB-DEFAULT tightening
    would otherwise revert the effective cap to the LOOSER Settings default (e.g. deep budget
    5->2 erased -> back to 5). Clamping to the tightest floor means an erased tightening can
    only tighten, never loosen (the invariant 'a tampered row can only tighten by being
    ignored, never loosen')."""
    return {
        **default_limits(settings),
        "max_notional": "0",
        "daily_loss_limit": "0",
        "max_positions": 0,
        "concentration_pct": "0",
        "triage_threshold": 100,          # lower loosens -> 100 is tightest (nothing escalates)
        "deep_model_daily_budget": 0,     # higher loosens -> 0 is tightest (no deep escalation)
    }


def hard_bounds(settings) -> dict:
    """The most-permissive value each field may take (a hard config ceiling derived from the
    funded balance; a floor for a lower-loosens field). No ``/limits`` call may cross it."""
    fb = funded_balance(settings)
    return {
        "max_notional": fb,                 # cannot risk more than the funded balance (O1)
        "daily_loss_limit": fb,
        "max_positions": Decimal("50"),
        "concentration_pct": Decimal("1"),  # 100%
        "triage_threshold": Decimal("0"),   # floor: 0 (lower loosens)
        "deep_model_daily_budget": Decimal("50"),
    }


def current_limits(state) -> dict:
    """The effective runtime limits: Settings defaults overlaid with the most recent
    HMAC-VERIFIED ``EVENT_LIMITS_CHANGE`` audit payload (SEC-15 / SEC-21 audited source of
    truth). A keyless DB-writer can forge or mutate a ``limits_change`` row to inflate the
    deep budget or drop the triage threshold, so every candidate row must pass
    ``verify_entry`` before it may take effect. We walk rows newest-first and apply the FIRST
    that verifies; an unverified latest row is SKIPPED (never allowed to loosen the effective
    limits) in favour of the most recent verified row, else the conservative Settings default
    baseline (budget 5 / threshold 60). Fail closed: a tampered row can only tighten by being
    ignored, never loosen.

    BUG-126: the walk above is not enough on a BROKEN chain. A keyless writer that DELETES
    a genuine SUB-DEFAULT tightening (deep budget 5->2 erased) leaves an older, LOOSER
    verified row (or no row) behind, and returning it reverts to the looser cap — a
    loosening. So on a chain that does NOT verify, a surviving verified row is honored
    ONLY when the break is provably a forged APPEND above it: the verified prefix
    1..candidate.seq is contiguous/linked/HMAC-valid AND the signed high-water does not
    exceed the live head (a head below the high-water is a deleted genuine tail, even
    when the surviving prefix looks clean). Anything else fails closed to the tightest
    floor — an erased tightening can only tighten, never loosen."""
    with state.session_factory() as s:
        rows = s.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == EVENT_LIMITS_CHANGE)
            .order_by(AuditEntry.seq.desc())
        ).scalars().all()
    candidate = None
    for row in rows:
        if not state.audit.verify_entry(row):
            continue   # forged/mutated row -> ignore (fail closed); do not loosen
        candidate = row
        break          # first verified row (newest-first) is the audited source of truth
    if state.audit.verify_chain().ok:
        limits = default_limits(state.settings)
        if candidate is not None and (candidate.payload or {}).get("limits"):
            limits.update(candidate.payload["limits"])
        return limits
    # Chain is broken: fall back to the candidate ONLY for a forged append above an
    # intact verified prefix. A deleted genuine tail (head below the signed high-water),
    # a break at/below the candidate, or no surviving candidate all fail closed.
    hwm = state.audit.high_water_mark()
    with state.session_factory() as s:
        chain_rows = s.execute(
            select(AuditEntry).order_by(AuditEntry.seq)
        ).scalars().all()
    head_seq = chain_rows[-1].seq if chain_rows else 0
    if hwm == -1 or (hwm is not None and head_seq < hwm):
        # Watermark tamper, or a deleted genuine tail: the surviving rows cannot prove
        # what the operator last set -> tightest floor (SEC-21 round-3).
        return fail_closed_limits(state.settings)
    if candidate is None:
        return fail_closed_limits(state.settings)
    by_seq = {r.seq: r for r in chain_rows}
    prev = None
    for seq in range(1, candidate.seq + 1):
        r = by_seq.get(seq)
        if r is None or r.prev_hash != prev or not state.audit.verify_entry(r):
            return fail_closed_limits(state.settings)
        prev = r.entry_hash
    limits = default_limits(state.settings)
    if (candidate.payload or {}).get("limits"):
        limits.update(candidate.payload["limits"])
    return limits
