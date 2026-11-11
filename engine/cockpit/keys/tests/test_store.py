"""Owner-only key file (CSL-59; PRODUCT.md 055cc7a5 A44, 5.4).

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/keys/tests -t engine -v``.
Fakes only: an in-memory keyring and temp directories. No provider, venue, network or real keyring.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from cockpit.keys import KeyRefused, KeyStore

FAKE = "fake-key-not-real-0123"
KEYS_DIR = Path(__file__).resolve().parents[1]


class FakeKeyring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get(self, service: str, name: str) -> str | None:
        return self.data.get((service, name))

    def set(self, service: str, name: str, secret: str) -> None:
        self.data[(service, name)] = secret

    def delete(self, service: str, name: str) -> None:
        self.data.pop((service, name), None)


class KeyringHostTests(unittest.TestCase):
    def test_every_kind_uses_the_keyring_when_there_is_one(self) -> None:
        ring = FakeKeyring()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        store = KeyStore(keyring=ring, key_file=os.path.join(d, "keys.json"))
        for kind in ("model_provider", "broker", "venue"):
            with self.subTest(kind=kind):
                self.assertEqual(store.put(kind, "acme", FAKE), "keyring")
                self.assertEqual(store.get(kind, "acme"), FAKE)
        self.assertFalse(os.path.exists(os.path.join(d, "keys.json")))
        store.delete("broker", "acme")
        self.assertIsNone(store.get("broker", "acme"))
        self.assertEqual(store.get("venue", "acme"), FAKE)


@unittest.skipIf(os.name == "nt", "POSIX owner-only file")
class NoKeychainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="cockpit-keys-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        os.chmod(self.dir, 0o700)
        self.path = os.path.join(self.dir, "model-keys.json")
        self.store = KeyStore(keyring=None, key_file=self.path)

    def refused(self, cause: str, fn, *args) -> KeyRefused:
        with self.assertRaises(KeyRefused) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.cause, cause)
        self.assertNotIn(FAKE, str(ctx.exception))
        return ctx.exception

    def test_model_provider_key_round_trips_in_an_owner_only_file(self) -> None:
        self.assertEqual(self.store.put("model_provider", "openai", FAKE), "owner_only_file")
        self.store.put("model_provider", "refuseless", FAKE + "2")
        st = os.lstat(self.path)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        self.assertEqual(st.st_uid, os.geteuid())
        self.assertEqual(KeyStore(keyring=None, key_file=self.path).get("model_provider", "openai"), FAKE)
        self.store.delete("model_provider", "openai")
        self.assertIsNone(self.store.get("model_provider", "openai"))
        self.assertEqual(self.store.get("model_provider", "refuseless"), FAKE + "2")
        self.assertEqual([n for n in os.listdir(self.dir) if n.startswith(".keys-")], [])
        lock = os.lstat(os.path.join(self.dir, ".keys.lock"))
        self.assertEqual(stat.S_IMODE(lock.st_mode), 0o600)
        self.assertEqual(lock.st_uid, os.geteuid())
        self.assertTrue(stat.S_ISREG(lock.st_mode))

    def test_broker_and_venue_keys_never_go_to_a_file(self) -> None:
        for kind in ("broker", "venue"):
            with self.subTest(kind=kind):
                self.refused("keychain_required", self.store.put, kind, "kalshi", FAKE)
                self.refused("keychain_required", self.store.get, kind, "kalshi")
        self.assertFalse(os.path.exists(self.path))

    def test_file_that_is_not_owner_only_is_refused_not_repaired(self) -> None:
        self.store.put("model_provider", "openai", FAKE)
        for mode in (0o640, 0o604, 0o660):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.path, mode)
                self.refused("key_file_not_owner_only", self.store.get, "model_provider", "openai")
                self.refused("key_file_not_owner_only", self.store.put, "model_provider", "x", FAKE)
                self.assertEqual(stat.S_IMODE(os.lstat(self.path).st_mode), mode)

    def test_directory_must_be_owner_only_and_real(self) -> None:
        os.chmod(self.dir, 0o755)
        self.refused("key_dir_not_owner_only", self.store.put, "model_provider", "openai", FAKE)
        os.chmod(self.dir, 0o700)
        link = self.dir + "-link"
        os.symlink(self.dir, link)
        self.addCleanup(os.unlink, link)
        self.refused("key_dir_not_owner_only", KeyStore(keyring=None, key_file=os.path.join(link, "k.json")).put,
                     "model_provider", "openai", FAKE)
        self.refused("key_dir_missing", KeyStore(keyring=None, key_file=os.path.join(self.dir, "no", "k.json")).get,
                     "model_provider", "openai")

    def test_symlinked_key_file_is_refused(self) -> None:
        target = os.path.join(self.dir, "elsewhere.json")
        Path(target).write_text(json.dumps({"version": 1, "keys": {"openai": FAKE}}))
        os.chmod(target, 0o600)
        os.symlink(target, self.path)
        self.refused("key_file_not_owner_only", self.store.get, "model_provider", "openai")
        self.refused("key_file_not_owner_only", self.store.put, "model_provider", "openai", FAKE)

    def test_malformed_file_and_inputs_refuse(self) -> None:
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.write(fd, b"{not json")
        os.close(fd)
        self.refused("key_file_malformed", self.store.get, "model_provider", "openai")
        Path(self.path).write_text(json.dumps({"version": 2, "keys": {}}))
        self.refused("key_file_malformed", self.store.get, "model_provider", "openai")
        os.unlink(self.path)
        self.refused("key_kind_unknown", self.store.put, "exchange", "openai", FAKE)
        self.refused("key_name_malformed", self.store.put, "model_provider", "../etc", FAKE)
        self.refused("secret_malformed", self.store.put, "model_provider", "openai", "")
        self.refused("key_file_not_configured", KeyStore(keyring=None).put, "model_provider", "openai", FAKE)

    def test_concurrent_puts_keep_every_key(self) -> None:
        names = [f"k{i:02d}" for i in range(32)]
        barrier = threading.Barrier(len(names))
        errors: list[BaseException] = []

        def work(entry: str) -> None:
            try:
                barrier.wait(timeout=10)
                KeyStore(keyring=None, key_file=self.path).put("model_provider", entry, FAKE)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(entry,)) for entry in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        fresh = KeyStore(keyring=None, key_file=self.path)
        self.assertEqual(
            {entry: fresh.get("model_provider", entry) for entry in names},
            {entry: FAKE for entry in names},
        )

    def test_writable_ancestor_is_refused(self) -> None:
        outer = os.path.join(self.dir, "open")
        os.mkdir(outer)
        os.chmod(outer, 0o777)  # mkdir applies umask, so 0o777 there would still be 0755
        inner = os.path.join(outer, "inner")
        os.mkdir(inner, 0o700)
        path = os.path.join(inner, "k.json")
        store = KeyStore(keyring=None, key_file=path)
        self.refused("key_dir_not_owner_only", store.put, "model_provider", "openai", FAKE)
        self.refused("key_dir_not_owner_only", store.get, "model_provider", "openai")
        self.assertFalse(os.path.exists(path))
        self.assertNotIn(".keys.lock", os.listdir(inner))

    def test_symlink_in_the_directory_chain_is_refused(self) -> None:
        real = os.path.join(self.dir, "real")
        os.mkdir(real, 0o700)
        link = os.path.join(self.dir, "link")
        os.symlink(real, link)
        inner = os.path.join(link, "inner")
        os.mkdir(inner, 0o700)
        path = os.path.join(inner, "k.json")
        store = KeyStore(keyring=None, key_file=path)
        self.refused("key_dir_not_owner_only", store.put, "model_provider", "openai", FAKE)
        self.assertFalse(os.path.exists(os.path.join(real, "inner", "k.json")))

    def test_loose_or_symlinked_lock_is_refused_not_repaired(self) -> None:
        self.store.put("model_provider", "openai", FAKE)
        lock = os.path.join(self.dir, ".keys.lock")
        os.chmod(lock, 0o644)
        self.refused("key_file_not_owner_only", self.store.put, "model_provider", "other", FAKE)
        self.assertEqual(stat.S_IMODE(os.lstat(lock).st_mode), 0o644)
        # Reads are refused too (README: refused on read and on write), never repaired.
        self.refused("key_file_not_owner_only", self.store.get, "model_provider", "openai")
        self.assertEqual(stat.S_IMODE(os.lstat(lock).st_mode), 0o644)
        os.unlink(lock)
        os.symlink(self.path, lock)
        self.refused("key_file_not_owner_only", self.store.put, "model_provider", "other", FAKE)
        self.assertTrue(stat.S_ISLNK(os.lstat(lock).st_mode))
        self.refused("key_file_not_owner_only", self.store.get, "model_provider", "openai")
        os.unlink(lock)
        self.assertEqual(self.store.get("model_provider", "openai"), FAKE)

    def test_file_body_names_kind_and_holds_no_broker_key(self) -> None:
        self.store.put("model_provider", "openai", FAKE)
        body = json.loads(Path(self.path).read_text())
        self.assertEqual((body["version"], body["kind"], sorted(body["keys"])), (1, "model_provider", ["openai"]))


class StaticTests(unittest.TestCase):
    def test_no_network_keyring_library_or_frozen_app_import(self) -> None:
        pat = re.compile(r"^\s*(import|from)\s+(urllib|http|requests|httpx|socket|ssl|keyring|subprocess|app)\b", re.M)
        for py in KEYS_DIR.rglob("*.py"):
            with self.subTest(file=py.name):
                self.assertIsNone(pat.search(py.read_text()))

    def test_only_detect_loads_the_keyring_library_lazily(self) -> None:
        # The keyring library is optional and is loaded only by detect.py, lazily, through an injectable loader.
        lazy = re.compile(r"import_module\(\s*['\"]([^'\"]+)['\"]")
        for py in KEYS_DIR.rglob("*.py"):
            if py.parent.name == "tests":
                continue
            with self.subTest(file=py.name):
                found = lazy.findall(py.read_text())
                self.assertEqual(found, ["keyring"] if py.name == "detect.py" else [])


if __name__ == "__main__":
    unittest.main()
