"""CSL-371: the agent CLI child gets an allowlisted environment, not the parent's.

``SubprocessTransport`` used to pass ``os.environ.copy()`` minus ``JEV_API_KEY``, so every other
secret in the operator's shell reached the third-party ``claude``/``codex`` process. These tests
pin both halves of the fix: secret-shaped names never cross, and the names a CLI needs to start
and find its own login store still do.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

from agent_runner import transport as transport_mod
from agent_runner.transport import SubprocessTransport, build_child_env

SECRETS = {
    "JEV_API_KEY": "jev-bearer",
    "GITHUB_TOKEN": "ghs_example",
    "GH_TOKEN": "gho_example",
    "GH_ENTERPRISE_TOKEN": "ghe_example",
    "GH_HOST": "github.example",
    "OPENAI_API_KEY": "sk-example",
    "ANTHROPIC_API_KEY": "sk-ant-example",
    "CLAUDE_CODE_OAUTH_TOKEN": "oauth-example",
    "CSL_DISCORD_BOT_MCP_TOKEN": "discord-example",
    "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
    "AWS_SECRET_ACCESS_KEY": "aws-secret",
    "ALPACA_SECRET_KEY": "alpaca-secret",
    "SOME_SERVICE_KEY": "svc-key",
    "DB_PASSWORD": "pw",
    "MY_PAT": "pat",
    "LC_INTERNAL_BEARER": "bearer-example",
    "COCKPIT_ENV_FILE": "/srv/cockpit/.env",
    "UNRELATED_FLAG": "1",
}

NEEDED = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/home/operator",
    "USERPROFILE": r"C:\Users\operator",
    "SYSTEMROOT": r"C:\Windows",
    "COMSPEC": r"C:\Windows\system32\cmd.exe",
    "PATHEXT": ".COM;.EXE;.BAT;.CMD",
    "APPDATA": r"C:\Users\operator\AppData\Roaming",
    "LOCALAPPDATA": r"C:\Users\operator\AppData\Local",
    "TEMP": "/tmp",
    "TMP": "/tmp",
    "TMPDIR": "/tmp",
    "LANG": "en_US.UTF-8",
    "LC_ALL": "en_US.UTF-8",
    "TZ": "America/Los_Angeles",
    "CLAUDE_CONFIG_DIR": "/home/operator/.claude",
    "CODEX_HOME": "/home/operator/.codex",
}


def test_secret_shaped_names_never_reach_the_child_env():
    env = build_child_env({**SECRETS, **NEEDED})
    leaked = sorted(set(env) & set(SECRETS))
    assert leaked == [], f"secret-shaped names leaked into the agent env: {leaked}"


def test_the_names_a_cli_needs_still_reach_the_child_env():
    env = build_child_env({**SECRETS, **NEEDED})
    assert env == NEEDED


@pytest.mark.parametrize("name", sorted(transport_mod._CHILD_ENV_ALLOWLIST))
def test_every_allowlisted_name_passes(name):
    """POSITIVE CONTROL for the secret-name guard: no allowlisted name may be shadowed by it,
    or an operator would lose PATH-class variables without any error."""
    assert build_child_env({name: "value"}) == {name: "value"}


@pytest.mark.parametrize("name", ["Path", "SystemRoot", "https_proxy", "no_proxy", "windir"])
def test_windows_names_match_case_insensitively_and_keep_their_spelling(name, monkeypatch):
    monkeypatch.setattr(transport_mod, "_CASE_INSENSITIVE_ENV", True)
    assert build_child_env({name: "value"}) == {name: "value"}


@pytest.mark.parametrize("name", ["http_proxy", "https_proxy", "all_proxy", "no_proxy"])
def test_posix_lowercase_proxy_names_pass(name, monkeypatch):
    monkeypatch.setattr(transport_mod, "_CASE_INSENSITIVE_ENV", False)
    assert build_child_env({name: "value"}) == {name: "value"}


@pytest.mark.parametrize("name", ["home", "path", "UserProfile", "Path", "lang", "tz", "lc_all"])
def test_posix_names_are_case_sensitive(name, monkeypatch):
    """On POSIX ``home`` is not ``HOME``: an unrelated variable must not ride the allowlist
    (Codex, #240)."""
    monkeypatch.setattr(transport_mod, "_CASE_INSENSITIVE_ENV", False)
    assert build_child_env({name: "secret-bearing custom data"}) == {}


def test_lowercase_proxy_with_credentials_is_dropped_on_posix(monkeypatch):
    monkeypatch.setattr(transport_mod, "_CASE_INSENSITIVE_ENV", False)
    assert build_child_env({"https_proxy": "http://u:p@proxy.example:8080"}) == {}


@pytest.mark.parametrize("name", [
    # Devin on #240: an LC_ prefix let these through, and BEARER was not a marker.
    "LC_INTERNAL_BEARER", "LC_TOKEN", "LC_ANYTHING", "LC_TERMINAL",
])
def test_only_real_locale_categories_pass_not_any_lc_name(name):
    assert build_child_env({name: "value"}) == {}


@pytest.mark.parametrize("name", [
    "LC_ALL", "LC_CTYPE", "LC_COLLATE", "LC_MESSAGES", "LC_MONETARY", "LC_NUMERIC", "LC_TIME",
    "LC_ADDRESS", "LC_IDENTIFICATION", "LC_MEASUREMENT", "LC_NAME", "LC_PAPER", "LC_TELEPHONE",
])
def test_posix_and_glibc_locale_categories_pass(name):
    assert build_child_env({name: "en_US.UTF-8"}) == {name: "en_US.UTF-8"}


@pytest.mark.parametrize("name", [
    "INTERNAL_BEARER", "SERVICE_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS", "PROXY_AUTH",
    "AUTHORIZATION", "OAUTH_CLIENT", "BROWSER_COOKIE", "SESSIONID", "SSH_PRIVATE", "PRIVATE_KEY",
    "GPG_PASSPHRASE", "SIGNING_MATERIAL", "JWT", "SERVICE_APIKEY", "DB_PWD", "SMTP_PASS",
    "SENTRY_DSN", "LC_INTERNAL_BEARER",
])
def test_secret_detection_covers_credential_shaped_names(name):
    """The guard is the second line of defence, so it must recognise these spellings on its own,
    independently of the allowlist."""
    assert transport_mod._looks_secret(name)


def test_secret_detection_catches_an_allowlisted_lc_secret(monkeypatch):
    """Even if a future edit allowlisted LC_INTERNAL_BEARER, the guard drops it."""
    monkeypatch.setattr(transport_mod, "_CHILD_ENV_ALLOWLIST",
                        transport_mod._CHILD_ENV_ALLOWLIST | {"LC_INTERNAL_BEARER"})
    assert build_child_env({"LC_INTERNAL_BEARER": "x"}) == {}


def test_secret_guard_wins_even_if_a_secret_name_is_allowlisted(monkeypatch):
    """Belt and braces: a future edit that allowlists a token name still does not leak it."""
    monkeypatch.setattr(transport_mod, "_CHILD_ENV_ALLOWLIST",
                        transport_mod._CHILD_ENV_ALLOWLIST | {"GITHUB_TOKEN", "JEV_API_KEY"})
    assert build_child_env({"GITHUB_TOKEN": "x", "JEV_API_KEY": "y"}) == {}


@pytest.mark.parametrize("url", [
    "http://user:pass@proxy.example:8080",
    "user:pass@proxy.example:8080",
    "https://token@proxy.example",
])
def test_proxy_urls_with_credentials_are_dropped(url):
    assert build_child_env({"HTTPS_PROXY": url, "http_proxy": url}) == {}


def test_proxy_urls_without_credentials_pass():
    env = {"HTTPS_PROXY": "http://proxy.example:8080", "no_proxy": "localhost,127.0.0.1",
           "http_proxy": "http://proxy.example:8080/path@x"}
    assert build_child_env(env) == env


def test_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_from_os_environ")
    monkeypatch.setenv("TZ", "UTC")
    env = build_child_env()
    assert "GITHUB_TOKEN" not in env
    assert env.get("TZ") == "UTC"


_DUMP_ENV = "import json, os, sys; sys.stdout.write(json.dumps(dict(os.environ)))"


def test_real_child_does_not_see_parent_secrets(monkeypatch):
    """End to end through the REAL transport and a REAL process."""
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    result = SubprocessTransport().run([sys.executable, "-c", _DUMP_ENV],
                                       input_text="", timeout_s=60, cwd=None)
    assert result.returncode == 0, result.stderr
    child = json.loads(result.stdout)
    leaked = sorted(set(child) & set(SECRETS))
    # A platform may inject variables of its own (macOS adds __CF_USER_TEXT_ENCODING), so the
    # check is "none of ours", plus the positive control that an allowlisted one arrived.
    assert leaked == [], f"parent secrets reached the agent child: {leaked}"
    assert any(value == "America/Los_Angeles" for name, value in child.items()
               if name.upper() == "TZ")
    for name in ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT"):
        parent_value = transport_mod.os.environ.get(name)
        if parent_value is not None:
            assert child.get(name) == parent_value, f"{name} did not reach the child"


# ---------------------------------------------------------------------------------------
# Authenticated-CLI smoke (Devin on #240). Opt-in, because CI and most dev hosts have no
# logged-in runtime. ``claude auth status`` and ``codex login status`` read the local login store
# and spend no model turn. Run on the deploy host with COCKPIT_CLI_AUTH_SMOKE=1 to check that
# each CLI still finds its login through the allowlisted environment.
# ---------------------------------------------------------------------------------------
_AUTH_SMOKE = os.environ.get("COCKPIT_CLI_AUTH_SMOKE") == "1"


@pytest.mark.skipif(not _AUTH_SMOKE, reason="set COCKPIT_CLI_AUTH_SMOKE=1 on a logged-in host")
@pytest.mark.parametrize("argv", [["claude", "auth", "status"], ["codex", "login", "status"]],
                         ids=["claude", "codex"])
def test_cli_is_still_logged_in_under_the_allowlisted_env(argv):
    if shutil.which(argv[0]) is None:
        pytest.skip(f"{argv[0]} CLI not installed on this host")
    result = SubprocessTransport().run(argv, input_text="", timeout_s=120, cwd=None)
    assert result.returncode == 0, (
        f"{argv[0]} does not see its login under the allowlisted env "
        f"(an env-only credential would now be dropped by design): {result.stderr!r}")
