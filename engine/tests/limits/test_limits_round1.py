"""Round 1 of #331's reads on the caps source (Codex astra R1, R4-R6; Codex sol R1, R5-R7;
DeepSeek F1-F4, F11, F12). Each test here was red on 2bdb5597. Made-up data only."""

import errno
import os
import tempfile
import threading
import unittest
from unittest import mock

import cockpit.limits.source as source_mod
from cockpit.limits import FileHighWater, create_high_water
from cockpit.research.canonical import canonical, parse
from cockpit.research.errors import Refused

from .test_limits_source import NOW, envelope, fixture_bytes, fixture_document, source


def doc(sequence, account="paper-acct-0001", cap=50000):
    d = fixture_document()
    d["sequence"] = sequence
    d["scope"]["account"] = account
    d["caps"]["max_notional_minor"] = cap
    return d


class FloorCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self._tmp.name, "floor")
        os.mkdir(self.dir, 0o700)
        os.chmod(self.dir, 0o700)
        self.path = os.path.join(self.dir, "limits-high-water.json")
        create_high_water(self.path)

    def tearDown(self):
        os.chmod(self.dir, 0o700)
        self._tmp.cleanup()

    def load(self, sequence, account="paper-acct-0001", store=None, cap=50000):
        src = source(store or FileHighWater(self.path), account=account)
        return src.load(envelope(doc(sequence, account, cap)), now_ms=NOW)

    def floor(self):
        with open(self.path, "rb") as f:
            return {k: v["sequence"] for k, v in parse(f.read())["entries"].items()}

    def refused(self, code, fn, *args, **kwargs):
        with self.assertRaises(Refused) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code, ctx.exception)


class PausedRead:
    """Pauses one store's transaction right after it has read the floor (the reviewers' schedule)."""

    def __init__(self, store):
        self.read_done = threading.Event()
        self.go_on = threading.Event()
        real = store._read

        def paused():
            value = real()
            self.read_done.set()
            self.go_on.wait(10)
            return value

        store._read = paused


class FloorTransaction(FloorCase):
    """astra R1, sol R1, DeepSeek F1: one transaction across instances and processes."""

    def test_a_load_waits_while_another_process_holds_the_floor_lock(self):
        import fcntl
        fd = os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        done = threading.Event()
        t = threading.Thread(target=lambda: (self.load(1), done.set()))
        t.start()
        try:
            self.assertFalse(done.wait(0.3))  # not yet: the lock is held elsewhere
        finally:
            os.close(fd)
        t.join()
        self.assertTrue(done.is_set())

    def test_the_floor_never_goes_back_when_two_stores_race(self):
        slow = FileHighWater(self.path)
        pause = PausedRead(slow)
        results = {}
        a = threading.Thread(target=lambda: results.__setitem__("a", self.load(2, store=slow).sequence))
        a.start()
        self.assertTrue(pause.read_done.wait(10))
        b = threading.Thread(target=lambda: results.__setitem__("b", self.load(3).sequence))
        b.start()
        b.join(0.3)
        pause.go_on.set()
        a.join()
        b.join()
        self.assertEqual(results, {"a": 2, "b": 3})
        self.assertEqual(self.floor(), {"paper-acct-0001|fakevenue|paper": 3})
        self.refused("rollback", self.load, 2)

    def test_two_files_at_one_sequence_are_not_both_accepted(self):
        slow = FileHighWater(self.path)
        pause = PausedRead(slow)
        outcomes = []

        def run(store, cap):
            try:
                self.load(5, store=store, cap=cap)
                outcomes.append("accepted")
            except Refused as exc:
                outcomes.append(exc.code)

        a = threading.Thread(target=run, args=(slow, 50000))
        a.start()
        self.assertTrue(pause.read_done.wait(10))
        b = threading.Thread(target=run, args=(None, 60000))
        b.start()
        b.join(0.3)
        pause.go_on.set()
        a.join()
        b.join()
        self.assertEqual(sorted(outcomes), ["accepted", "sequence_conflict"])

    def test_another_scope_is_not_lost(self):
        slow = FileHighWater(self.path)
        pause = PausedRead(slow)
        a = threading.Thread(target=lambda: self.load(4, store=slow))
        a.start()
        self.assertTrue(pause.read_done.wait(10))
        b = threading.Thread(target=lambda: self.load(7, account="paper-acct-0002"))
        b.start()
        b.join(0.3)
        pause.go_on.set()
        a.join()
        b.join()
        self.assertEqual(self.floor(), {"paper-acct-0001|fakevenue|paper": 4, "paper-acct-0002|fakevenue|paper": 7})

    def test_the_lock_file_must_be_owner_only_and_present(self):
        os.chmod(self.path + ".lock", 0o644)
        self.refused("high_water_not_owner_only", self.load, 1)
        os.unlink(self.path + ".lock")
        self.refused("high_water_missing", self.load, 1)


class FloorWrites(FloorCase):
    """astra R4/R5, sol R5, DeepSeek F2/F3: short, failed and oversized writes keep the floor."""

    def test_short_writes_are_completed(self):
        os.unlink(self.path)
        os.unlink(self.path + ".lock")
        real_write = os.write

        def seven(fd, data):
            return real_write(fd, bytes(data)[:7])

        with mock.patch("os.write", seven):
            create_high_water(self.path)
            self.load(3)
        self.assertEqual(self.floor(), {"paper-acct-0001|fakevenue|paper": 3})
        self.refused("rollback", self.load, 2)

    def test_a_write_that_makes_no_progress_keeps_the_previous_floor(self):
        self.load(3)
        before = open(self.path, "rb").read()
        for failure in (lambda fd, data: 0, mock.Mock(side_effect=OSError(errno.ENOSPC, "No space left on device"))):
            with self.subTest(failure=failure):
                with mock.patch("os.write", failure):
                    self.refused("high_water_unwritable", self.load, 4)
                self.assertEqual(open(self.path, "rb").read(), before)
                self.assertEqual(sorted(os.listdir(self.dir)), ["limits-high-water.json", "limits-high-water.json.lock"])
        self.refused("rollback", self.load, 2)

    def test_a_data_fsync_failure_is_refused_and_keeps_the_previous_floor(self):
        self.load(3)
        before = open(self.path, "rb").read()
        with mock.patch.object(source_mod, "_fsync_file", mock.Mock(side_effect=OSError(errno.EIO, "I/O"))):
            self.refused("high_water_unwritable", self.load, 4)
        self.assertEqual(open(self.path, "rb").read(), before)

    def test_a_directory_fsync_failure_is_refused(self):
        with mock.patch.object(source_mod, "_fsync_dir", mock.Mock(side_effect=OSError(errno.EIO, "I/O"))):
            self.refused("high_water_unwritable", self.load, 4)

    def test_an_unwritable_directory_is_a_refusal(self):
        os.chmod(self.dir, 0o500)
        try:
            self.refused("high_water_unwritable", self.load, 4)
        finally:
            os.chmod(self.dir, 0o700)
        self.refused("high_water_unwritable", create_high_water, os.path.join(self._tmp.name, "nope", "f.json"))

    def big_floor(self):
        """A valid floor larger than the caps file, so a ceiling between the two touches only the floor."""
        entries = {"synthetic-%02d|fakevenue|paper" % i: {"sequence": 1, "digest": "sha256:" + "1" * 64}
                   for i in range(12)}
        raw = canonical({"schema": source_mod.HIGH_WATER_SCHEMA, "entries": entries})
        with open(self.path, "wb") as f:
            f.write(raw)
        self.assertGreater(len(raw), len(fixture_bytes()) + 10)
        return raw

    def test_a_floor_that_would_outgrow_its_reader_is_refused_before_writing(self):
        before = self.big_floor()
        with mock.patch.object(source_mod, "MAX_FILE", len(before) + 1):
            self.refused("high_water_full", self.load, 1)
        self.assertEqual(open(self.path, "rb").read(), before)
        self.load(1)  # still readable and usable

    def test_a_floor_file_past_the_ceiling_is_refused_on_read(self):
        before = self.big_floor()
        with mock.patch.object(source_mod, "MAX_FILE", len(before) - 1):
            self.refused("high_water_invalid", self.load, 1)

    def test_create_on_a_directory_or_after_the_floor_was_deleted(self):
        self.refused("path_invalid", create_high_water, self.dir)
        os.unlink(self.path)
        self.refused("high_water_exists", create_high_water, self.path)  # its lock file remains


class UnsupportedPlatform(FloorCase):
    """sol R6, astra Q10: no process lock or no POSIX owners means refuse, not pretend."""

    def test_no_fcntl_or_no_posix_owners_refuses_the_file_floor(self):
        for name, value in (("fcntl", None), ("_POSIX", False)):
            with self.subTest(name=name):
                with mock.patch.object(source_mod, name, value):
                    self.refused("platform_unsupported", FileHighWater, self.path)
                    self.refused("platform_unsupported", create_high_water, self.path + ".other")


class SourceSmallReads(unittest.TestCase):
    """DeepSeek F4, F11, F12."""

    def test_currency_other_than_usd_is_refused(self):
        d = fixture_document()
        d["currency"] = "EUR"
        with self.assertRaises(Refused) as ctx:
            source().load(envelope(d), now_ms=NOW)
        self.assertEqual(ctx.exception.code, "currency_unsupported")

    def test_a_signed_live_document_reports_live_not_authorised(self):
        d = fixture_document()
        d["scope"]["mode"] = "live"
        with self.assertRaises(Refused) as ctx:
            source().load(envelope(d), now_ms=NOW)
        self.assertEqual(ctx.exception.code, "live_not_authorised")

    def test_a_verifier_whose_key_id_raises_is_invalid(self):
        class Raising:
            @property
            def key_id(self):
                raise RuntimeError("no key id")

            def verify(self, message, signature):
                return True

        with self.assertRaises(Refused) as ctx:
            source(verifier=Raising())
        self.assertEqual(ctx.exception.code, "verifier_invalid")

    def test_fixture_still_loads(self):
        self.assertEqual(source().load(fixture_bytes(), now_ms=NOW).sequence, 1)


if __name__ == "__main__":
    unittest.main()
