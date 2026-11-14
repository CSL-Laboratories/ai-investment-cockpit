"""B38: leftover temp files of a killed save are removed by the next save or acceptance (CSL-59).

DeepSeek's second read of #330 (F-D), rechecked by the Liaison: a process killed between writing the temp file
``.keys-<pid>-<16 hex>`` and renaming it over the key file or the keychain record leaves that file in the
owner-only key folder, and nothing removed it; on a host with no keychain it holds the model keys as they were
being written, so a key deleted later is still in it. Lucas, Nov 13, 2026, asked "Should Cockpit clean these
up?": "Sweep them (Recommended)". So a save or an acceptance, while it holds ``.keys.lock`` and before it writes,
removes regular, owner-only files of the running user whose name is exactly that pattern, and nothing else. A
read takes no lock and sweeps nothing; a delete sweeps nothing either.

Spawned child processes that exit at the rename, temp folders, a dict or a JSON file for a keychain, made-up
keys only.
"""

from __future__ import annotations

import errno
import json
import multiprocessing
import os
import re
import stat
import threading
from unittest import mock

from cockpit.keys import KeyStore, doctor, doctor_line
from cockpit.keys import store as store_mod
from cockpit.keys.detect import _KEYCHAIN_NAMES, KeychainStatus
from cockpit.keys.tests.fakes import FAKE, MARKER
from cockpit.keys.tests.test_b24_enrolment import FileRing
from cockpit.keys.tests.test_b24_keychain_record import A, Ring, _Case

OLD = "made-up-old-model-key-not-real-4567"  # the key that must not outlive its deletion
TEMP = re.compile(r"\.keys-[0-9]+-[0-9a-f]{16}")


def _killed_at_the_key_file_rename(path: str) -> None:
    """In a child process: a model-key save that exits (os._exit) where it would rename its temp file."""
    os.rename = lambda *args, **kwargs: os._exit(23)  # type: ignore[assignment]
    KeyStore(keyring=None, key_file=path).put("model_provider", "new", FAKE)


def _killed_at_the_record_rename(path: str, ring_file: str) -> None:
    """In a child process: a first broker save that exits where it would rename the record's temp file."""
    os.rename = lambda *args, **kwargs: os._exit(24)  # type: ignore[assignment]
    KeyStore(keyring=FileRing(ring_file), key_file=path, keychain=A,
             keychain_names=_KEYCHAIN_NAMES).put("broker", "acct", FAKE)


def _paused_at_the_rename(path: str, ready, go) -> None:  # type: ignore[no-untyped-def]
    """In a child process: a model-key save that holds the lock and waits, its temp file written, at the rename."""
    real = os.rename

    def rename(*args, **kwargs):  # type: ignore[no-untyped-def]
        ready.set()
        if not go.wait(60):
            os._exit(3)
        return real(*args, **kwargs)

    os.rename = rename  # type: ignore[assignment]
    KeyStore(keyring=None, key_file=path).put("model_provider", "first", FAKE + "-first")


class LeftoverTests(_Case):
    def leftovers(self) -> list[str]:
        return sorted(name for name in os.listdir(self.leaf) if TEMP.fullmatch(name))

    def holding(self, secret: str) -> list[str]:
        """Every regular file in the key folder whose bytes contain ``secret``."""
        found = []
        for name in os.listdir(self.leaf):
            full = os.path.join(self.leaf, name)
            if stat.S_ISREG(os.lstat(full).st_mode):
                with open(full, "rb") as fh:
                    if secret.encode() in fh.read():
                        found.append(name)
        return sorted(found)

    def spawn(self, target, *args) -> int:  # type: ignore[no-untyped-def]
        child = multiprocessing.get_context("spawn").Process(target=target, args=args)
        child.start()
        try:
            child.join(60)
        finally:
            if child.is_alive():  # a regression that hangs it must not leave it running
                child.terminate()
                child.join(10)
        return child.exitcode  # type: ignore[return-value]

    def test_a_save_killed_at_the_key_file_rename_leaves_a_temp_file_that_the_next_save_removes(self) -> None:
        store = KeyStore(keyring=None, key_file=self.path)
        store.put("model_provider", "old", OLD)
        self.assertEqual(self.spawn(_killed_at_the_key_file_rename, self.path), 23)
        (left,) = self.leftovers()  # the killed save's temp file, with the keys as it was writing them
        self.assertEqual(stat.S_IMODE(os.lstat(os.path.join(self.leaf, left)).st_mode), 0o600)
        self.assertEqual(self.holding(OLD), sorted([left, "model-keys.json"]))
        self.assertEqual(store.get("model_provider", "old"), OLD)
        self.assertEqual(self.leftovers(), [left])  # a read sweeps nothing
        store.delete("model_provider", "old")
        self.assertEqual(self.holding(OLD), [left])  # a delete sweeps nothing: the deleted key is still there
        store.put("model_provider", "other", FAKE)  # the next save removes it
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.holding(OLD), [])  # the deleted key is in no file
        self.assertEqual(sorted(os.listdir(self.leaf)), [".keys.lock", "model-keys.json"])
        self.assertEqual((store.get("model_provider", "new"), store.get("model_provider", "other")), (None, FAKE))

    def test_a_first_save_killed_at_the_record_rename_leaves_a_temp_file_that_the_acceptance_removes(self) -> None:
        ring_file = os.path.join(self.root, "ring.json")
        self.assertEqual(self.spawn(_killed_at_the_record_rename, self.path, ring_file), 24)
        ring = FileRing(ring_file)
        self.assertEqual(ring.data, {MARKER: A})  # the marker was written; the record was not published
        (left,) = self.leftovers()
        with open(os.path.join(self.leaf, left), "rb") as fh:
            self.assertEqual(json.loads(fh.read()), {"version": 1, "keychain": A})  # the record it was writing
        self.refused("keychain_record_missing", self.store(ring).get, "broker", "acct")
        self.assertEqual(self.leftovers(), [left])  # a read sweeps nothing
        self.assertEqual(self.store(ring).accept_keychain(A), "")  # the acceptance removes it
        self.assertEqual(sorted(os.listdir(self.leaf)), [".keychain.json", ".keys.lock"])
        self.store(ring).put("broker", "acct", FAKE)
        self.assertEqual(self.store(ring).get("broker", "acct"), FAKE)

    def test_every_save_and_the_acceptance_sweep_while_holding_the_lock(self) -> None:
        held: list[bool] = []
        real = store_mod._sweep_leftovers

        def sweep(dir_fd: int) -> None:
            # Another open of .keys.lock cannot take it while the sweeping operation holds it.
            fd = os.open(".keys.lock", os.O_RDWR | os.O_NOFOLLOW, dir_fd=dir_fd)
            try:
                store_mod.fcntl.flock(fd, store_mod.fcntl.LOCK_EX | store_mod.fcntl.LOCK_NB)  # type: ignore[union-attr]
            except OSError as exc:
                held.append(exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN))
            else:
                held.append(False)
            finally:
                os.close(fd)
            real(dir_fd)

        def plant() -> str:
            name = f".keys-{os.getpid()}-{os.urandom(8).hex()}"
            fd = os.open(os.path.join(self.leaf, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            return name

        ring = Ring()
        with mock.patch.object(store_mod, "_sweep_leftovers", sweep):
            KeyStore(keyring=None, key_file=self.path).put("model_provider", "first", FAKE)  # makes the folder
            for label, operation in (
                ("model-key save", lambda: KeyStore(keyring=None, key_file=self.path).put("model_provider", "x", FAKE)),
                ("first broker save", lambda: self.store(ring).put("broker", "acct", FAKE)),
                ("later venue save", lambda: self.store(ring).put("venue", "acct", FAKE)),
                ("acceptance", lambda: self.store(ring).accept_keychain(A)),
            ):
                with self.subTest(operation=label):
                    planted = plant()
                    operation()
                    self.assertNotIn(planted, os.listdir(self.leaf))
        self.assertEqual(held, [True] * 5)
        self.assertEqual(self.leftovers(), [])
        planted = plant()  # reads and deletes do not sweep (a model-key delete holds the lock, and still does not)
        self.assertEqual(self.store(ring).get("broker", "acct"), FAKE)
        self.store(ring).delete("venue", "acct")
        self.assertEqual(KeyStore(keyring=None, key_file=self.path).get("model_provider", "x"), FAKE)
        KeyStore(keyring=None, key_file=self.path).delete("model_provider", "x")
        self.assertEqual(self.leftovers(), [planted])

    def test_only_cockpit_s_own_leftovers_are_removed(self) -> None:
        store = KeyStore(keyring=None, key_file=self.path)
        store.put("model_provider", "openai", FAKE)
        line = doctor_line(doctor(KeychainStatus(False, "", "no_keychain_backend"), self.path))
        own = ".keys-12345-0123456789abcdef"
        link, folder, loose, theirs, pipe = (".keys-1-aaaaaaaaaaaaaaaa", ".keys-2-bbbbbbbbbbbbbbbb",
                                             ".keys-3-cccccccccccccccc", ".keys-4-dddddddddddddddd",
                                             ".keys-11-eeeeeeeeeeeeeeee")
        lookalikes = (".keys-5-ABCDEF0123456789", ".keys-6-0123456789abcde", ".keys-7-0123456789abcdef0",
                      ".keys-x-0123456789abcdef", ".keys--0123456789abcdef", ".keys-8-0123456789abcdef.tmp",
                      "keys-9-0123456789abcdef", ".Keys-10-0123456789abcdef")

        def make(name: str, mode: int = 0o600, body: bytes = b"made-up") -> None:
            fd = os.open(os.path.join(self.leaf, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
            os.chmod(os.path.join(self.leaf, name), mode)

        make(own)
        os.symlink(self.path, os.path.join(self.leaf, link))  # a link to the key file itself
        os.mkdir(os.path.join(self.leaf, folder), 0o700)
        make(loose, 0o640)
        make(theirs)
        os.mkfifo(os.path.join(self.leaf, pipe), 0o600)  # owner-only, but not a regular file
        os.chmod(os.path.join(self.leaf, pipe), 0o600)
        for name in lookalikes:
            make(name)
        before = {name: os.lstat(os.path.join(self.leaf, name)) for name in os.listdir(self.leaf) if name != own}
        real_lstat = os.lstat

        def lstat(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            st = real_lstat(path, *args, **kwargs)
            if path == theirs:  # as if another user (root, say) had put it there
                fields = list(st[:10])
                fields[4] = st.st_uid + 1
                return os.stat_result(fields)
            return st

        with mock.patch("cockpit.keys.store.os.lstat", lstat):
            store.put("model_provider", "other", FAKE)  # not refused because of any of them
        after = {name: os.lstat(os.path.join(self.leaf, name)) for name in os.listdir(self.leaf)}
        self.assertNotIn(own, after)
        for name in (link, folder, loose, theirs, pipe) + lookalikes:
            with self.subTest(left_alone=name):
                self.assertIn(name, after)
                self.assertEqual((after[name].st_ino, after[name].st_mode), (before[name].st_ino, before[name].st_mode))
        self.assertEqual(os.readlink(os.path.join(self.leaf, link)), self.path)
        self.assertEqual(store.get("model_provider", "openai"), FAKE)
        # Reported as the folder's other entries are: the doctor line does not change.
        self.assertEqual(doctor_line(doctor(KeychainStatus(False, "", "no_keychain_backend"), self.path)), line)

    def test_two_processes_at_once_a_save_in_flight_keeps_its_temp_file(self) -> None:
        KeyStore(keyring=None, key_file=self.path).put("model_provider", "zero", FAKE)  # the folder and the lock
        context = multiprocessing.get_context("spawn")
        ready, go = context.Event(), context.Event()
        child = context.Process(target=_paused_at_the_rename, args=(self.path, ready, go))
        child.start()
        try:
            self.assertTrue(ready.wait(60))  # the child holds the lock, its temp file written, at the rename
            (in_flight,) = self.leftovers()
            reached = threading.Event()
            real_flock = store_mod.fcntl.flock  # type: ignore[union-attr]
            worker: list[threading.Thread] = []

            def flock(fd: int, operation: int) -> None:
                if worker and threading.current_thread() is worker[0] and operation & store_mod.fcntl.LOCK_EX:  # type: ignore[union-attr]
                    reached.set()
                real_flock(fd, operation)

            with mock.patch.object(store_mod.fcntl, "flock", flock):
                worker.append(threading.Thread(daemon=True, target=lambda: KeyStore(
                    keyring=None, key_file=self.path).put("model_provider", "second", FAKE + "-second")))
                worker[0].start()
                self.assertTrue(reached.wait(30))  # the second save has asked for the lock the child holds
                worker[0].join(0.5)
                self.assertTrue(worker[0].is_alive())  # and waits there
                self.assertEqual(self.leftovers(), [in_flight])  # the child's temp file is not swept
                go.set()
                child.join(60)
                worker[0].join(30)
            self.assertEqual(child.exitcode, 0)
            self.assertFalse(worker[0].is_alive())
        finally:
            go.set()
            if child.is_alive():
                child.terminate()
                child.join(10)
        store = KeyStore(keyring=None, key_file=self.path)
        self.assertEqual((store.get("model_provider", "first"), store.get("model_provider", "second")),
                         (FAKE + "-first", FAKE + "-second"))
        self.assertEqual(self.leftovers(), [])
