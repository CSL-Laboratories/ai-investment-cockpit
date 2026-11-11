"""Owner-only key file (CSL-59; PRODUCT.md 055cc7a5 A44, 5.4).

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/keys/tests -t engine -v``.
Fakes only: an in-memory keyring and temp directories. No provider, venue, network or real keyring.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from cockpit.keys import KeyRefused, KeyStore, KeychainStatus
from cockpit.keys.detect import doctor
from cockpit.keys.store import key_file_state

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
        self.assertEqual(self.store.get("model_provider", "openai"), FAKE)
        os.unlink(lock)
        os.symlink(self.path, lock)
        self.refused("key_file_not_owner_only", self.store.put, "model_provider", "other", FAKE)
        self.assertTrue(stat.S_ISLNK(os.lstat(lock).st_mode))
        self.assertIsNone(self.store.get("model_provider", "other"))

    def test_file_body_names_kind_and_holds_no_broker_key(self) -> None:
        self.store.put("model_provider", "openai", FAKE)
        body = json.loads(Path(self.path).read_text())
        self.assertEqual((body["version"], body["kind"], sorted(body["keys"])), (1, "model_provider", ["openai"]))

    def test_fifo_read_does_not_block(self) -> None:
        os.mkfifo(self.path, 0o600)
        err = self._returns_within(1.5, lambda: self.store.get("model_provider", "openai"), self.path)
        self.assertIsInstance(err, KeyRefused)
        self.assertEqual(err.cause, "key_file_not_owner_only")
        self.assertEqual(key_file_state(self.path), "file_not_owner_only:fifo")
        none = KeychainStatus(False, "", "no_keychain_backend")
        self.assertEqual(doctor(none, self.path)["level"], "fail")

    def test_fifo_lock_save_does_not_block(self) -> None:
        lock = os.path.join(self.dir, ".keys.lock")
        os.mkfifo(lock, 0o600)
        err = self._returns_within(1.5, lambda: self.store.put("model_provider", "openai", FAKE), lock)
        self.assertIsInstance(err, KeyRefused)
        self.assertEqual(err.cause, "key_file_not_owner_only")
        self.assertFalse(os.path.lexists(self.path))
        self._assert_no_secret_leftover()

    def test_symlink_and_socket_are_classified_from_lstat(self) -> None:
        target = os.path.join(self.dir, "elsewhere.json")
        payload = json.dumps({"version": 1, "kind": "model_provider", "keys": {"openai": FAKE}}).encode()
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, payload)
        os.close(fd)
        os.symlink(target, self.path)
        self.assertEqual(key_file_state(self.path), "file_not_owner_only:symlink")
        err = self._returns_within(1.5, lambda: self.store.get("model_provider", "openai"), None)
        self.assertIsInstance(err, KeyRefused)
        self.assertEqual(err.cause, "key_file_not_owner_only")
        self.assertNotIn(FAKE, str(err))
        os.unlink(self.path)

        os.mknod(self.path, stat.S_IFSOCK | 0o600)
        self.assertTrue(stat.S_ISSOCK(os.lstat(self.path).st_mode))
        self.assertEqual(key_file_state(self.path), "file_not_owner_only:socket")
        err = self._returns_within(1.5, lambda: self.store.get("model_provider", "openai"), None)
        self.assertIsInstance(err, KeyRefused)
        self.assertEqual(err.cause, "key_file_not_owner_only")

    def test_character_device_is_not_classified_as_a_regular_file(self) -> None:
        import cockpit.keys.store as store_mod

        self.assertTrue(hasattr(store_mod, "_entry_problem"), "classifier missing")
        st = os.lstat("/dev/null")
        if not stat.S_ISCHR(st.st_mode):
            self.skipTest("/dev/null is not a character device")
        self.assertEqual(store_mod._entry_problem(st), "device")
        node = os.path.join(self.dir, "node")
        try:
            os.mknod(node, stat.S_IFCHR | 0o600, os.makedev(1, 3))
        except OSError as exc:
            if exc.errno != errno.EPERM:
                raise
        else:
            self.assertEqual(key_file_state(node), "file_not_owner_only:device")

    def test_close_error_does_not_leave_a_temp_key(self) -> None:
        import cockpit.keys.store as store_mod

        real_close = store_mod.os.close
        real_rename = store_mod.os.rename
        raised: list[str] = []

        def close(fd: int) -> None:
            try:
                target = os.readlink(f"/proc/self/fd/{fd}")
            except OSError:
                target = ""
            real_close(fd)
            if ".keys-" in target:
                raised.append(target)
                raise OSError(errno.EIO, "simulated close failure")

        def rename(*args: object, **kwargs: object) -> None:
            raise OSError(errno.EIO, "simulated rename failure")

        store_mod.os.close = close
        store_mod.os.rename = rename
        old = os.umask(0)
        try:
            with self.assertRaises(OSError):
                self.store.put("model_provider", "openai", FAKE)
        finally:
            os.umask(old)
            store_mod.os.close = real_close
            store_mod.os.rename = real_rename
        self.assertGreater(len(raised), 0)
        self.assertTrue(all(".keys-" in target for target in raised))
        temps = [name for name in os.listdir(self.dir) if name.startswith(".keys-")]
        self.assertEqual(temps, [])
        self.assertFalse(os.path.lexists(self.path))
        self._assert_no_secret_leftover()

    def test_failed_save_does_not_leave_a_readable_secret(self) -> None:
        import cockpit.keys.store as store_mod

        real_rename = store_mod.os.rename
        real_unlink = store_mod.os.unlink
        real_fsync = store_mod.os.fsync
        events: list[str] = []

        def rename(*args: object, **kwargs: object) -> None:
            raise OSError(errno.EIO, "simulated rename failure")

        def unlink(path: str, *args: object, **kwargs: object) -> None:
            if isinstance(path, str) and path.startswith(".keys-"):
                raise OSError(errno.EPERM, "simulated unlink failure")
            real_unlink(path, *args, **kwargs)

        def fsync(fd: int) -> None:
            kind = "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
            events.append("fsync-" + kind)
            real_fsync(fd)

        store_mod.os.rename = rename
        store_mod.os.unlink = unlink
        store_mod.os.fsync = fsync
        old = os.umask(0)
        try:
            with self.assertRaises(OSError):
                self.store.put("model_provider", "openai", FAKE)
        finally:
            os.umask(old)
            store_mod.os.rename = real_rename
            store_mod.os.unlink = real_unlink
            store_mod.os.fsync = real_fsync
        temps = [name for name in os.listdir(self.dir) if name.startswith(".keys-")]
        self.assertTrue(temps)
        for name in temps:
            path = os.path.join(self.dir, name)
            st = os.lstat(path)
            self.assertTrue(stat.S_ISREG(st.st_mode))
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
            data = _read_nofollow(path)
            self.assertIsNotNone(data)
            self.assertNotIn(FAKE.encode(), data or b"")
        self.assertIn("fsync-dir", events)

    def test_save_fsyncs_the_directory_after_rename(self) -> None:
        import cockpit.keys.store as store_mod

        real_rename = store_mod.os.rename
        real_fsync = store_mod.os.fsync
        events: list[str] = []

        def rename(*args: object, **kwargs: object) -> None:
            events.append("rename")
            real_rename(*args, **kwargs)

        def fsync(fd: int) -> None:
            kind = "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
            events.append("fsync-" + kind)
            real_fsync(fd)

        store_mod.os.rename = rename
        store_mod.os.fsync = fsync
        try:
            self.assertEqual(self.store.put("model_provider", "openai", FAKE), "owner_only_file")
        finally:
            store_mod.os.rename = real_rename
            store_mod.os.fsync = real_fsync
        self.assertIn("rename", events)
        self.assertIn("fsync-dir", events)
        self.assertGreater(events.index("fsync-dir"), events.index("rename"))
        self.assertEqual(stat.S_IMODE(os.lstat(self.path).st_mode), 0o600)

    def test_key_file_opens_are_nofollow_nonblock_and_0600(self) -> None:
        import cockpit.keys.store as store_mod

        real_open = store_mod.os.open
        seen: list[int] = []

        def opened(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            if not flags & os.O_DIRECTORY:
                seen.append(flags)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        store_mod.os.open = opened
        old = os.umask(0)
        try:
            self.store.put("model_provider", "openai", FAKE)
            self.assertEqual(self.store.get("model_provider", "openai"), FAKE)
        finally:
            os.umask(old)
            store_mod.os.open = real_open
        self.assertTrue(seen)
        for flags in seen:
            self.assertTrue(flags & os.O_NOFOLLOW, oct(flags))
            self.assertTrue(flags & os.O_NONBLOCK, oct(flags))
        self.assertEqual(stat.S_IMODE(os.lstat(self.path).st_mode), 0o600)
        lock = os.lstat(os.path.join(self.dir, ".keys.lock"))
        self.assertEqual(stat.S_IMODE(lock.st_mode), 0o600)
        self.assertTrue(stat.S_ISREG(lock.st_mode))

    def _returns_within(self, seconds: float, fn, unblock: str | None):
        box: list[BaseException] = []

        def run() -> None:
            try:
                fn()
            except BaseException as exc:  # thread boundary; the caller asserts the type
                box.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(seconds)
        if thread.is_alive():
            if unblock is not None:
                try:
                    fd = os.open(unblock, os.O_WRONLY | os.O_NONBLOCK)
                    os.close(fd)
                except OSError:
                    pass
                thread.join(seconds)
            self.fail("call blocked on a special file")
        return box[0] if box else None

    def _assert_no_secret_leftover(self) -> None:
        marker = FAKE.encode()
        for name in os.listdir(self.dir):
            if name == "model-keys.json":
                continue
            path = os.path.join(self.dir, name)
            if not stat.S_ISREG(os.lstat(path).st_mode):
                continue
            data = _read_nofollow(path)
            self.assertNotIn(marker, data or b"", name)
            self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode) & 0o077, 0, name)


def _read_nofollow(path: str) -> bytes | None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            chunks.append(block)
            if sum(len(part) for part in chunks) > 1_000_000:
                break
        return b"".join(chunks)
    finally:
        os.close(fd)


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
