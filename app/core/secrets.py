"""WP0 — secrets via keyring (Windows Credential Manager; docs/setup/accounts-and-keys.md
§5). Invariant 4: no secret value may appear in code, config, logs, or prompts — callers
must call ``.reveal()`` explicitly at the point of use; ``repr``/``str`` never expose it.
"""
from __future__ import annotations

import keyring

_SERVICE = "ai-investment-cockpit"


class Redacted:
    """Wraps a secret value so logging/``repr``/``str`` never leak it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:  # noqa: D105 — intentional redaction, not a no-op stub
        return "Redacted('***')"

    def __str__(self) -> str:
        return "Redacted('***')"


def get_secret(name: str) -> Redacted | None:
    """Look up a secret by name. Returns ``None`` when unset — callers fail closed."""
    value = keyring.get_password(_SERVICE, name)
    return Redacted(value) if value is not None else None


def require_secret(name: str) -> Redacted:
    """Look up a secret by name; raises when unset (fail loud at startup)."""
    secret = get_secret(name)
    if secret is None:
        raise KeyError(f"secret {name!r} not found in keyring service {_SERVICE!r}")
    return secret


def set_secret(name: str, value: str) -> None:
    keyring.set_password(_SERVICE, name, value)
