"""WP0 test fixtures — an in-memory keyring so secret round-trips run on any CI host
(the OS credential store differs per platform and is unavailable on headless Linux CI)."""
from __future__ import annotations

import os

import keyring
import pytest
from keyring.backend import KeyringBackend

from tests.o7_contract_report import O7_NEGATIVE_LINES


class InMemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        self._store.pop((service, username), None)


@pytest.fixture(autouse=True, scope="session")
def _in_memory_keyring():
    prev = keyring.get_keyring()
    keyring.set_keyring(InMemoryKeyring())
    yield
    keyring.set_keyring(prev)


# Defined BEFORE _isolate_audit_head on purpose: same-scope autouse fixtures run in
# definition order, so the wipe must happen before anything sets a COCKPIT_* value it needs.
@pytest.fixture(autouse=True)
def _no_ambient_cockpit_env(monkeypatch):
    """The suite must not read the developer's shell.

    A test asserting an unconfigured default constructs a bare ``Settings()``, which reads the
    real environment — so on a machine with operator config exported (COCKPIT_BRIEF_CHANNEL_ID,
    COCKPIT_OPERATOR_IDS, COCKPIT_HALT_PATH are the usual three) it fails, while the same
    commit is green in CI. That is the worst shape of flake: it tracks who ran it, not what
    changed. Tests that WANT a value still set it themselves — their own monkeypatch runs after
    this one.
    """
    for name in [k for k in os.environ if k.startswith("COCKPIT_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _isolate_audit_head(tmp_path, monkeypatch):
    """Never let a test anchor its temporary DB into the operator's runtime head file.

    ``audit_head_file`` is relative to the process cwd by default. The suite also runs from
    ``app/``, so composed scheduler tests previously advanced ``app/data/audit-head.json`` with
    temporary chains, making the real DB correctly fail closed with ``AnchorRewind``. Give every
    test its own witness mirror; tests that need a specific path may still override this env value.
    """
    monkeypatch.setenv("COCKPIT_AUDIT_HEAD_FILE", str(tmp_path / "audit-head.json"))


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Show O7 negative-control failures under ``pytest -q``.

    Passing tests hide captured stdout. The candidate command is green only
    when those controls fail as designed, so the CI log has to print the lines.
    """
    lines = config.stash.get(O7_NEGATIVE_LINES, [])
    if not lines:
        return
    terminalreporter.write_sep("-", "O7 negative controls (fail as designed)")
    for line in lines:
        terminalreporter.write_line(line)
