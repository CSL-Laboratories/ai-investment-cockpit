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
``fail`` and ``null`` backends and every other backend do not count. A refusal says why when the backend
looks meant as a supported keychain: ``unsupported_keychain_class:<module.Class>`` (it derives from, or
carries the module and class name of, a supported keychain class but is not that class), or
``keychain_module_unavailable:<module>:<ExceptionType>`` (its class names a supported keychain whose module
failed to import here; the exception's message is never shown). The library's chainer gets the same two:
``unsupported_keyring_chain:<module.Class>`` (a chain that derives from, or carries the module and class name
of, the library's ``ChainerBackend``; Cockpit does not read its members) or
``keychain_module_unavailable:keyring.backends.chainer:<ExceptionType>``, and the library's own chain found
inside another chain gives ``unsupported_nested_keyring_chain`` (the inner chain is not read). A module no
candidate claims, such as another platform's keychain, failing to import is not reported. A reason may name
the refused class or an exception's class, read from the interpreter's own type fields (no metaclass hook
runs), cleaned to one line and capped by ``_safe``; Cockpit does not read exception messages or stored values
for it. The selected keychain is shown by Cockpit's own name for its class, never by the class's text.

A keyring chain (the library's own ``ChainerBackend``, by identity) is read once and reduced to one OS
keychain: when it holds several, the one first in Cockpit's fixed order (``_OS_CLASSES``), not in the
library's, so one host picks the same keychain in every session that sees the same set of available
keychain classes and the same explicit backend setting (``PYTHON_KEYRING_BACKEND`` or ``keyringrc.cfg``).
The other backends in the chain are ignored and named in the doctor line; Cockpit sends them no password
operation. It does not stop the library's own discovery from running a backend's code: building the
chain, the library reads every member's priority, and a priority that raises there makes detection fail
closed (no keychain).
Recommendation B13, which Lucas accepted on Nov 12, 2026 ("I agree with all B10-B14 Recommendations"):
"use the OS backend, name the ignored one in the doctor line".
Storage stays bound to the chosen backend object and its genuine class (``OsKeyring``): every password
operation calls that class's own top-level method, so a global keyring or a chain replaced after the check,
or a top-level password method shadowed on the instance, redirects nothing, and a reassigned ``__class__``
is refused at use. That is all pinning does: the class's methods still consult the instance, its helpers
and its state, so code that changes the selected object after the check can redirect a key.
What this cannot defend against: hostile code already running in this process, such as a replaced import
of a keyring module, a monkey-patched genuine class, or a changed helper, state or class of the selected
object.
The doctor line names where keys are kept and the key-file state; it reads no stored key or value. On a
keychain host it also reads the keychain record beside the key file (B24, see ``store``), from that file
only: it never opens the keychain, so it does not read Cockpit's marker there.
"""

from __future__ import annotations

import importlib
import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cockpit.keys.store import KeyRefused, KeyStore, key_file_state, keychain_record_state, record_way_out

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
        return KeychainStatus(False, "", f"keyring_library_error:{_exception_name(exc)}"), None
    try:
        genuine = _Genuine.load()
        backend = mod.get_keyring()
        if genuine.is_chainer(backend):
            return _select_from_chain(backend, genuine)
        return _usable_os_backend(backend, (), genuine)
    except Exception as exc:  # noqa: BLE001 - failed detection never grants keychain access
        return KeychainStatus(False, "", f"keyring_backend_error:{_exception_name(exc)}"), None


def _genuine_class(module: str, name: str) -> tuple[type | None, str]:
    """The library's class ``module.name``, or None and why: the import error's type, never its message."""
    try:
        found = getattr(importlib.import_module(module), name)
    except Exception as exc:  # noqa: BLE001 - a backend module that will not import here is a keychain this host lacks
        return None, _type_field(type(exc), "__name__") or "?"
    return (found, "") if isinstance(found, type) else (None, "NotAClass")


@dataclass(frozen=True)
class _Genuine:
    """The keyring library's own classes, imported from the library. Compared only with ``is``."""

    os_classes: tuple[type | None, ...]  # in ``_OS_CLASSES`` order; None where it will not import here
    failures: tuple[str, ...]  # per ``_OS_CLASSES`` entry: the import error's type name, or ""
    sentinels: tuple[type | None, ...]
    chainer: type | None
    chainer_failure: str  # the chainer module's import error type name, or ""

    @classmethod
    def load(cls) -> _Genuine:
        found = [_genuine_class(*spec) for spec in _OS_CLASSES]
        return cls(tuple(kind for kind, _ in found), tuple(failure for _, failure in found),
                   tuple(_genuine_class(*spec)[0] for spec in _SENTINEL_CLASSES),
                   *_genuine_class(*_CHAINER_CLASS))

    def diagnose(self, obj: Any) -> str | None:
        """Why a backend that is not admitted looks meant as a supported keychain or as the library's chain,
        or None if it does not.

        For the doctor only; admission never depends on it. Reads only the interpreter's own fields of the
        object's type (no instance attribute, no metaclass hook), so a chain is not enumerated here.
        """
        kind = type(obj)
        if self.chainer is not None and kind is self.chainer:
            return "unsupported_nested_keyring_chain"  # the library's own chain inside another: not read
        module = _type_field(kind, "__module__")
        qualname = _type_field(kind, "__qualname__")
        bases = _true_mro(kind)[1:]
        candidates = [(spec, genuine, failure, "unsupported_keychain_class")
                      for spec, genuine, failure in zip(_OS_CLASSES, self.os_classes, self.failures)]
        candidates.append((_CHAINER_CLASS, self.chainer, self.chainer_failure, "unsupported_keyring_chain"))
        for (spec_module, spec_name), genuine, failure, unsupported in candidates:
            names_it = module == spec_module and qualname == spec_name
            if genuine is None:
                if failure and names_it:
                    return f"keychain_module_unavailable:{spec_module}:{_safe(failure)}"
            elif names_it or any(base is genuine for base in bases):
                return f"{unsupported}:{_type_name(obj)}"
        return None

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


def _type_field(kind: type, field: str) -> str:
    """``kind.__module__``, ``__qualname__`` or ``__name__`` as the interpreter stores it, or "".

    Read through ``type``'s own descriptor, so a metaclass of the class (code of the refused backend)
    is not consulted; only an exact ``str`` is used. For display only: admission is by class identity.
    """
    try:
        value = type.__dict__[field].__get__(kind)
    except Exception:  # noqa: BLE001 - an unreadable field is shown as unknown
        return ""
    return value if type(value) is str else ""


def _exception_name(exc: BaseException) -> str:
    return _safe(_type_field(type(exc), "__name__") or "?")


def _true_mro(kind: type) -> tuple[type, ...]:
    # The interpreter's own MRO for the class, not what a metaclass may claim. Diagnostics only.
    try:
        mro = type.__dict__["__mro__"].__get__(kind)
    except Exception:  # noqa: BLE001 - a class without a readable MRO is simply not diagnosed
        return ()
    return mro if type(mro) is tuple else ()


def _safe(text: str) -> str:
    """One line of letters, digits and ``._-<>`` (anything else shown as ``?``), at most 120 characters."""
    return "".join(ch if ch in _SAFE_NAME else "?" for ch in str(text))[:120]


def _refused(reason: str) -> tuple[KeychainStatus, Any]:
    return KeychainStatus(False, "", reason), None


def _select_from_chain(chain: Any, genuine: _Genuine) -> tuple[KeychainStatus, Any]:
    # Read once: the library recomputes ``ChainerBackend.backends`` on every access.
    children = tuple(chain.backends)
    ranked = [(rank, index) for index, rank in enumerate(genuine.rank(child) for child in children)
              if rank is not None]
    if not ranked:
        why = next((reason for reason in map(genuine.diagnose, children) if reason), "no_keychain_backend")
        return _refused(why)
    chosen = min(ranked)[1]  # first in Cockpit's order; the chain's order only breaks a tie (same class twice)
    # Cockpit sends the rest no password operation; only their type's name is read, to name them.
    # The library's non-storing sentinels (fail, null) are skipped without a name.
    ignored = tuple(_type_name(child) for index, child in enumerate(children)
                    if index != chosen and not genuine.is_sentinel(child))
    return _usable_os_backend(children[chosen], ignored, genuine)


def _usable_os_backend(backend: Any, ignored: tuple[str, ...], genuine: _Genuine) -> tuple[KeychainStatus, Any]:
    rank = genuine.rank(backend)
    if rank is None:
        return _refused(genuine.diagnose(backend) or "no_keychain_backend")
    try:
        priority = float(type(backend).priority)  # the genuine class's own probe, not an instance attribute
    except Exception as exc:  # noqa: BLE001 - failed detection never grants keychain access
        return _refused(f"keychain_unusable:{_exception_name(exc)}")
    if not math.isfinite(priority) or priority <= 0:
        return _refused("keychain_unusable:priority")
    return KeychainStatus(True, _display_name(rank), "ok", ignored), backend


def _display_name(rank: int) -> str:
    # Cockpit's own name for an admitted class ("SecretService Keyring", "kwallet DBusKeyring", ...), the
    # library's naming scheme applied to the fixed list: nothing the class itself says is shown.
    module, name = _OS_CLASSES[rank]
    return f"{module.rpartition('.')[2]} {name}"


# Every name a keychain record may hold (B24): one per allowlist entry, so equal names mean the same class.
_KEYCHAIN_NAMES = tuple(_display_name(rank) for rank in range(len(_OS_CLASSES)))


def _type_name(obj: Any) -> str:
    """``module.Class`` of a backend, made safe for a one-line doctor message."""
    kind = type(obj)
    return _safe(f"{_type_field(kind, '__module__') or '?'}.{_type_field(kind, '__qualname__') or '?'}")


class OsKeyring:
    """Adapter from the keyring library to ``KeyStore``'s injected ``get``/``set``/``delete``.

    Pins the checked backend object and its class at construction, and calls that class's own top-level
    password method with the object. So a global keyring or chain replaced after the check, or a top-level
    password method shadowed on the instance, redirects nothing; and if the object's ``__class__`` has been
    reassigned since the check, every call is refused (``keychain_changed``). That is all it does: the
    class's methods still consult the instance (in keyring 25.7.0, Secret Service's call
    ``self.get_preferred_collection()``), so code that changes the selected object's helpers or state after
    the check can redirect a key. Such code is hostile code inside the process, which this module cannot
    defend against.
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self._cls = type(backend)

    def _checked(self) -> Any:
        # Not a defence against instance mutation in general (see the class docstring); a cheap refusal of
        # one visible form of it, a class swapped under the selected object.
        if type(self._backend) is not self._cls:
            raise KeyRefused("keychain_changed", "the keychain object's class changed after the check")
        return self._backend

    def get(self, service: str, name: str) -> str | None:
        return self._cls.get_password(self._checked(), service, name)

    def set(self, service: str, name: str, secret: str) -> None:
        self._cls.set_password(self._checked(), service, name, secret)

    def delete(self, service: str, name: str) -> None:
        backend = self._checked()
        try:
            self._cls.delete_password(backend, service, name)
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
    # B24: the store records and checks which keychain broker and venue keys are in, by Cockpit's name for it.
    return KeyStore(keyring=ring, key_file=path, config_home=_config_home_for(path, env),
                    keychain=status.backend if status.available else None, keychain_names=_KEYCHAIN_NAMES), status


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
            _record_into(out, status.backend, keychain_record_state(os.fspath(key_file), _KEYCHAIN_NAMES))
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


def _record_into(out: dict[str, str], current: str, rec: dict[str, str]) -> None:
    # B24: the keychain record beside the key file, read only; the doctor never opens the keychain.
    state = rec["state"]
    if state == "ok" and rec["keychain"] != current:
        state = "keychain_differs"
    if state == "none":
        return
    if state == "ok":
        if "previous" in rec:
            out["keychain_switched_from"] = rec["previous"]
        return
    if state == "keychain_differs":
        why = f"saved in {rec['keychain']}, this run selected {current}; {record_way_out(state, current, rec['keychain'])}"
    elif state.startswith("keychain_record_"):
        why = f"{rec.get('detail', '')}; {record_way_out(state, current, None)}"
    else:  # a folder on the way to the record is not owner-only
        why = f"{rec.get('detail', '')}; Cockpit never repairs the key folder"
    out.update(level="fail", broker=f"refused:{state}", venue=f"refused:{state}", keychain_record=why)


def doctor_line(result: dict[str, str]) -> str:
    if result.get("keychain", "none") != "none":
        if result.get("broker", "keyring").startswith("refused:"):  # B24: the keychain record refuses them
            line = (f"keys: {result['level']} - OS keychain ({result['keychain']}); model-provider keys in the "
                    f"keychain; broker and venue keys refused ({result['broker'].split(':', 1)[1]}): "
                    f"{result['keychain_record']}")
        else:
            line = (f"keys: {result['level']} - OS keychain ({result['keychain']}); "
                    "model-provider, broker and venue keys in the keychain")
        if "keychain_switched_from" in result:
            line += f"; switched from {result['keychain_switched_from']} with accept-keychain"
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
