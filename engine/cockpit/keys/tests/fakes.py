"""Fakes for the keyring-chain (B13) and first-save (B14) tests (CSL-59).

No real keyring, keychain, key, provider, venue or network. Every value is made up.
"""

from __future__ import annotations

import types
from typing import Any

FAKE = "fake-key-not-real-0123"


def os_backend(module: str = "keyring.backends.SecretService", name: str = "SecretService Keyring",
               priority: object = 5, qualname: str = "Keyring") -> Any:
    """A stand-in OS keychain backend that records each call and keeps values in a dict."""

    backend = type(qualname, (), {"__module__": module, "name": name, "priority": priority})()
    backend.calls, backend.data = [], {}

    def get_password(service: str, name: str) -> str | None:
        backend.calls.append(("get", service, name))
        return backend.data.get((service, name))

    def set_password(service: str, name: str, secret: str) -> None:
        backend.calls.append(("set", service, name))
        backend.data[(service, name)] = secret

    def delete_password(service: str, name: str) -> None:
        backend.calls.append(("delete", service, name))
        backend.data.pop((service, name), None)

    backend.get_password, backend.set_password, backend.delete_password = get_password, set_password, delete_password
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


def chain(children: list[Any]) -> Any:
    """A stand-in for the library's ``keyring.backends.chainer.ChainerBackend`` with fixed children.

    Its own password methods record into ``calls``; storage must never go through the chain.
    """

    obj = type("ChainerBackend", (), {"__module__": "keyring.backends.chainer"})()
    obj.backends, obj.calls = list(children), []
    obj.get_password = lambda s, n: obj.calls.append(("get", s, n))
    obj.set_password = lambda s, n, p: obj.calls.append(("set", s, n))
    obj.delete_password = lambda s, n: obj.calls.append(("delete", s, n))
    return obj


def loader(backend: Any) -> Any:
    """A ``load`` callable for ``store_for_host``: a module whose ``get_keyring()`` returns ``backend``."""

    mod = types.SimpleNamespace(backend=backend)
    mod.get_keyring = lambda: mod.backend
    return lambda: mod
