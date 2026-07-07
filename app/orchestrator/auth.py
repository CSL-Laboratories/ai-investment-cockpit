"""WP5 — API-key auth dependency + loopback network posture (security-architecture §6).

The orchestrator API binds ``127.0.0.1`` only and puts **API-key auth on every route**
(§6, threat: a LAN attacker has no reachable surface, and even loopback callers must hold
the key). The key is loaded from keyring (``core.secrets``) — NEVER hard-coded, never in
env/logs (Invariant 4). An unauthenticated or wrong-key call is rejected ``401`` before any
handler runs.

The dependency is a callable-class instance so tests can inject a fixed key provider without
touching the real keyring; production defaults to the keyring lookup.
"""
from __future__ import annotations

import hmac
from typing import Callable

from fastapi import Header, HTTPException, status

from core.secrets import get_secret

API_KEY_HEADER = "X-API-Key"
API_KEY_SECRET_NAME = "cockpit_api_key"          # keyring entry name (value never in code)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def keyring_api_key_provider() -> str | None:
    """Resolve the expected API key from keyring (Windows Credential Manager at runtime).
    Returns ``None`` when unset — the guard then fails closed (401), never fail-open."""
    red = get_secret(API_KEY_SECRET_NAME)
    return red.reveal() if red is not None else None


class ApiKeyAuth:
    """FastAPI dependency: require a valid ``X-API-Key`` header on every route.

    Comparison is constant-time (``hmac.compare_digest``). A missing/empty configured key
    fails closed (401) rather than admitting all callers.
    """

    def __init__(self, key_provider: Callable[[], str | None] = keyring_api_key_provider) -> None:
        self._provider = key_provider

    async def __call__(
        self,
        x_api_key: str | None = Header(default=None, alias=API_KEY_HEADER),
    ) -> str:
        expected = self._provider()
        if not expected:
            # No key provisioned -> refuse every call (fail-closed, never fail-open).
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key not configured",
            )
        if not x_api_key or not hmac.compare_digest(str(x_api_key), str(expected)):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        return x_api_key


def is_loopback(host: str) -> bool:
    return host in _LOOPBACK_HOSTS


def assert_loopback(host: str) -> None:
    """Refuse to bind a non-loopback host (security-architecture §6 — zero inbound ports)."""
    if not is_loopback(host):
        raise ValueError(
            f"refusing to bind non-loopback host {host!r}; the API is loopback-only "
            "(security-architecture §6)"
        )
