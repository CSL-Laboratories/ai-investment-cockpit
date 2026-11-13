"""Keychain detection and the doctor line (CSL-59; PRODUCT.md 055cc7a5 A44, 3.8, 5.4).

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/keys/tests -t engine -v``.
Fakes only: fake keyring modules and backends, temp directories. No real keyring, provider, venue or network.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import tempfile
import types
import unittest

from cockpit.keys import KeyRefused, detect_keychain, doctor, doctor_line, store_for_host
from cockpit.keys.__main__ import main
from cockpit.keys.detect import default_key_file
from cockpit.keys.tests.fakes import KEYRING, Keychains, loader, recorder, requires_keyring, untouchable

FAKE = "fake-key-not-real-0123"


def _backend(module: str, name: str = "Fake", priority: object = 5, **attrs: object) -> object:
    ns = {"__module__": module, "name": name}
    if isinstance(priority, Exception):
        exc = priority

        class _Raises:
            def __get__(self, obj: object, owner: object) -> float:
                raise exc

        ns["priority"] = _Raises()
    else:
        ns["priority"] = priority
    ns.update(attrs)
    return type("Backend", (), ns)()


class FakeKeyringModule(types.SimpleNamespace):
    def __init__(self, backend: object) -> None:
        super().__init__(backend=backend, store={}, calls=[])
        if backend is not None:
            backend.get_password = self.get_password
            backend.set_password = self.set_password
            backend.delete_password = self.delete_password

    def get_keyring(self) -> object:
        return self.backend

    def get_password(self, service: str, name: str) -> str | None:
        self.calls.append(("get", service, name))
        return self.store.get((service, name))

    def set_password(self, service: str, name: str, secret: str) -> None:
        self.calls.append(("set", service, name))
        self.store[(service, name)] = secret

    def delete_password(self, service: str, name: str) -> None:
        self.calls.append(("delete", service, name))
        self.store.pop((service, name), None)


class DetectTests(unittest.TestCase):
    # OS keychains are admitted by class identity, so the tests that need one use the keyring library's
    # own classes (``Keychains``) with their probe and password methods replaced for the test.
    def setUp(self) -> None:
        self.k = Keychains(self)

    @requires_keyring
    def test_mixed_chainers_use_the_os_backend_without_touching_the_others(self):
        # B13 (accepted by Lucas, Nov 12, 2026): a chain of one OS keychain plus other backends uses
        # the OS keychain; until then (#323) such a chain was refused. Cockpit sends the other no call.
        for module in ("keyrings.alt.file", "keyring.backends.SecretServiceEvil"):
            for reverse in (False, True):
                safe = self.k.os("SecretService")
                unsafe, touched = untouchable(module)
                children = [safe, unsafe][::(-1 if reverse else 1)]
                chain = self.k.chain(children)
                store, status = store_for_host(load=loader(chain))
                self.assertTrue(status.available)
                self.assertEqual(status.ignored, (f"{module}.PlaintextKeyring",))
                for kind in ("broker", "venue"):
                    self.assertEqual(store.put(kind, "fixture", FAKE), "keyring")
                    self.assertEqual(store.get(kind, "fixture"), FAKE)
                self.assertEqual([c[0] for c in safe.calls], ["set", "get", "set", "get"])
                self.assertEqual(touched, [])
                self.assertEqual(chain.calls, [])

    @requires_keyring
    def test_store_pins_checked_os_backend_not_module_or_chainer(self):
        safe = self.k.os("SecretService")
        unsafe = recorder("keyrings.alt.file")
        unsafe_mod = types.SimpleNamespace(get_keyring=lambda: unsafe)
        chain = self.k.chain([safe])
        mod = types.SimpleNamespace(backend=chain)
        mod.get_keyring = lambda: mod.backend
        loads = []

        def load():
            loads.append(True)
            return mod if len(loads) == 1 else unsafe_mod

        store, status = store_for_host(load=load)
        self.assertTrue(status.available)
        chain.children = [unsafe]
        mod.backend = unsafe
        store.put("broker", "fixture", FAKE)
        self.assertEqual(store.get("broker", "fixture"), FAKE)
        store.delete("broker", "fixture")
        self.assertEqual([c[0] for c in safe.calls], ["set", "get", "delete"])
        self.assertEqual(unsafe.calls, [])
        self.assertEqual(chain.calls, [])
        self.assertEqual(len(loads), 1)

    @requires_keyring
    def test_cyclic_or_unreadable_chain_is_unavailable(self):
        chain = self.k.chain([])
        chain.children.append(chain)
        self.assertFalse(detect_keychain(loader(chain)).available)

    @requires_keyring
    def test_real_backend_is_a_keychain_and_detection_touches_no_key(self) -> None:
        backend = self.k.os("SecretService")
        status = detect_keychain(loader(backend))
        self.assertTrue(status.available)
        self.assertEqual(status.backend, "SecretService Keyring")
        self.assertEqual(backend.calls, [])

    def test_library_missing_reads_as_no_keychain(self) -> None:
        def load() -> object:
            raise ImportError("no keyring")

        status = detect_keychain(load)
        self.assertFalse(status.available)
        self.assertEqual(status.reason, "keyring_library_missing")

    @requires_keyring
    def test_fail_null_and_unusable_backends_are_not_keychains(self) -> None:
        cases = {
            "fail": lambda: self.k.os("fail", priority=0),
            "null": lambda: self.k.os("null", priority=-1),
            "priority0": lambda: self.k.os("SecretService", priority=0),
            "raises": lambda: self.k.os("SecretService", priority=RuntimeError("no dbus")),
            "empty_chainer": lambda: self.k.chain([]),
        }
        for label, make in cases.items():
            with self.subTest(label):
                self.assertFalse(detect_keychain(loader(make())).available)

    @requires_keyring
    def test_chainer_with_a_real_child_is_a_keychain(self) -> None:
        chain = self.k.chain([self.k.os("fail", priority=0), self.k.os("KWallet5")])
        status = detect_keychain(loader(chain))
        self.assertTrue(status.available)
        self.assertEqual(status.backend, "kwallet DBusKeyring")

    def test_get_keyring_error_reads_as_no_keychain(self) -> None:
        mod = FakeKeyringModule(None)
        mod.get_keyring = lambda: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
        status = detect_keychain(lambda: mod)
        self.assertFalse(status.available)
        self.assertTrue(status.reason.startswith("keyring_backend_error"))

    def test_keyrings_alt_and_non_os_file_backends_are_not_keychains(self) -> None:
        non_os = [
            _backend("keyrings.alt.file", "PlaintextKeyring", priority=0.5),
            _backend("keyrings.alt.file", "EncryptedKeyring", priority=0.6),
            _backend("keyrings.cryptfile.cryptfile", "CryptFileKeyring", priority=2.5),
        ]
        chain = None
        if KEYRING:
            # The library's own chain holding only a file backend, so the file child itself is examined (a
            # look-alike chain would be refused before its child is looked at). Needs the keyring library.
            chain = self.k.chain([_backend("keyrings.alt.file", "PlaintextKeyring", priority=0.5)])
            non_os.append(chain)
        for backend in non_os:
            with self.subTest(backend=backend.name):
                store, status = store_for_host("/tmp/unused-keys.json", lambda b=backend: FakeKeyringModule(b))
                self.assertFalse(status.available)
                for kind in ("broker", "venue"):
                    with self.assertRaises(KeyRefused) as ctx:
                        store.put(kind, "acme", FAKE)
                    self.assertEqual(ctx.exception.cause, "keychain_required")
        if chain is not None:
            self.assertEqual(chain.reads, 1)  # its members were enumerated, and the file child refused


@unittest.skipIf(os.name == "nt", "POSIX owner-only file")
class StoreForHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="cockpit-keys-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        os.chmod(self.dir, 0o700)
        self.path = os.path.join(self.dir, "model-keys.json")

    @requires_keyring
    def test_keychain_host_routes_every_kind_to_the_keychain(self) -> None:
        mod = loader(Keychains(self).os("macOS"))
        store, status = store_for_host(self.path, mod)
        self.assertTrue(status.available)
        for kind in ("model_provider", "broker", "venue"):
            self.assertEqual(store.put(kind, "acme", FAKE), "keyring")
            self.assertEqual(store.get(kind, "acme"), FAKE)
        store.delete("venue", "acme")
        self.assertIsNone(store.get("venue", "acme"))
        self.assertFalse(os.path.exists(self.path))

    def test_no_keychain_host_uses_owner_only_file_for_model_keys_only(self) -> None:
        store, status = store_for_host(self.path, lambda: FakeKeyringModule(_backend("keyring.backends.fail")))
        self.assertFalse(status.available)
        self.assertEqual(store.put("model_provider", "acme", FAKE), "owner_only_file")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        for kind in ("broker", "venue"):
            with self.assertRaises(KeyRefused) as cm:
                store.put(kind, "acme", FAKE)
            self.assertEqual(cm.exception.cause, "keychain_required")


@unittest.skipIf(os.name == "nt", "POSIX owner-only file")
class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="cockpit-keys-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        os.chmod(self.dir, 0o700)
        self.path = os.path.join(self.dir, "model-keys.json")
        self.none = detect_keychain(lambda: FakeKeyringModule(_backend("keyring.backends.null")))

    @requires_keyring
    def test_keychain_line(self) -> None:
        status = detect_keychain(loader(Keychains(self).os("Windows")))
        result = doctor(status, self.path)
        self.assertEqual(result["level"], "ok")
        self.assertEqual(doctor_line(result), "keys: ok - OS keychain (Windows WinVaultKeyring); "
                                              "model-provider, broker and venue keys in the keychain")

    def test_no_keychain_line_names_file_state_and_refusals(self) -> None:
        result = doctor(self.none, self.path)
        self.assertEqual(result["level"], "warn")
        line = doctor_line(result)
        self.assertIn("no OS keychain (no_keychain_backend)", line)
        self.assertIn(f"owner-only file {self.path} [no_file_yet]", line)
        self.assertIn("broker and venue keys refused (keychain_required)", line)
        store, _ = store_for_host(self.path, lambda: FakeKeyringModule(_backend("keyring.backends.null")))
        store.put("model_provider", "acme", FAKE)
        line = doctor_line(doctor(self.none, self.path))
        self.assertIn("[owner_only]", line)
        self.assertNotIn(FAKE, line)
        self.assertNotIn("acme", line)
        before = set(os.listdir(self.dir))
        doctor(self.none, self.path)
        self.assertEqual(set(os.listdir(self.dir)), before)

    def test_loose_file_or_dir_is_fail(self) -> None:
        with open(self.path, "w") as fh:
            fh.write("{}")
        os.chmod(self.path, 0o644)
        self.assertEqual(doctor(self.none, self.path)["level"], "fail")
        os.chmod(self.path, 0o600)
        os.chmod(self.dir, 0o755)
        self.assertEqual(doctor(self.none, self.path)["level"], "fail")
        os.chmod(self.dir, 0o700)

    def test_writable_ancestor_is_fail_and_creates_nothing(self) -> None:
        outer = os.path.join(self.dir, "open")
        os.mkdir(outer)
        os.chmod(outer, 0o777)  # mkdir applies umask, so 0o777 there would still be 0755
        inner = os.path.join(outer, "inner")
        os.mkdir(inner, 0o700)
        path = os.path.join(inner, "model-keys.json")
        result = doctor(self.none, path)
        self.assertEqual(result["level"], "fail")
        self.assertTrue(result["key_file"].startswith("dir_not_owner_only"))
        self.assertEqual(os.listdir(inner), [])

    def test_symlinked_key_file_is_fail(self) -> None:
        target = os.path.join(self.dir, "elsewhere.json")
        with open(target, "w") as fh:
            fh.write("{}")
        os.chmod(target, 0o600)
        os.symlink(target, self.path)
        self.assertEqual(doctor(self.none, self.path)["level"], "fail")

    def test_unconfigured_file_is_fail(self) -> None:
        result = doctor(self.none, None)
        self.assertEqual(result["level"], "fail")
        self.assertIn("key_file_not_configured", doctor_line(result))

    def test_default_key_file_follows_xdg(self) -> None:
        self.assertEqual(default_key_file({"XDG_CONFIG_HOME": "/x", "HOME": "/h"}), "/x/csl-cockpit/model-keys.json")
        self.assertEqual(default_key_file({"HOME": "/h"}), "/h/.config/csl-cockpit/model-keys.json")

    def test_cli_prints_one_line_and_exit_code_follows_level(self) -> None:
        from cockpit.keys import __main__ as cli
        import cockpit.keys.detect as det
        orig = cli.detect_keychain
        cli.detect_keychain = lambda: self.none  # type: ignore[assignment]
        self.addCleanup(setattr, cli, "detect_keychain", orig)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["doctor", "--key-file", self.path])
        self.assertEqual(rc, 0)
        self.assertEqual(len(buf.getvalue().strip().splitlines()), 1)
        self.assertTrue(buf.getvalue().startswith("keys: warn"))
        os.chmod(self.dir, 0o755)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["doctor", "--key-file", self.path])
        os.chmod(self.dir, 0o700)
        self.assertEqual(rc, 1)
        self.assertIsNotNone(det)


if __name__ == "__main__":
    unittest.main()
