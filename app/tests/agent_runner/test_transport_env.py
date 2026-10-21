"""CSL-371: the agent CLI child gets an allowlisted environment, not the parent's.

``SubprocessTransport`` used to pass ``os.environ.copy()`` minus ``JEV_API_KEY``, so every other
secret in the operator's shell reached the third-party ``claude``/``codex`` process. These tests
pin both halves of the fix: secret-shaped names never cross, and the names a CLI needs to start
and find its own login store still do.
"""
from __future__ import annotations

import json
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
def test_names_match_case_insensitively_and_keep_their_spelling(name):
    assert build_child_env({name: "value"}) == {name: "value"}


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
