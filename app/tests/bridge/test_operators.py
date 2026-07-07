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


def test_env_parses_multiple(monkeypatch, st):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", "a, b  c,,d")
    assert authorized_operator_ids(st.settings) == frozenset({"a", "b", "c", "d"})


def test_settings_seam_takes_precedence(st):
    class S:
        operator_ids = ["seam-only"]

    assert is_authorized("seam-only", S())
    assert not is_authorized(OPERATOR, S())      # env ignored when the seam is present
