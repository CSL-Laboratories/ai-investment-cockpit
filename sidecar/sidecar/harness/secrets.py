"""Alpaca paper credentials from the cockpit keyring, then from the environment.

The keyring service and secret names match ``app/core/secrets.py``: service
``ai-investment-cockpit``, names ``alpaca_key`` and ``alpaca_secret``. A partial pair is
ignored. Values are never included in exceptions.
"""

from __future__ import annotations

import os

KEYRING_SERVICE = "ai-investment-cockpit"
KEYRING_KEY = "alpaca_key"
KEYRING_SECRET = "alpaca_secret"
ENV_KEY = "SIDECAR_ALPACA_KEY_ID"
ENV_SECRET = "SIDECAR_ALPACA_SECRET"


class CredentialsMissing(RuntimeError):
    """Neither keyring nor the environment had a complete paper-key pair."""


def _keyring_pair() -> tuple[str, str] | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_KEY)
        secret = keyring.get_password(KEYRING_SERVICE, KEYRING_SECRET)
    except Exception:
        return None
    if key and secret:
        return key, secret
    return None


def load_alpaca_credentials() -> tuple[str, str]:
    pair = _keyring_pair()
    if pair is not None:
        return pair
    key = os.environ.get(ENV_KEY)
    secret = os.environ.get(ENV_SECRET)
    if key and secret:
        return key, secret
    raise CredentialsMissing(
        "No Alpaca paper credentials. Provision both keyring items "
        f"`keyring set {KEYRING_SERVICE} {KEYRING_KEY}` and "
        f"`keyring set {KEYRING_SERVICE} {KEYRING_SECRET}`, or export both "
        f"{ENV_KEY} and {ENV_SECRET}. The sidecar only reads the paper API."
    )
