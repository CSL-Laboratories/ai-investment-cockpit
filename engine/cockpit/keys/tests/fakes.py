"""Fakes for the keyring-chain (B13) and first-save (B14) tests (CSL-59).

No real keychain, key, provider, venue or network. Every value is made up.

OS keychains are admitted by class identity, so a test that needs one uses an instance of the keyring
library's own class (``Keychains.os``), with that class's availability probe (``priority``) and its three
password methods replaced on the class for the duration of the test. Nothing reaches a real keychain or
D-Bus. Look-alikes (``untouchable``, ``recorder``) are plain classes and are never OS keychains.
"""

from __future__ import annotations

import importlib
import types
import unittest
from typing import Any
from unittest import mock

FAKE = "fake-key-not-real-0123"

try:
    importlib.import_module("keyring")
    KEYRING = True
except ImportError:  # the keyring library is optional
    KEYRING = False
requires_keyring = unittest.skipUnless(KEYRING, "needs the keyring library (OS keychains are its own classes)")

GENUINE = {
    "SecretService": ("keyring.backends.SecretService", "Keyring"),
    "KWallet5": ("keyring.backends.kwallet", "DBusKeyring"),
    "libsecret": ("keyring.backends.libsecret", "Keyring"),
    "KWallet4": ("keyring.backends.kwallet", "DBusKeyringKWallet4"),
    "macOS": ("keyring.backends.macOS", "Keyring"),
    "Windows": ("keyring.backends.Windows", "WinVaultKeyring"),
    "fail": ("keyring.backends.fail", "Keyring"),
    "null": ("keyring.backends.null", "Keyring"),
}
NAMES = {"SecretService": "SecretService Keyring", "KWallet5": "kwallet DBusKeyring", "libsecret": "libsecret Keyring",
         "KWallet4": "kwallet DBusKeyringKWallet4", "macOS": "macOS Keyring", "Windows": "Windows WinVaultKeyring"}


def genuine_class(kind: str) -> type:
    module, name = GENUINE[kind]
    return getattr(importlib.import_module(module), name)


class Keychains:
    """Instances of the library's genuine backend classes, patched on the class until the test ends."""

    def __init__(self, test: unittest.TestCase) -> None:
        self.test = test
        self._patched: set[type] = set()

    def _patch(self, target: object, name: str, value: object) -> None:
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.test.addCleanup(patcher.stop)

    def os(self, kind: str = "SecretService", priority: object = 5) -> Any:
        """A genuine instance whose class answers ``priority`` and records password calls per instance."""

        cls = genuine_class(kind)
        if isinstance(priority, Exception):
            exc = priority

            class _Raises:
                def __get__(self, obj: object, owner: object) -> float:
                    raise exc

            self._patch(cls, "priority", _Raises())
        else:
            self._patch(cls, "priority", priority)
        if cls not in self._patched:
            self._patched.add(cls)

            def get_password(self: Any, service: str, name: str) -> str | None:
                self.calls.append(("get", service, name))
                return self.data.get((service, name))

            def set_password(self: Any, service: str, name: str, secret: str) -> None:
                self.calls.append(("set", service, name))
                self.data[(service, name)] = secret

            def delete_password(self: Any, service: str, name: str) -> None:
                self.calls.append(("delete", service, name))
                self.data.pop((service, name), None)

            for method, fn in (("get_password", get_password), ("set_password", set_password),
                               ("delete_password", delete_password)):
                self._patch(cls, method, fn)
        backend = cls()
        backend.calls, backend.data = [], {}
        return backend

    def chain(self, children: list[Any]) -> Any:
        """The library's own ``ChainerBackend``, each instance with its own fixed children.

        ``backends`` answers ``obj.children`` (a list the test may change) and counts reads in
        ``obj.reads``; the chain's own password methods record into ``obj.calls``. Storage must never
        go through the chain.
        """

        cls = genuine_class_chainer()
        if cls not in self._patched:
            self._patched.add(cls)

            class _Children:
                def __get__(self, obj: Any, owner: object) -> list[Any]:
                    if obj is None:
                        return []
                    obj.reads += 1
                    return obj.children

            self._patch(cls, "backends", _Children())
            for method in ("get_password", "set_password", "delete_password"):
                self._patch(cls, method, lambda self, *args, _m=method: self.calls.append((_m,) + args[:2]))
        obj = cls()
        obj.children, obj.reads, obj.calls = list(children), 0, []
        return obj


def forget_class(test: unittest.TestCase, cls: type) -> None:
    """Drop a test-made subclass of the library's base class from keyring's global backend registry
    when the test ends (the library registers every concrete subclass at class creation)."""

    registry = getattr(cls, "_classes", None)
    if isinstance(registry, set):
        test.addCleanup(registry.discard, cls)


def genuine_class_chainer() -> type:
    return getattr(importlib.import_module("keyring.backends.chainer"), "ChainerBackend")


def recorder(module: str = "keyrings.alt.file", qualname: str = "PlaintextKeyring", name: str = "file PlaintextKeyring",
             priority: object = 5, bases: tuple[type, ...] = ()) -> Any:
    """A working look-alike store (never an OS keychain): records each call, keeps values in a dict.

    ``bases`` may hold the library's ``KeyringBackend``; then call ``forget_class`` on its type.
    """

    def get_password(self: Any, service: str, name: str) -> str | None:
        self.calls.append(("get", service, name))
        return self.data.get((service, name))

    def set_password(self: Any, service: str, name: str, secret: str) -> None:
        self.calls.append(("set", service, name))
        self.data[(service, name)] = secret

    def delete_password(self: Any, service: str, name: str) -> None:
        self.calls.append(("delete", service, name))
        self.data.pop((service, name), None)

    namespace = {"__module__": module, "name": name, "priority": priority, "get_password": get_password,
                 "set_password": set_password, "delete_password": delete_password}
    backend = type(qualname, bases, namespace)()
    backend.calls, backend.data = [], {}
    return backend


def untouchable(module: str | object, qualname: str = "PlaintextKeyring") -> tuple[Any, list[str]]:
    """A backend that must never be used: any attribute read on the instance is recorded and raises.

    Detection may read the class's own ``__module__`` and ``__qualname__`` (to name it in the doctor
    line); reading ``priority``, calling a password method or storing through it shows up in the list.
    """

    touched: list[str] = []

    def __getattribute__(self: object, attr: str) -> object:
        touched.append(attr)
        raise AssertionError(f"ignored backend was touched: {attr}")

    cls = type(qualname, (), {"__module__": module, "__getattribute__": __getattribute__})
    return cls(), touched


def lookalike_chain(children: list[Any], module: str = "keyring.backends.chainer") -> Any:
    """A plain object that only claims to be the library's chainer; it is never read as a chain."""

    obj = type("ChainerBackend", (), {"__module__": module})()
    obj.backends, obj.calls = list(children), []
    return obj


def loader(backend: Any) -> Any:
    """A ``load`` callable for ``store_for_host``: a module whose ``get_keyring()`` returns ``backend``."""

    mod = types.SimpleNamespace(backend=backend)
    mod.get_keyring = lambda: mod.backend
    return lambda: mod
