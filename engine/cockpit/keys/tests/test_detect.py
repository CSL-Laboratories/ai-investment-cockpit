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
    def test_real_backend_is_a_keychain_and_detection_touches_no_key(self) -> None:
        mod = FakeKeyringModule(_backend("keyring.backends.SecretService", "SecretService Keyring"))
        status = detect_keychain(lambda: mod)
        self.assertTrue(status.available)
        self.assertEqual(status.backend, "SecretService Keyring")
        self.assertEqual(mod.calls, [])

    def test_library_missing_reads_as_no_keychain(self) -> None:
        def load() -> object:
            raise ImportError("no keyring")

        status = detect_keychain(load)
        self.assertFalse(status.available)
        self.assertEqual(status.reason, "keyring_library_missing")

    def test_fail_null_and_unusable_backends_are_not_keychains(self) -> None:
        cases = {
            "fail": _backend("keyring.backends.fail"),
            "null": _backend("keyring.backends.null"),
            "priority0": _backend("keyring.backends.SecretService", priority=0),
            "raises": _backend("keyring.backends.SecretService", priority=RuntimeError("no dbus")),
            "empty_chainer": _backend("keyring.backends.chainer", backends=[]),
        }
        for label, backend in cases.items():
            with self.subTest(label):
                self.assertFalse(detect_keychain(lambda b=backend: FakeKeyringModule(b)).available)

    def test_chainer_with_a_real_child_is_a_keychain(self) -> None:
        chain = _backend("keyring.backends.chainer", backends=[
            _backend("keyring.backends.fail"), _backend("keyring.backends.kwallet", "KWallet")])
        status = detect_keychain(lambda: FakeKeyringModule(chain))
        self.assertTrue(status.available)
        self.assertEqual(status.backend, "KWallet")

    def test_get_keyring_error_reads_as_no_keychain(self) -> None:
        mod = FakeKeyringModule(None)
        mod.get_keyring = lambda: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
        status = detect_keychain(lambda: mod)
        self.assertFalse(status.available)
        self.assertTrue(status.reason.startswith("keyring_backend_error"))


@unittest.skipIf(os.name == "nt", "POSIX owner-only file")
class StoreForHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="cockpit-keys-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        os.chmod(self.dir, 0o700)
        self.path = os.path.join(self.dir, "model-keys.json")

    def test_keychain_host_routes_every_kind_to_the_keychain(self) -> None:
        mod = FakeKeyringModule(_backend("keyring.backends.macOS", "Keyring"))
        store, status = store_for_host(self.path, lambda: mod)
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

    def test_keychain_line(self) -> None:
        status = detect_keychain(lambda: FakeKeyringModule(_backend("keyring.backends.Windows", "Windows WinVaultKeyring")))
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
