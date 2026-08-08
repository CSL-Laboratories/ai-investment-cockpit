"""WP0 test fixtures — an in-memory keyring so secret round-trips run on any CI host
(Windows Credential Manager is unavailable on Linux CI)."""
from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend


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


@pytest.fixture(autouse=True)
def _isolate_audit_head(tmp_path, monkeypatch):
    """Never let a test anchor its temporary DB into the operator's runtime head file.

    ``audit_head_file`` is relative to the process cwd by default. The suite also runs from
    ``app/``, so composed scheduler tests previously advanced ``app/data/audit-head.json`` with
    temporary chains, making the real DB correctly fail closed with ``AnchorRewind``. Give every
    test its own witness mirror; tests that need a specific path may still override this env value.
    """
    monkeypatch.setenv("COCKPIT_AUDIT_HEAD_FILE", str(tmp_path / "audit-head.json"))
