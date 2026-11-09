"""Keychain detection and the ``doctor`` line for Cockpit keys (CSL-59, candidate; CSLR-D-011).

PRODUCT.md 055cc7a5, A44, 3.8 and 5.4: a host with an OS keychain keeps every saved key there; a host
with none keeps user-entered model-provider keys in an owner-only file and refuses broker and venue keys.
This module decides which of those two hosts Cockpit is on and says so in one ``doctor`` line.

Detection is read-only. It never writes, reads or deletes a key in the keychain (no canary round trip),
and it makes no provider, venue or network call. The keyring library is optional and loaded lazily
through an injectable loader, so tests use fakes and a host without the library reads as "no keychain".
A backend counts as a keychain only when it is a real one: the library's ``fail`` and ``null``
backends, a chainer with no usable backends, and any backend with priority <= 0 do not count.
The doctor line names where keys are kept and the key-file state; it never prints a key or its value.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cockpit.keys.store import KeyStore, key_file_state

__all__ = ["KeychainStatus", "OsKeyring", "default_key_file", "detect_keychain", "doctor", "doctor_line", "store_for_host"]

_NOT_A_KEYCHAIN = ("keyring.backends.fail", "keyring.backends.null")


@dataclass(frozen=True)
class KeychainStatus:
    available: bool
    backend: str  # e.g. "SecretService Keyring", or "" when none
    reason: str  # "ok" or why there is none


def _load_keyring_module() -> Any:
    return importlib.import_module("keyring")


def detect_keychain(load: Callable[[], Any] = _load_keyring_module) -> KeychainStatus:
    """Say whether this host has a usable OS keychain. Read-only; never touches a stored key."""

    try:
        mod = load()
    except ImportError:
        return KeychainStatus(False, "", "keyring_library_missing")
    except Exception as exc:  # a broken install reads as no keychain, never as a crash
        return KeychainStatus(False, "", f"keyring_library_error:{type(exc).__name__}")
    try:
        backend = mod.get_keyring()
    except Exception as exc:
        return KeychainStatus(False, "", f"keyring_backend_error:{type(exc).__name__}")
    return _classify(backend)


def _classify(backend: Any) -> KeychainStatus:
    module = type(backend).__module__ or ""
    name = _backend_name(backend)
    if module.startswith(_NOT_A_KEYCHAIN):
        return KeychainStatus(False, "", "no_keychain_backend")
    children = getattr(backend, "backends", None)
    if module.startswith("keyring.backends.chainer"):
        usable = [b for b in (children or ()) if _classify(b).available]
        if not usable:
            return KeychainStatus(False, "", "no_keychain_backend")
        return KeychainStatus(True, ", ".join(_backend_name(b) for b in usable), "ok")
    try:
        priority = float(getattr(type(backend), "priority", getattr(backend, "priority", 0)))
    except Exception as exc:  # keyring backends raise from ``priority`` when unusable (e.g. no D-Bus)
        return KeychainStatus(False, "", f"keychain_unusable:{type(exc).__name__}")
    if priority <= 0:
        return KeychainStatus(False, "", "keychain_unusable:priority")
    return KeychainStatus(True, name, "ok")


def _backend_name(backend: Any) -> str:
    return str(getattr(backend, "name", "") or type(backend).__name__)


class OsKeyring:
    """Adapter from the keyring library to ``KeyStore``'s injected ``get``/``set``/``delete``."""

    def __init__(self, mod: Any) -> None:
        self._mod = mod

    def get(self, service: str, name: str) -> str | None:
        return self._mod.get_password(service, name)

    def set(self, service: str, name: str, secret: str) -> None:
        self._mod.set_password(service, name, secret)

    def delete(self, service: str, name: str) -> None:
        try:
            self._mod.delete_password(service, name)
        except Exception as exc:
            if type(exc).__name__ != "PasswordDeleteError":
                raise


def default_key_file(env: dict[str, str] | None = None) -> str:
    """Owner-only file location (candidate): ``$XDG_CONFIG_HOME/csl-cockpit/model-keys.json``."""

    env = dict(os.environ) if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "csl-cockpit", "model-keys.json")


def store_for_host(key_file: str | os.PathLike[str] | None = None,
                   load: Callable[[], Any] = _load_keyring_module) -> tuple[KeyStore, KeychainStatus]:
    """Build the ``KeyStore`` this host should use: the OS keyring if detected, otherwise ``keyring=None``."""

    status = detect_keychain(load)
    ring = OsKeyring(load()) if status.available else None
    return KeyStore(keyring=ring, key_file=key_file if key_file is not None else default_key_file()), status


def _file_state(path: str) -> str:
    # Same owned-chain and directory-fd check as the store. Read-only.
    return key_file_state(path)


def doctor(status: KeychainStatus, key_file: str | os.PathLike[str] | None) -> dict[str, str]:
    """Structured doctor result: ``level`` is ``ok``, ``warn`` or ``fail``. Never contains a key value."""

    if status.available:
        return {"check": "keys", "level": "ok", "keychain": status.backend,
                "model_provider": "keyring", "broker": "keyring", "venue": "keyring"}
    out = {"check": "keys", "keychain": "none", "reason": status.reason,
           "broker": "refused:keychain_required", "venue": "refused:keychain_required"}
    if os.name == "nt":
        out.update(level="fail", model_provider="refused:owner_only_file_unsupported")
        return out
    if key_file is None:
        out.update(level="fail", model_provider="refused:key_file_not_configured")
        return out
    path = os.fspath(key_file)
    state = _file_state(path)
    bad = state.startswith(("dir_not_owner_only", "file_not_owner_only"))
    out.update(level="fail" if bad else "warn", model_provider=f"owner_only_file:{path}", key_file=state)
    return out


def doctor_line(result: dict[str, str]) -> str:
    if result["level"] == "ok":
        return f"keys: ok - OS keychain ({result['keychain']}); model-provider, broker and venue keys in the keychain"
    head = f"keys: {result['level']} - no OS keychain ({result['reason']})"
    mp = result["model_provider"]
    if mp.startswith("refused:"):
        return f"{head}; model-provider keys {mp}; broker and venue keys refused (keychain_required)"
    return (f"{head}; model-provider keys in owner-only file {mp.split(':', 1)[1]} [{result['key_file']}]; "
            "broker and venue keys refused (keychain_required)")
