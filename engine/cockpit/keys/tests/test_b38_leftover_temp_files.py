"""B38: leftover temp files of a killed save are removed by the next save or acceptance (CSL-59).

DeepSeek's second read of #330 (F-D), rechecked by the Liaison: a process killed between writing the temp file
``.keys-<pid>-<16 hex>`` and renaming it over the key file or the keychain record leaves that file in the
owner-only key folder, and nothing removed it; on a host with no keychain it holds the model keys as they were
being written, so a key deleted later is still in it. Lucas, Nov 13, 2026, asked "Should Cockpit clean these
up?": "Sweep them (Recommended)". So the operations that take ``.keys.lock`` to write (a broker or venue save on a
keychain host, a model-provider save on a host with no keychain, an acceptance), while they hold it and before
they write, remove regular, owner-only files of the running user whose name is exactly that pattern, and nothing
else, best effort. A model-provider save on a keychain host, a read and a delete sweep nothing. After DeepSeek's
third read and Codex's fourth read: a key file may not have a temp file's name (F-1, R4-1), the scope above is
pinned (F-2, R4-2), failures to list, look or remove are tested (F-3, R4-3), and the process tests reap what
they start (R4-4).

Spawned child processes that exit at the rename, temp folders, a dict or a JSON file for a keychain, made-up
keys only.
"""

from __future__ import annotations

import contextlib
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

    def reap(self, child) -> None:  # type: ignore[no-untyped-def]
        """A child that is still running is terminated, then killed, and reaped; the test fails if it stays alive
        (Codex's fourth read, R4-4). A child that never started is left as it is."""
        if child.pid is None:
            return
        if child.is_alive():  # a regression that hangs it must not leave it running
            child.terminate()
            child.join(10)
        if child.is_alive():
            child.kill()
            child.join(10)
        self.assertFalse(child.is_alive(), "a child process outlived the test")

    def spawn(self, target, *args) -> int:  # type: ignore[no-untyped-def]
        child = multiprocessing.get_context("spawn").Process(target=target, args=args)
        try:
            child.start()
            child.join(60)
        finally:
            self.reap(child)
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
                ("model save on a keychain host", lambda: self.store(ring).put("model_provider", "m", FAKE)),
                ("acceptance", lambda: self.store(ring).accept_keychain(A)),
            ):
                with self.subTest(operation=label):
                    planted = plant()
                    operation()
                    self.assertNotIn(planted, os.listdir(self.leaf))
        self.assertEqual(held, [True] * 6)
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
        worker: list[threading.Thread] = []
        try:
            child.start()
            self.assertTrue(ready.wait(60))  # the child holds the lock, its temp file written, at the rename
            (in_flight,) = self.leftovers()
            reached = threading.Event()
            real_flock = store_mod.fcntl.flock  # type: ignore[union-attr]

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
            # Codex's fourth read, R4-4: on any failure above, release the child, end it (kill if need be) and
            # reap it, then wait for the worker, and fail if either is still alive.
            go.set()
            self.reap(child)
            for thread in worker:
                thread.join(30)
                self.assertFalse(thread.is_alive(), "the second save's thread outlived the test")
        store = KeyStore(keyring=None, key_file=self.path)
        self.assertEqual((store.get("model_provider", "first"), store.get("model_provider", "second")),
                         (FAKE + "-first", FAKE + "-second"))
        self.assertEqual(self.leftovers(), [])


def _key_file_body(path: str, keys: dict[str, str]) -> bytes:
    """What the store writes for ``keys`` (made by the store itself under another name, then renamed)."""
    folder = os.path.dirname(path)
    other = os.path.join(folder, "made-by-the-store.json")
    store = KeyStore(keyring=None, key_file=other)
    for name, value in keys.items():
        store.put("model_provider", name, value)
    os.rename(other, path)
    with open(path, "rb") as fh:
        return fh.read()


class ReservedTempNameTests(_Case):
    """DeepSeek's third read F-1, Codex's fourth read R4-1: a key file named like a temp file was swept as a
    leftover before it was read, so its other keys were lost; and another key file's save in the folder deleted
    it. Now such a name is refused (key_file_not_configured) before anything is opened, created or swept."""

    TEMP_NAMED = ".keys-1234-0123456789abcdef"

    def test_a_key_file_named_like_a_temp_file_is_refused_before_anything_is_created(self) -> None:
        path = os.path.join(self.leaf, self.TEMP_NAMED)
        store = KeyStore(keyring=None, key_file=path)
        for call in ((store.put, "model_provider", "one", FAKE), (store.put, "model_provider", "two", FAKE + "-2"),
                     (store.get, "model_provider", "one"), (store.delete, "model_provider", "one")):
            exc = self.refused("key_file_not_configured", *call)
            self.assertIn("a temp file (.keys-<digits>-<16 hex digits>)", exc.detail)
        self.assertFalse(os.path.lexists(self.leaf))  # not even the key folder or the lock

    def test_an_existing_key_file_named_like_a_temp_file_keeps_its_bytes(self) -> None:
        os.mkdir(self.leaf, 0o700)
        path = os.path.join(self.leaf, self.TEMP_NAMED)
        before = _key_file_body(path, {"one": FAKE, "two": FAKE + "-2"})
        listing = sorted(os.listdir(self.leaf))
        ring = Ring()
        refusing = [KeyStore(keyring=None, key_file=path),
                    KeyStore(keyring=ring, key_file=path, keychain=A, keychain_names=_KEYCHAIN_NAMES)]
        self.refused("key_file_not_configured", refusing[0].put, "model_provider", "three", FAKE)
        self.refused("key_file_not_configured", refusing[0].get, "model_provider", "one")
        self.refused("key_file_not_configured", refusing[0].delete, "model_provider", "one")
        for kind in ("broker", "venue"):
            self.refused("key_file_not_configured", refusing[1].put, kind, "acct", FAKE)
            self.refused("key_file_not_configured", refusing[1].get, kind, "acct")
            self.refused("key_file_not_configured", refusing[1].delete, kind, "acct")
        self.refused("key_file_not_configured", refusing[1].accept_keychain, A)
        self.assertEqual(ring.calls, [])
        # A model-provider key on the keychain host uses no file, so it is saved; its cleanup sweep skips a
        # reserved key file name, so the file is not taken for a leftover (Codex R4-2 with R4-1).
        self.assertEqual(refusing[1].put("model_provider", "acct", FAKE), "keyring")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual((sorted(os.listdir(self.leaf)), ring.calls), (listing, [("set", "model_provider:acct")]))
        for status in (KeychainStatus(True, A, "ok"), KeychainStatus(False, "", "no_keychain_backend")):
            with self.subTest(keychain=status.available):
                result = doctor(status, path)
                self.assertEqual(result["level"], "fail")
                self.assertIn("key_file_not_configured", doctor_line(result))

    def test_the_command_fails_a_key_file_named_like_a_temp_file(self) -> None:
        from cockpit.keys import __main__ as cli
        from cockpit.keys.tests.test_b24_keychain_record import _Out

        os.mkdir(self.leaf, 0o700)
        path = os.path.join(self.leaf, self.TEMP_NAMED)
        before = _key_file_body(path, {"one": FAKE})  # a file already sitting under that name
        for status in (KeychainStatus(True, A, "ok"), KeychainStatus(False, "", "no_keychain_backend")):
            with self.subTest(keychain=status.available):
                out = _Out(False)
                with mock.patch.object(cli, "detect_keychain", lambda: status), \
                        contextlib.redirect_stdout(out):
                    self.assertEqual(cli.main(["doctor", "--key-file", path]), 1)
                self.assertIn("key_file_not_configured", out.getvalue())
                with open(path, "rb") as fh:
                    self.assertEqual(fh.read(), before)

    def test_two_key_files_in_one_folder_never_sweep_each_other(self) -> None:
        # Codex R4-1: a second key-file configuration sharing the folder. Each save sweeps only leftovers.
        first = KeyStore(keyring=None, key_file=os.path.join(self.leaf, "first.json"))
        second = KeyStore(keyring=None, key_file=os.path.join(self.leaf, "second.json"))
        first.put("model_provider", "a", FAKE + "-a")
        second.put("model_provider", "b", FAKE + "-b")
        fd = os.open(os.path.join(self.leaf, ".keys-77-00000000000000aa"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        first.put("model_provider", "c", FAKE + "-c")
        second.put("model_provider", "d", FAKE + "-d")
        self.assertEqual(sorted(os.listdir(self.leaf)), [".keys.lock", "first.json", "second.json"])
        self.assertEqual([first.get("model_provider", k) for k in ("a", "c")], [FAKE + "-a", FAKE + "-c"])
        self.assertEqual([second.get("model_provider", k) for k in ("b", "d")], [FAKE + "-b", FAKE + "-d"])


class SweepScopeTests(_Case):
    def plant(self) -> str:
        os.makedirs(self.leaf, 0o700, exist_ok=True)
        name = ".keys-4242-0123456789abcdef"
        fd = os.open(os.path.join(self.leaf, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        return name

    def test_an_unlistable_folder_is_not_swept_and_the_save_goes_on(self) -> None:
        # DeepSeek's third read F-3: best effort; a listing that fails leaves the leftover and the save completes.
        left = self.plant()
        store = KeyStore(keyring=None, key_file=self.path)
        with mock.patch("cockpit.keys.store.os.listdir", side_effect=PermissionError(errno.EACCES, "EACCES")):
            self.assertEqual(store.put("model_provider", "acct", FAKE), "owner_only_file")
        self.assertIn(left, os.listdir(self.leaf))
        self.assertEqual(store.get("model_provider", "acct"), FAKE)

    def test_an_entry_that_cannot_be_looked_at_or_removed_stays_and_the_save_goes_on(self) -> None:
        # Codex's fourth read R4-3: an lstat or unlink error is ignored for that entry.
        store = KeyStore(keyring=None, key_file=self.path)
        real_lstat, real_unlink = os.lstat, os.unlink
        for failing in ("lstat", "unlink"):
            with self.subTest(failing=failing):
                left = self.plant()

                def lstat(name, *args, **kwargs):  # type: ignore[no-untyped-def]
                    if failing == "lstat" and name == left:
                        raise PermissionError(errno.EACCES, "EACCES")
                    return real_lstat(name, *args, **kwargs)

                def unlink(name, *args, **kwargs):  # type: ignore[no-untyped-def]
                    if failing == "unlink" and name == left:
                        raise PermissionError(errno.EACCES, "EACCES")
                    return real_unlink(name, *args, **kwargs)

                with mock.patch("cockpit.keys.store.os.lstat", lstat), mock.patch("cockpit.keys.store.os.unlink", unlink):
                    self.assertEqual(store.put("model_provider", failing, FAKE), "owner_only_file")
                self.assertIn(left, os.listdir(self.leaf))
                self.assertEqual(store.get("model_provider", failing), FAKE)
                os.unlink(os.path.join(self.leaf, left))


class ModelSaveOnAKeychainHostTests(_Case):
    """B38 for a model-provider save on a keychain host (Lucas: "the next save or acceptance removes leftover temp
    files"; Codex's fourth read R4-2, DeepSeek's third read F-2). That save uses no file, so its sweep is cleanup
    only: it never creates the folder or the lock, never waits for the lock, never refuses or fails the save, and
    reads or writes no record."""

    LEFT = ".keys-4242-0123456789abcdef"

    def folder(self, mode: int = 0o700, lock: bool = True) -> None:
        os.mkdir(self.leaf, mode)
        os.chmod(self.leaf, mode)
        if lock:
            os.close(os.open(os.path.join(self.leaf, ".keys.lock"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        os.close(os.open(os.path.join(self.leaf, self.LEFT), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))

    def test_it_sweeps_before_the_keychain_write_and_writes_no_record(self) -> None:
        self.folder()
        seen: list[bool] = []
        leaf = self.leaf

        class Watching(Ring):
            def set(inner, service: str, name: str, secret: str) -> None:  # noqa: N805
                seen.append(os.path.lexists(os.path.join(leaf, ModelSaveOnAKeychainHostTests.LEFT)))
                super().set(service, name, secret)

        ring = Watching()
        self.assertEqual(self.store(ring).put("model_provider", "acct", FAKE), "keyring")
        self.assertEqual(seen, [False])  # swept before the key went into the keychain
        self.assertEqual(sorted(os.listdir(self.leaf)), [".keys.lock"])  # no record written for a model key
        self.assertEqual(ring.calls, [("set", "model_provider:acct")])  # and no marker asked for

    def test_with_no_key_folder_nothing_is_created(self) -> None:
        ring = Ring()
        self.assertEqual(self.store(ring).put("model_provider", "acct", FAKE), "keyring")
        self.assertFalse(os.path.lexists(self.leaf))

    def test_with_no_lock_file_nothing_is_created_or_swept(self) -> None:
        self.folder(lock=False)
        self.assertEqual(self.store(Ring()).put("model_provider", "acct", FAKE), "keyring")
        self.assertEqual(os.listdir(self.leaf), [self.LEFT])

    def test_an_unsafe_folder_is_not_swept_and_the_save_succeeds(self) -> None:
        self.folder(mode=0o755)
        ring = Ring()
        self.assertEqual(self.store(ring).put("model_provider", "acct", FAKE), "keyring")
        self.assertIn(self.LEFT, os.listdir(self.leaf))
        self.assertEqual(stat.S_IMODE(os.lstat(self.leaf).st_mode), 0o755)  # never repaired
        self.assertEqual(ring.data, {("csl-cockpit", "model_provider:acct"): FAKE})

    def test_a_loose_lock_file_is_not_swept_under_and_the_save_succeeds(self) -> None:
        self.folder()
        os.chmod(os.path.join(self.leaf, ".keys.lock"), 0o644)
        self.assertEqual(self.store(Ring()).put("model_provider", "acct", FAKE), "keyring")
        self.assertIn(self.LEFT, os.listdir(self.leaf))

    def test_a_lock_held_by_another_writer_does_not_make_the_save_wait(self) -> None:
        self.folder()
        holder = os.open(os.path.join(self.leaf, ".keys.lock"), os.O_RDWR)
        done: list[str] = []
        worker = threading.Thread(daemon=True, target=lambda: done.append(self.store(Ring()).put("model_provider", "acct", FAKE)))
        try:
            store_mod.fcntl.flock(holder, store_mod.fcntl.LOCK_EX)  # type: ignore[union-attr]
            worker.start()
            worker.join(10)
            waited = worker.is_alive()
        finally:
            os.close(holder)  # lets go of the lock, so a save that did wait can finish
            worker.join(30)
        self.assertFalse(waited, "the model save waited for the folder's lock")
        self.assertEqual(done, ["keyring"])
        self.assertIn(self.LEFT, os.listdir(self.leaf))  # not swept this time

    def test_a_host_without_owner_only_files_is_left_untouched(self) -> None:
        self.folder()
        with mock.patch.object(store_mod, "_owner_only_supported", lambda: False):
            self.assertEqual(self.store(Ring()).put("model_provider", "acct", FAKE), "keyring")
        self.assertEqual(sorted(os.listdir(self.leaf)), sorted([".keys.lock", self.LEFT]))

    def test_an_error_on_the_way_means_not_swept_and_the_save_succeeds(self) -> None:
        self.folder()
        for failure in (OSError(errno.EIO, "EIO"), store_mod.KeyRefused("key_dir_not_owner_only", "0o755")):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch.object(store_mod, "_open_owned_dir", side_effect=failure):
                self.assertEqual(self.store(Ring()).put("model_provider", "acct", FAKE), "keyring")
            self.assertIn(self.LEFT, os.listdir(self.leaf))
        with mock.patch.object(store_mod, "_open_owned_dir", side_effect=KeyboardInterrupt()), \
                self.assertRaises(KeyboardInterrupt):  # an interrupt is not swallowed
            self.store(Ring()).put("model_provider", "acct", FAKE)
