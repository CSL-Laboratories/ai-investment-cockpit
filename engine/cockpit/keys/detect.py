"""Keychain detection and the ``doctor`` line for Cockpit keys (CSL-59, candidate; CSLR-D-011).

PRODUCT.md 055cc7a5, A44, 3.8 and 5.4: a host with an OS keychain keeps every saved key there; a host
with none keeps user-entered model-provider keys in an owner-only file and refuses broker and venue keys.
This module decides which of those two hosts Cockpit is on and says so in one ``doctor`` line.

Detection is read-only. It never writes, reads or deletes a key in the keychain (no canary round trip),
and it makes no provider, venue or network call. The keyring library is optional and loaded lazily
through an injectable loader, so tests use fakes and a host without the library reads as "no keychain".
A backend counts as a keychain only when it is a real one: the library's ``fail`` and ``null``
backends, unknown/file backends, and unusable OS backends do not count.

A keyring chain (the library's ``ChainerBackend``) is reduced to one backend whose module is one of
the five OS keychains below: when it holds several, the one first in Cockpit's fixed order
(``_OS_ORDER``), not in the library's, so one host picks the same keychain in every session. Every
other backend in the chain is ignored and never called, not even to read its priority, and the doctor
line names it. A chain with no OS keychain is refused.
Recommendation B13, which Lucas accepted on Nov 12, 2026 ("I agree with all B10-B14 Recommendations"):
"use the OS backend, name the ignored one in the doctor line".
The chain is read once, and storage stays bound to the chosen OS backend object, never to the chain
or the module, so a chain that changes after the check cannot redirect a key to another backend.
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
# Which OS keychain a chain of several uses. Lucas, Nov 12, 2026, asked "On KDE, should Cockpit use the
# same keychain in every session (a fixed order)?": "Yes, fixed order". This is keyring 25.7.0's own order
# outside a KDE desktop session (Secret Service 5, KWallet 5 4.9, libsecret 4.8, KWallet 4 3.9); the
# library raises KWallet to 5.1 (and KWallet 4 to 4.1) when XDG_CURRENT_DESKTOP contains KDE, so with its
# order one host would use KWallet in its desktop session and Secret Service over SSH, in a service or in
# cron. A fixed order picks the same keychain in every session. Entries are a class ("module.Class") or,
# as a fallback, a module; macOS and Windows never share a chain with the others. A tie keeps the chain's
# order.
_OS_ORDER = (
    "keyring.backends.SecretService",
    "keyring.backends.kwallet.DBusKeyring",  # KWallet 5
    "keyring.backends.libsecret",
    "keyring.backends.kwallet.DBusKeyringKWallet4",  # KWallet 4
    "keyring.backends.kwallet",  # any other KWallet class
    "keyring.backends.macOS",
    "keyring.backends.Windows",
)
_OS_RANK = {key: rank for rank, key in enumerate(_OS_ORDER)}
assert _OS_KEYCHAINS <= _OS_RANK.keys()
_CHAINER = "keyring.backends.chainer"
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
        backend = mod.get_keyring()
        if _module_of(backend) == _CHAINER:
            return _select_from_chain(backend)
        return _usable_os_backend(backend, ())
    except Exception as exc:  # noqa: BLE001 - failed detection never grants keychain access
        return KeychainStatus(False, "", f"keyring_backend_error:{type(exc).__name__}"), None


def _module_of(obj: Any) -> str:
    # From the object's real type, never from the instance. Only an exact ``str`` counts, so a str
    # subclass with its own ``__eq__``/``__hash__`` cannot pass for an allowlisted module name.
    module = getattr(type(obj), "__module__", "")
    return module if type(module) is str else ""


def _select_from_chain(chain: Any) -> tuple[KeychainStatus, Any]:
    # Read once: the library recomputes ``ChainerBackend.backends`` on every access.
    children = tuple(chain.backends)
    modules = [_module_of(child) for child in children]
    ranked = [(_os_rank(child, module), index) for index, (child, module) in enumerate(zip(children, modules))
              if module in _OS_KEYCHAINS]
    if not ranked:
        return KeychainStatus(False, "", "no_keychain_backend"), None
    chosen = min(ranked)[1]  # first in Cockpit's order; the chain's order only breaks a tie
    # The rest are never called, not even for their priority; only their type's name is read.
    # The library's non-storing sentinels (fail, null) are skipped without a name.
    ignored = tuple(_type_name(child) for index, (child, module) in enumerate(zip(children, modules))
                    if index != chosen and module not in _NOT_A_KEYCHAIN)
    return _usable_os_backend(children[chosen], ignored)


def _os_rank(backend: Any, module: str) -> int:
    # The class's place in ``_OS_ORDER`` if listed, else its module's. Read from the type, never the instance.
    qualname = getattr(type(backend), "__qualname__", "")
    if type(qualname) is str and f"{module}.{qualname}" in _OS_RANK:
        return _OS_RANK[f"{module}.{qualname}"]
    return _OS_RANK[module]


def _usable_os_backend(backend: Any, ignored: tuple[str, ...]) -> tuple[KeychainStatus, Any]:
    if _module_of(backend) not in _OS_KEYCHAINS:
        return KeychainStatus(False, "", "no_keychain_backend"), None
    try:
        priority = float(backend.priority)
    except Exception as exc:  # noqa: BLE001 - failed detection never grants keychain access
        return KeychainStatus(False, "", f"keychain_unusable:{type(exc).__name__}"), None
    if not math.isfinite(priority) or priority <= 0:
        return KeychainStatus(False, "", "keychain_unusable:priority"), None
    return KeychainStatus(True, _backend_name(backend), "ok", ignored), backend


def _backend_name(backend: Any) -> str:
    return str(getattr(backend, "name", "") or type(backend).__name__)


def _type_name(obj: Any) -> str:
    """``module.Class`` of an ignored backend, made safe for a one-line doctor message."""
    kind = type(obj)
    module = _module_of(obj) or "?"
    qualname = getattr(kind, "__qualname__", "")
    text = f"{module}.{qualname if type(qualname) is str else '?'}"
    return "".join(ch if ch in _SAFE_NAME else "?" for ch in text)[:120]


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
