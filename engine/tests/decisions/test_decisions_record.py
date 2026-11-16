"""The decision record and model-risk log: append-only, owner-only, hash-chained, kept across
a restart. Made-up data only: the case and proposal fixtures from tests/research."""

import hashlib
import os
import stat
import tempfile
import threading
import unittest

from cockpit.decisions import GENESIS, RECORD_SCHEMA, DecisionRecord
from cockpit.research import parse_case, parse_proposal
from cockpit.research.canonical import canonical, parse
from cockpit.research.errors import Refused

RESEARCH_FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "research", "fixtures")
POSIX = hasattr(os, "getuid")
EFFECT = "sha256:" + "ab" * 32
PROMPT = "Made-up prompt. Answer with one cockpit.proposal/1 object."


def read(name):
    with open(os.path.join(RESEARCH_FIXTURES, name), encoding="ascii") as f:
        return f.read()


def make_case():
    return parse_case(read("case_valid.json"))


def make_proposal():
    return parse_proposal(read("proposal_valid.txt"), case=make_case())


class Clock:
    def __init__(self, start=1_800_000_000_000):
        self.now = start

    def __call__(self):
        self.now += 1
        return self.now


class StrSub(str):
    pass


def json_copy(value):
    return parse(canonical(value))


class RecordTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self._tmp.name, "records")
        os.mkdir(self.dir, 0o700)
        os.chmod(self.dir, 0o700)
        self.clock = Clock()

    def tearDown(self):
        self._tmp.cleanup()

    def new(self):
        return DecisionRecord.create(self.dir, make_case(), clock=self.clock)

    def reopen(self):
        return DecisionRecord.open(self.dir, "case-0001-made-up", clock=self.clock)

    def full(self, rec=None, answer="approve"):
        rec = rec or self.new()
        out = rec.log_model_output(route_record_id="route-rec-made-up-1", model_id="made-up-model",
                                   model_version="made-up-model@2026-11-01", prompt=PROMPT,
                                   output=read("proposal_valid.txt"))
        rec.record_proposal(make_proposal(), from_output=out)
        rec.record_answer(proposal_digest=make_proposal().digest(), approval_id="appr-made-up-1",
                          effect_digest=EFFECT, answered_by="operator-made-up", answer=answer,
                          reason="Made-up reason.")
        return rec

    def raw(self):
        with open(os.path.join(self.dir, "case-0001-made-up.jsonl"), "rb") as f:
            return f.read()

    def write_raw(self, data):
        """Overwrite the record in place (same file), as an outside editor would."""
        path = os.path.join(self.dir, "case-0001-made-up.jsonl")
        with open(path, "r+b") as f:
            f.truncate(0)
            f.write(data)

    def refused(self, code, fn, *args, **kwargs):
        with self.assertRaises(Refused) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code, ctx.exception)


class DecisionRecordFlow(RecordTestCase):
    def test_constructor_is_closed(self):
        with self.assertRaises(TypeError):
            DecisionRecord()

    def test_full_flow_rows_and_chain(self):
        rec = self.full()
        rows = rec.rows()
        self.assertEqual([r["kind"] for r in rows], ["case_opened", "model_output", "proposal", "answer"])
        self.assertEqual([r["seq"] for r in rows], [0, 1, 2, 3])
        self.assertEqual(rows[0]["prev"], GENESIS)
        self.assertTrue(all(r["schema"] == RECORD_SCHEMA for r in rows))
        self.assertTrue(rec.final)
        lines = self.raw().split(b"\n")
        self.assertEqual(lines[-1], b"")
        for i in range(1, 4):
            self.assertEqual(rows[i]["prev"], "sha256:" + hashlib.sha256(lines[i - 1] + b"\n").hexdigest())
        for line in lines[:-1]:
            self.assertEqual(canonical(parse(line)), line)

    def test_model_risk_log_keeps_prompt_output_and_model_exactly(self):
        rec = self.new()
        prompt = "Made-up prompt with text a model might write: caf" + chr(0xE9) + " " + chr(0x2713) + "\r\n\x00end"
        output = "Made-up output " + chr(0x1F600)
        rec.log_model_output(route_record_id="route-rec-made-up-1", model_id="made-up-model",
                             model_version="opus[1m]", prompt=prompt, output=output)
        row = self.reopen().rows()[1]
        self.assertEqual(row["kind"], "model_output")
        self.assertEqual(DecisionRecord.text(row["body"]["prompt"]), prompt)
        self.assertEqual(DecisionRecord.text(row["body"]["output"]), output)
        self.assertEqual(row["body"]["model_version"], "opus[1m]")
        self.assertEqual(row["body"]["route_record_id"], "route-rec-made-up-1")

    def test_answer_row_carries_prompt_output_model_version_digest_and_principal(self):
        rows = self.full().rows()
        answer = rows[3]["body"]
        out = rows[1]["body"]
        self.assertEqual(answer["proposal_digest"], make_proposal().digest())
        self.assertEqual(answer["effect_digest"], EFFECT)
        self.assertEqual(answer["answered_by"], "operator-made-up")
        self.assertEqual(answer["model"], {"model_id": "made-up-model", "model_version": "made-up-model@2026-11-01",
                                           "prompt_sha256": out["prompt"]["sha256"],
                                           "output_sha256": out["output"]["sha256"]})

    def test_restart_keeps_the_record(self):
        before = self.full(answer="veto").rows()
        after = self.reopen()
        self.assertEqual(after.rows(), before)
        self.assertTrue(after.final)
        self.assertEqual(after.rows()[3]["body"]["answer"], "veto")

    def test_reopen_and_continue(self):
        rec = self.new()
        out = rec.log_model_output(route_record_id="r-1", model_id="m", model_version="v1", prompt="p", output="o")
        rec2 = self.reopen()
        rec2.record_proposal_refused("quantity_forbidden", from_output=out)
        out2 = rec2.log_model_output(route_record_id="r-2", model_id="m", model_version="v1", prompt="p",
                                     output=read("proposal_valid.txt"))
        rec2.record_proposal(make_proposal(), from_output=out2)
        self.assertEqual([r["kind"] for r in self.reopen().rows()],
                         ["case_opened", "model_output", "proposal_refused", "model_output", "proposal"])

    def test_files_are_owner_only(self):
        self.new()
        mode = stat.S_IMODE(os.stat(os.path.join(self.dir, "case-0001-made-up.jsonl")).st_mode)
        if POSIX:
            self.assertEqual(mode & 0o077, 0)


class DecisionRecordOrder(RecordTestCase):
    def test_create_twice_is_refused(self):
        self.new()
        self.refused("record_exists", self.new)

    def test_nothing_after_the_answer(self):
        rec = self.full()
        self.refused("record_final", rec.log_model_output, route_record_id="r", model_id="m",
                     model_version="v", prompt="p", output="o")
        self.refused("record_final", rec.record_proposal_refused, "x", from_output=1)

    def test_proposal_needs_an_unanswered_model_output(self):
        rec = self.new()
        self.refused("row_order", rec.record_proposal, make_proposal(), from_output=0)
        self.refused("row_invalid", rec.record_proposal, make_proposal(), from_output=5)
        out = rec.log_model_output(route_record_id="r", model_id="m", model_version="v", prompt="p", output="o")
        rec.record_proposal_refused("not_json", from_output=out)
        self.refused("row_order", rec.record_proposal, make_proposal(), from_output=out)
        for bad in (True, "1", 1.0, None):
            with self.subTest(bad=repr(bad)):
                self.refused("row_invalid", rec.record_proposal, make_proposal(), from_output=bad)

    def test_one_proposal_per_record(self):
        rec = self.new()
        out = rec.log_model_output(route_record_id="r", model_id="m", model_version="v", prompt="p",
                                   output=read("proposal_valid.txt"))
        rec.record_proposal(make_proposal(), from_output=out)
        self.refused("row_order", rec.log_model_output, route_record_id="r", model_id="m",
                     model_version="v", prompt="p", output="o")
        self.refused("row_order", rec.record_proposal, make_proposal(), from_output=out)

    def test_proposal_must_fit_the_case(self):
        rec = self.new()
        out = rec.log_model_output(route_record_id="r", model_id="m", model_version="v", prompt="p", output="o")
        other = parse_proposal(read("proposal_valid.txt").replace("FAKE-001", "FAKE-002"))
        self.refused("instrument_mismatch", rec.record_proposal, other, from_output=out)
        self.refused("wrong_type", rec.record_proposal, other.value(), from_output=out)

    def test_answer_rules(self):
        rec = self.new()
        good = dict(proposal_digest=make_proposal().digest(), approval_id="appr-1", effect_digest=EFFECT,
                    answered_by="operator-made-up", answer="approve", reason="")
        self.refused("row_order", rec.record_answer, **good)
        out = rec.log_model_output(route_record_id="r", model_id="m", model_version="v", prompt="p",
                                   output=read("proposal_valid.txt"))
        rec.record_proposal(make_proposal(), from_output=out)
        bad_cases = [
            ("digest_mismatch", {"proposal_digest": "sha256:" + "0" * 64}),
            ("row_invalid", {"answer": "maybe"}), ("row_invalid", {"answer": "APPROVE"}),
            ("row_invalid", {"answer": True}), ("row_invalid", {"answered_by": ""}),
            ("row_invalid", {"answered_by": "Operator"}), ("row_invalid", {"answered_by": None}),
            ("row_invalid", {"effect_digest": "ab" * 32}), ("row_invalid", {"effect_digest": "sha256:" + "AB" * 32}),
            ("row_invalid", {"approval_id": ""}), ("row_invalid", {"reason": "caf" + chr(0xE9)}),
            ("row_invalid", {"reason": "x" * 2001}), ("row_invalid", {"reason": "a\x00"}),
            ("row_invalid", {"reason": 1}),
        ]
        for code, change in bad_cases:
            with self.subTest(change=repr(change)[:50]):
                self.refused(code, rec.record_answer, **dict(good, **change))
        self.assertFalse(rec.final)
        rec.record_answer(**good)
        self.assertTrue(rec.final)

    def test_model_output_fields(self):
        rec = self.new()
        good = dict(route_record_id="r", model_id="m", model_version="v", prompt="p", output="o")
        for code, change in (("wrong_type", {"prompt": b"p"}), ("wrong_type", {"output": None}),
                             ("wrong_type", {"prompt": StrSub("p")}),
                             ("text_invalid", {"prompt": "a" + chr(0xD800)}),
                             ("text_invalid", {"output": chr(0xDFFF)}),
                             ("too_large", {"output": "x" * ((1 << 20) + 1)}),
                             ("row_invalid", {"model_id": ""}), ("row_invalid", {"model_version": "a b"}),
                             ("row_invalid", {"model_id": 1}), ("row_invalid", {"route_record_id": "R"})):
            with self.subTest(code=code, change=repr(change)[:40]):
                self.refused(code, rec.log_model_output, **dict(good, **change))
        self.assertEqual(len(rec.rows()), 1)

    def test_clock(self):
        rec = self.new()
        for value, code in ((1.5, "clock_invalid"), (True, "clock_invalid"), (-1, "clock_invalid"),
                            (2**53, "clock_invalid"), (None, "clock_invalid"), (0, "clock_regressed")):
            with self.subTest(value=repr(value)):
                rec._clock = lambda v=value: v
                self.refused(code, rec.log_model_output, route_record_id="r", model_id="m",
                             model_version="v", prompt="p", output="o")
        self.refused("clock_invalid", DecisionRecord.create, self.dir, make_case(), clock=None)

    def test_a_refused_append_writes_nothing(self):
        rec = self.new()
        before = self.raw()
        self.refused("row_order", rec.record_proposal, make_proposal(), from_output=0)
        self.assertEqual(self.raw(), before)


class DecisionRecordTamper(RecordTestCase):
    def test_edited_byte_in_any_row_but_the_last_is_refused(self):
        self.full()
        data = self.raw()
        edited = data.replace(b'"route_record_id":"route-rec-made-up-1"', b'"route_record_id":"route-rec-made-up-2"')
        self.assertNotEqual(edited, data)
        self.write_raw(edited)
        self.refused("chain_broken", self.reopen)

    def test_an_edit_of_the_last_row_is_refused_by_the_head(self):
        # No later row names the last row, so the chain alone cannot see an edit of it; the
        # head file the record keeps (and a head a caller kept elsewhere) can.
        rec = self.full()
        head = rec.head()
        data = self.raw()
        edited = data.replace(b'"answer":"approve"', b'"answer":"veto"')
        self.assertNotEqual(edited, data)
        self.write_raw(edited)
        self.refused("head_mismatch", self.reopen)  # round 1: the persisted head sees it
        self.refused("head_mismatch", DecisionRecord.open, self.dir, "case-0001-made-up",
                     clock=self.clock, expect_head=head)
        self.write_raw(data)
        self.assertEqual(DecisionRecord.open(self.dir, "case-0001-made-up", clock=self.clock,
                                             expect_head=head).rows()[3]["body"]["answer"], "approve")
        for bad in ("", "sha256:" + "0" * 63, None, 1):
            with self.subTest(bad=repr(bad)):
                if bad is None:
                    continue
                self.refused("invalid_value", DecisionRecord.open, self.dir, "case-0001-made-up",
                             clock=self.clock, expect_head=bad)

    def test_edited_row_with_recomputed_digest_breaks_the_next_row(self):
        self.full()
        lines = self.raw().split(b"\n")
        lines[2] = lines[2].replace(b'"conviction_bps":2500', b'"conviction_bps":9999')
        self.write_raw(b"\n".join(lines))
        with self.assertRaises(Refused) as ctx:
            self.reopen()
        self.assertIn(ctx.exception.code, ("row_invalid", "chain_broken"))

    def test_removed_reordered_appended_and_torn_rows(self):
        self.full()
        lines = self.raw().split(b"\n")[:-1]
        variants = {
            "removed": b"\n".join(lines[:1] + lines[2:]) + b"\n",
            "reordered": b"\n".join([lines[0], lines[2], lines[1], lines[3]]) + b"\n",
            "duplicated": b"\n".join(lines + [lines[3]]) + b"\n",
            "first_removed": b"\n".join(lines[1:]) + b"\n",
            "garbage": b"\n".join(lines) + b"\n{}\n",
            "non_canonical": b"\n".join(lines).replace(b'{"at_ms"', b'{ "at_ms"', 1) + b"\n",
        }
        for name, data in variants.items():
            with self.subTest(name=name):
                self.assertNotEqual(data, b"\n".join(lines) + b"\n")
                self.write_raw(data)
                with self.assertRaises(Refused):
                    self.reopen()
        self.write_raw(b"\n".join(lines))
        self.refused("record_torn", self.reopen)
        self.write_raw(b"")
        self.refused("record_empty", self.reopen)

    def rechain(self, rows):
        """Write ``rows`` as a record with a correct chain: what a forger who recomputes digests writes."""
        prev, out = GENESIS, b""
        for i, row in enumerate(rows):
            row = dict(row, seq=i, prev=prev)
            line = canonical(row) + b"\n"
            prev = "sha256:" + hashlib.sha256(line).hexdigest()
            out += line
        self.write_raw(out)

    def test_a_rechained_forgery_is_still_held_to_the_row_rules(self):
        self.full()
        rows = self.reopen().rows()
        self.rechain(rows)
        self.assertEqual(self.reopen().rows(), rows)  # the forger's tool is faithful
        forgeries = {
            "answer names another model version": ("row_invalid", 3, ("model", "model_version"), "other@1"),
            "answer names another prompt": ("row_invalid", 3, ("model", "prompt_sha256"), "0" * 64),
            "answer for another proposal": ("digest_mismatch", 3, ("proposal_digest",), "sha256:" + "1" * 64),
            "answer is neither approve nor veto": ("row_invalid", 3, ("answer",), "approve_all"),
            "proposal carries a quantity": ("quantity_forbidden", 2, ("proposal", "quantity"), 5),
            "proposal digest not its proposal's": ("row_invalid", 2, ("proposal_digest",), "sha256:" + "2" * 64),
            "output hash not its text": ("row_invalid", 1, ("output", "sha256"), "3" * 64),
            "case digest not its case": ("row_invalid", 0, ("case_digest",), "sha256:" + "4" * 64),
        }
        for name, (code, index, path, value) in forgeries.items():
            with self.subTest(name=name):
                forged = [json_copy(r) for r in rows]
                target = forged[index]["body"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.rechain(forged)
                self.refused(code, self.reopen)
        forged = [json_copy(r) for r in rows]
        forged.append(dict(forged[3]))
        self.rechain(forged)
        self.refused("record_final", self.reopen)

    def test_record_of_another_case_under_this_name(self):
        self.full()
        other_dir = os.path.join(self._tmp.name, "other")
        os.mkdir(other_dir, 0o700)
        os.chmod(other_dir, 0o700)
        DecisionRecord.create(other_dir, make_case(), clock=self.clock)
        os.rename(os.path.join(other_dir, "case-0001-made-up.jsonl"), os.path.join(other_dir, "case-0002.jsonl"))
        self.refused("row_invalid", DecisionRecord.open, other_dir, "case-0002", clock=self.clock)

    def test_second_writer_or_outside_edit_is_refused_not_interleaved(self):
        rec = self.new()
        other = self.reopen()
        other.log_model_output(route_record_id="r", model_id="m", model_version="v", prompt="p", output="o")
        before = self.raw()
        self.refused("record_changed", rec.log_model_output, route_record_id="r", model_id="m",
                     model_version="v", prompt="p", output="o")
        self.assertEqual(self.raw(), before)

    def test_replaced_file_is_refused(self):
        rec = self.new()
        data = self.raw()
        path = os.path.join(self.dir, "case-0001-made-up.jsonl")
        tmp = os.path.join(self.dir, "replacement")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, data)
        os.close(fd)
        os.replace(tmp, path)  # a new file under the same name, same bytes
        self.refused("record_changed", rec.log_model_output, route_record_id="r", model_id="m",
                     model_version="v", prompt="p", output="o")

    def test_missing_record_and_bad_ids(self):
        self.refused("record_missing", self.reopen)
        for bad in ("../case-0001-made-up", "Case", "", "a/b", None, StrSub("case-0001-made-up")):
            with self.subTest(bad=repr(bad)):
                self.refused("invalid_value", DecisionRecord.open, self.dir, bad, clock=self.clock)

    def test_bad_case_object(self):
        self.refused("wrong_type", DecisionRecord.create, self.dir, make_case().value(), clock=self.clock)


@unittest.skipUnless(POSIX and hasattr(os, "symlink"), "POSIX owners and links")
class DecisionRecordOwnerOnly(RecordTestCase):
    def test_directory_open_to_group_or_others_is_refused(self):
        for mode in (0o750, 0o705, 0o777):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.dir, mode)
                self.refused("directory_not_owner_only", self.new)
        os.chmod(self.dir, 0o700)

    def test_record_readable_by_others_is_refused(self):
        self.new()
        os.chmod(os.path.join(self.dir, "case-0001-made-up.jsonl"), 0o644)
        self.refused("record_not_owner_only", self.reopen)

    def test_symlinks_are_refused(self):
        self.full()
        real = os.path.join(self._tmp.name, "real.jsonl")
        os.rename(os.path.join(self.dir, "case-0001-made-up.jsonl"), real)
        os.symlink(real, os.path.join(self.dir, "case-0001-made-up.jsonl"))
        self.refused("record_not_regular_file", self.reopen)
        link_dir = os.path.join(self._tmp.name, "linkdir")
        os.symlink(self.dir, link_dir)
        self.refused("directory_invalid", DecisionRecord.open, link_dir, "case-0001-made-up", clock=self.clock)

    def test_missing_directory(self):
        self.refused("directory_missing", DecisionRecord.create, os.path.join(self._tmp.name, "nope"),
                     make_case(), clock=self.clock)


@unittest.skipUnless(hasattr(os, "getuid"), "POSIX flock")
class DecisionRecordProcessLock(RecordTestCase):
    def test_an_append_waits_for_another_holder_of_the_file_lock(self):
        import fcntl
        rec = self.new()
        path = os.path.join(self.dir, "case-0001-made-up.jsonl")
        fd = os.open(path, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX)
        done = threading.Event()
        errors = []

        def append():
            try:
                rec.log_model_output(route_record_id="r", model_id="m", model_version="v", prompt="p", output="o")
            except BaseException as exc:  # asserted below
                errors.append(exc)
            done.set()

        t = threading.Thread(target=append)
        t.start()
        try:
            self.assertFalse(done.wait(0.3))  # not yet: the lock is held elsewhere
        finally:
            os.close(fd)
        t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.reopen().rows()), 2)

    def test_a_writer_that_lands_while_waiting_makes_the_wait_end_in_a_refusal(self):
        import fcntl
        rec = self.new()
        other = self.reopen()
        path = os.path.join(self.dir, "case-0001-made-up.jsonl")
        fd = os.open(path, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX)
        result = []

        def append():
            try:
                rec.log_model_output(route_record_id="r", model_id="m", model_version="v", prompt="p", output="o")
                result.append("written")
            except Refused as exc:
                result.append(exc.code)

        t = threading.Thread(target=append)
        t.start()
        self.assertEqual(result, [])
        os.close(fd)
        t.join()
        # Whichever got the lock first wrote; the other must be refused, never interleaved.
        try:
            other.log_model_output(route_record_id="r2", model_id="m", model_version="v", prompt="p", output="o")
            second = "written"
        except Refused as exc:
            second = exc.code
        self.assertEqual(sorted(result + [second]), ["record_changed", "written"])
        self.assertEqual(len(self.reopen().rows()), 2)


class DecisionRecordThreads(RecordTestCase):
    def test_concurrent_appends_on_one_handle_keep_one_chain(self):
        rec = self.new()
        errors = []
        start = threading.Barrier(8)

        def worker(i):
            try:
                start.wait()
                rec.log_model_output(route_record_id=f"r-{i}", model_id="m", model_version="v",
                                     prompt=f"p{i}", output="o")
            except BaseException as exc:  # recorded and asserted below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        rows = self.reopen().rows()
        self.assertEqual([r["seq"] for r in rows], list(range(9)))


if __name__ == "__main__":
    unittest.main()
