"""B29: a pipe, folder, socket or device at the key file, the lock or the keychain record is refused, unopened.

#319's key file reader opened the key file with ``os.open`` (no ``O_NONBLOCK``) and then ``os.fdopen``: a
named pipe at the path made every read wait until something wrote to it, and a folder there raised
``IsADirectoryError`` instead of a refusal. Lucas, Nov 13, 2026, asked "Fix it in #330 (about two lines)?":
"Fix it in #330 (Recommended)" (decision sheet B29, relayed by the Liaison). Now the store decides from
``lstat`` before it opens an entry it did not just create, so anything that is not a regular file is
refused without being opened (or an open being tried); the open itself is ``O_NOFOLLOW|O_NONBLOCK``, so an
entry swapped in after the check cannot make it wait, and ``fstat`` of the open file must show the same
regular file. The refusal is the one the store already gave for an unusable entry: ``key_file_not_owner_only``
for the key file and the lock, ``keychain_record_not_owner_only`` for the record.

A device node cannot be made without privileges, so the device case is a regular file that every ``stat``
call reports as a character device; the lane's root run also checks a real one (``/dev/null`` bound over
the path). Every case runs in a thread with a time limit, so a run that waits on a pipe ends as a failure.
Temp folders and made-up keys only.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
import unittest
from collections.abc import Callable
from unittest import mock

from cockpit.keys import KeyRefused, KeyStore
from cockpit.keys.detect import _KEYCHAIN_NAMES
from cockpit.keys.store import key_file_state
from cockpit.keys.tests.fakes import FAKE, RECORDS

POSIX = os.name == "posix" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "mkfifo") and hasattr(os, "mknod")
KINDS = ("pipe", "folder", "socket", "device")
_REAL_OPEN = os.open


class _Ring:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, service: str, name: str) -> str | None:
        return self.data.get((service, name))

    def set(self, service: str, name: str, secret: str) -> None:
        self.data[(service, name)] = secret

    def delete(self, service: str, name: str) -> None:
        self.data.pop((service, name), None)


@unittest.skipUnless(POSIX, "owner-only key file requires POSIX")
class NotARegularFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="cockpit-b29-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.leaf = os.path.join(self.root, "keys")
        self.path = os.path.join(self.leaf, "model-keys.json")
        self.tried: list[str] = []  # every entry the store opened or tried to open, other than to create it

        def spy(path, flags, mode=0o777, *, dir_fd=None):  # type: ignore[no-untyped-def]
            if not (flags & os.O_CREAT and flags & os.O_EXCL):
                self.tried.append(os.path.basename(os.fspath(path)))
            return _REAL_OPEN(path, flags, mode, dir_fd=dir_fd)

        patcher = mock.patch.object(os, "open", spy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fresh(self) -> None:
        shutil.rmtree(self.leaf, True)
        os.mkdir(self.leaf, 0o700)
        self.tried.clear()

    def place(self, kind: str, name: str) -> str:
        path = os.path.join(self.leaf, name)
        if kind == "pipe":
            os.mkfifo(path, 0o600)
        elif kind == "folder":
            os.mkdir(path, 0o700)
        elif kind == "socket":  # a socket node, made without the socket module (the package imports none)
            try:
                os.mknod(path, stat.S_IFSOCK | 0o600)
            except PermissionError:
                self.skipTest("this system makes socket nodes only for a privileged user")
        else:
            os.close(_REAL_OPEN(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            self.as_device(path)
        self.tried.clear()
        return path

    def as_device(self, path: str) -> None:
        """Every stat call reports this file as a character device (one cannot be made unprivileged)."""
        inode = os.lstat(path).st_ino

        def seen(read: Callable[..., os.stat_result]) -> Callable[..., os.stat_result]:
            def as_a_device(*args: object, **kwargs: object) -> os.stat_result:
                st = read(*args, **kwargs)
                if st.st_ino != inode:
                    return st
                kind, (fields, extra) = st.__reduce__()
                return kind((stat.S_IFCHR | 0o600, *fields[1:]), extra)
            return as_a_device

        for name in ("lstat", "fstat", "stat"):
            patcher = mock.patch.object(os, name, seen(getattr(os, name)))
            patcher.start()
            self.addCleanup(patcher.stop)

    def outcome(self, fn: Callable[..., object], *args: object, pipe: str | None = None) -> tuple:
        """What ``fn(*args)`` did within 10 s: ("refused", cause), ("returned", value), ("raised", type) or
        ("waiting",). A read left waiting on ``pipe`` is let go (a writer opens and closes it) so it ends."""
        out: list[tuple] = []

        def run() -> None:
            try:
                out.append(("returned", fn(*args)))
            except KeyRefused as exc:
                out.append(("refused", exc.cause))
            except Exception as exc:  # noqa: BLE001 - the test reports whatever happens
                out.append(("raised", type(exc).__name__))

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(10)
        if out:
            return out[0]
        if pipe is not None:
            try:
                os.close(_REAL_OPEN(pipe, os.O_WRONLY | os.O_NONBLOCK))
            except OSError:
                pass
            thread.join(5)
        return ("waiting",)

    def assert_left_as_it_was(self, path: str, kind: str) -> None:
        mode = os.lstat(path).st_mode
        expected = {"pipe": stat.S_ISFIFO, "folder": stat.S_ISDIR, "socket": stat.S_ISSOCK, "device": stat.S_ISCHR}
        self.assertTrue(expected[kind](mode), kind)

    def test_anything_but_a_regular_file_at_the_key_file_path_is_refused_unopened(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.fresh()
                path = self.place(kind, "model-keys.json")
                store = KeyStore(keyring=None, key_file=self.path)
                for operation, args in ((store.get, ("model_provider", "openai")),
                                        (store.put, ("model_provider", "openai", FAKE)),
                                        (store.delete, ("model_provider", "openai"))):
                    self.assertEqual(self.outcome(operation, *args, pipe=path), ("refused", "key_file_not_owner_only"),
                                     operation.__name__)
                self.assertNotIn("model-keys.json", self.tried)
                self.assertTrue(key_file_state(self.path).startswith("file_not_owner_only"))  # the doctor: lstat only
                self.assert_left_as_it_was(path, kind)

    def test_anything_but_a_regular_file_at_the_lock_path_is_refused_unopened(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.fresh()
                path = self.place(kind, ".keys.lock")
                store = KeyStore(keyring=None, key_file=self.path)
                for operation, args in ((store.get, ("model_provider", "openai")),
                                        (store.put, ("model_provider", "openai", FAKE)),
                                        (store.delete, ("model_provider", "openai"))):
                    self.assertEqual(self.outcome(operation, *args, pipe=path), ("refused", "key_file_not_owner_only"),
                                     operation.__name__)
                self.assertNotIn(".keys.lock", self.tried)
                self.assertFalse(os.path.lexists(self.path))  # no key written
                self.assert_left_as_it_was(path, kind)

    @unittest.skipUnless(RECORDS, "no keychain record where this module has no owner-only file (Windows)")
    def test_anything_but_a_regular_file_at_the_record_path_is_refused_unopened(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.fresh()
                path = self.place(kind, ".keychain.json")
                ring = _Ring()
                store = KeyStore(keyring=ring, key_file=self.path, keychain=_KEYCHAIN_NAMES[0],
                                 keychain_names=_KEYCHAIN_NAMES)
                for operation, args in ((store.get, ("broker", "acct")), (store.put, ("broker", "acct", FAKE)),
                                        (store.delete, ("broker", "acct"))):
                    self.assertEqual(self.outcome(operation, *args, pipe=path),
                                     ("refused", "keychain_record_not_owner_only"), operation.__name__)
                self.assertNotIn(".keychain.json", self.tried)
                self.assertEqual(ring.data, {})
                self.assert_left_as_it_was(path, kind)

    @unittest.skipUnless(getattr(os, "mkfifo", None) in getattr(os, "supports_dir_fd", set()), "needs mkfifo with dir_fd")
    def test_a_pipe_swapped_in_after_the_check_cannot_make_a_read_wait(self) -> None:
        # The gap between deciding and opening: the key file is a regular file when the store looks, and a
        # pipe when it opens. The open cannot wait, and the open file is checked again.
        os.mkdir(self.leaf, 0o700)
        KeyStore(keyring=None, key_file=self.path).put("model_provider", "openai", FAKE)
        swapped: list[str] = []

        def swap_then_open(path, flags, mode=0o777, *, dir_fd=None):  # type: ignore[no-untyped-def]
            if os.fspath(path) == "model-keys.json" and not flags & os.O_CREAT and not swapped:
                os.unlink(path, dir_fd=dir_fd)
                os.mkfifo(path, 0o600, dir_fd=dir_fd)
                swapped.append("pipe")
            return _REAL_OPEN(path, flags, mode, dir_fd=dir_fd)

        store = KeyStore(keyring=None, key_file=self.path)
        with mock.patch.object(os, "open", swap_then_open):
            result = self.outcome(store.get, "model_provider", "openai", pipe=self.path)
        self.assertEqual(swapped, ["pipe"])
        self.assertEqual(result, ("refused", "key_file_not_owner_only"))


if __name__ == "__main__":
    unittest.main()
