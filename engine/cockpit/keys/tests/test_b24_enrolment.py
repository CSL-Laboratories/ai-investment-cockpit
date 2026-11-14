"""B24 after the first outside read of #330: enrolment order, failures and retries, calls in flight (CSL-59).

Codex (GPT-6), first read of #330 at 5c0b006a, F1: the first save wrote the record and only then the marker,
so a save cut off between the two could leave a record without its marker for good, and accept-keychain
committed the new record before its marker write. Now the marker comes first and the record second; a
marker the call added is taken back if the record cannot be written; a process killed between the two
leaves the marker alone, which the next broker or venue operation refuses (keychain_record_missing) until
accept-keychain records it; a matching record without a marker is completed by the next save. Also: what a
call already in flight gets when an acceptance changes the record, the reserved key file names on a
keychain host, and parser failures. Temp folders, a dict for a keychain, made-up keys only.
"""

from __future__ import annotations

import contextlib
import json
import multiprocessing
import os
import threading
import unittest
from unittest import mock

from cockpit.keys import KeyStore
from cockpit.keys import __main__ as cli
from cockpit.keys import store as store_mod
from cockpit.keys.detect import _KEYCHAIN_NAMES, KeychainStatus
from cockpit.keys.tests.fakes import FAKE, MARKER
from cockpit.keys.tests.test_b24_keychain_record import RECORD, A, B, Ring, _Case, _Out


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

    def test_a_first_save_whose_marker_write_fails_leaves_nothing_and_can_be_retried(self) -> None:
        ring = _Failing()
        with self.assertRaises(RuntimeError):
            self.store(ring).put("broker", "acct", FAKE)
        self.assertFalse(os.path.lexists(self.record))
        self.assertEqual(ring.data, {})
        ring = Ring()  # the keychain answers again
        self.store(ring).put("broker", "acct", FAKE)
        self.assertEqual(ring.data, {MARKER: A, ("csl-cockpit", "broker:acct"): FAKE})

    def test_a_first_save_whose_record_write_fails_takes_its_marker_back(self) -> None:
        ring = Ring()
        with mock.patch("cockpit.keys.store.os.rename", side_effect=OSError(5, "EIO")), self.assertRaises(OSError):
            self.store(ring).put("venue", "acct", FAKE)
        self.assertFalse(os.path.lexists(self.record))
        self.assertEqual(ring.data, {})  # the marker it wrote is gone, and no key was stored
        self.store(ring).put("venue", "acct", FAKE)  # a plain retry works
        self.assertEqual(json.loads(self.read_record()), {"version": 1, "keychain": A})
        self.assertEqual(ring.data, {MARKER: A, ("csl-cockpit", "venue:acct"): FAKE})

    def test_a_first_save_killed_between_its_two_writes_is_refused_until_accepted(self) -> None:
        ring_file = os.path.join(self.root, "ring.json")
        child = multiprocessing.get_context("spawn").Process(target=_killed_between_the_two_writes,
                                                            args=(self.path, ring_file))
        child.start()
        child.join(60)
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

    def test_an_acceptance_whose_record_write_fails_keeps_the_record_and_any_earlier_marker(self) -> None:
        self.saved(A)
        before = self.read_record()
        for earlier in ({}, {MARKER: B}):
            with self.subTest(earlier_marker=bool(earlier)):
                ring = Ring(earlier)
                with mock.patch("cockpit.keys.store.os.rename", side_effect=OSError(5, "EIO")), \
                        self.assertRaises(OSError):
                    self.store(ring, B).accept_keychain(B)
                self.assertEqual(self.read_record(), before)
                self.assertEqual(ring.data, earlier)  # its own marker taken back, an earlier one left

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
        # Only a candidate build of #330 (record first) could leave this; it is honoured, and completed.
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


class InFlightTests(_Case):
    def test_a_read_already_past_its_check_finishes_on_the_keychain_it_checked(self) -> None:
        # The promise for a get or delete in flight: it finishes against the keychain it checked; every
        # operation that starts after the acceptance returned sees the new record.
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

    def test_an_acceptance_waits_for_a_save_in_flight(self) -> None:
        ring = self.saved(A)
        later = self.store(Ring(), B)
        during: list[object] = []

        class AcceptingDuringTheSave(Ring):
            def get(inner, service: str, name: str) -> str | None:  # noqa: N805
                return ring.get(service, name)

            def set(inner, service: str, name: str, secret: str) -> None:  # noqa: N805
                if name == "venue:acct":
                    worker = threading.Thread(target=lambda: during.append(later.accept_keychain(B)), daemon=True)
                    worker.start()
                    worker.join(0.5)
                    during.append(json.loads(self.read_record()))  # still A: the acceptance waits on the lock
                    during.append(worker)
                ring.set(service, name, secret)

        self.store(AcceptingDuringTheSave(), A).put("venue", "acct", FAKE)
        worker = during[1]
        worker.join(10)  # type: ignore[attr-defined]
        self.assertEqual(during[0], {"version": 1, "keychain": A})
        self.assertEqual(during[2:], [A])  # the acceptance finished after the save, and saw A
        self.assertEqual(ring.data[("csl-cockpit", "venue:acct")], FAKE)


class ParserTests(_Case):
    def test_every_parser_failure_of_the_record_is_malformed(self) -> None:
        self.write_record(b"[" * 2000 + b"]" * 2000)  # nested, within the size limit
        self.every_recorded_operation_refused(self.store(Ring()), "keychain_record_malformed")
        self.write_record(json.dumps({"version": 1, "keychain": A}).encode())
        for failure in (RecursionError("deep"), MemoryError(), TypeError("x")):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch("cockpit.keys.store.json.loads", side_effect=failure):
                self.every_recorded_operation_refused(self.store(Ring()), "keychain_record_malformed")

    def test_every_parser_failure_of_the_key_file_is_malformed(self) -> None:
        os.mkdir(self.leaf, 0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"[" * 200000 + b"]" * 200000)  # too deep for the parser
        store = KeyStore(keyring=None, key_file=self.path)
        self.refused("key_file_malformed", store.get, "model_provider", "openai")
        with mock.patch("cockpit.keys.store.json.loads", side_effect=RecursionError("deep")):
            self.refused("key_file_malformed", store.get, "model_provider", "openai")


if __name__ == "__main__":
    unittest.main()
