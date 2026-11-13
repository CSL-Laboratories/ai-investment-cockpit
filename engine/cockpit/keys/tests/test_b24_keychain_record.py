"""B24: Cockpit remembers which OS keychain holds broker and venue keys, and refuses when that changes (CSL-59).

Lucas, Nov 13, 2026, asked "today Cockpit does not remember which OS keychain it saved a broker or venue key
in. If a later run selects a different keychain, an old key can reappear, or a deleted one come back. What
should it do?": "Remember and refuse (Recommended)" (option B of #326's description; decision sheet B24,
relayed by the Liaison). The record is ``.keychain.json`` beside the key file; Cockpit also leaves one
non-secret marker in the recorded keychain. Every refusal path has its own test here.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/keys/tests -t engine -v``.
Temp folders, a dict for a keychain, made-up keys; no real keychain, provider, venue or network.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import stat
import tempfile
import threading
import unittest
from unittest import mock

from cockpit.keys import KEY_KINDS, KeyRefused, KeyStore, doctor, doctor_line, store_for_host
from cockpit.keys import __main__ as cli
from cockpit.keys.detect import _KEYCHAIN_NAMES, KeychainStatus
from cockpit.keys.store import RECORD_KINDS
from cockpit.keys.tests.fakes import (
    FAKE, MARKER, RECORDS, Keychains, key_file, loader, recorder, requires_keyring, with_marker,
)

A, B = "SecretService Keyring", "kwallet DBusKeyring"
RECORD = ".keychain.json"


class Ring:
    """A keychain: a dict, and every call made to it."""

    def __init__(self, data: dict | None = None) -> None:
        self.data: dict = dict(data or {})
        self.calls: list[tuple[str, str]] = []

    def get(self, service: str, name: str) -> str | None:
        self.calls.append(("get", name))
        return self.data.get((service, name))

    def set(self, service: str, name: str, secret: str) -> None:
        self.calls.append(("set", name))
        self.data[(service, name)] = secret

    def delete(self, service: str, name: str) -> None:
        self.calls.append(("delete", name))
        self.data.pop((service, name), None)


@unittest.skipUnless(RECORDS, "no keychain record where this module has no owner-only file (Windows)")
class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="cockpit-b24-")
        self.addCleanup(self._remove)
        self.leaf = os.path.join(self.root, "csl-cockpit")
        self.path = os.path.join(self.leaf, "model-keys.json")
        self.record = os.path.join(self.leaf, RECORD)

    def _remove(self) -> None:
        for folder, dirs, _ in os.walk(self.root):
            for name in dirs:
                if not os.path.islink(os.path.join(folder, name)):
                    os.chmod(os.path.join(folder, name), 0o700)
        shutil.rmtree(self.root, True)

    def store(self, ring: Ring, keychain: str = A) -> KeyStore:
        return KeyStore(keyring=ring, key_file=self.path, keychain=keychain, keychain_names=_KEYCHAIN_NAMES)

    def saved(self, keychain: str = A) -> Ring:
        """A host whose first broker save in ``keychain`` happened: the record and the marker exist."""
        ring = Ring()
        self.store(ring, keychain).put("broker", "acct", FAKE)
        return ring

    def refused(self, cause: str, fn, *args) -> KeyRefused:  # type: ignore[no-untyped-def]
        with self.assertRaises(KeyRefused) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.cause, cause)
        self.assertNotIn(FAKE, str(ctx.exception))
        return ctx.exception

    def every_recorded_operation_refused(self, store: KeyStore, cause: str) -> list[KeyRefused]:
        out = []
        for kind in RECORD_KINDS:
            out.append(self.refused(cause, store.get, kind, "acct"))
            out.append(self.refused(cause, store.put, kind, "acct", FAKE + "-new"))
            out.append(self.refused(cause, store.delete, kind, "acct"))
        return out

    def write_record(self, body: bytes, mode: int = 0o600) -> None:
        os.makedirs(self.leaf, 0o700, exist_ok=True)
        fd = os.open(self.record, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        os.chmod(self.record, mode)

    def read_record(self) -> bytes:
        with open(self.record, "rb") as fh:
            return fh.read()


class RecordTests(_Case):
    def test_the_first_broker_or_venue_save_records_the_keychain_and_leaves_the_marker(self) -> None:
        for kind in RECORD_KINDS:
            with self.subTest(kind=kind):
                shutil.rmtree(self.leaf, True)
                ring = Ring()
                self.assertEqual(self.store(ring).put(kind, "acct", FAKE), "keyring")
                self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": A})
                self.assertNotIn(FAKE.encode(), self.read_record())  # names only, never a key
                self.assertEqual(stat.S_IMODE(os.lstat(self.record).st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(os.lstat(self.leaf).st_mode), 0o700)
                self.assertEqual(sorted(os.listdir(self.leaf)), [".keychain.json", ".keys.lock"])  # no temp file left
                self.assertEqual(ring.data, {MARKER: A, ("csl-cockpit", f"{kind}:acct"): FAKE})
                self.assertEqual(ring.calls, [("get", MARKER[1]), ("set", MARKER[1]), ("set", f"{kind}:acct")])

    def test_the_record_is_owner_only_at_any_umask(self) -> None:
        for value in (0o000, 0o022, 0o077):
            with self.subTest(umask=oct(value)):
                shutil.rmtree(self.leaf, True)
                old = os.umask(value)
                try:
                    self.saved()
                finally:
                    os.umask(old)
                self.assertEqual(stat.S_IMODE(os.lstat(self.record).st_mode), 0o600)

    def test_a_later_run_with_the_same_keychain_works_as_before(self) -> None:
        ring = self.saved()
        ring.calls.clear()
        store = self.store(ring)
        self.assertEqual(store.get("broker", "acct"), FAKE)
        store.put("venue", "acct", FAKE)
        store.delete("broker", "acct")
        self.assertEqual(ring.calls, [("get", "broker:acct"), ("set", "venue:acct"), ("delete", "broker:acct")])
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": A})

    def test_a_later_run_with_another_keychain_is_refused_every_broker_and_venue_operation(self) -> None:
        self.saved(A)
        before = self.read_record()
        other = Ring()
        for exc in self.every_recorded_operation_refused(self.store(other, B), "keychain_differs"):
            self.assertIn(f"saved in {A}, this run selected {B}", str(exc))
            self.assertIn(f"python -m cockpit.keys accept-keychain '{B}'", str(exc))
            self.assertIn(f"or make {A} available again", str(exc))
        self.assertEqual((other.calls, other.data), ([], {}))  # the other keychain is sent nothing
        self.assertEqual(self.read_record(), before)  # never re-recorded

    def test_model_provider_keys_are_neither_recorded_nor_refused(self) -> None:
        ring = Ring()
        store = self.store(ring)
        store.put("model_provider", "acct", FAKE)
        self.assertEqual(store.get("model_provider", "acct"), FAKE)
        store.delete("model_provider", "acct")
        self.assertFalse(os.path.lexists(self.leaf))  # nothing recorded, nothing created
        self.assertNotIn(MARKER, ring.data)
        self.saved(A)
        other = Ring()
        later = self.store(other, B)  # a different keychain: broker and venue refused, model keys not
        later.put("model_provider", "acct", FAKE)
        self.assertEqual(later.get("model_provider", "acct"), FAKE)
        later.delete("model_provider", "acct")
        self.assertEqual(other.calls, [("set", "model_provider:acct"), ("get", "model_provider:acct"),
                                       ("delete", "model_provider:acct")])

    def test_the_first_run_after_this_change_has_nothing_to_compare(self) -> None:
        # Keys saved before this rule: no record and no marker. Reads and deletes work as before and record
        # nothing (nothing is created); the next broker or venue save records the keychain selected then.
        ring = Ring({("csl-cockpit", "broker:old"): FAKE})
        store = self.store(ring)
        self.assertEqual(store.get("broker", "old"), FAKE)
        store.delete("venue", "old")
        self.assertFalse(os.path.lexists(self.leaf))
        self.assertNotIn(MARKER, ring.data)
        store.put("venue", "new", FAKE)
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": A})
        self.assertEqual(ring.data[MARKER], A)

    def test_a_record_deleted_while_the_keychain_holds_the_marker_is_refused_and_never_re_recorded(self) -> None:
        ring = self.saved()
        os.unlink(self.record)
        for exc in self.every_recorded_operation_refused(self.store(ring), "keychain_record_missing"):
            self.assertIn(f"accept-keychain '{A}'", str(exc))
        self.assertFalse(os.path.lexists(self.record))
        self.assertEqual(ring.data[("csl-cockpit", "broker:acct")], FAKE)  # untouched
        shutil.rmtree(self.leaf)  # the whole folder gone: the same (a refused save may make the empty folder)
        self.every_recorded_operation_refused(self.store(ring), "keychain_record_missing")
        self.assertFalse(os.path.lexists(self.record))

    def test_a_malformed_record_is_refused_and_left_as_it_is(self) -> None:
        bodies = {
            "empty": b"",
            "not JSON": b"{version: 1}",
            "not UTF-8": b"\xff\xfe",
            "a list": b"[]",
            "version true": json.dumps({"version": True, "keychain": A}).encode(),
            "version 1.0": json.dumps({"version": 1.0, "keychain": A}).encode(),
            "version 2": json.dumps({"version": 2, "keychain": A}).encode(),
            "no version": json.dumps({"keychain": A}).encode(),
            "no keychain": json.dumps({"version": 1}).encode(),
            "keychain not a name Cockpit knows": json.dumps({"version": 1, "keychain": "keyrings.alt.file PlaintextKeyring"}).encode(),
            "keychain with a line break": json.dumps({"version": 1, "keychain": A + "\n"}).encode(),
            "keychain not a string": json.dumps({"version": 1, "keychain": 1}).encode(),
            "an extra field": json.dumps({"version": 1, "keychain": A, "key": FAKE}).encode(),
            "previous equal to keychain": json.dumps({"version": 1, "keychain": A, "previous": A}).encode(),
            "previous unknown": json.dumps({"version": 1, "keychain": A, "previous": "x"}).encode(),
            "a field twice": b'{"version": 1, "keychain": "kwallet DBusKeyring", "keychain": "SecretService Keyring"}',
            "too large": json.dumps({"version": 1, "keychain": A}).encode() + b" " * 4096,
        }
        ring = Ring()
        for label, body in bodies.items():
            with self.subTest(label):
                self.write_record(body)
                for exc in self.every_recorded_operation_refused(self.store(ring), "keychain_record_malformed"):
                    self.assertNotIn("PlaintextKeyring", str(exc))  # nothing from the file is shown
                self.assertEqual(self.read_record(), body)
        self.assertEqual(ring.calls, [])

    def test_a_record_that_is_a_link_is_refused_and_not_followed(self) -> None:
        target = os.path.join(self.root, "elsewhere.json")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(json.dumps({"version": 1, "keychain": A}).encode())
        os.makedirs(self.leaf, 0o700)
        os.symlink(target, self.record)
        ring = Ring()
        for exc in self.every_recorded_operation_refused(self.store(ring), "keychain_record_not_owner_only"):
            self.assertIn("ELOOP", str(exc))
            self.assertIn("never repairs", str(exc))
        self.assertTrue(os.path.islink(self.record))
        self.assertEqual(ring.calls, [])

    def test_a_record_that_is_not_owner_only_is_refused_and_never_repaired(self) -> None:
        body = json.dumps({"version": 1, "keychain": A}).encode()
        for mode in (0o644, 0o640, 0o604, 0o660, 0o606, 0o610):
            with self.subTest(mode=oct(mode)):
                self.write_record(body, mode)
                self.every_recorded_operation_refused(self.store(Ring()), "keychain_record_not_owner_only")
                self.assertEqual(stat.S_IMODE(os.lstat(self.record).st_mode), mode)

    def test_a_record_that_is_not_a_regular_file_is_refused_without_waiting(self) -> None:
        os.mkdir(self.leaf, 0o700)
        os.mkdir(self.record, 0o700)  # a folder in its place
        self.every_recorded_operation_refused(self.store(Ring()), "keychain_record_not_owner_only")
        os.rmdir(self.record)
        os.mkfifo(self.record, 0o600)  # a FIFO would stall a plain open until someone writes to it
        done: list[str] = []

        def read() -> None:
            try:
                self.store(Ring()).get("broker", "acct")
            except KeyRefused as exc:
                done.append(exc.cause)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(10)
        self.assertEqual(done, ["keychain_record_not_owner_only"])

    def test_a_record_owned_by_another_user_is_refused(self) -> None:
        self.saved()
        real_fstat, inode = os.fstat, os.lstat(self.record).st_ino

        def fstat(fd: int) -> os.stat_result:  # the record, and only it, reads as another user's
            st = real_fstat(fd)
            if st.st_ino != inode:
                return st
            kind, (fields, extra) = st.__reduce__()
            return kind((*fields[:4], st.st_uid + 1, *fields[5:]), extra)

        with mock.patch.object(os, "fstat", fstat):
            self.every_recorded_operation_refused(self.store(Ring()), "keychain_record_not_owner_only")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can read a mode-000 file")
    def test_an_unreadable_record_is_refused(self) -> None:
        self.saved()
        os.chmod(self.record, 0o000)
        for exc in self.every_recorded_operation_refused(self.store(Ring()), "keychain_record_unreadable"):
            self.assertIn("EACCES", str(exc))
        self.assertEqual(stat.S_IMODE(os.lstat(self.record).st_mode), 0o000)

    def test_a_key_folder_that_is_not_owner_only_refuses_broker_and_venue_keys(self) -> None:
        self.saved()
        for mode in (0o750, 0o705, 0o777):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.leaf, mode)
                self.every_recorded_operation_refused(self.store(Ring()), "key_dir_not_owner_only")
                self.assertEqual(stat.S_IMODE(os.lstat(self.leaf).st_mode), mode)
        os.chmod(self.leaf, 0o700)

    def test_a_record_that_cannot_be_written_stores_no_key(self) -> None:
        os.chmod(self.root, 0o500)  # the key folder cannot be made
        self.addCleanup(os.chmod, self.root, 0o700)
        if os.access(self.root, os.W_OK):
            self.skipTest("this user can write to a read-only folder (root)")
        ring = Ring()
        for kind in RECORD_KINDS:
            self.refused("key_dir_not_owner_only", self.store(ring).put, kind, "acct", FAKE)
        self.assertEqual(ring.data, {})

    def test_a_failed_write_leaves_the_old_record_and_no_temporary_file(self) -> None:
        self.saved(A)
        before = self.read_record()
        with mock.patch("cockpit.keys.store.os.rename", side_effect=OSError(5, "EIO")):
            with self.assertRaises(OSError):
                self.store(Ring({MARKER: B}), B).accept_keychain(B)
        self.assertEqual(self.read_record(), before)
        self.assertEqual(sorted(os.listdir(self.leaf)), [".keychain.json", ".keys.lock"])

    def test_a_key_file_named_like_the_record_or_the_lock_is_refused(self) -> None:
        for name in (".keychain.json", ".keys.lock"):
            with self.subTest(name=name):
                store = KeyStore(keyring=None, key_file=os.path.join(self.root, name))
                self.refused("key_file_not_configured", store.put, "model_provider", "acct", FAKE)

class NoRecordTests(unittest.TestCase):
    def test_no_record_is_kept_without_an_owner_only_file(self) -> None:
        # Windows: this module has no owner-only file there, and only the Windows vault can be selected. On
        # Windows this runs as it is; elsewhere the store's calls see ``os.name == "nt"``.
        root = tempfile.mkdtemp(prefix="cockpit-b24-")
        self.addCleanup(shutil.rmtree, root, True)
        leaf = os.path.join(root, "csl-cockpit")
        ring = Ring()
        store = KeyStore(keyring=ring, key_file=os.path.join(leaf, "model-keys.json"),
                         keychain="Windows WinVaultKeyring", keychain_names=_KEYCHAIN_NAMES)
        with mock.patch.object(os, "name", "nt") if RECORDS else contextlib.nullcontext():
            for kind in RECORD_KINDS:
                store.put(kind, "acct", FAKE)
                self.assertEqual(store.get(kind, "acct"), FAKE)
                store.delete(kind, "acct")
            with self.assertRaises(KeyRefused) as ctx:
                store.accept_keychain("Windows WinVaultKeyring")
            self.assertEqual(ctx.exception.cause, "keychain_record_unsupported")
        self.assertFalse(os.path.lexists(leaf))
        self.assertEqual(ring.data, {})


class AcceptTests(_Case):
    def test_accept_keychain_records_a_real_change_and_keeps_the_previous_one(self) -> None:
        self.saved(A)
        other = Ring()
        later = self.store(other, B)
        exc = self.refused("keychain_name_mismatch", later.accept_keychain, A)
        self.assertIn(f"this run selected {B}", str(exc))
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": A})
        self.assertEqual(later.accept_keychain(B), A)
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": B, "previous": A})
        self.assertEqual(other.data, {MARKER: B})
        later.put("broker", "acct", FAKE)
        self.assertEqual(later.get("broker", "acct"), FAKE)
        self.refused("keychain_differs", self.store(Ring(), A).get, "broker", "acct")  # and the old one is refused now

    def test_accept_keychain_replaces_a_malformed_record_but_never_a_link_or_a_loose_one(self) -> None:
        self.write_record(b"not json")
        self.assertEqual(self.store(Ring()).accept_keychain(A), "")
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": A})
        os.chmod(self.record, 0o644)
        self.refused("keychain_record_not_owner_only", self.store(Ring()).accept_keychain, A)
        self.assertEqual(stat.S_IMODE(os.lstat(self.record).st_mode), 0o644)
        os.unlink(self.record)
        os.symlink(os.path.join(self.root, "nowhere"), self.record)
        self.refused("keychain_record_not_owner_only", self.store(Ring()).accept_keychain, A)
        self.assertTrue(os.path.islink(self.record))

    def test_accept_keychain_after_a_deleted_record(self) -> None:
        ring = self.saved(A)
        os.unlink(self.record)
        self.refused("keychain_record_missing", self.store(ring).get, "broker", "acct")
        self.assertEqual(self.store(ring).accept_keychain(A), "")
        self.assertEqual(self.store(ring).get("broker", "acct"), FAKE)

    def test_the_command_runs_only_at_a_terminal(self) -> None:
        self.saved(A)
        before = self.read_record()
        for stdin, stdout in ((False, True), (True, False), (False, False)):
            with self.subTest(stdin=stdin, stdout=stdout), self.cli(B, stdin, stdout) as (rc, out):
                pass
            self.assertEqual(rc[0], 1)
            self.assertIn("only at an interactive terminal", out.getvalue())
        self.assertEqual(self.read_record(), before)

    def test_the_command_at_a_terminal_records_the_change(self) -> None:
        self.saved(A)
        with self.cli(B, True, True) as (rc, out):
            pass
        self.assertEqual((rc[0], out.getvalue().strip()), (0, f"keys: ok - broker and venue keys are kept in {B} (switched from {A})"))
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": B, "previous": A})
        with self.cli(A, True, True) as (rc, out):  # not the keychain this run selected
            pass
        self.assertEqual(rc[0], 1)
        self.assertIn("keychain_name_mismatch", out.getvalue())

    @contextlib.contextmanager
    def cli(self, name: str, stdin_tty: bool, stdout_tty: bool):  # type: ignore[no-untyped-def]
        out, rc = _Out(stdout_tty), []
        store = self.store(Ring(), B)
        status = KeychainStatus(True, B, "ok")
        with mock.patch.object(cli, "store_for_host", lambda path: (store, status)), \
                mock.patch.object(cli.sys, "stdin", _Out(stdin_tty)), contextlib.redirect_stdout(out):
            rc.append(cli.main(["accept-keychain", name, "--key-file", self.path]))
            yield rc, out


class _Out(io.StringIO):
    """A stream that says whether it is a terminal."""

    def __init__(self, tty: bool) -> None:
        super().__init__()
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


class RecordDoctorTests(_Case):
    def line(self, keychain: str = A) -> tuple[str, str]:
        result = doctor(KeychainStatus(True, keychain, "ok"), self.path)
        return result["level"], doctor_line(result)

    def test_no_record_and_a_matching_record_leave_the_line_as_it_was(self) -> None:
        plain = f"keys: ok - OS keychain ({A}); model-provider, broker and venue keys in the keychain"
        self.assertEqual(self.line(), ("ok", plain))
        self.assertFalse(os.path.lexists(self.leaf))  # the doctor creates nothing
        self.saved(A)
        self.assertEqual(self.line(), ("ok", plain))

    def test_a_different_keychain_fails_and_names_both_and_the_way_out(self) -> None:
        self.saved(A)
        self.assertEqual(self.line(B), ("fail", (
            f"keys: fail - OS keychain ({B}); model-provider keys in the keychain; broker and venue keys refused "
            f"(keychain_differs): saved in {A}, this run selected {B}; to keep broker and venue keys in {B}, run "
            f"at a terminal: python -m cockpit.keys accept-keychain '{B}'; or make {A} available again")))

    def test_a_broken_record_fails_with_its_reason(self) -> None:
        cases = {"keychain_record_malformed": (b"[]", 0o600), "keychain_record_not_owner_only": (b"{}", 0o644)}
        for cause, (body, mode) in cases.items():
            with self.subTest(cause):
                self.write_record(body, mode)
                level, line = self.line()
                self.assertEqual(level, "fail")
                self.assertIn(f"broker and venue keys refused ({cause})", line)
                self.assertIn(f"accept-keychain '{A}'", line)
        os.unlink(self.record)
        os.symlink(os.path.join(self.root, "nowhere"), self.record)
        self.assertIn("(keychain_record_not_owner_only): ELOOP; Cockpit never repairs .keychain.json", self.line()[1])
        os.unlink(self.record)
        os.chmod(self.leaf, 0o755)
        self.assertIn("broker and venue keys refused (key_dir_not_owner_only)", self.line()[1])
        os.chmod(self.leaf, 0o700)

    def test_an_accepted_switch_stays_visible(self) -> None:
        self.saved(A)
        self.store(Ring(), B).accept_keychain(B)
        self.assertEqual(self.line(B), ("ok", f"keys: ok - OS keychain ({B}); model-provider, broker and venue keys in "
                                              f"the keychain; switched from {A} with accept-keychain"))

    def test_the_command_exits_1_when_the_keychain_differs(self) -> None:
        self.saved(A)
        out = io.StringIO()
        with mock.patch.object(cli, "detect_keychain", lambda: KeychainStatus(True, B, "ok")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["doctor", "--key-file", self.path]), 1)
        self.assertIn("(keychain_differs)", out.getvalue())


@requires_keyring
@unittest.skipUnless(RECORDS, "no keychain record where this module has no owner-only file (Windows)")
class GenuineKeychainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.k = Keychains(self)

    def test_a_change_of_keychain_is_refused_for_broker_and_venue_keys(self) -> None:
        # The order is fixed, but which keychains answer can differ between runs (a daemon reachable on the
        # desktop and not over SSH). Before B24 a later run read whatever the newly selected keychain held.
        # Now the recorded keychain is compared by Cockpit's name for its class (identity, as in admission):
        # a run that selects another class is refused and sends it nothing; the recorded one answers again.
        for kind in RECORD_KINDS:
            with self.subTest(kind=kind):
                path = key_file(self)  # one host, three runs
                kwallet, ss = self.k.os("KWallet5"), self.k.os()
                plain = recorder()
                first, status = store_for_host(path, loader(self.k.chain([kwallet, plain])))
                first.put(kind, "fixture", FAKE + "-OLD")
                second, status = store_for_host(path, loader(self.k.chain([ss, plain])))
                self.assertEqual(status.backend, A)
                for operation in (lambda: second.get(kind, "fixture"), lambda: second.put(kind, "fixture", FAKE),
                                  lambda: second.delete(kind, "fixture")):
                    with self.assertRaises(KeyRefused) as ctx:
                        operation()
                    self.assertEqual(ctx.exception.cause, "keychain_differs")
                self.assertEqual((ss.calls, ss.data, plain.calls), ([], {}, []))
                third, status = store_for_host(path, loader(self.k.chain([kwallet, plain])))
                self.assertEqual(third.get(kind, "fixture"), FAKE + "-OLD")
                self.assertEqual(kwallet.data, with_marker({("csl-cockpit", f"{kind}:fixture"): FAKE + "-OLD"}, B))

    def test_every_kind_still_round_trips_on_a_fresh_host(self) -> None:
        store, status = store_for_host(key_file(self), loader(self.k.os()))
        for kind in KEY_KINDS:
            store.put(kind, "fixture", FAKE)
            self.assertEqual(store.get(kind, "fixture"), FAKE)


if __name__ == "__main__":
    unittest.main()
