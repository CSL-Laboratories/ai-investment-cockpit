"""RW-10 — `.env` carries durable NON-SECRET config, and provably nothing else (Inv 4).

Adding an env file solved a real fault: every ``COCKPIT_*`` value used to live only in the
environment of whatever shell launched ``main.py``, so a reboot or a second terminal silently
lost ``COCKPIT_BRIEF_CHANNEL_ID`` and the brief was computed, stored, audited — and delivered
nowhere. But a config file on disk is exactly where a credential ends up when someone is in a
hurry, and Invariant 4 says credentials live in Windows Credential Manager and NOWHERE else.

So the file ships with a guard, and this module is the guard's test. It asserts three things:

  * every keyring name the app actually reads is REFUSED in .env — including the check that
    this list has not gone stale against the real definitions in main.py / orchestrator.auth;
  * credential-SHAPED names are refused even when we have never seen them before, because a
    denylist of known vendors would miss the next one; and
  * the raised error names the KEY and the LINE and never the VALUE, so the exception is safe
    to log — which is the reason it is an exception rather than a warning nobody reads.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from config.settings import (
    EnvFileSecretError,
    Settings,
    _KEYRING_SECRET_NAMES,
    assert_no_secrets_in_env_file,
)

SECRET_VALUE = "sk-live-do-not-log-me-0123456789"


def write_env(tmp_path, body: str):
    p = tmp_path / ".env"
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------------------
# refused: real keyring names
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", _KEYRING_SECRET_NAMES)
def test_every_keyring_secret_name_is_refused(tmp_path, name):
    """THE control. Each of these has a keyring home; none may sit in a file on disk."""
    env = write_env(tmp_path, f"{name.replace('-', '_').upper()}={SECRET_VALUE}\n")
    with pytest.raises(EnvFileSecretError):
        assert_no_secrets_in_env_file(env)


@pytest.mark.parametrize("name", _KEYRING_SECRET_NAMES)
def test_keyring_names_are_refused_under_the_cockpit_prefix_too(tmp_path, name):
    """``COCKPIT_ALPACA_KEY=`` is the same mistake wearing the app's own prefix."""
    env = write_env(tmp_path, f"COCKPIT_{name.replace('-', '_').upper()}={SECRET_VALUE}\n")
    with pytest.raises(EnvFileSecretError):
        assert_no_secrets_in_env_file(env)


def test_the_keyring_name_list_has_not_gone_stale():
    """``_KEYRING_SECRET_NAMES`` is a copy. A copy that drifts silently stops guarding.

    Compares it against the ACTUAL definitions, so adding a sixth secret to main.py without
    adding it to the guard fails here rather than shipping an unguarded name.
    """
    import main
    from orchestrator.auth import API_KEY_SECRET_NAME

    live = {
        main._HMAC_SECRET_NAME, main._DISCORD_TOKEN_NAME, main._ALPACA_KEY_NAME,
        main._ALPACA_SECRET_NAME, main._FINNHUB_KEY_NAME, API_KEY_SECRET_NAME,
    }
    missing = live - set(_KEYRING_SECRET_NAMES)
    assert missing == set(), f"secret names the .env guard does not know about: {missing}"


# ---------------------------------------------------------------------------------------
# refused: credential SHAPES we have never seen before
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("key", [
    "COCKPIT_SOME_NEW_VENDOR_API_KEY",
    "COCKPIT_ROBINHOOD_TOKEN",
    "COCKPIT_BROKER_SECRET",
    "COCKPIT_DB_PASSWORD",
    "COCKPIT_SIGNING_PRIVATE_KEY",
    "COCKPIT_WEBHOOK_CREDENTIAL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
])
def test_credential_shaped_names_are_refused_even_when_unknown(tmp_path, key):
    """A denylist of today's vendors would miss tomorrow's. Shape is the backstop."""
    env = write_env(tmp_path, f"{key}={SECRET_VALUE}\n")
    with pytest.raises(EnvFileSecretError):
        assert_no_secrets_in_env_file(env)


# ---------------------------------------------------------------------------------------
# allowed: config that merely LOOKS credential-shaped
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("line", [
    "COCKPIT_SEC_USER_AGENT=Lucas Guerra lucas@example.com",
    "COCKPIT_FINRA_USER_AGENT=Cockpit lucas@example.com",
    "COCKPIT_AUDIT_LOG_CHANNEL_ID=123456789012345678",
    "COCKPIT_BRIEF_CHANNEL_ID=123456789012345678",
    "COCKPIT_MODE=read_only",
    "COCKPIT_CALENDAR_LOOKAHEAD_DAYS=3",
    "COCKPIT_SEC_RATE_LIMIT_PER_SEC=2.0",
    "COCKPIT_LOG_LEVEL=INFO",
])
def test_genuine_non_secret_config_is_allowed(tmp_path, line):
    """A guard that blocks the config it exists to enable would just get switched off."""
    assert_no_secrets_in_env_file(write_env(tmp_path, line + "\n"))


def test_a_channel_id_is_not_a_credential():
    """Explicit, because it is the one that would hurt: the bot TOKEN is the secret; the
    channel ID is an identifier that appears in any Discord URL."""
    assert "COCKPIT_AUDIT_LOG_CHANNEL_ID" not in [
        n.upper() for n in _KEYRING_SECRET_NAMES
    ]


# ---------------------------------------------------------------------------------------
# parsing edge cases
# ---------------------------------------------------------------------------------------
def test_comments_and_blank_lines_are_ignored(tmp_path):
    env = write_env(tmp_path, "\n# COCKPIT_ALPACA_KEY=would-be-bad-if-live\n\nCOCKPIT_MODE=read_only\n")
    assert_no_secrets_in_env_file(env)


def test_export_prefixed_lines_are_still_checked(tmp_path):
    """A shell-style file is a plausible thing to paste in; the guard must still see it."""
    env = write_env(tmp_path, f"export COCKPIT_ALPACA_KEY={SECRET_VALUE}\n")
    with pytest.raises(EnvFileSecretError):
        assert_no_secrets_in_env_file(env)


def test_a_missing_env_file_is_not_an_error(tmp_path):
    """No .env is the normal case (CI, a fresh clone). It must not fail closed on absence."""
    assert_no_secrets_in_env_file(tmp_path / "does-not-exist.env")


def test_offending_line_number_is_reported(tmp_path):
    env = write_env(tmp_path, f"COCKPIT_MODE=read_only\n# note\nCOCKPIT_FINNHUB_KEY={SECRET_VALUE}\n")
    with pytest.raises(EnvFileSecretError, match=r"line 3"):
        assert_no_secrets_in_env_file(env)


# ---------------------------------------------------------------------------------------
# THE non-negotiable: the guard must not leak what it is guarding
# ---------------------------------------------------------------------------------------
def test_the_error_names_the_key_but_never_the_value(tmp_path):
    """An exception that quotes the secret turns a safety check into a disclosure.

    This is why ``_iter_env_file`` yields only keys and deliberately never parses the
    right-hand side — the value never enters the process, so it cannot reach a traceback,
    a log record, or a crash report.
    """
    env = write_env(tmp_path, f"COCKPIT_ALPACA_SECRET={SECRET_VALUE}\n")
    with pytest.raises(EnvFileSecretError) as exc:
        assert_no_secrets_in_env_file(env)
    message = str(exc.value)
    assert "COCKPIT_ALPACA_SECRET" in message
    assert SECRET_VALUE not in message
    assert "sk-live" not in message


def test_multiple_offenders_are_all_reported(tmp_path):
    """Reporting one at a time turns cleanup into a guessing game."""
    env = write_env(
        tmp_path,
        f"COCKPIT_ALPACA_KEY={SECRET_VALUE}\nCOCKPIT_MODE=read_only\n"
        f"COCKPIT_FINNHUB_KEY={SECRET_VALUE}\n",
    )
    with pytest.raises(EnvFileSecretError) as exc:
        assert_no_secrets_in_env_file(env)
    assert "COCKPIT_ALPACA_KEY" in str(exc.value)
    assert "COCKPIT_FINNHUB_KEY" in str(exc.value)


# ---------------------------------------------------------------------------------------
# the feature itself still works
# ---------------------------------------------------------------------------------------
def test_settings_load_from_an_env_file(tmp_path, monkeypatch):
    """The durability fix: a value in the file reaches Settings without any shell export."""
    env = write_env(tmp_path, "COCKPIT_BRIEF_CHANNEL_ID=42\nCOCKPIT_LOG_LEVEL=DEBUG\n")
    monkeypatch.delenv("COCKPIT_BRIEF_CHANNEL_ID", raising=False)
    s = Settings(_env_file=str(env))
    assert s.brief_channel_id == 42
    assert s.log_level == "DEBUG"


def test_operator_watchlist_is_normalized_deduplicated_and_durable(tmp_path, monkeypatch):
    env = write_env(
        tmp_path,
        "COCKPIT_WATCHLIST=intc, AAPL spcx,googl,WDC,aapl\n",
    )
    monkeypatch.delenv("COCKPIT_WATCHLIST", raising=False)
    s = Settings(_env_file=str(env))
    assert s.watchlist == "INTC,AAPL,SPCX,GOOGL,WDC"
    assert s.watchlist_symbols == frozenset({"INTC", "AAPL", "SPCX", "GOOGL", "WDC"})


def test_operator_allowlist_loads_from_env_file_and_is_frozen_at_startup(tmp_path, monkeypatch):
    synthetic_id = "123456789012345678"
    env = write_env(tmp_path, f"COCKPIT_OPERATOR_IDS={synthetic_id}\n")
    monkeypatch.delenv("COCKPIT_OPERATOR_IDS", raising=False)
    s = Settings(_env_file=str(env))
    assert s.operator_ids == frozenset({synthetic_id})
    assert "operator_ids_config" not in s.model_dump()

    # Reassigning the raw pydantic field cannot alter the private authorization snapshot.
    s.operator_ids_config = "999999999999999999"
    assert s.operator_ids == frozenset({synthetic_id})


@pytest.mark.parametrize(
    "raw",
    [
        "bad operator!",
        "123456789012345678,234567890123456789",
        "x" * 513,
    ],
)
def test_operator_allowlist_fails_closed_on_malformed_or_unbounded_config(raw):
    with pytest.raises(Exception) as exc:
        Settings(operator_ids_config=raw)
    assert "operator" in str(exc.value)


@pytest.mark.parametrize(
    "raw",
    [
        "AAPL,$SPY",
        "AAPL;MSFT",
        "A-",
        "A..B",
        ",".join(f"S{i}" for i in range(26)),
        ",".join(["AAPL"] * 26),
        "A" * 513,
    ],
)
def test_operator_watchlist_fails_closed_on_bad_or_unbounded_input(raw):
    with pytest.raises(Exception) as exc:
        Settings(watchlist=raw)
    assert "watchlist" in str(exc.value)


def test_a_real_environment_variable_overrides_the_file(tmp_path, monkeypatch):
    """Precedence matters for a one-off run: env WINS, so nobody has to edit a file to
    override a single value for a single invocation."""
    env = write_env(tmp_path, "COCKPIT_BRIEF_CHANNEL_ID=42\n")
    monkeypatch.setenv("COCKPIT_BRIEF_CHANNEL_ID", "99")
    assert Settings(_env_file=str(env)).brief_channel_id == 99


def test_mode_still_defaults_to_read_only_with_an_env_file_present(tmp_path):
    """The ladder's safe floor is not something an env file may quietly move."""
    env = write_env(tmp_path, "COCKPIT_LOG_LEVEL=INFO\n")
    assert Settings(_env_file=str(env)).mode.value == "read_only"


# ---------------------------------------------------------------------------------------
# blank == unset (found by actually copying .env.example, which is how an operator starts)
# ---------------------------------------------------------------------------------------
def test_a_verbatim_copy_of_the_example_file_boots(tmp_path, monkeypatch):
    """THE regression. ``cp .env.example .env`` is step one of the setup instructions, and
    the first version of this feature made it raise five ValidationErrors and refuse to boot
    over blank optional fields — turning "I haven't filled that in yet" into a dead process.
    """
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    env = write_env(tmp_path, example)
    for key in [k for k in os.environ if k.startswith("COCKPIT_")]:
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=str(env))
    assert s.mode.value == "read_only"
    assert s.brief_channel_id is None            # blank -> unset, feed logs NOT PROVISIONED
    assert s.audit_log_channel_id is None
    assert s.sec_user_agent is None
    assert s.calendar_lookahead_days == 3        # a real value in the file still lands


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_optional_values_mean_unset(tmp_path, monkeypatch, blank):
    env = write_env(tmp_path, f"COCKPIT_BRIEF_CHANNEL_ID={blank}\n")
    monkeypatch.delenv("COCKPIT_BRIEF_CHANNEL_ID", raising=False)
    assert Settings(_env_file=str(env)).brief_channel_id is None


def test_blank_falls_back_to_a_non_none_default(tmp_path, monkeypatch):
    """Blank means "use the default", which for a defaulted field is its default — not None."""
    env = write_env(tmp_path, "COCKPIT_SEC_RATE_LIMIT_PER_SEC=\n")
    monkeypatch.delenv("COCKPIT_SEC_RATE_LIMIT_PER_SEC", raising=False)
    assert Settings(_env_file=str(env)).sec_rate_limit_per_sec == 2.0


@pytest.mark.parametrize("line,field", [
    ("COCKPIT_BRIEF_CHANNEL_ID=not-a-number", "brief_channel_id"),
    ("COCKPIT_MODE=auto_full_please", "mode"),
    ("COCKPIT_SEC_RATE_LIMIT_PER_SEC=10.0", "sec_rate_limit_per_sec"),
    ("COCKPIT_SEC_RATE_LIMIT_PER_SEC=-1", "sec_rate_limit_per_sec"),
])
def test_blank_handling_did_not_turn_a_BAD_value_into_a_good_one(tmp_path, monkeypatch, line, field):
    """NOT A WEAKENED VALIDATOR. Only whitespace-only strings are dropped; a present but
    invalid value must still be rejected exactly as before — including the SEC rate ceiling.
    """
    env = write_env(tmp_path, line + "\n")
    monkeypatch.delenv(line.split("=")[0], raising=False)
    with pytest.raises(Exception) as exc:
        Settings(_env_file=str(env))
    assert field in str(exc.value)


# ---- the template must keep pace with the Settings class --------------------------------
def _documented_env_names() -> set[str]:
    """Every ``COCKPIT_*`` name the shipped template mentions, commented-out lines included —
    a commented default is documentation, which is what this guards."""
    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^\s*#?\s*(COCKPIT_[A-Z0-9_]+)\s*=", text, flags=re.MULTILINE))


def _env_names_for(name: str, field) -> set[str]:
    """The env names pydantic-settings will actually accept for ``field``. Must honour
    ``validation_alias``/``AliasChoices`` — ``operator_ids_config`` reads COCKPIT_OPERATOR_IDS,
    not COCKPIT_OPERATOR_IDS_CONFIG, and a naive f"COCKPIT_{name.upper()}" reports it missing
    when it is in fact documented."""
    alias = getattr(field, "validation_alias", None)
    if alias is None:
        return {f"COCKPIT_{name.upper()}"}
    choices = getattr(alias, "choices", None) or [alias]
    return {str(c).upper() if str(c).startswith("COCKPIT_") else f"COCKPIT_{str(c).upper()}"
            for c in choices}


def test_every_setting_is_documented_in_the_env_template():
    """An operator provisions from ``.env.example``; a field absent from it is a control they
    cannot know exists. That is not hypothetical — COCKPIT_DECISION_AGENT_CWD was undocumented
    here, and setting it silently disabled the SEC-14 grounding verifier (see
    tests/workflows/test_grounding.py::test_grounding_survives_a_pinned_decision_cwd)."""
    documented = _documented_env_names()
    missing = sorted(
        name for name, field in Settings.model_fields.items()
        if not (_env_names_for(name, field) & documented)
    )
    assert not missing, (
        f"{len(missing)} Settings field(s) missing from app/.env.example: {missing}"
    )


# Read from os.environ at import time (settings.py:40) to LOCATE the env file, so it cannot
# itself be a Settings field. A scheduled task whose cwd is not the repo needs it, so it is
# documented on purpose.
_NOT_SETTINGS_FIELDS = {"COCKPIT_ENV_FILE"}


def test_the_env_template_does_not_document_names_that_no_longer_exist():
    """The other direction: a stale name in the template is a value an operator can set and
    watch do nothing, because ``extra='ignore'`` swallows it silently."""
    known: set[str] = set(_NOT_SETTINGS_FIELDS)
    for name, field in Settings.model_fields.items():
        known |= _env_names_for(name, field)
    stale = sorted(_documented_env_names() - known)
    assert not stale, f"app/.env.example documents unknown setting(s): {stale}"


def test_the_documented_api_port_is_the_one_the_app_binds():
    """The template said 8765 while Settings defaulted to 8787, so an operator who uncommented
    the line moved the loopback API out from under every runbook."""
    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    documented = re.search(r"^\s*#?\s*COCKPIT_API_PORT\s*=\s*(\d+)", text, flags=re.MULTILINE)
    assert documented is not None
    assert int(documented.group(1)) == Settings().api_port
