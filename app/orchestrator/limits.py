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
    """Read the latest committed limits from one verified snapshot (SEC-15 / SEC-21).

    BUG-126 / RW-22: verifying only through a surviving candidate misses a later erased
    tightening. Verify the complete committed prefix through its signed high-water,
    then select from that SAME snapshot. Only forged rows appended above an intact
    committed history may be ignored; any damage within it yields the tightest floor.
    """
    with state.session_factory() as s, s.begin():
        verified = state.audit.verify_committed_prefix_session(s)
        if not verified.ok:
            return fail_closed_limits(state.settings)
        candidate = s.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == EVENT_LIMITS_CHANGE,
                   AuditEntry.seq <= verified.head_seq)
            .order_by(AuditEntry.seq.desc())
            .limit(1)
        ).scalar_one_or_none()
        if candidate is None and verified.ignored_forged_tail:
            return fail_closed_limits(state.settings)
        limits = default_limits(state.settings)
        if candidate is not None and (candidate.payload or {}).get("limits"):
            limits.update(candidate.payload["limits"])
        return limits
