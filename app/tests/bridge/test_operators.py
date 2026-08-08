"""WP6 — operator allowlist."""
from __future__ import annotations

from bridge.operators import authorized_operator_ids, is_authorized

from .conftest import FOREIGN, OPERATOR


def test_allowlisted_operator_is_authorized(st):
    assert is_authorized(OPERATOR, st.settings)
    assert OPERATOR in authorized_operator_ids(st.settings)


def test_foreign_user_not_authorized(st):
    assert not is_authorized(FOREIGN, st.settings)


def test_none_and_empty_not_authorized(st):
    assert not is_authorized(None, st.settings)
    assert not is_authorized("", st.settings)
    assert not is_authorized("   ", st.settings)


def test_direct_env_fallback_accepts_one_numeric_discord_id(monkeypatch, st):
    operator = "345678901234567890"
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", operator)
    assert authorized_operator_ids() == frozenset({operator})


def test_empty_startup_snapshot_never_falls_back_to_later_environment(monkeypatch):
    class EmptyStartup:
        operator_ids = frozenset()

    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", "345678901234567890")
    assert authorized_operator_ids(EmptyStartup()) == frozenset()
    assert not is_authorized("345678901234567890", EmptyStartup())


def test_malformed_or_unbounded_direct_environment_fails_closed(monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", "bad operator!")
    assert authorized_operator_ids() == frozenset()
    monkeypatch.setenv(
        "COCKPIT_OPERATOR_IDS", "345678901234567890,456789012345678901"
    )
    assert authorized_operator_ids() == frozenset()


def test_settings_seam_takes_precedence(st):
    class S:
        operator_ids = ["345678901234567890"]

    assert is_authorized("345678901234567890", S())
    assert not is_authorized(OPERATOR, S())      # env ignored when the seam is present
