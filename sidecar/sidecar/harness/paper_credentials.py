"""Credentials for the sidecar's own Alpaca paper account.

This loader never reads the cockpit keyring service or the cockpit env names.
A missing sidecar pair is a hard stop. It does not fall back.
"""

from __future__ import annotations

import os

SIDECAR_KEYRING_SERVICE = "ai-investment-cockpit-sidecar"
SIDECAR_KEYRING_KEY = "paper_key"
SIDECAR_KEYRING_SECRET = "paper_secret"
ENV_KEY = "SIDECAR_PAPER_ALPACA_KEY_ID"
ENV_SECRET = "SIDECAR_PAPER_ALPACA_SECRET"


class SidecarCredentialsMissing(RuntimeError):
    """The sidecar paper account credentials were not provisioned."""


def _keyring_pair() -> tuple[str, str] | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        key = keyring.get_password(SIDECAR_KEYRING_SERVICE, SIDECAR_KEYRING_KEY)
        secret = keyring.get_password(SIDECAR_KEYRING_SERVICE, SIDECAR_KEYRING_SECRET)
    except Exception:
        return None
    if key and secret:
        return key, secret
    return None


def load_sidecar_paper_credentials() -> tuple[str, str]:
    pair = _keyring_pair()
    if pair is not None:
        return pair
    key = os.environ.get(ENV_KEY)
    secret = os.environ.get(ENV_SECRET)
    if key and secret:
        return key, secret
    raise SidecarCredentialsMissing(
        "No sidecar paper credentials. This path does not use the cockpit account. "
        f"Provision `keyring set {SIDECAR_KEYRING_SERVICE} {SIDECAR_KEYRING_KEY}` and "
        f"`keyring set {SIDECAR_KEYRING_SERVICE} {SIDECAR_KEYRING_SECRET}`, or export both "
        f"{ENV_KEY} and {ENV_SECRET}."
    )
