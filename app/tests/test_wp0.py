"""WP0 acceptance — app imports cleanly; settings default read_only; secrets round-trip
via keyring; the model map loads (docs/build-scaffold-ultracode WP0)."""
from __future__ import annotations

import importlib

import pytest

MODULES = [
    "config.settings",
    "config.models",
    "core.secrets",
    "core.ids",
    "core.clock",
    "core.logging",
]


@pytest.mark.parametrize("mod", MODULES)
def test_imports_clean(mod):
    importlib.import_module(mod)


def test_mode_defaults_read_only():
    from config.settings import Mode, get_settings

    assert get_settings().mode is Mode.read_only


def test_api_binds_loopback():
    from config.settings import get_settings

    # loopback only — no inbound exposure (docs/security-architecture §6)
    assert get_settings().api_host == "127.0.0.1"


def test_secret_roundtrip_via_keyring():
    from core.secrets import get_secret, require_secret, set_secret

    set_secret("test_alpaca_key", "sk-not-a-real-secret")
    got = require_secret("test_alpaca_key")
    assert got.reveal() == "sk-not-a-real-secret"
    # Redacted never leaks the value when logged/str'd
    assert "sk-not-a-real-secret" not in repr(got)
    assert "sk-not-a-real-secret" not in str(got)
    assert get_secret("a_missing_secret_name") is None


def test_model_map_pins_claude_on_decision_aliases():
    from config.models import load_model_map, runtime_for

    m = load_model_map()
    assert {"triage", "deep"} <= set(m)
    # decision aliases must be Claude (Invariant 2); codex is research-only
    assert runtime_for("triage") == "claude"
    assert runtime_for("deep") == "claude"
    assert runtime_for("codex") == "codex"
