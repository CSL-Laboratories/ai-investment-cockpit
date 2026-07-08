"""G8 — orchestrator/limits.py extraction tests (Owner B).

Enforces the WP8 'orchestrator/limits.py' contract of docs/build-scaffold-ultracode.md WP8
and SEC-15 of docs/security-hardening-backlog.md: the audited-limits reader moved out of
app.py without behavior change — ``current_limits(state)`` reads the latest
EVENT_LIMITS_CHANGE payload over Settings defaults, app.py keeps thin aliases (its
existing tests stay green), and workflow consumers (the SEC-21 DeepBudget) read the
effective values from here, never raw Settings. Hermetic: tmp SQLite, FrozenClock.
"""
from __future__ import annotations

from sqlalchemy import select, update

from config.settings import Settings
from orchestrator import app as orch_app
from orchestrator import limits as limits_mod
from orchestrator.limits import (
    DEFAULT_LIMITS,
    EVENT_LIMITS_CHANGE,
    LIMIT_LOOSEN_DIR,
    current_limits,
    default_limits,
)
from schemas.models.audit_entry import AuditEntry
from workflows.budget import DeepBudget


def test_defaults_match_settings_baseline():
    assert default_limits(Settings()) == DEFAULT_LIMITS
    assert DEFAULT_LIMITS["triage_threshold"] == 60
    assert DEFAULT_LIMITS["deep_model_daily_budget"] == 5


def test_current_limits_defaults_then_reflects_audited_change(st):
    assert current_limits(st) == default_limits(st.settings)

    st.audit.append(EVENT_LIMITS_CHANGE, actor="operator",
                    payload={"limits": {"deep_model_daily_budget": 2, "triage_threshold": 80}})
    limits = current_limits(st)
    assert limits["deep_model_daily_budget"] == 2
    assert limits["triage_threshold"] == 80
    assert limits["max_notional"] == DEFAULT_LIMITS["max_notional"]  # untouched keys default

    # latest entry wins (SEC-15 source of truth = latest EVENT_LIMITS_CHANGE)
    st.audit.append(EVENT_LIMITS_CHANGE, actor="operator",
                    payload={"limits": {"deep_model_daily_budget": 7}})
    assert current_limits(st)["deep_model_daily_budget"] == 7


def test_app_module_keeps_thin_aliases():
    """orchestrator/app.py behavior is unchanged: its names ARE the extracted ones."""
    assert orch_app.EVENT_LIMITS_CHANGE == EVENT_LIMITS_CHANGE == "limits_change"
    assert orch_app._current_limits is limits_mod.current_limits
    assert orch_app._hard_bounds is limits_mod.hard_bounds
    assert orch_app._funded_balance is limits_mod.funded_balance
    assert orch_app._LIMIT_LOOSEN_DIR is limits_mod.LIMIT_LOOSEN_DIR


def test_loosen_direction_map_is_preserved():
    assert LIMIT_LOOSEN_DIR["triage_threshold"] == -1     # LOWER loosens (SEC-15)
    assert LIMIT_LOOSEN_DIR["deep_model_daily_budget"] == 1
    assert set(LIMIT_LOOSEN_DIR) == set(DEFAULT_LIMITS)


def test_deep_budget_reads_audited_limit(st):
    """SEC-21: the deep budget cap is the AUDITED value, never raw Settings."""
    assert DeepBudget(st).limit == 5
    st.audit.append(EVENT_LIMITS_CHANGE, actor="operator",
                    payload={"limits": {"deep_model_daily_budget": 1}})
    assert DeepBudget(st).limit == 1


def test_current_limits_ignores_forged_newer_limits_row(st):
    """SEC-15/SEC-21 (adversarial): a keyless DB-writer forges a NEWER limits_change row to
    inflate the deep budget to the hard ceiling (50). Without verify_entry, current_limits
    returned the tampered 50 (fail-open escalation). It must skip the unverified newest row
    and fall back to the most recent HMAC-VERIFIED change (budget 2) — never 50."""
    # A genuine, HMAC-signed operator change to a TIGHT budget of 2.
    st.audit.append(EVENT_LIMITS_CHANGE, actor="operator",
                    payload={"limits": {"deep_model_daily_budget": 2}})

    # Keyless attacker forges a NEWER limits_change row (no HMAC key => bogus entry_hash)
    # that tries to loosen the budget to the hard ceiling.
    with st.session_factory() as s, s.begin():
        head = s.execute(
            select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1)
        ).scalar_one()
        s.add(AuditEntry(
            id="forged-limits", seq=head.seq + 1, prev_hash=head.entry_hash,
            entry_hash="deadbeef" * 8, event_type=EVENT_LIMITS_CHANGE, actor="operator",
            refs={}, payload={"limits": {"deep_model_daily_budget": 50},
                              "_ts": head.created_at.isoformat()},
            created_at=head.created_at,
        ))

    # The forged newest row fails verify_entry -> ignored; the verified budget 2 wins.
    assert current_limits(st)["deep_model_daily_budget"] == 2
    assert DeepBudget(st).limit == 2

    # And if there is NO verified change at all, fail CLOSED to the tightest floor (0) — never
    # the forged 50, and never the LOOSER default 5. Invalidating every limits_change row leaves
    # the chain unverifiable (broken HMAC); reverting to the default (5) would LOOSEN the cap the
    # operator set (SEC-21 round-3), so current_limits clamps to the tightest instead.
    with st.session_factory() as s, s.begin():
        s.execute(
            AuditEntry.__table__.update()
            .where(AuditEntry.event_type == EVENT_LIMITS_CHANGE)
            .where(AuditEntry.id != "forged-limits")
            .values(entry_hash="cafebabe" * 8)
        )
    got = current_limits(st)["deep_model_daily_budget"]
    assert got == 0                                          # tightest floor (fail closed)
    assert got != DEFAULT_LIMITS["deep_model_daily_budget"]  # never loosens back to the default 5
    assert got != 50                                         # and never the forged ceiling


def test_current_limits_erased_sub_default_tightening_never_loosens(st):
    """SEC-21 round-3 (keyless-writer BYPASS): an operator-audited SUB-DEFAULT tightening
    (deep budget 5->2) is invalidated/deleted by a keyless DB writer. The per-row verify
    walk then finds NO verified limits_change row and (old code) reverted to the LOOSER
    default 5 — LOOSENING the cap the operator set, contradicting 'a tampered row can only
    tighten by being ignored, never loosen'. A full-chain check catches the broken HMAC /
    seq gap, so current_limits must FAIL CLOSED (never return a value looser than 2)."""
    # A genuine, HMAC-signed operator tightening to a SUB-DEFAULT budget of 2 (clean chain).
    st.audit.append(EVENT_LIMITS_CHANGE, actor="operator",
                    payload={"limits": {"deep_model_daily_budget": 2}})
    assert current_limits(st)["deep_model_daily_budget"] == 2      # clean chain honors the 2

    # (a) byte-flip the tightening row -> its HMAC breaks, verify_chain fails, no verified row.
    with st.session_factory() as s, s.begin():
        row = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_LIMITS_CHANGE)).scalars().one()
        s.execute(update(AuditEntry).where(AuditEntry.id == row.id).values(
            payload={"limits": {"deep_model_daily_budget": 50}}))   # try to LOOSEN to 50
    flipped = current_limits(st)["deep_model_daily_budget"]
    assert flipped != 5, "must not revert to the LOOSER default 5"
    assert flipped != 50, "must not honor the tampered loosening"
    assert flipped <= 2, "fail closed: never looser than the operator's audited 2"

    # (b) delete it outright -> seq gap; verify_chain fails; still must not loosen to 5.
    with st.session_factory() as s, s.begin():
        s.execute(AuditEntry.__table__.delete().where(
            AuditEntry.event_type == EVENT_LIMITS_CHANGE))
    deleted = current_limits(st)["deep_model_daily_budget"]
    assert deleted != 5 and deleted <= 2
