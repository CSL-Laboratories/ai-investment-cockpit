"""B13 against the real keyring library: its own ``ChainerBackend`` and OS backend classes (CSL-59).

The library builds and orders the chain itself (``ChainerBackend.backends``, by priority). Its OS
backend classes are the real ``SecretService.Keyring``, ``libsecret.Keyring`` and ``WinVaultKeyring``,
with their availability probe answered and their three password methods pointed at a dict, so no
real keychain is read or written. The other backends are stand-ins with the module names of
``keyrings.alt`` and of a pip credential helper (invented); the real packages are not installed.
Skipped when the library is missing. Every value is made up.
"""

from __future__ import annotations

import importlib
import unittest
from typing import Any
from unittest import mock

from cockpit.keys import KEY_KINDS, KeyRefused, doctor, doctor_line, store_for_host
from cockpit.keys.tests.fakes import FAKE, loader

try:
    _kr_backend = importlib.import_module("keyring.backend")
    _chainer = importlib.import_module("keyring.backends.chainer")
    _ss = importlib.import_module("keyring.backends.SecretService")
    _libsecret = importlib.import_module("keyring.backends.libsecret")
    _windows = importlib.import_module("keyring.backends.Windows")
except ImportError:  # the keyring library is optional
    _kr_backend = None

PATH = "/nonexistent-cockpit-b13/model-keys.json"


def _standin(module: str, qualname: str, priority: float) -> tuple[Any, list[str]]:
    """A non-OS backend the library may chain. The library reads its ``priority`` to order the
    chain (and ``__class__`` for its ``isinstance`` check); any other read (a password method,
    storage through it) is recorded and raises."""

    touched: list[str] = []

    def __getattribute__(self: object, attr: str) -> object:
        if attr == "priority":
            return priority
        if attr == "__class__":
            return type(self)
        touched.append(attr)
        raise AssertionError(f"ignored backend was touched: {attr}")

    cls = type(qualname, (), {"__module__": module, "__getattribute__": __getattribute__})
    return cls(), touched


@unittest.skipIf(_kr_backend is None, "keyring library not installed")
class RealChainerTests(unittest.TestCase):
    def real_os_backend(self, cls: type, priority: float) -> tuple[Any, dict, list]:
        data: dict = {}
        calls: list = []

        def get_password(self: object, service: str, user: str) -> str | None:
            calls.append(("get", user))
            return data.get((service, user))

        def set_password(self: object, service: str, user: str, secret: str) -> None:
            calls.append(("set", user))
            data[(service, user)] = secret

        def delete_password(self: object, service: str, user: str) -> None:
            calls.append(("delete", user))
            data.pop((service, user), None)

        for name, value in (("priority", priority), ("get_password", get_password),
                            ("set_password", set_password), ("delete_password", delete_password)):
            patcher = mock.patch.object(cls, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return cls(), data, calls

    def real_chain(self, rings: list[Any]) -> Any:
        for target, value in (("get_all_keyring", lambda: list(rings)), ("_limit", None)):
            patcher = mock.patch.object(_kr_backend, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        chain = _chainer.ChainerBackend()
        self.assertEqual(type(chain).__module__, "keyring.backends.chainer")
        return chain

    def test_linux_desktop_chain_uses_secret_service_and_ignores_the_rest(self) -> None:
        ss, data, calls = self.real_os_backend(_ss.Keyring, 5)
        plain, plain_touched = _standin("keyrings.alt.file", "PlaintextKeyring", 0.5)
        helper, helper_touched = _standin("review_standin.pip_credential_helper", "CredentialHelper", 9)
        chain = self.real_chain([plain, ss, helper])
        self.assertEqual([type(b).__name__ for b in chain.backends], ["CredentialHelper", "Keyring", "PlaintextKeyring"])
        store, status = store_for_host(PATH, loader(chain))
        self.assertTrue(status.available)
        self.assertEqual(status.backend, "SecretService Keyring")
        self.assertEqual(status.ignored, ("review_standin.pip_credential_helper.CredentialHelper",
                                          "keyrings.alt.file.PlaintextKeyring"))
        for kind in KEY_KINDS:
            self.assertEqual(store.put(kind, "fixture", FAKE), "keyring")
            self.assertEqual(store.get(kind, "fixture"), FAKE)
        self.assertEqual(sorted(user for _, user in data), ["broker:fixture", "model_provider:fixture", "venue:fixture"])
        self.assertEqual(len(calls), 6)
        self.assertEqual((plain_touched, helper_touched), ([], []))
        self.assertIn("other keyring backends ignored: review_standin.pip_credential_helper.CredentialHelper, "
                      "keyrings.alt.file.PlaintextKeyring", doctor_line(doctor(status, None)))

    def test_windows_chain_with_a_credential_helper_uses_the_windows_vault(self) -> None:
        vault, data, _ = self.real_os_backend(_windows.WinVaultKeyring, 5)
        helper, touched = _standin("review_standin.pip_credential_helper", "CredentialHelper", 9)
        store, status = store_for_host(PATH, loader(self.real_chain([vault, helper])))
        self.assertTrue(status.available)
        self.assertEqual(status.backend, "Windows WinVaultKeyring")
        result = doctor(status, None)
        self.assertEqual(result["level"], "ok")
        for kind in KEY_KINDS:  # on Windows too: no owner-only file is needed for model keys
            self.assertEqual(store.backend_for(kind), "keyring")
            store.put(kind, "fixture", FAKE)
        self.assertEqual(len(data), 3)
        self.assertEqual(touched, [])

    def test_two_real_os_keychains_use_the_first_in_the_library_order(self) -> None:
        lib, lib_data, lib_calls = self.real_os_backend(_libsecret.Keyring, 4.8)
        ss, ss_data, _ = self.real_os_backend(_ss.Keyring, 5)
        store, status = store_for_host(PATH, loader(self.real_chain([lib, ss])))
        self.assertEqual(status.backend, "SecretService Keyring")
        self.assertEqual(status.ignored, ("keyring.backends.libsecret.Keyring",))
        store.put("broker", "fixture", FAKE)
        self.assertEqual((list(ss_data.values()), lib_data, lib_calls), ([FAKE], {}, []))

    def test_a_real_chain_with_no_os_keychain_is_refused(self) -> None:
        plain, plain_touched = _standin("keyrings.alt.file", "PlaintextKeyring", 0.5)
        helper, helper_touched = _standin("review_standin.pip_credential_helper", "CredentialHelper", 9)
        store, status = store_for_host(PATH, loader(self.real_chain([plain, helper])))
        self.assertFalse(status.available)
        for kind in ("broker", "venue"):
            with self.assertRaises(KeyRefused) as ctx:
                store.put(kind, "fixture", FAKE)
            self.assertEqual(ctx.exception.cause, "keychain_required")
        self.assertEqual((plain_touched, helper_touched), ([], []))
        ss, data, _ = self.real_os_backend(_ss.Keyring, 5)
        store, status = store_for_host(PATH, loader(self.real_chain([plain, helper, ss])))
        self.assertTrue(status.available)


if __name__ == "__main__":
    unittest.main()
