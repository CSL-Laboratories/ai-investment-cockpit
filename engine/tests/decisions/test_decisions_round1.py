"""Round 1 of #331's reads on the decision record (Codex astra R2-R4, R6; Codex sol R2-R4, R6, R7;
DeepSeek F5-F8, C2). Each test here was red on 2bdb5597. Made-up data only."""

import errno
import hashlib
import os
import threading
import unittest
from unittest import mock

import cockpit.decisions.record as record_mod
from cockpit.decisions import GENESIS, DecisionRecord
from cockpit.research import parse_proposal
from cockpit.research.canonical import canonical
from cockpit.research.errors import Refused

from .test_decisions_record import EFFECT, PROMPT, RecordTestCase, json_copy, make_case, make_proposal, read

POUT = read("proposal_valid.txt")  # a model output whose proposal is the fixture proposal


def output_with(old, new):
    text = POUT.replace(old, new)
    assert text != POUT
    return text


class RoundOneBase(RecordTestCase):
    def head_path(self):
        return os.path.join(self.dir, "case-0001-made-up.head")

    def log(self, rec, output=POUT, prompt=PROMPT):
        return rec.log_model_output(route_record_id="route-rec-made-up-1", model_id="made-up-model",
                                    model_version="made-up-model@2026-11-01", prompt=prompt, output=output)

    def rechain(self, rows):
        prev, out = GENESIS, b""
        for row in rows:
            row = dict(row, prev=prev)
            line = canonical(row) + b"\n"
            prev = "sha256:" + hashlib.sha256(line).hexdigest()
            out += line
        self.write_raw(out)


class ProposalProvenance(RoundOneBase):
    """sol R3, astra R2, DeepSeek F5: the accepted proposal is the one its named output holds."""

    def test_a_proposal_its_output_does_not_hold_is_refused(self):
        rec = self.new()
        out = self.log(rec, output=output_with('"conviction_bps": 2500', '"conviction_bps": 1'))
        before = self.raw()
        self.refused("provenance_mismatch", rec.record_proposal, make_proposal(), from_output=out)
        self.assertEqual(self.raw(), before)

    def test_side_and_citations_must_match_too(self):
        for old, new in (('"side": "buy"', '"side": "sell"'), ('"P1W"', '"P2W"'),
                         ('"ev-1",\n    "ev-2"', '"ev-2",\n    "ev-1"')):
            with self.subTest(new=new):
                self.setUp()
                rec = self.new()
                out = self.log(rec, output=output_with(old, new))
                self.refused("provenance_mismatch", rec.record_proposal, make_proposal(), from_output=out)
                self.tearDown()

    def test_output_that_is_not_a_proposal_cannot_back_one(self):
        for text in ("o", "I think buy.", POUT.replace('"side": "buy",', '"side": "buy", "quantity": 1,')):
            with self.subTest(text=text[:20]):
                self.setUp()
                rec = self.new()
                out = self.log(rec, output=text)
                self.refused("provenance_mismatch", rec.record_proposal, make_proposal(), from_output=out)
                rec.record_proposal_refused("not_json", from_output=out)  # it stays in the log as refused
                self.tearDown()

    def test_layout_of_the_output_does_not_matter(self):
        rec = self.new()
        compact = canonical(make_proposal().value()).decode("ascii")
        out = self.log(rec, output="  " + compact + "\n")
        rec.record_proposal(make_proposal(), from_output=out)
        self.assertEqual(self.reopen().rows()[2]["body"]["proposal_digest"], make_proposal().digest())

    def test_replay_refuses_a_rechained_proposal_its_output_does_not_hold(self):
        self.full()
        rows = self.reopen().rows()
        forged = [json_copy(r) for r in rows[:3]]
        forged[2]["body"]["proposal"]["conviction_bps"] = 9999
        forged[2]["body"]["proposal_digest"] = parse_proposal(
            POUT.replace('"conviction_bps": 2500', '"conviction_bps": 9999')).digest()
        self.rechain(forged)
        self.refused("provenance_mismatch", DecisionRecord.open, self.dir, "case-0001-made-up",
                     clock=self.clock, expect_head=self.reopen_head_of_forgery())

    def reopen_head_of_forgery(self):
        data = self.raw()
        last = data[:-1].rsplit(b"\n", 1)[-1] + b"\n"
        return "sha256:" + hashlib.sha256(last).hexdigest()


class PersistedHead(RoundOneBase):
    """sol R2, DeepSeek F5/F7: a reopened record is writable only at the head it last wrote."""

    def test_dropping_a_final_veto_is_refused_on_a_plain_open(self):
        self.full(answer="veto")
        lines = self.raw().split(b"\n")[:-1]
        self.write_raw(b"\n".join(lines[:3]) + b"\n")
        self.refused("head_mismatch", self.reopen)

    def test_a_rechained_veto_to_approve_is_refused_on_a_plain_open(self):
        self.full(answer="veto")
        rows = self.reopen().rows()
        forged = [json_copy(r) for r in rows]
        forged[3]["body"]["answer"] = "approve"
        self.rechain(forged)
        self.refused("head_mismatch", self.reopen)

    def test_the_head_file_must_exist_and_be_owner_only(self):
        self.full()
        os.chmod(self.head_path(), 0o644)
        self.refused("head_not_owner_only", self.reopen)
        os.unlink(self.head_path())
        self.refused("head_missing", self.reopen)

    def test_head_moves_with_every_append_and_restart_keeps_it(self):
        rec = self.new()
        self.log(rec)
        head = rec.head()
        self.assertEqual(self.reopen().head(), head)
        with open(self.head_path(), "rb") as f:
            self.assertIn(head.encode("ascii"), f.read())

    def test_create_refuses_a_leftover_head(self):
        with open(os.open(self.head_path(), os.O_WRONLY | os.O_CREAT, 0o600), "wb") as f:
            f.write(b"x")
        self.refused("record_exists", self.new)


class AppendChecksContents(RoundOneBase):
    """astra R3, DeepSeek F6: an outside edit of the same length is refused at the append."""

    def test_same_length_edit_then_append_is_refused_and_nothing_is_written(self):
        rec = self.new()
        self.log(rec)
        data = self.raw()
        edited = data.replace(b'"at_ms":1800000000001', b'"at_ms":1800000000009', 1)
        self.assertEqual(len(edited), len(data))
        self.assertNotEqual(edited, data)
        self.write_raw(edited)
        self.refused("record_changed", self.log, rec)
        self.assertEqual(self.raw(), edited)
        self.assertFalse(rec.final)

    def test_head_file_changed_outside_is_refused(self):
        rec = self.new()
        with open(self.head_path(), "r+b") as f:
            data = f.read()
            f.seek(0)
            f.write(data.replace(rec.head().encode("ascii"), ("sha256:" + "1" * 64).encode("ascii")))
        self.refused("record_changed", self.log, rec)


class SizeCeiling(RoundOneBase):
    """astra R4, sol R4: an append the reader would refuse is refused before it is written."""

    def test_append_past_the_ceiling_is_refused_and_the_record_stays_readable(self):
        rec = self.new()
        size = len(self.raw())
        with mock.patch.object(record_mod, "_MAX_FILE", size + 600):
            self.refused("too_large", self.log, rec)
            self.assertEqual(len(self.raw()), size)
            self.assertEqual(len(self.reopen().rows()), 1)

    def test_reopen_refuses_a_record_past_the_ceiling(self):
        rec = self.new()
        self.log(rec)
        with mock.patch.object(record_mod, "_MAX_FILE", len(self.raw()) - 1):
            self.refused("too_large", self.reopen)


class FailedAppends(RoundOneBase):
    """DeepSeek F8, sol D07/D08, astra M24: a failed write or sync is refused and leaves the record readable."""

    def test_a_write_that_fails_half_way_is_rolled_back(self):
        rec = self.new()
        before = self.raw()
        real_write = os.write
        calls = []

        def half(fd, data):
            if not calls:
                calls.append(1)
                return real_write(fd, bytes(data)[:5])
            raise OSError(errno.ENOSPC, "No space left on device")

        with mock.patch("os.write", half):
            self.refused("write_failed", self.log, rec)
        self.assertEqual(self.raw(), before)
        self.assertEqual(len(self.reopen().rows()), 1)
        self.log(rec)  # the handle is still good
        self.assertEqual(len(self.reopen().rows()), 2)

    def record_only_boom(self):
        """An fsync that fails for the record file only (the head file's sync still works)."""
        real = record_mod._fsync_file
        path = os.path.join(self.dir, "case-0001-made-up.jsonl")

        def boom(fd):
            if os.path.exists(path) and os.path.samestat(os.fstat(fd), os.stat(path)):
                raise OSError(errno.EIO, "Input/output error")
            return real(fd)

        return boom

    def test_a_record_fsync_failure_on_append_is_refused_and_rolled_back(self):
        rec = self.new()
        before = self.raw()
        with mock.patch.object(record_mod, "_fsync_file", self.record_only_boom()):
            self.refused("write_failed", self.log, rec)
        self.assertEqual(self.raw(), before)
        self.assertEqual(len(self.reopen().rows()), 1)

    def test_a_record_fsync_failure_on_create_is_refused_and_leaves_nothing(self):
        with mock.patch.object(record_mod, "_fsync_file", self.record_only_boom()):
            self.refused("write_failed", self.new)
        self.assertEqual(os.listdir(self.dir), [])
        self.new()

    def test_a_directory_fsync_failure_on_create_is_refused(self):
        def boom(path):
            raise OSError(errno.EIO, "Input/output error")

        with mock.patch.object(record_mod, "_fsync_dir", boom):
            self.refused("write_failed", self.new)

    def test_a_directory_fsync_failure_on_append_is_refused(self):
        rec = self.new()

        def boom(path):
            raise OSError(errno.EIO, "Input/output error")

        with mock.patch.object(record_mod, "_fsync_dir", boom):
            self.refused("write_failed", self.log, rec)


class RowRules(RoundOneBase):
    """DeepSeek C2, sol D02, astra M14/M18/M19/M20: rules other checks used to mask."""

    def test_rechained_wrong_seq_is_refused(self):
        self.full()
        rows = self.reopen().rows()
        for bad in ([0, 0, 2, 3], [0, 2, 3, 4], [1, 2, 3, 4]):
            with self.subTest(seqs=bad):
                forged = [dict(json_copy(r), seq=s) for r, s in zip(rows, bad)]
                self.rechain(forged)
                self.refused("chain_broken", self.reopen)

    def test_prompt_and_output_hash_must_match_their_text(self):
        rec = self.new()
        self.log(rec, output="made-up output")
        rows = self.reopen().rows()
        for blob in ("prompt", "output"):
            with self.subTest(blob=blob):
                forged = [json_copy(r) for r in rows]
                forged[1]["body"][blob]["sha256"] = "0" * 64
                self.rechain(forged)
                self.refused("row_invalid", self.reopen)

    def test_a_directory_at_the_record_path_is_refused(self):
        os.mkdir(os.path.join(self.dir, "case-0001-made-up.jsonl"), 0o700)
        self.refused("record_not_regular_file", self.reopen)


class UnsupportedPlatform(RoundOneBase):
    """sol R6, astra Q10: without owner checks or a process lock the record refuses, not pretends."""

    def test_no_fcntl_refuses(self):
        with mock.patch.object(record_mod, "fcntl", None):
            self.refused("platform_unsupported", self.new)
        self.new()
        with mock.patch.object(record_mod, "fcntl", None):
            self.refused("platform_unsupported", self.reopen)

    def test_no_posix_owners_refuses(self):
        with mock.patch.object(record_mod, "_POSIX", False):
            self.refused("platform_unsupported", self.new)


class SharedLockOnOpen(RoundOneBase):
    def test_open_waits_for_a_writer_holding_the_lock(self):
        import fcntl
        self.new()
        fd = os.open(os.path.join(self.dir, "case-0001-made-up.jsonl"), os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX)
        done = threading.Event()
        t = threading.Thread(target=lambda: (self.reopen(), done.set()))
        t.start()
        try:
            self.assertFalse(done.wait(0.3))
        finally:
            os.close(fd)
        t.join()
        self.assertTrue(done.is_set())


if __name__ == "__main__":
    unittest.main()
