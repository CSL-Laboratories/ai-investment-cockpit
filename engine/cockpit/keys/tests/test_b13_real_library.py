"""B13 against the real keyring library: its own ``ChainerBackend`` and OS backend classes (CSL-59).

The library builds and orders the chain itself (``ChainerBackend.backends``, by priority). Its OS
backend classes are the real ``SecretService.Keyring``, ``libsecret.Keyring`` and ``WinVaultKeyring``,
with their availability probe answered and their three password methods pointed at a dict, so no
real keychain is read or written. The other backends are stand-ins with the module names of
``keyrings.alt`` and of a pip credential helper (invented); the real packages are not installed.
Skipped when the library is missing. Every value is made up.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import pathlib
import sys
import tempfile
import types
import unittest
from typing import Any
from unittest import mock

from cockpit.keys import KEY_KINDS, KeyRefused, doctor, doctor_line, store_for_host
from cockpit.keys.tests.fakes import FAKE, forget_class, loader

try:
    _kr_backend = importlib.import_module("keyring.backend")
    _core = importlib.import_module("keyring.core")
    _chainer = importlib.import_module("keyring.backends.chainer")
    _ss = importlib.import_module("keyring.backends.SecretService")
    _libsecret = importlib.import_module("keyring.backends.libsecret")
    _kwallet = importlib.import_module("keyring.backends.kwallet")
    _windows = importlib.import_module("keyring.backends.Windows")
except ImportError:  # the keyring library is optional
    _kr_backend = None
try:
    _secretstorage = importlib.import_module("secretstorage")  # a keyring dependency on Linux only
except ImportError:
    _secretstorage = None

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

    def test_two_real_os_keychains_use_secret_service_before_libsecret(self) -> None:
        lib, lib_data, lib_calls = self.real_os_backend(_libsecret.Keyring, 4.8)
        ss, ss_data, _ = self.real_os_backend(_ss.Keyring, 5)
        store, status = store_for_host(PATH, loader(self.real_chain([lib, ss])))
        self.assertEqual(status.backend, "SecretService Keyring")
        self.assertEqual(status.ignored, ("keyring.backends.libsecret.Keyring",))
        store.put("broker", "fixture", FAKE)
        self.assertEqual((list(ss_data.values()), lib_data, lib_calls), ([FAKE], {}, []))

    @unittest.skipIf(_secretstorage is None, "secretstorage (Linux-only keyring dependency) not installed")
    def linux_host(self, stack: contextlib.ExitStack, *, ss: bool, kw5: bool, kw4: bool,
                   lib: bool) -> tuple[list[type], dict[type, dict]]:
        """Answer the daemon probes of the library's Linux OS backends (only the probes; the priority
        code is the library's), and point each answering class's password methods at its own dict."""

        class _Conn:
            def close(self) -> None:
                pass

        owners = [name for name, up in (("org.kde.kwalletd5", kw5), ("org.kde.kwalletd", kw4)) if up]

        class _Bus:
            def name_has_owner(self, name: str) -> bool:
                return name in owners

            def list_activatable_names(self) -> list[str]:
                return list(owners)

        class _GError(Exception):
            pass

        dbus = type("dbus", (), {"SessionBus": staticmethod(lambda mainloop=None: _Bus()), "DBusException": Exception})
        secret = types.SimpleNamespace(ServiceFlags=types.SimpleNamespace(OPEN_SESSION=1),
                                       Service=types.SimpleNamespace(get_sync=lambda flags, cancellable: object()))
        for target, name, value in ((_secretstorage, "dbus_init", lambda *a, **k: _Conn()),
                                    (_secretstorage, "check_service_availability", lambda conn: ss),
                                    (_kwallet, "dbus", dbus), (_kwallet, "DBusGMainLoop", lambda: None),
                                    (_libsecret, "available", lib), (_libsecret, "Secret", secret),
                                    (_libsecret, "GLib", types.SimpleNamespace(Error=_GError)),
                                    (_kr_backend, "_limit", None)):
            stack.enter_context(mock.patch.object(target, name, value, create=True))
        classes = [cls for cls, up in ((_ss.Keyring, ss), (_kwallet.DBusKeyring, kw5), (_libsecret.Keyring, lib),
                                       (_kwallet.DBusKeyringKWallet4, kw4)) if up]
        stores: dict[type, dict] = {cls: {} for cls in classes}
        for cls, data in stores.items():
            for method, fn in (("get_password", lambda self, s, u, d=data: d.get((s, u))),
                               ("set_password", lambda self, s, u, p, d=data: d.__setitem__((s, u), p)),
                               ("delete_password", lambda self, s, u, d=data: d.pop((s, u), None))):
                stack.enter_context(mock.patch.object(cls, method, fn))
        return classes, stores

    @unittest.skipIf(_secretstorage is None, "secretstorage (Linux-only keyring dependency) not installed")
    def test_one_kde_host_picks_the_same_keychain_in_every_session(self) -> None:
        # The library's own priority code orders the chain; only the daemon probes are answered. KWallet is
        # 5.1 in a KDE desktop session and 4.9 without XDG_CURRENT_DESKTOP (SSH, a service, cron), against
        # Secret Service's 5, so the library's order flips between sessions. Cockpit's fixed order (Lucas:
        # "Yes, fixed order") picks the same keychain in both, and a key saved on the desktop is found elsewhere.
        cases = {
            # name: (Secret Service, KWallet 5, KWallet 4, libsecret answer; expected keychain)
            "SecretService + KWallet 5": ((True, True, False, False), "SecretService Keyring"),
            "SecretService + KWallet 5 + KWallet 4": ((True, True, True, False), "SecretService Keyring"),
            "KWallet 5 only": ((False, True, False, False), "kwallet DBusKeyring"),
            "libsecret + KWallet 4": ((False, False, True, True), "libsecret Keyring"),
            "all four": ((True, True, True, True), "SecretService Keyring"),
        }
        no_desktop = {k: v for k, v in os.environ.items() if k != "XDG_CURRENT_DESKTOP"}
        for label, ((ss_up, kw5_up, kw4_up, lib_up), expected) in cases.items():
            with self.subTest(label), contextlib.ExitStack() as stack:
                classes, stores = self.linux_host(stack, ss=ss_up, kw5=kw5_up, kw4=kw4_up, lib=lib_up)
                rings = [cls() for cls in classes]
                picks = []
                for env, clear in (({"XDG_CURRENT_DESKTOP": "KDE"}, False), (no_desktop, True)):
                    with mock.patch.dict("os.environ", env, clear=clear):
                        chain = self.real_chain(rings)
                        library_order = [type(b).__name__ for b in chain.backends]
                        store, status = store_for_host(PATH, loader(chain))
                        picks.append((status.backend, sorted(status.ignored)))
                        if not picks[1:]:
                            store.put("broker", "fixture", FAKE)
                        else:
                            self.assertEqual(store.get("broker", "fixture"), FAKE)
                    if kw5_up and ss_up:  # the library's own order does flip between the two sessions
                        self.assertEqual(library_order[0], "DBusKeyring" if not clear else "Keyring")
                self.assertEqual([backend for backend, _ in picks], [expected, expected])
                self.assertEqual(picks[0][1], picks[1][1])
                self.assertEqual(sum(len(data) for data in stores.values()), 1)

    @unittest.skipIf(_secretstorage is None, "secretstorage (Linux-only keyring dependency) not installed")
    def test_python_keyring_backend_still_pins_one_keychain(self) -> None:
        # With PYTHON_KEYRING_BACKEND set, the library's own selection returns that one backend, not a
        # chain, and Cockpit uses it in every session, whatever the fixed order would pick from a chain.
        no_desktop = {k: v for k, v in os.environ.items() if k not in ("XDG_CURRENT_DESKTOP", "PYTHON_KEYRING_BACKEND")}
        with contextlib.ExitStack() as stack:
            _, stores = self.linux_host(stack, ss=True, kw5=True, kw4=False, lib=False)
            for env, clear in (({"XDG_CURRENT_DESKTOP": "KDE"}, False), (no_desktop, True)):
                env = dict(env, PYTHON_KEYRING_BACKEND="keyring.backends.kwallet.DBusKeyring")
                with mock.patch.dict("os.environ", env, clear=clear):
                    chosen = _core._detect_backend()
                    self.assertIs(type(chosen), _kwallet.DBusKeyring)
                    store, status = store_for_host(PATH, loader(chosen))
                    self.assertEqual((status.backend, status.ignored), ("kwallet DBusKeyring", ()))
                    store.put("broker", "fixture", FAKE)
            self.assertEqual((len(stores[_kwallet.DBusKeyring]), stores[_ss.Keyring]), (1, {}))

    def test_the_library_reads_an_ignored_backends_priority_but_cockpit_sends_it_nothing(self) -> None:
        # Codex's P2: building the chain, the library itself reads every member's priority (to filter and
        # to sort). Cockpit's guarantee starts after that: no password operation reaches an ignored backend.
        reads: list[int] = []
        touched: list[str] = []

        class Helper:
            __module__ = "review_standin.pip_credential_helper"
            __qualname__ = "Helper"

            def __getattribute__(self, attr: str) -> object:
                if attr == "priority":
                    reads.append(1)
                    return 9
                if attr == "__class__":
                    return type(self)
                touched.append(attr)
                raise AssertionError(f"ignored backend was touched: {attr}")

        ss, data, calls = self.real_os_backend(_ss.Keyring, 5)
        store, status = store_for_host(PATH, loader(self.real_chain([ss, Helper()])))
        self.assertTrue(status.available)
        self.assertEqual(status.ignored, ("review_standin.pip_credential_helper.Helper",))
        self.assertGreaterEqual(len(reads), 1)  # the library's own discovery ran the backend's code
        for kind in KEY_KINDS:
            store.put(kind, "fixture", FAKE)
            self.assertEqual(store.get(kind, "fixture"), FAKE)
        self.assertEqual(touched, [])
        self.assertEqual(len(data), len(KEY_KINDS))

    def test_a_priority_that_raises_while_the_library_builds_the_chain_fails_closed(self) -> None:
        # The library propagates the error out of ``ChainerBackend.backends``; detection then reports no
        # keychain (keyring_backend_error), names nothing and stores nothing, though a keychain is present.
        for fail_at in (1, 2):
            with self.subTest(fail_at=fail_at):
                reads: list[int] = []

                class Broken:
                    __module__ = "review_standin.broken"

                    @property
                    def priority(self) -> float:
                        reads.append(1)
                        if len(reads) >= fail_at:
                            raise RuntimeError("fixture helper broke")
                        return 1

                ss, data, calls = self.real_os_backend(_ss.Keyring, 5)
                store, status = store_for_host(PATH, loader(self.real_chain([ss, Broken()])))
                self.assertFalse(status.available)
                self.assertEqual(status.reason, "keyring_backend_error:RuntimeError")
                self.assertEqual(status.ignored, ())
                with self.assertRaises(KeyRefused):
                    store.put("broker", "fixture", FAKE)
                self.assertEqual((data, calls), ({}, []))

    def test_a_forged_class_through_the_librarys_own_loader_is_refused(self) -> None:
        # Codex's P1 through keyring's real selection: PYTHON_KEYRING_BACKEND and keyringrc.cfg name a
        # file-writing class whose metadata claims Secret Service. The library loads it; Cockpit refuses it.
        written: dict = {}

        class Spoof(_kr_backend.KeyringBackend):
            priority = 9  # type: ignore[assignment]

            def get_password(self, service: str, user: str) -> str | None:
                return written.get((service, user))

            def set_password(self, service: str, user: str, secret: str) -> None:
                written[(service, user)] = secret

            def delete_password(self, service: str, user: str) -> None:
                written.pop((service, user), None)

        Spoof.__module__, Spoof.__qualname__, Spoof.__name__ = "keyring.backends.SecretService", "Keyring", "Keyring"
        forget_class(self, Spoof)
        plugin = types.ModuleType("keys_lane_plugin")
        plugin.Spoof = Spoof
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            cfg = os.path.join(tmp, "keyringrc.cfg")
            with open(cfg, "w", encoding="utf-8") as fh:
                fh.write("[backend]\ndefault-keyring=keys_lane_plugin.Spoof\n")
            stack.enter_context(mock.patch.dict(sys.modules, {"keys_lane_plugin": plugin}))
            stack.enter_context(mock.patch.object(_core, "_config_path", return_value=pathlib.Path(cfg)))
            stack.enter_context(mock.patch.object(_kr_backend, "_limit", None))
            no_env = {k: v for k, v in os.environ.items() if k != "PYTHON_KEYRING_BACKEND"}
            for route, env in (("PYTHON_KEYRING_BACKEND", dict(no_env, PYTHON_KEYRING_BACKEND="keys_lane_plugin.Spoof")),
                               ("keyringrc.cfg", no_env)):
                with self.subTest(route), mock.patch.dict(os.environ, env, clear=True):
                    chosen = _core._detect_backend()
                    self.assertIs(type(chosen), Spoof)  # the library did load the forged class
                    store, status = store_for_host(PATH, loader(chosen))
                    self.assertFalse(status.available)
                    self.assertEqual(status.reason, "no_keychain_backend")
                    with self.assertRaises(KeyRefused):
                        store.put("broker", "fixture", FAKE)
                    self.assertEqual(written, {})

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
