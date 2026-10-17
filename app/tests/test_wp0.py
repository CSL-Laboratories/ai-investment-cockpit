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
    from config.settings import DecisionRuntime, Mode, get_settings

    assert get_settings().mode is Mode.read_only
    assert get_settings().decision_runtime is DecisionRuntime.claude


def test_decision_runtime_accepts_only_supported_providers(monkeypatch):
    from pydantic import ValidationError

    from config.settings import DecisionRuntime, Settings

    monkeypatch.setenv("COCKPIT_DECISION_RUNTIME", "codex")
    assert Settings().decision_runtime is DecisionRuntime.codex
    monkeypatch.setenv("COCKPIT_DECISION_RUNTIME", "jev")
    assert Settings().decision_runtime is DecisionRuntime.jev
    monkeypatch.setenv("COCKPIT_DECISION_RUNTIME", "auto")
    assert Settings().decision_runtime is DecisionRuntime.auto
    monkeypatch.setenv("COCKPIT_DECISION_RUNTIME", "other")
    with pytest.raises(ValidationError):
        Settings()


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


def test_model_map_keeps_claude_default_and_declares_codex_decision_overrides():
    from config.models import cli_model_for, load_model_map, pinned_model_for, runtime_for

    m = load_model_map()
    assert {"triage", "deep"} <= set(m)
    # The conservative/default auto-compatible mapping stays Claude.
    assert runtime_for("triage") == "claude"
    assert runtime_for("deep") == "claude"
    # Human-gated read_only/paper workflows may explicitly select Codex for the same roles.
    assert runtime_for("triage", "codex") == "codex"
    assert runtime_for("deep", "codex") == "codex"
    assert cli_model_for("triage", "codex") == "gpt-5.5"
    assert pinned_model_for("deep", "codex") == "gpt-5.5"
    assert runtime_for("codex") == "codex"
