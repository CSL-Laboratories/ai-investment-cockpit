"""Keychain detection and the ``doctor`` line for Cockpit keys (CSL-59, candidate; CSLR-D-011).

PRODUCT.md 055cc7a5, A44, 3.8 and 5.4: a host with an OS keychain keeps every saved key there; a host
with none keeps user-entered model-provider keys in an owner-only file and refuses broker and venue keys.
This module decides which of those two hosts Cockpit is on and says so in one ``doctor`` line.

Detection is read-only. It never writes, reads or deletes a key in the keychain (no canary round trip),
and it makes no provider, venue or network call. The keyring library is optional and loaded lazily
through an injectable loader, so tests use fakes and a host without the library reads as "no keychain".
A backend counts as a keychain only when it is a real one: the library's ``fail`` and ``null``
backends, unknown/file backends, mixed chainers, and unusable OS backends do not count.
A safe chainer is reduced to its first usable OS backend; storage stays bound to that backend.
The doctor line names where keys are kept and the key-file state; it never prints a key or its value.
"""

from __future__ import annotations

import importlib
import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cockpit.keys.store import KeyStore, key_file_state

__all__ = ["KeychainStatus", "OsKeyring", "default_key_file", "detect_keychain", "doctor", "doctor_line", "store_for_host"]

_NOT_A_KEYCHAIN = ("keyring.backends.fail", "keyring.backends.null")
_OS_KEYCHAINS = frozenset((
    "keyring.backends.SecretService", "keyring.backends.libsecret",
    "keyring.backends.kwallet", "keyring.backends.macOS", "keyring.backends.Windows",
))


@dataclass(frozen=True)
class KeychainStatus:
    available: bool
    backend: str  # e.g. "SecretService Keyring", or "" when none
    reason: str  # "ok" or why there is none


def _load_keyring_module() -> Any:
    return importlib.import_module("keyring")


def detect_keychain(load: Callable[[], Any] = _load_keyring_module) -> KeychainStatus:
    """Say whether this host has a usable OS keychain. Never touches a stored key."""
    return _detect_backend(load)[0]


def _detect_backend(load: Callable[[], Any]) -> tuple[KeychainStatus, Any]:
    try:
        mod = load()
    except ImportError:
        return KeychainStatus(False, "", "keyring_library_missing"), None
    except Exception as exc:  # noqa: BLE001 - failed detection never grants keychain access
        return KeychainStatus(False, "", f"keyring_library_error:{type(exc).__name__}"), None
    try:
        backend = mod.get_keyring()
        return _select_backend(backend, set())
    except Exception as exc:  # noqa: BLE001 - failed detection never grants keychain access
        return KeychainStatus(False, "", f"keyring_backend_error:{type(exc).__name__}"), None


def _select_backend(backend: Any, seen: set[int]) -> tuple[KeychainStatus, Any]:
    unavailable = KeychainStatus(False, "", "no_keychain_backend")
    if id(backend) in seen:
        return unavailable, None
    seen = seen | {id(backend)}
    module = type(backend).__module__ or ""
    if module in _NOT_A_KEYCHAIN:
        return unavailable, None
    if module == "keyring.backends.chainer":
        selected = None
        for child in backend.backends:
            # Only the standard non-storing sentinels are safe to ignore.
            if type(child).__module__ in _NOT_A_KEYCHAIN:
                continue
            status, candidate = _select_backend(child, seen)
            if not status.available:
                return unavailable, None
            if selected is None:
                selected = candidate
        if selected is None:
            return unavailable, None
        return KeychainStatus(True, _backend_name(selected), "ok"), selected
    if module not in _OS_KEYCHAINS:
        return unavailable, None
    try:
        priority = float(backend.priority)
    except Exception as exc:  # noqa: BLE001 - failed detection never grants keychain access
        return KeychainStatus(False, "", f"keychain_unusable:{type(exc).__name__}"), None
    if not math.isfinite(priority) or priority <= 0:
        return KeychainStatus(False, "", "keychain_unusable:priority"), None
    return KeychainStatus(True, _backend_name(backend), "ok"), backend


def _backend_name(backend: Any) -> str:
    return str(getattr(backend, "name", "") or type(backend).__name__)


class OsKeyring:
    """Adapter from the keyring library to ``KeyStore``'s injected ``get``/``set``/``delete``."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def get(self, service: str, name: str) -> str | None:
        return self._backend.get_password(service, name)

    def set(self, service: str, name: str, secret: str) -> None:
        self._backend.set_password(service, name, secret)

    def delete(self, service: str, name: str) -> None:
        try:
            self._backend.delete_password(service, name)
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

    status, backend = _detect_backend(load)
    # Pin the checked OS backend, never the mutable global module or chainer.
    ring = OsKeyring(backend) if status.available else None
    return KeyStore(keyring=ring, key_file=key_file if key_file is not None else default_key_file()), status


def _file_state(path: str) -> str:
    # Same owned-chain and directory-fd check as the store. Read-only.
    return key_file_state(path)


def doctor(status: KeychainStatus, key_file: str | os.PathLike[str] | None) -> dict[str, str]:
    """Structured doctor result: ``level`` is ``ok``, ``warn`` or ``fail``. Never contains a key value."""

    if status.available:
        out = {"check": "keys", "level": "ok", "keychain": status.backend,
               "model_provider": "keyring", "broker": "keyring", "venue": "keyring"}
        if key_file is not None and os.name != "nt":
            # A model-key file left from a host without a keychain is still checked.
            state = _file_state(os.fspath(key_file))
            if state.startswith(("dir_not_owner_only", "file_not_owner_only")):
                out.update(level="fail", key_file=state, stale_key_file=os.fspath(key_file))
        return out
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
    if result.get("keychain", "none") != "none":
        line = (f"keys: {result['level']} - OS keychain ({result['keychain']}); "
                "model-provider, broker and venue keys in the keychain")
        if "stale_key_file" in result:
            line += f"; old model-key file {result['stale_key_file']} is not owner-only [{result['key_file']}]"
        return line
    head = f"keys: {result['level']} - no OS keychain ({result['reason']})"
    mp = result["model_provider"]
    if mp.startswith("refused:"):
        return f"{head}; model-provider keys {mp}; broker and venue keys refused (keychain_required)"
    return (f"{head}; model-provider keys in owner-only file {mp.split(':', 1)[1]} [{result['key_file']}]; "
            "broker and venue keys refused (keychain_required)")
