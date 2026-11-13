"""Keychain detection and the ``doctor`` line for Cockpit keys (CSL-59, candidate; CSLR-D-011).

PRODUCT.md 055cc7a5, A44, 3.8 and 5.4: a host with an OS keychain keeps every saved key there; a host
with none keeps user-entered model-provider keys in an owner-only file and refuses broker and venue keys.
This module decides which of those two hosts Cockpit is on and says so in one ``doctor`` line.

Detection is read-only. It never writes, reads or deletes a key in the keychain (no canary round trip),
and it makes no provider, venue or network call. The keyring library is optional and loaded lazily
through an injectable loader, so tests use fakes and a host without the library reads as "no keychain".
A backend counts as an OS keychain only when its type *is* one of the keyring library's own classes
listed in ``_OS_CLASSES`` (exact identity: not a subclass, not a look-alike with the same module or
class name, not a proxy), imported from the library itself; a class whose module will not import here
is a keychain this host does not have. Its priority must read as a finite number above 0. The library's
``fail`` and ``null`` backends and every other backend do not count.

A keyring chain (the library's own ``ChainerBackend``, by identity) is read once and reduced to one OS
keychain: when it holds several, the one first in Cockpit's fixed order (``_OS_CLASSES``), not in the
library's, so one host picks the same keychain in every session. The other backends in the chain are
ignored and named in the doctor line; Cockpit sends them no password operation. It does not stop the
library's own discovery from running a backend's code: building the chain, the library reads every
member's priority, and a priority that raises there makes detection fail closed (no keychain).
Recommendation B13, which Lucas accepted on Nov 12, 2026 ("I agree with all B10-B14 Recommendations"):
"use the OS backend, name the ignored one in the doctor line".
Storage stays bound to the chosen backend object and its genuine class: every password operation goes
through that class's own method, never through the chain, the module, or an attribute set on the
instance later, so neither a chain that changes after the check nor a later swap can redirect a key.
What this cannot defend against: hostile code already running in this process, such as a replaced
import of a keyring module or a monkey-patched genuine class.
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

# The supported OS keychains: the keyring library's own classes, admitted by identity, in Cockpit's fixed
# order. Lucas, Nov 12, 2026, asked "On KDE, should Cockpit use the same keychain in every session (a fixed
# order)?": "Yes, fixed order". This is keyring 25.7.0's own order outside a KDE desktop session (Secret
# Service 5, KWallet 5 4.9, libsecret 4.8, KWallet 4 3.9); the library raises KWallet to 5.1 (and KWallet 4
# to 4.1) when XDG_CURRENT_DESKTOP contains KDE, so with its order one host would use KWallet in its desktop
# session and Secret Service over SSH, in a service or in cron. macOS and Windows never share a chain with
# the others. A class not listed here is not an OS keychain, whatever its module or name says.
_OS_CLASSES = (
    ("keyring.backends.SecretService", "Keyring"),
    ("keyring.backends.kwallet", "DBusKeyring"),  # KWallet 5
    ("keyring.backends.libsecret", "Keyring"),
    ("keyring.backends.kwallet", "DBusKeyringKWallet4"),  # KWallet 4
    ("keyring.backends.macOS", "Keyring"),
    ("keyring.backends.Windows", "WinVaultKeyring"),
)
_SENTINEL_CLASSES = (("keyring.backends.fail", "Keyring"), ("keyring.backends.null", "Keyring"))
_CHAINER_CLASS = ("keyring.backends.chainer", "ChainerBackend")
_NAMED_IGNORED = 8  # the doctor line names at most this many ignored backends, then counts the rest
_SAFE_NAME = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-<>")


@dataclass(frozen=True)
class KeychainStatus:
    available: bool
    backend: str  # e.g. "SecretService Keyring", or "" when none
    reason: str  # "ok" or why there is none
    ignored: tuple[str, ...] = ()  # other backends of the chain, never used ("module.Class")


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
        genuine = _Genuine.load()
        backend = mod.get_keyring()
        if genuine.is_chainer(backend):
            return _select_from_chain(backend, genuine)
        return _usable_os_backend(backend, (), genuine)
    except Exception as exc:  # noqa: BLE001 - failed detection never grants keychain access
        return KeychainStatus(False, "", f"keyring_backend_error:{type(exc).__name__}"), None


def _genuine_class(module: str, name: str) -> type | None:
    try:
        found = getattr(importlib.import_module(module), name)
    except Exception:  # noqa: BLE001 - a backend module that will not import here is a keychain this host lacks
        return None
    return found if isinstance(found, type) else None


@dataclass(frozen=True)
class _Genuine:
    """The keyring library's own classes, imported from the library. Compared only with ``is``."""

    os_classes: tuple[type | None, ...]  # in ``_OS_CLASSES`` order; None where it will not import here
    sentinels: tuple[type | None, ...]
    chainer: type | None

    @classmethod
    def load(cls) -> _Genuine:
        return cls(tuple(_genuine_class(*spec) for spec in _OS_CLASSES),
                   tuple(_genuine_class(*spec) for spec in _SENTINEL_CLASSES),
                   _genuine_class(*_CHAINER_CLASS))

    def rank(self, obj: Any) -> int | None:
        """Place of ``type(obj)`` in the fixed order, or None when it is not a genuine OS keychain class."""
        kind = type(obj)
        for rank, genuine in enumerate(self.os_classes):
            if genuine is not None and kind is genuine:
                return rank
        return None

    def is_chainer(self, obj: Any) -> bool:
        return self.chainer is not None and type(obj) is self.chainer

    def is_sentinel(self, obj: Any) -> bool:
        return any(genuine is not None and type(obj) is genuine for genuine in self.sentinels)


def _module_of(obj: Any) -> str:
    # For display only (admission is by class identity). Only an exact ``str`` is shown.
    module = getattr(type(obj), "__module__", "")
    return module if type(module) is str else ""


def _select_from_chain(chain: Any, genuine: _Genuine) -> tuple[KeychainStatus, Any]:
    # Read once: the library recomputes ``ChainerBackend.backends`` on every access.
    children = tuple(chain.backends)
    ranked = [(rank, index) for index, rank in enumerate(genuine.rank(child) for child in children)
              if rank is not None]
    if not ranked:
        return KeychainStatus(False, "", "no_keychain_backend"), None
    chosen = min(ranked)[1]  # first in Cockpit's order; the chain's order only breaks a tie (same class twice)
    # Cockpit sends the rest no password operation; only their type's name is read, to name them.
    # The library's non-storing sentinels (fail, null) are skipped without a name.
    ignored = tuple(_type_name(child) for index, child in enumerate(children)
                    if index != chosen and not genuine.is_sentinel(child))
    return _usable_os_backend(children[chosen], ignored, genuine)


def _usable_os_backend(backend: Any, ignored: tuple[str, ...], genuine: _Genuine) -> tuple[KeychainStatus, Any]:
    if genuine.rank(backend) is None:
        return KeychainStatus(False, "", "no_keychain_backend"), None
    try:
        priority = float(type(backend).priority)  # the genuine class's own probe, not an instance attribute
    except Exception as exc:  # noqa: BLE001 - failed detection never grants keychain access
        return KeychainStatus(False, "", f"keychain_unusable:{type(exc).__name__}"), None
    if not math.isfinite(priority) or priority <= 0:
        return KeychainStatus(False, "", "keychain_unusable:priority"), None
    return KeychainStatus(True, _backend_name(backend), "ok", ignored), backend


def _backend_name(backend: Any) -> str:
    return str(getattr(type(backend), "name", "") or type(backend).__name__)


def _type_name(obj: Any) -> str:
    """``module.Class`` of an ignored backend, made safe for a one-line doctor message."""
    kind = type(obj)
    module = _module_of(obj) or "?"
    qualname = getattr(kind, "__qualname__", "")
    text = f"{module}.{qualname if type(qualname) is str else '?'}"
    return "".join(ch if ch in _SAFE_NAME else "?" for ch in text)[:120]


class OsKeyring:
    """Adapter from the keyring library to ``KeyStore``'s injected ``get``/``set``/``delete``.

    Pins the checked backend object and its class at construction. Every call goes through that class's
    own method, so an attribute set on the instance, or a ``__class__`` reassigned, after the check
    changes nothing.
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self._cls = type(backend)

    def get(self, service: str, name: str) -> str | None:
        return self._cls.get_password(self._backend, service, name)

    def set(self, service: str, name: str, secret: str) -> None:
        self._cls.set_password(self._backend, service, name, secret)

    def delete(self, service: str, name: str) -> None:
        try:
            self._cls.delete_password(self._backend, service, name)
        except Exception as exc:
            if type(exc).__name__ != "PasswordDeleteError":
                raise


def _home(env: dict[str, str]) -> str:
    return env.get("HOME") or os.path.expanduser("~")


def default_key_file(env: dict[str, str] | None = None) -> str:
    """Owner-only file location (candidate): ``$XDG_CONFIG_HOME/csl-cockpit/model-keys.json``."""

    env = dict(os.environ) if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(_home(env), ".config")
    return os.path.join(base, "csl-cockpit", "model-keys.json")


def _config_home_for(path: str, env: dict[str, str]) -> str | None:
    """The home folder whose missing ``.config`` a first save may create, or ``None``.

    Recommendation B14, which Lucas accepted on Nov 12, 2026: "also create `~/.config` when it is
    missing, owner-only, and only when the home folder is the user's own". Only for the default file
    ``~/.config/csl-cockpit/model-keys.json``;
    any other key file (a custom path, or ``$XDG_CONFIG_HOME`` pointing elsewhere) gets no such help.
    ``KeyStore`` checks the home folder itself before it creates anything.
    """

    home = _home(env)
    if not os.path.isabs(home):
        return None
    default = os.path.join(home, ".config", "csl-cockpit", "model-keys.json")
    return home if os.path.abspath(path) == os.path.abspath(default) else None


def store_for_host(key_file: str | os.PathLike[str] | None = None,
                   load: Callable[[], Any] = _load_keyring_module) -> tuple[KeyStore, KeychainStatus]:
    """Build the ``KeyStore`` this host should use: the OS keyring if detected, otherwise ``keyring=None``."""

    status, backend = _detect_backend(load)
    # Pin the checked OS backend, never the mutable global module or chainer.
    ring = OsKeyring(backend) if status.available else None
    env = dict(os.environ)
    path = os.fspath(key_file) if key_file is not None else default_key_file(env)
    return KeyStore(keyring=ring, key_file=path, config_home=_config_home_for(path, env)), status


def _file_state(path: str) -> str:
    # Same owned-chain and directory-fd check as the store. Read-only.
    return key_file_state(path)


def doctor(status: KeychainStatus, key_file: str | os.PathLike[str] | None) -> dict[str, str]:
    """Structured doctor result: ``level`` is ``ok``, ``warn`` or ``fail``. Never contains a key value."""

    if status.available:
        out = {"check": "keys", "level": "ok", "keychain": status.backend,
               "model_provider": "keyring", "broker": "keyring", "venue": "keyring"}
        if status.ignored:
            named = list(status.ignored[:_NAMED_IGNORED])
            if len(status.ignored) > _NAMED_IGNORED:
                named.append(f"and {len(status.ignored) - _NAMED_IGNORED} more")
            out["ignored_backends"] = ", ".join(named)
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
        if "ignored_backends" in result:
            line += f"; other keyring backends ignored: {result['ignored_backends']}"
        if "stale_key_file" in result:
            line += f"; old model-key file {result['stale_key_file']} is not owner-only [{result['key_file']}]"
        return line
    head = f"keys: {result['level']} - no OS keychain ({result['reason']})"
    mp = result["model_provider"]
    if mp.startswith("refused:"):
        return f"{head}; model-provider keys {mp}; broker and venue keys refused (keychain_required)"
    return (f"{head}; model-provider keys in owner-only file {mp.split(':', 1)[1]} [{result['key_file']}]; "
            "broker and venue keys refused (keychain_required)")
