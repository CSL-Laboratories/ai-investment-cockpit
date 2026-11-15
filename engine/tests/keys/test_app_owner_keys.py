"""Item 5 verifier (Rule CA): the app-side owner-only key wiring of #319 (CSL-59).

Lucas, Nov 11, 12:52am PST: "Lift the #318 hold and have new app/ code wire in #318 and #319".
PRODUCT.md A44, 3.8 and 5.4 (owner-only file for model-provider keys only); paper only stays
PRODUCT.md 5.2 / CP0-v2 (pin d84cdd26, CSLR-D-012). These tests check that
``app/core/owner_keys.py`` fails closed for anyone but the owner: a key file, lock or directory
that is not the running user's and owner-only is refused on read and on write, a refusal never
falls back to the environment or the app's own keyring, and broker and venue keys never land in a
file. Values come back redacted.
"""
from __future__ import annotations

import importlib
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[3] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import keyring  # noqa: E402

from core import engine_path, secrets  # noqa: E402
from core.owner_keys import KeyRefused, OwnerKeys  # noqa: E402

from cockpit.keys import detect  # noqa: E402
from cockpit.keys.tests.fakes import MARKER, RECORDS  # noqa: E402

FAKE = "sk-item5-fake"
POSIX = os.name == "posix" and hasattr(os, "O_NOFOLLOW")
POSIX_ONLY = "owner-only key file is POSIX-only (PRODUCT.md A44); KeyStore refuses it on Windows"


def _no_keychain():
    raise ImportError("no keyring library")


class _Backend:
    """A stand-in OS keychain backend's state: its priority and the values it holds."""

    priority = 5

    def __init__(self) -> None:
        self.data: dict = {}


def _keychain_module(test: unittest.TestCase, backend: _Backend, module: str = "keyring.backends.SecretService"):
    """A keyring module whose ``get_keyring()`` returns an instance of the library's own class from
    ``module`` (Cockpit admits OS keychains by class identity, #326), with that class's probe and
    password methods pointed at ``backend`` until the test ends. No real keychain is reached."""
    cls = getattr(importlib.import_module(module), "Keyring")
    for name, value in (("priority", backend.priority),
                        ("get_password", lambda self, s, n: backend.data.get((s, n))),
                        ("set_password", lambda self, s, n, v: backend.data.__setitem__((s, n), v)),
                        ("delete_password", lambda self, s, n: backend.data.pop((s, n), None))):
        patcher = mock.patch.object(cls, name, value)
        patcher.start()
        test.addCleanup(patcher.stop)
    instance = cls()
    return types.SimpleNamespace(get_keyring=lambda: instance)


@unittest.skipUnless(POSIX, POSIX_ONLY)
class OwnerOnlyFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o700)
        self.path = os.path.join(self.dir, "model-keys.json")
        self.keys = OwnerKeys.for_host(self.path, load=_no_keychain)

    def tearDown(self) -> None:
        for root, dirs, files in os.walk(self.dir, topdown=False):
            for name in files + dirs:
                full = os.path.join(root, name)
                if os.path.islink(full) or os.path.isfile(full):
                    os.unlink(full)
                else:
                    os.chmod(full, 0o700)
                    os.rmdir(full)
        os.rmdir(self.dir)

    def refused(self, cause: str, fn, *args) -> None:
        with self.assertRaises(KeyRefused) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.cause, cause)
        self.assertNotIn(FAKE, str(ctx.exception))

    def test_owner_round_trip_is_redacted(self) -> None:
        self.assertFalse(self.keys.keychain)
        self.assertEqual(self.keys.put("model_provider", "openai", FAKE), "owner_only_file")
        value = self.keys.model_key("openai")
        self.assertIsInstance(value, secrets.Redacted)
        self.assertEqual(value.reveal(), FAKE)
        self.assertNotIn(FAKE, repr(value) + str(value) + f"{value}")
        self.assertEqual(stat.S_IMODE(os.lstat(self.path).st_mode), 0o600)
        self.assertIsNone(self.keys.model_key("anthropic"))
        self.keys.put("model_provider", "anthropic", secrets.Redacted(FAKE + "2"))
        self.assertEqual(self.keys.model_key("anthropic").reveal(), FAKE + "2")
        self.keys.delete("model_provider", "openai")
        self.assertIsNone(self.keys.model_key("openai"))

    def test_a_file_readable_by_others_is_refused_not_repaired(self) -> None:
        self.keys.put("model_provider", "openai", FAKE)
        for mode in (0o644, 0o640, 0o604, 0o660, 0o666):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.path, mode)
                self.refused("key_file_not_owner_only", self.keys.model_key, "openai")
                self.refused("key_file_not_owner_only", self.keys.put, "model_provider", "x", FAKE)
                self.assertEqual(stat.S_IMODE(os.lstat(self.path).st_mode), mode)
        os.chmod(self.path, 0o600)
        self.assertEqual(self.keys.model_key("openai").reveal(), FAKE)

    def test_a_directory_others_can_enter_is_refused(self) -> None:
        self.keys.put("model_provider", "openai", FAKE)
        for mode in (0o755, 0o750, 0o711, 0o777):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.dir, mode)
                self.refused("key_dir_not_owner_only", self.keys.model_key, "openai")
                self.refused("key_dir_not_owner_only", self.keys.put, "model_provider", "x", FAKE)
        os.chmod(self.dir, 0o700)

    def test_a_non_owner_process_is_refused(self) -> None:
        self.keys.put("model_provider", "openai", FAKE)
        other = os.geteuid() + 4242
        with mock.patch("os.geteuid", return_value=other):
            self.refused("key_dir_not_owner_only", self.keys.model_key, "openai")
            self.refused("key_dir_not_owner_only", self.keys.put, "model_provider", "openai", "overwrite")
            self.refused("key_dir_not_owner_only", self.keys.delete, "model_provider", "openai")
            doctor = self.keys.doctor()
            self.assertEqual(doctor["level"], "fail")
            self.assertNotIn(FAKE, json.dumps(doctor) + self.keys.doctor_line())
        self.assertEqual(self.keys.model_key("openai").reveal(), FAKE)

    def test_a_loose_or_symlinked_lock_refuses_reads_and_fails_doctor(self) -> None:
        self.keys.put("model_provider", "openai", FAKE)
        lock = os.path.join(self.dir, ".keys.lock")
        os.chmod(lock, 0o644)
        self.refused("key_file_not_owner_only", self.keys.model_key, "openai")
        self.assertEqual(self.keys.doctor()["level"], "fail")
        os.unlink(lock)
        os.symlink(self.path, lock)
        self.refused("key_file_not_owner_only", self.keys.model_key, "openai")
        self.assertEqual(self.keys.doctor()["level"], "fail")
        os.unlink(lock)
        self.assertEqual(self.keys.model_key("openai").reveal(), FAKE)
        self.assertEqual(self.keys.doctor()["level"], "warn")

    def test_a_symlinked_key_file_is_refused(self) -> None:
        real = os.path.join(self.dir, "real.json")
        OwnerKeys.for_host(real, load=_no_keychain).put("model_provider", "openai", FAKE)
        os.symlink(real, self.path)
        self.refused("key_file_not_owner_only", self.keys.model_key, "openai")

    def test_a_file_labelled_another_kind_is_refused(self) -> None:
        self.keys.put("model_provider", "openai", FAKE)
        body = json.loads(Path(self.path).read_text())
        for kind in ("broker", "venue", None, 1, ["model_provider"]):
            with self.subTest(kind=kind):
                body["kind"] = kind
                Path(self.path).write_text(json.dumps(body))
                os.chmod(self.path, 0o600)
                self.refused("key_file_malformed", self.keys.model_key, "openai")
        del body["kind"]
        Path(self.path).write_text(json.dumps(body))
        self.refused("key_file_malformed", self.keys.model_key, "openai")

    def test_broker_and_venue_keys_never_go_to_a_file(self) -> None:
        for kind in ("broker", "venue"):
            with self.subTest(kind=kind):
                self.refused("keychain_required", self.keys.put, kind, "alpaca", FAKE)
                self.refused("keychain_required", self.keys.get, kind, "alpaca")
        self.assertFalse(os.path.exists(self.path))

    def test_a_refusal_never_falls_back(self) -> None:
        self.keys.put("model_provider", "openai", FAKE)
        os.chmod(self.path, 0o644)
        keyring.set_keyring(_InMemory())
        try:
            secrets.set_secret("openai", "from-app-keyring")
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "from-env", "OPENAI": "from-env"}):
                self.refused("key_file_not_owner_only", self.keys.model_key, "openai")
        finally:
            keyring.set_keyring(_PREVIOUS)

    def test_unknown_kinds_and_bad_names_are_refused(self) -> None:
        self.refused("key_kind_unknown", self.keys.get, "live_broker", "x")
        self.refused("key_name_malformed", self.keys.get, "model_provider", "../x")
        self.refused("key_name_malformed", self.keys.put, "model_provider", "A B", FAKE)


_PREVIOUS = keyring.get_keyring()


class _InMemory(keyring.backend.KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.store: dict = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


class KeychainHostTests(unittest.TestCase):
    def test_with_a_keychain_every_kind_goes_to_it(self) -> None:
        backend = _Backend()
        mod = _keychain_module(self, backend)
        with tempfile.TemporaryDirectory() as tmp:
            keys = OwnerKeys.for_host(os.path.join(tmp, "k.json"), load=lambda: mod)
            self.assertTrue(keys.keychain)
            for kind in ("model_provider", "broker", "venue"):
                self.assertEqual(keys.put(kind, "acct", FAKE), "keyring")
                self.assertEqual(keys.get(kind, "acct").reveal(), FAKE)
            self.assertFalse(os.path.exists(os.path.join(tmp, "k.json")))
            self.assertEqual(sorted(backend.data), sorted([("csl-cockpit", f"{k}:acct") for k in
                                                           ("broker", "model_provider", "venue")]
                                                          + ([MARKER] if RECORDS else [])))  # B24

    def test_fail_null_and_nan_backends_are_not_keychains(self) -> None:
        for module, priority in (("keyring.backends.fail", 5), ("keyring.backends.null", 5),
                                 ("keyring.backends.SecretService", 0),
                                 ("keyring.backends.SecretService", float("nan"))):
            with self.subTest(module=module, priority=priority):
                backend = _Backend()
                backend.priority = priority
                mod = _keychain_module(self, backend, module)
                status = detect.detect_keychain(lambda: mod)
                self.assertFalse(status.available)

    @unittest.skipUnless(POSIX, POSIX_ONLY)
    def test_doctor_fails_a_loose_old_key_file_even_with_a_keychain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o700)
            path = os.path.join(tmp, "k.json")
            OwnerKeys.for_host(path, load=_no_keychain).put("model_provider", "openai", FAKE)
            mod = _keychain_module(self, _Backend())
            keys = OwnerKeys.for_host(path, load=lambda: mod)
            self.assertEqual(keys.doctor()["level"], "ok")
            os.chmod(path, 0o644)
            result = keys.doctor()
            self.assertEqual(result["level"], "fail")
            self.assertIn("not owner-only", keys.doctor_line())
            self.assertNotIn(FAKE, json.dumps(result) + keys.doctor_line())

    def test_owner_keys_only_wraps_a_real_store(self) -> None:
        with self.assertRaises(TypeError):
            OwnerKeys(object(), object(), None)

    def test_only_the_keys_and_panel_packages_are_importable(self) -> None:
        with self.assertRaises(engine_path.EngineImportRefused):
            engine_path.import_engine("cockpit.paper.gateway")
        fake = types.ModuleType("cockpit")
        fake.__file__ = "/elsewhere/cockpit/__init__.py"
        with mock.patch.dict(sys.modules, {"cockpit": fake}):
            with self.assertRaises(engine_path.EngineImportRefused):
                engine_path.import_engine("cockpit.keys")


class ComposeMountTests(unittest.TestCase):
    """The #319 mount line: compose() hangs OwnerKeys off the running app's state."""

    def test_compose_exposes_owner_keys_on_app_state(self) -> None:
        from main import compose

        class _NoKeychain(_InMemory):
            """The app's store on a host without an OS keychain (read as keyring's null backend)."""

        _NoKeychain.__module__ = "keyring.backends.null"
        previous, cwd = keyring.get_keyring(), os.getcwd()
        keyring.set_keyring(_NoKeychain())
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            env = {"COCKPIT_HALT_PATH": os.path.join(tmp, "HALT"),
                   "XDG_CONFIG_HOME": os.path.join(tmp, "xdg")}
            try:
                os.chdir(tmp)
                with mock.patch.dict(os.environ, env):
                    _, app, _ = compose(db_url=f"sqlite:///{Path(tmp, 'c.db').as_posix()}",
                                        parquet_root=os.path.join(tmp, "raw"))
                    keys = app.state.owner_keys
                    self.assertIsInstance(keys, OwnerKeys)
                    with self.assertRaises(KeyRefused):
                        keys.get("wallet", "openai")
                    self.assertTrue(keys.doctor_line().startswith("keys: "))
                    if POSIX:
                        self.assertFalse(keys.keychain)
                        key_dir = os.path.join(tmp, "xdg", "csl-cockpit")
                        os.makedirs(key_dir)
                        os.chmod(key_dir, 0o700)
                        self.assertEqual(keys.put("model_provider", "openai", FAKE), "owner_only_file")
                        self.assertEqual(keys.model_key("openai").reveal(), FAKE)
                        loose = [os.path.join(key_dir, f) for f in os.listdir(key_dir) if f.endswith(".json")]
                        self.assertEqual(len(loose), 1)
                        os.chmod(loose[0], 0o644)
                        secrets.set_secret("openai", "from-app-keyring")
                        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "from-env"}):
                            with self.assertRaises(KeyRefused) as ctx:
                                keys.model_key("openai")
                        self.assertEqual(ctx.exception.cause, "key_file_not_owner_only")
                        os.chmod(loose[0], 0o600)
                        with mock.patch("os.geteuid", return_value=os.geteuid() + 4242), \
                                self.assertRaises(KeyRefused):
                            keys.model_key("openai")
            finally:
                os.chdir(cwd)
                keyring.set_keyring(previous)


if __name__ == "__main__":
    unittest.main()
