"""The CLI is a stateless client: the transcript file is the only state."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cockpit_spine import cli
from cockpit_spine.audit import verify_chain
from cockpit_spine.fake_source import FAIL_PROMPT, QUARANTINE_PROMPT
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
