"""The CLI is a stateless client: the transcript file is the only state."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

from cockpit_spine import cli
from cockpit_spine.audit import verify_chain
from cockpit_spine.fake_source import (
    FAIL_PROMPT, QUARANTINE_PROMPT, FakeProposalSource, proposal_id_for,
)
from cockpit_spine.tests.support.scripted import counter_clock


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="cli-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.log = self.dir / "t.jsonl"
        self.clock = counter_clock()

    def run_cli(self, *args: str, as_json: bool = False) -> tuple[int, str]:
        out = io.StringIO()
        argv = ["--log", str(self.log), *(["--json"] if as_json else []), *args]
        code = cli.main(argv, out=out, clock=self.clock)
        return code, out.getvalue()

    def json_of(self, *args: str) -> tuple[int, dict]:
        code, text = self.run_cli(*args, as_json=True)
        return code, json.loads(text)

    def open(self, mode: str = "paper") -> None:
        self.assertEqual(0, self.run_cli("open", "--session-id", "s-1", "--mode", mode)[0])

    def records(self) -> list[dict]:
        return cli.read_log(self.log)


def digest_of(response: dict) -> str:
    return next(i["value"] for i in response["lines"] if i["label"] == "confirm digest")


class FlowTests(CliCase):
    def test_open_propose_approve_veto_stop_end_to_end(self) -> None:
        self.open()
        _, first = self.json_of("propose", "--request-id", "r1")
        _, second = self.json_of("propose", "--request-id", "r2")
        self.assertTrue(first["ok"] and second["ok"])
        status = self.json_of("status")[1]
        digests = [i["value"] for i in status["lines"] if i["label"] == "confirm digest"]
        ids = [i["value"] for i in status["lines"] if i["label"] == "proposal"]
        self.assertEqual(["prop-r1", "prop-r2"], ids)
        code, approved = self.json_of("approve", "--proposal-id", ids[0], "--operator", "op",
                                      "--confirm-digest", digests[0])
        self.assertEqual((0, "approved"), (code, approved["code"]))
        code, vetoed = self.json_of("veto", "--proposal-id", ids[1], "--operator", "op",
                                    "--reason", "no")
        self.assertEqual((0, "vetoed"), (code, vetoed["code"]))
        code, stopped = self.json_of("stop", "--operator", "op", "--reason", "end")
        self.assertEqual((0, "stopped"), (code, stopped["code"]))
        code, after = self.json_of("propose", "--request-id", "r3")
        self.assertEqual((2, "session_stopped"), (code, after["code"]))
        verify_chain(self.records())
        self.assertEqual(
            ["session_opened", "proposal_requested", "proposal_received", "proposal_requested",
             "proposal_received", "proposal_approved", "proposal_vetoed", "session_stopped",
             "command_refused"],
            [r["kind"] for r in self.records()])

    def test_text_output_is_the_rendered_lines(self) -> None:
        self.open()
        code, text = self.run_cli("status")
        self.assertEqual(0, code)
        self.assertTrue(text.startswith("ok: status\n"))
        self.assertIn("state: running", text)
        self.assertIn("[projection:x2-fake-driver-cells-accepted@sha256:", text)

    def test_scripted_failure_paths(self) -> None:
        self.open()
        code, failed = self.json_of("propose", "--request-id", "r1", "--prompt", FAIL_PROMPT)
        self.assertEqual((2, False), (code, failed["ok"]))
        code, quarantined = self.json_of("propose", "--request-id", "r2",
                                         "--prompt", QUARANTINE_PROMPT)
        self.assertEqual(2, code)
        self.assertFalse([i for i in quarantined["lines"] if i["label"] == "proposal"])

    def test_a_refusal_exits_2_and_is_audited(self) -> None:
        self.open()
        code, response = self.json_of("approve", "--proposal-id", "nope", "--operator", "op",
                                      "--confirm-digest", "x")
        self.assertEqual((2, "unknown_proposal"), (code, response["code"]))
        self.assertEqual("command_refused", self.records()[-1]["kind"])

    def test_status_and_audit_do_not_change_the_log(self) -> None:
        self.open()
        self.json_of("propose", "--request-id", "r1")
        before = self.log.read_bytes()
        self.run_cli("status")
        self.run_cli("audit")
        self.assertEqual(before, self.log.read_bytes())

    def test_each_invocation_replays_to_the_same_head(self) -> None:
        self.open()
        head = self.json_of("propose", "--request-id", "r1")[1]["head"]
        self.assertEqual(head, self.json_of("status")[1]["head"])

    def test_an_operator_name_with_a_space_is_refused_and_the_view_keeps_working(self) -> None:
        self.open()
        digest = digest_of(self.json_of("propose", "--request-id", "r1")[1])
        before = len(self.records())
        for args in (
            ("veto", "--proposal-id", "prop-1", "--reason", "no"),
            ("approve", "--proposal-id", "prop-1", "--confirm-digest", digest),
            ("stop", "--reason", "halt"),
        ):
            code, response = self.json_of(args[0], "--operator", "Samir Shah", *args[1:])
            self.assertEqual((2, "operator_invalid"), (code, response["code"]))
        self.assertEqual(before + 3, len(self.records()))
        self.assertEqual({"command_refused"}, {r["kind"] for r in self.records()[before:]})
        code, status = self.json_of("status")
        self.assertEqual((0, True, 0), (code, status["ok"], status["withheld"]))
        self.assertEqual("running", next(i["value"] for i in status["lines"]
                                         if i["label"] == "state"))

    def test_a_spaced_session_id_cannot_be_opened(self) -> None:
        code = self.run_cli("open", "--session-id", "my session", "--mode", "paper")[0]
        self.assertEqual(3, code)
        self.assertFalse(self.log.exists())


class UnusableTests(CliCase):
    def test_help_exits_zero_and_bad_arguments_still_exit_three(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(0, cli.main(["--help"], out=io.StringIO()))
            self.assertEqual(0, cli.main(["--log", str(self.log), "stop", "--help"],
                                         out=io.StringIO()))
            self.assertEqual(3, cli.main(["--log"], out=io.StringIO()))

    def test_opening_twice_or_with_an_auto_mode_is_refused(self) -> None:
        self.assertEqual(3, self.run_cli("open", "--session-id", "s", "--mode", "auto_full")[0])
        self.assertFalse(self.log.exists())
        self.open()
        before = self.log.read_bytes()
        self.assertEqual(3, self.run_cli("open", "--session-id", "s", "--mode", "paper")[0])
        self.assertEqual(before, self.log.read_bytes())

    def test_missing_or_tampered_transcripts_are_refused_and_left_alone(self) -> None:
        self.assertEqual(3, self.run_cli("status")[0])
        self.open()
        self.json_of("propose", "--request-id", "r1")
        original = self.log.read_text()
        self.log.write_text(original.replace("paper", "read_only"))
        tampered = self.log.read_bytes()
        code, text = self.run_cli("status")
        self.assertEqual(3, code)
        self.assertTrue(text.startswith("unusable:"))
        self.assertEqual(tampered, self.log.read_bytes())
        self.log.write_text("not json\n")
        self.assertEqual(3, self.run_cli("status")[0])
        self.log.write_text("")
        self.assertEqual(3, self.run_cli("status")[0])

    def test_bad_arguments_exit_3(self) -> None:
        self.assertEqual(3, cli.main(["--log", str(self.log), "nonsense"], out=io.StringIO()))
        self.assertEqual(3, cli.main([], out=io.StringIO()))

    def test_a_write_failure_is_an_unusable_log(self) -> None:
        parent = self.dir / "not-a-directory"
        parent.write_text("x")
        code, text = self._run_at(
            parent / "t.jsonl", "open", "--session-id", "s-1", "--mode", "paper")
        self.assertEqual(3, code)
        self.assertTrue(text.startswith("unusable: OSError"))
        self.assertEqual([], list(self.dir.glob("*.tmp")))

    def test_long_request_ids_do_not_share_a_proposal_id(self) -> None:
        left = "a" * 59 + "x"
        right = "a" * 59 + "y"
        self.assertNotEqual(proposal_id_for(left), proposal_id_for(right))
        self.assertLessEqual(len(proposal_id_for(left)), 64)
        self.assertEqual("prop-r1", proposal_id_for("r1"))

    def test_a_short_id_equal_to_a_long_ids_digest_prefix_does_not_collide(self) -> None:
        """Devin 4382680635: the readable and digest forms must not share a namespace."""

        long = "x" * 60
        short = hashlib.sha256(long.encode("utf-8")).hexdigest()[:59]
        self.assertNotEqual(proposal_id_for(short), proposal_id_for(long))
        self.open()
        _, first = self.json_of("propose", "--request-id", short)
        _, second = self.json_of("propose", "--request-id", long)
        self.assertEqual(("proposal_received", "proposal_received"),
                         (first["code"], second["code"]))
        received = [r["data"]["payload"]["proposal_id"] for r in self.records()
                    if r["kind"] == "proposal_received"]
        self.assertEqual([proposal_id_for(short), proposal_id_for(long)], received)

    def test_an_overlapping_stop_cannot_drop_the_proposal(self) -> None:
        self.open()
        started = threading.Event()
        release = threading.Event()

        class Gate:
            adapter_kind = "fake-inline"

            def fetch(self, *, request_id: str, mode: str, prompt: str):
                started.set()
                release.wait(5)
                return FakeProposalSource().fetch(
                    request_id=request_id, mode=mode, prompt=prompt)

        codes: dict[str, int] = {}

        def propose() -> None:
            codes["propose"] = cli.main(
                ["--log", str(self.log), "propose", "--request-id", "r1", "--prompt", "p"],
                source_factory=Gate, out=io.StringIO(), clock=self.clock)

        def stop() -> None:
            codes["stop"] = cli.main(
                ["--log", str(self.log), "stop", "--operator", "op", "--reason", "end"],
                out=io.StringIO(), clock=self.clock)

        first = threading.Thread(target=propose)
        second = threading.Thread(target=stop)
        first.start()
        self.assertTrue(started.wait(5))
        second.start()
        time.sleep(0.3)
        release.set()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual((0, 0), (codes["propose"], codes["stop"]))
        kinds = [record["kind"] for record in self.records()]
        self.assertLess(kinds.index("proposal_received"), kinds.index("session_stopped"))

    def test_a_hung_source_cannot_block_stop_and_times_out_to_watch_only(self) -> None:
        """Codex 4383700919: a source that never returns must not hold stop off the lock.

        The fetch deadline records a refusal fail-closed to watch_only. Stop, started
        while that fetch is still blocked, persists session_stopped inside the lock
        deadline. The source is never released.
        """

        self.open()
        started = threading.Event()
        fetch_deadline = 0.4
        lock_deadline = 2.0

        class Hung:
            adapter_kind = "fake-inline"

            def fetch(self, *, request_id: str, mode: str, prompt: str):
                started.set()
                threading.Event().wait()

        codes: dict[str, int] = {}
        propose_out = io.StringIO()

        def propose() -> None:
            codes["propose"] = cli.main(
                ["--log", str(self.log), "--json", "propose", "--request-id", "r1",
                 "--prompt", "p"],
                source_factory=Hung, out=propose_out, clock=self.clock,
                fetch_deadline_seconds=fetch_deadline,
                lock_deadline_seconds=lock_deadline)

        def stop() -> None:
            codes["stop"] = cli.main(
                ["--log", str(self.log), "stop", "--operator", "op", "--reason", "end"],
                out=io.StringIO(), clock=self.clock, lock_deadline_seconds=lock_deadline)

        began = time.monotonic()
        first = threading.Thread(target=propose)
        second = threading.Thread(target=stop)
        first.start()
        self.assertTrue(started.wait(2))
        second.start()
        first.join(5)
        second.join(5)
        self.assertLess(time.monotonic() - began, lock_deadline)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual((2, 0), (codes["propose"], codes["stop"]))
        body = json.loads(propose_out.getvalue())
        self.assertEqual((False, "source_timeout"), (body["ok"], body["code"]))
        self.assertFalse([item for item in body["lines"] if item["label"] == "proposal"])
        records = self.records()
        refusal = next(record for record in records if record["kind"] == "proposal_refused")
        self.assertEqual(
            ("watch_only", False, "source_timeout"),
            (refusal["data"]["fail_closed_to"], refusal["data"]["quarantined"],
             refusal["data"]["reason"]))
        kinds = [record["kind"] for record in records]
        self.assertNotIn("proposal_received", kinds)
        self.assertNotIn("proposal_approved", kinds)
        self.assertLess(kinds.index("proposal_refused"), kinds.index("session_stopped"))
        verify_chain(records)

    def test_lock_acquisition_gives_up_without_writing(self) -> None:
        # The default lock wait outlasts one fetch, so stop can land after a timeout.
        self.assertGreater(cli.LOCK_DEADLINE_SECONDS, cli.FETCH_DEADLINE_SECONDS)
        self.open()
        before = self.log.read_bytes()
        held = cli._lock(self.log)
        self.addCleanup(cli._unlock, held)
        out = io.StringIO()
        began = time.monotonic()
        code = cli.main(
            ["--log", str(self.log), "approve", "--proposal-id", "prop-r1",
             "--operator", "op", "--confirm-digest", "x"],
            out=out, clock=self.clock, lock_deadline_seconds=0.25)
        self.assertLess(time.monotonic() - began, 2.0)
        self.assertEqual(3, code)
        self.assertEqual("unusable: lock_timeout\n", out.getvalue())
        self.assertEqual(before, self.log.read_bytes())
        self.assertNotIn("proposal_approved", [record["kind"] for record in self.records()])

    def test_without_fcntl_the_cli_imports_and_locks_through_msvcrt(self) -> None:
        """Native Windows has no ``fcntl``: the CLI must still import, help and lock."""

        script = textwrap.dedent(f"""
            import io, sys, types
            sys.modules["fcntl"] = None
            calls = []
            msvcrt = types.ModuleType("msvcrt")
            msvcrt.LK_NBLCK, msvcrt.LK_UNLCK = 2, 0
            msvcrt.locking = lambda fd, mode, size: calls.append((mode, size))
            sys.modules["msvcrt"] = msvcrt
            from pathlib import Path
            from cockpit_spine import cli
            assert cli.fcntl is None
            assert cli.main(["--help"], out=io.StringIO()) == 0
            handle = cli._lock(Path({str(self.log)!r}))
            cli._unlock(handle)
            assert handle.closed
            assert calls == [(2, 1), (0, 1)], calls
            print("ok")
        """)
        done = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-c", script],
            capture_output=True, text=True, timeout=60,
            cwd=str(Path(cli.__file__).resolve().parents[1]),
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("ok", done.stdout)

    def _run_at(self, log: Path, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        code = cli.main(["--log", str(log), *args], out=out, clock=self.clock)
        return code, out.getvalue()


if __name__ == "__main__":
    unittest.main(verbosity=2)
