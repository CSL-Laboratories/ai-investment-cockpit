"""B24 after the outside reads of #330: enrolment order, failures and retries, calls in flight, the doctor (CSL-59).

Codex (GPT-6), first read of #330 at 5c0b006a, F1: the first save wrote the record and only then the marker.
Now the marker comes first and the record second. Codex's second read (R1): a take-back of the marker could
remove it after the record was already published (an error from the folder flush comes after the rename),
and the marker's account is shared by every key folder on that keychain, so the marker is never taken back.
If the record was not published, the marker stays alone and the next broker or venue operation is refused
(keychain_record_missing) until accept-keychain records it; if it was, both are there and nothing more is
needed. R2: only content errors of a record or key file (bad UTF-8 or JSON, a field twice, too deep) are
"malformed"; anything else stops the operation, so accept-keychain never replaces a valid record after one.
Also: calls already in flight when an acceptance changes the record, reserved key file names on a keychain
host, and B37 (Lucas, Nov 13, 2026: "Note + opt-in check (Recommended)"): the plain doctor notes a missing
record without contacting the keychain; `doctor --check-keychain` looks for the marker when asked.
Temp folders, a dict for a keychain, made-up keys only.
"""

from __future__ import annotations

import contextlib
import errno
import json
import multiprocessing
import os
import stat
import threading
import unittest
from unittest import mock

from cockpit.keys import KeyStore, doctor, doctor_line
from cockpit.keys import __main__ as cli
from cockpit.keys import detect as detect_mod
from cockpit.keys import store as store_mod
from cockpit.keys.detect import _KEYCHAIN_NAMES, KeychainStatus
from cockpit.keys.tests.fakes import FAKE, MARKER, Keychains, loader, no_record_note, requires_keyring
from cockpit.keys.tests.test_b24_keychain_record import RECORD, A, B, Ring, _Case, _Out

C = _KEYCHAIN_NAMES[2]  # a third keychain name, for a record's "previous"


class FileRing(Ring):
    """A keychain kept in a JSON file, so a second process sees what the first one wrote."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.data = {tuple(k.split("\n", 1)): v for k, v in json.load(fh).items()}

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"\n".join(k): v for k, v in self.data.items()}, fh)

    def set(self, service: str, name: str, secret: str) -> None:
        super().set(service, name, secret)
        self._save()

    def delete(self, service: str, name: str) -> None:
        super().delete(service, name)
        self._save()


def _killed_between_the_two_writes(path: str, ring_file: str) -> None:
    """In a child process: the first broker save, killed (os._exit) when it is about to write the record."""
    store_mod._write_record = lambda *args, **kwargs: os._exit(17)  # type: ignore[assignment]
    KeyStore(keyring=FileRing(ring_file), key_file=path, keychain=A,
             keychain_names=_KEYCHAIN_NAMES).put("broker", "acct", FAKE)


class _Failing(Ring):
    """A keychain whose writes to the marker fail."""

    def set(self, service: str, name: str, secret: str) -> None:
        if (service, name) == MARKER:
            raise RuntimeError("keychain locked: " + FAKE)
        super().set(service, name, secret)


class _FailingRead(Ring):
    """A keychain whose reads of the marker fail, as a locked one or a dismissed prompt would."""

    def get(self, service: str, name: str) -> str | None:
        self.calls.append(("get", name))
        if (service, name) == MARKER:
            raise RuntimeError("collection locked: " + FAKE)
        return self.data.get((service, name))


@contextlib.contextmanager
def _flush_fails_after_publication():  # type: ignore[no-untyped-def]
    """The folder flush after the record's rename fails: the record is already published when the error comes."""
    real = os.fsync

    def fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "EIO")
        real(fd)

    with mock.patch("cockpit.keys.store.os.fsync", fsync):
        yield


def _not_published():  # type: ignore[no-untyped-def]
    """The record's rename fails: it is not published."""
    return mock.patch("cockpit.keys.store.os.rename", side_effect=OSError(errno.EIO, "EIO"))


class EnrolmentTests(_Case):
    def test_the_marker_is_written_before_the_record(self) -> None:
        seen: list[bool] = []

        class Watching(Ring):
            def set(inner, service: str, name: str, secret: str) -> None:  # noqa: N805
                if (service, name) == MARKER:
                    seen.append(os.path.lexists(self.record))
                super().set(service, name, secret)

        self.store(Watching()).put("broker", "acct", FAKE)
        self.assertEqual(seen, [False])  # no record yet when the marker is written
        self.assertTrue(os.path.lexists(self.record))

    def test_a_first_save_whose_marker_write_fails_writes_neither_and_can_be_retried(self) -> None:
        ring = _Failing()
        with self.assertRaises(RuntimeError):
            self.store(ring).put("broker", "acct", FAKE)
        self.assertFalse(os.path.lexists(self.record))
        self.assertEqual(ring.data, {})
        ring = Ring()  # the keychain answers again: nothing was written, so a plain retry works
        self.store(ring).put("broker", "acct", FAKE)
        self.assertEqual(ring.data, {MARKER: A, ("csl-cockpit", "broker:acct"): FAKE})

    def test_a_first_save_whose_record_was_not_published_keeps_the_marker_and_needs_acceptance(self) -> None:
        ring = Ring()
        with _not_published(), self.assertRaises(OSError):
            self.store(ring).put("venue", "acct", FAKE)
        self.assertFalse(os.path.lexists(self.record))
        self.assertEqual(ring.data, {MARKER: A})  # the marker stays (never taken back); no key stored
        for exc in self.every_recorded_operation_refused(self.store(ring), "keychain_record_missing"):
            self.assertIn(f"accept-keychain '{A}'", str(exc))
        self.assertEqual(self.store(ring).accept_keychain(A), "")
        self.store(ring).put("venue", "acct", FAKE)
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": A})

    def test_a_first_save_whose_error_came_after_publication_keeps_both_and_needs_nothing(self) -> None:
        ring = Ring()
        with _flush_fails_after_publication(), self.assertRaises(OSError):
            self.store(ring).put("venue", "acct", FAKE)
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": A})  # published
        self.assertEqual(ring.data, {MARKER: A})  # the save itself failed, so no key
        self.store(ring).put("venue", "acct", FAKE)  # record and marker match: a plain retry works
        self.assertEqual(self.store(ring).get("venue", "acct"), FAKE)

    def test_a_first_save_killed_between_its_two_writes_is_refused_until_accepted(self) -> None:
        ring_file = os.path.join(self.root, "ring.json")
        child = multiprocessing.get_context("spawn").Process(target=_killed_between_the_two_writes,
                                                            args=(self.path, ring_file))
        child.start()
        try:
            child.join(60)
        finally:
            if child.is_alive():  # a regression that hangs it must not leave it running
                child.terminate()
                child.join(10)
        self.assertEqual(child.exitcode, 17)
        ring = FileRing(ring_file)
        self.assertEqual(ring.data, {MARKER: A})  # the marker, no key
        self.assertFalse(os.path.lexists(self.record))
        for exc in self.every_recorded_operation_refused(self.store(ring), "keychain_record_missing"):
            self.assertIn("a first save was cut off between its two writes", str(exc))
            self.assertIn(f"accept-keychain '{A}'", str(exc))
        self.assertEqual(self.store(ring).accept_keychain(A), "")
        self.store(ring).put("broker", "acct", FAKE)
        self.assertEqual(self.store(ring).get("broker", "acct"), FAKE)

    def test_an_acceptance_whose_marker_write_fails_changes_nothing(self) -> None:
        self.saved(A)
        before = self.read_record()
        with self.assertRaises(RuntimeError):
            self.store(_Failing(), B).accept_keychain(B)
        self.assertEqual(self.read_record(), before)

    def test_an_acceptance_whose_record_was_not_published_leaves_the_old_record_and_the_marker(self) -> None:
        self.saved(A)
        before = self.read_record()
        for earlier in ({}, {MARKER: B}):
            with self.subTest(earlier_marker=bool(earlier)):
                self.write_record(before)
                ring = Ring(earlier)
                with _not_published(), self.assertRaises(OSError):
                    self.store(ring, B).accept_keychain(B)
                self.assertEqual(self.read_record(), before)  # not published: the old record is still there
                self.assertEqual(ring.data, {MARKER: B})  # the marker stays either way
                self.refused("keychain_differs", self.store(ring, B).get, "broker", "acct")  # so B is refused
                self.assertEqual(self.store(ring, B).accept_keychain(B), A)  # until it is run again
                self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": B, "previous": A})

    def test_an_acceptance_whose_error_came_after_publication_has_already_switched(self) -> None:
        # An error does not mean the old record survived: here the new one was published first.
        self.saved(A)
        ring = Ring()
        with _flush_fails_after_publication(), self.assertRaises(OSError):
            self.store(ring, B).accept_keychain(B)
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": B, "previous": A})
        self.assertEqual(ring.data, {MARKER: B})
        self.assertEqual(self.store(ring, B).accept_keychain(B), A)  # run again: the history is kept
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": B, "previous": A})

    def test_the_command_reports_an_unfinished_acceptance(self) -> None:
        self.saved(A)
        before = self.read_record()
        out = _Out(True)
        store = self.store(_Failing(), B)
        with mock.patch.object(cli, "store_for_host", lambda path: (store, KeychainStatus(True, B, "ok"))), \
                mock.patch.object(cli.sys, "stdin", _Out(True)), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["accept-keychain", B, "--key-file", self.path]), 1)
        self.assertEqual(out.getvalue().strip(), "keys: failed - accept-keychain did not finish (RuntimeError); nothing is "
                                                 "confirmed: run it again, then check python -m cockpit.keys doctor")
        self.assertNotIn(FAKE, out.getvalue())  # the exception's message is never shown
        self.assertEqual(self.read_record(), before)

    def test_a_matching_record_without_a_marker_stays_valid_and_the_next_save_adds_it(self) -> None:
        # Left by a candidate build of #330, by a marker removed by hand, or by a marker write that never took
        # effect; it is honoured, and completed by the next save.
        self.write_record(json.dumps({"version": 1, "keychain": A}).encode())
        ring = Ring({("csl-cockpit", "broker:acct"): FAKE})
        self.assertEqual(self.store(ring).get("broker", "acct"), FAKE)
        self.assertEqual(ring.calls, [("get", "broker:acct")])  # a read does not look for the marker
        self.refused("keychain_differs", self.store(Ring(), B).get, "broker", "acct")  # still names its keychain
        self.store(ring).put("venue", "acct", FAKE)
        self.assertEqual(ring.data[MARKER], A)
        os.unlink(self.record)
        self.every_recorded_operation_refused(self.store(ring), "keychain_record_missing")

    def test_a_key_file_named_like_the_record_or_the_lock_is_refused_on_a_keychain_host(self) -> None:
        os.mkdir(self.leaf, 0o700)
        for name in (RECORD, ".keys.lock"):
            with self.subTest(name=name):
                ring = Ring()
                store = KeyStore(keyring=ring, key_file=os.path.join(self.leaf, name), keychain=A,
                                 keychain_names=_KEYCHAIN_NAMES)
                for kind in ("broker", "venue"):
                    self.refused("key_file_not_configured", store.get, kind, "acct")
                    self.refused("key_file_not_configured", store.put, kind, "acct", FAKE)
                    self.refused("key_file_not_configured", store.delete, kind, "acct")
                self.refused("key_file_not_configured", store.accept_keychain, A)
                self.assertEqual((ring.calls, os.listdir(self.leaf)), ([], []))

    def test_the_doctor_fails_a_reserved_key_file_name_without_a_traceback(self) -> None:
        # DeepSeek's read of #330, F4: the doctor raised KeyRefused (a traceback from the command) for these.
        os.mkdir(self.leaf, 0o700)
        for name in (RECORD, ".keys.lock"):
            with self.subTest(name=name):
                path = os.path.join(self.leaf, name)
                result = doctor(KeychainStatus(True, A, "ok"), path)
                self.assertEqual((result["level"], result["broker"]), ("fail", "refused:key_file_not_configured"))
                self.assertIn("broker and venue keys refused (key_file_not_configured): the key file is named like "
                              "Cockpit's own .keychain.json or .keys.lock", doctor_line(result))
                result = doctor(KeychainStatus(False, "", "no_keychain_backend"), path)
                self.assertEqual((result["level"], result["model_provider"]), ("fail", "refused:key_file_not_configured"))
                out = _Out(False)
                with mock.patch.object(cli, "detect_keychain", lambda: KeychainStatus(True, A, "ok")), \
                        contextlib.redirect_stdout(out):
                    self.assertEqual(cli.main(["doctor", "--key-file", path]), 1)
                self.assertIn("(key_file_not_configured)", out.getvalue())
                self.assertEqual(os.listdir(self.leaf), [])


class InFlightTests(_Case):
    # The promise for a get or delete in flight: it finishes against the keychain it checked; every operation
    # that starts after the acceptance returned sees the new record. A save holds the lock, so it never runs
    # across an acceptance.
    def test_a_read_already_past_its_check_finishes_on_the_keychain_it_checked(self) -> None:
        ring = self.saved(A)
        later = self.store(Ring(), B)
        accepted: list[str] = []

        class AcceptingDuringTheRead(Ring):
            def get(inner, service: str, name: str) -> str | None:  # noqa: N805
                if name == "broker:acct" and not accepted:
                    accepted.append(later.accept_keychain(B))  # completes: a read holds no lock
                return ring.get(service, name)

        first = self.store(AcceptingDuringTheRead(), A)
        self.assertEqual(first.get("broker", "acct"), FAKE)
        self.assertEqual(accepted, [A])
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": B, "previous": A})
        self.refused("keychain_differs", first.get, "broker", "acct")
        self.refused("keychain_differs", first.delete, "broker", "acct")

    def test_a_delete_already_past_its_check_finishes_on_the_keychain_it_checked(self) -> None:
        ring = self.saved(A)
        later = self.store(Ring(), B)
        accepted: list[str] = []

        class AcceptingDuringTheDelete(Ring):
            def get(inner, service: str, name: str) -> str | None:  # noqa: N805
                return ring.get(service, name)

            def delete(inner, service: str, name: str) -> None:  # noqa: N805
                if name == "broker:acct" and not accepted:
                    accepted.append(later.accept_keychain(B))  # completes: a delete holds no lock
                ring.delete(service, name)

        first = self.store(AcceptingDuringTheDelete(), A)
        first.delete("broker", "acct")
        self.assertEqual(accepted, [A])
        self.assertNotIn(("csl-cockpit", "broker:acct"), ring.data)  # deleted in the keychain it checked
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": B, "previous": A})
        self.refused("keychain_differs", first.delete, "broker", "acct")
        self.refused("keychain_differs", first.get, "broker", "acct")

    def test_an_acceptance_waits_for_a_save_in_flight(self) -> None:
        ring = self.saved(A)
        later = self.store(Ring(), B)
        reached = threading.Event()  # the acceptance's thread has asked for the lock
        results: list[str] = []
        real_flock = store_mod.fcntl.flock  # type: ignore[union-attr]
        worker: list[threading.Thread] = []

        def flock(fd: int, operation: int) -> None:
            if worker and threading.current_thread() is worker[0] and operation & store_mod.fcntl.LOCK_EX:  # type: ignore[union-attr]
                reached.set()
            real_flock(fd, operation)

        seen_during: list[dict] = []

        class AcceptingDuringTheSave(Ring):
            def get(inner, service: str, name: str) -> str | None:  # noqa: N805
                return ring.get(service, name)

            def set(inner, service: str, name: str, secret: str) -> None:  # noqa: N805
                if name == "venue:acct":
                    worker.append(threading.Thread(target=lambda: results.append(later.accept_keychain(B)), daemon=True))
                    worker[0].start()
                    self.assertTrue(reached.wait(10))  # it has reached the lock this save holds
                    seen_during.append(json.loads(self.read_record()))  # and has not written: still A
                    self.assertTrue(worker[0].is_alive())
                ring.set(service, name, secret)

        with mock.patch.object(store_mod.fcntl, "flock", flock):
            self.store(AcceptingDuringTheSave(), A).put("venue", "acct", FAKE)
            worker[0].join(10)
        self.assertEqual(seen_during, [{"version": 1, "keychain": A}])
        self.assertEqual(results, [A])  # the acceptance finished after the save, and saw A
        self.assertEqual(ring.data[("csl-cockpit", "venue:acct")], FAKE)


class ParserTests(_Case):
    def test_content_errors_of_the_record_are_malformed(self) -> None:
        self.write_record(b"[" * 2000 + b"]" * 2000)  # nested, within the size limit
        self.every_recorded_operation_refused(self.store(Ring()), "keychain_record_malformed")
        self.write_record(json.dumps({"version": 1, "keychain": A}).encode())
        for failure in (RecursionError("deep"), ValueError("bad JSON")):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch("cockpit.keys.store.json.loads", side_effect=failure):
                self.every_recorded_operation_refused(self.store(Ring()), "keychain_record_malformed")

    def test_other_errors_while_reading_the_record_stop_the_operation(self) -> None:
        self.write_record(json.dumps({"version": 1, "keychain": A}).encode())
        for failure in (MemoryError(), TypeError("internal")):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch("cockpit.keys.store.json.loads", side_effect=failure):
                store = self.store(Ring())
                for call in ((store.get, "broker", "acct"), (store.put, "broker", "acct", FAKE),
                             (store.delete, "broker", "acct")):
                    with self.assertRaises(type(failure)):  # not turned into keychain_record_malformed
                        call[0](*call[1:])

    def test_an_unexpected_parser_error_never_lets_an_acceptance_replace_a_valid_record(self) -> None:
        # Codex's second read, R2: acceptance replaces a malformed record, so an unexpected error read as
        # "malformed" could have dropped this record's history.
        body = json.dumps({"version": 1, "keychain": A, "previous": C}).encode()
        self.write_record(body)
        with mock.patch("cockpit.keys.store.json.loads", side_effect=MemoryError()), self.assertRaises(MemoryError):
            self.store(Ring(), B).accept_keychain(B)
        self.assertEqual(self.read_record(), body)
        out = _Out(True)
        store = self.store(Ring(), B)
        with mock.patch.object(cli, "store_for_host", lambda path: (store, KeychainStatus(True, B, "ok"))), \
                mock.patch.object(cli.sys, "stdin", _Out(True)), contextlib.redirect_stdout(out), \
                mock.patch("cockpit.keys.store.json.loads", side_effect=MemoryError()):
            self.assertEqual(cli.main(["accept-keychain", B, "--key-file", self.path]), 1)
        self.assertIn("accept-keychain did not finish (MemoryError)", out.getvalue())
        self.assertEqual(self.read_record(), body)

    def test_content_errors_of_the_key_file_are_malformed(self) -> None:
        os.mkdir(self.leaf, 0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"[" * 200000 + b"]" * 200000)  # too deep for the parser
        store = KeyStore(keyring=None, key_file=self.path)
        self.refused("key_file_malformed", store.get, "model_provider", "openai")
        for failure in (RecursionError("deep"), ValueError("bad JSON")):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch("cockpit.keys.store.json.loads", side_effect=failure):
                self.refused("key_file_malformed", store.get, "model_provider", "openai")

    def test_other_errors_while_reading_the_key_file_stop_the_operation(self) -> None:
        os.mkdir(self.leaf, 0o700)
        store = KeyStore(keyring=None, key_file=self.path)
        store.put("model_provider", "openai", FAKE)
        for failure in (MemoryError(), TypeError("internal")):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch("cockpit.keys.store.json.loads", side_effect=failure):
                with self.assertRaises(type(failure)):  # not turned into key_file_malformed
                    store.get("model_provider", "openai")
                with self.assertRaises(type(failure)):
                    store.put("model_provider", "other", FAKE)
        self.assertEqual(store.get("model_provider", "openai"), FAKE)  # the file was never rewritten


class DoctorCheckTests(_Case):
    # B37 (Lucas, Nov 13, 2026: "Note + opt-in check (Recommended)").
    def check(self, ring: Ring) -> tuple[int, str]:
        out = _Out(False)
        store = self.store(ring)
        with mock.patch.object(cli, "store_for_host", lambda path: (store, KeychainStatus(True, A, "ok"))), \
                contextlib.redirect_stdout(out):
            rc = cli.main(["doctor", "--key-file", self.path, "--check-keychain"])
        return rc, out.getvalue().strip()

    def test_check_keychain_reports_a_missing_record_and_the_way_out(self) -> None:
        ring = Ring({MARKER: A})
        rc, line = self.check(ring)
        self.assertEqual(rc, 1)
        self.assertTrue(line.startswith(f"keys: fail - OS keychain ({A}); model-provider keys in the keychain; broker "
                                        f"and venue keys refused (keychain_record_missing): {A} holds Cockpit's record "
                                        "marker but .keychain.json is gone"), line)
        self.assertIn(f"python -m cockpit.keys accept-keychain '{A}'", line)
        self.assertEqual(ring.calls, [("get", MARKER[1])])  # one read: the marker

    def test_check_keychain_with_no_marker_says_so(self) -> None:
        ring = Ring()
        rc, line = self.check(ring)
        self.assertEqual(rc, 0)
        self.assertEqual(line, f"keys: ok - OS keychain ({A}); model-provider, broker and venue keys in the keychain; "
                               "no keychain record yet, and no Cockpit marker in the keychain: the next broker or venue "
                               "save records it")
        self.assertEqual(ring.calls, [("get", MARKER[1])])

    def test_check_keychain_reads_nothing_when_the_record_answers(self) -> None:
        ring = self.saved(A)
        ring.calls.clear()
        rc, line = self.check(ring)
        self.assertEqual((rc, line), (0, f"keys: ok - OS keychain ({A}); model-provider, broker and venue keys in the "
                                         "keychain"))
        self.assertEqual(ring.calls, [])

    def test_check_keychain_shows_only_the_type_when_the_read_fails(self) -> None:
        ring = _FailingRead()
        rc, line = self.check(ring)
        self.assertEqual(rc, 0)  # warn: the keychain could not be checked, nothing was found wrong
        self.assertTrue(line.startswith(f"keys: warn - OS keychain ({A})"), line)
        self.assertIn("keychain check did not finish (RuntimeError)", line)
        self.assertNotIn(FAKE, line)  # the error's message is never shown


@requires_keyring
class PlainDoctorTests(_Case):
    def test_the_plain_doctor_never_contacts_the_keychain(self) -> None:
        # The library's own Secret Service class, its password methods recording every call: detection runs
        # as in the product, and the plain doctor sends the keychain no call, whatever the record's state.
        ss = Keychains(self).os()
        out = _Out(False)

        def no_store(path: str) -> None:
            raise AssertionError("the plain doctor built a store")

        def run() -> tuple[int, str]:
            out.seek(0)
            out.truncate()
            with mock.patch.object(cli, "detect_keychain", lambda: detect_mod.detect_keychain(loader(ss))), \
                    mock.patch.object(cli, "store_for_host", no_store), contextlib.redirect_stdout(out):
                rc = cli.main(["doctor", "--key-file", self.path])
            return rc, out.getvalue().strip()

        plain = f"keys: ok - OS keychain ({A}); model-provider, broker and venue keys in the keychain"
        self.assertEqual(run(), (0, plain + no_record_note(A)))  # no record
        ss.data[MARKER] = A  # the marker without the record: the plain doctor cannot tell, and says how to look
        self.assertEqual(run(), (0, plain + no_record_note(A)))
        self.write_record(json.dumps({"version": 1, "keychain": A}).encode())
        self.assertEqual(run(), (0, plain))  # a matching record: no note
        self.assertEqual(ss.calls, [])


if __name__ == "__main__":
    unittest.main()
