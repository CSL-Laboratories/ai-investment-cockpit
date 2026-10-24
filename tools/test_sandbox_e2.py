#!/usr/bin/env python3
"""Unit tests for D10 E2 reconstruct-never-copy filter.

Covers drop classes C2/C3 (and C4–C7), CR, non-UTF-8, overlong, ANSI, a line
starting with ``::``, all-agree CONFLICT, empty capture, and the U2 residual
that a lone forged full-match stays observed. Origin stays unattributed.

Run: python3 -X utf8 -B tools/test_sandbox_e2.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "ci"
WORKFLOW = ROOT / ".github" / "workflows" / "claude.yml"
PIN_PATH = SCRIPTS / "sandbox_debug_filter.pin"
FILTER_PATH = SCRIPTS / "sandbox_debug_filter.py"


def load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


filt = load("sandbox_debug_filter")

C2 = "[SandboxDebug] Starting HTTP bridge: socat TCP-LISTEN:1234,bind=127.0.0.1,fork UNIX-CONNECT:/tmp/csl.sock"
C3 = "[SandboxDebug] proxy connect 169.254.169.254:80 via http://user:pass@127.0.0.1:1080"
C4 = "[SandboxDebug] Parent proxy configured: http://user:token@proxy.internal:3128"
C5 = '[SandboxDebug] [credential-mask] env var "CLAUDE_CODE_OAUTH_TOKEN"'
C6 = "[SandboxDebug] sandbox error: EACCES: permission denied, open '/home/runner/.ssh/id_rsa'"
C7 = "[SandboxDebug] Original command: cat /etc/passwd"
ANSI_WRAP = "\x1b[31m" + filt.MATCH_PREFIX + "network" + filt.MATCH_SUFFIX
WORKFLOW_CMD = "::warning::" + filt.MATCH_PREFIX + "network" + filt.MATCH_SUFFIX


def matching(tokens) -> str:
    return filt.MATCH_PREFIX + ", ".join(tokens) + filt.MATCH_SUFFIX


def capture(*lines: str, extra: bytes = b"") -> bytes:
    body = "".join(line + "\n" for line in lines).encode("utf-8")
    return body + extra


class FilterPinTests(unittest.TestCase):
    def test_source_sha256_matches_pin_file(self) -> None:
        expected = PIN_PATH.read_text(encoding="ascii").splitlines()[-1].strip()
        got = hashlib.sha256(FILTER_PATH.read_bytes()).hexdigest()
        self.assertEqual(got, expected)
        self.assertEqual(len(expected), 64)


class FilterDropClassTests(unittest.TestCase):
    def test_c2_socat_bridge_is_dropped(self) -> None:
        result = filt.filter_capture(capture(C2))
        self.assertEqual(result.status, filt.STATUS_NOT_OBSERVED)
        self.assertGreaterEqual(result.dropped, 1)
        self.assertIsNone(result.reconstructed)

    def test_c3_host_port_and_c4_proxy_url_are_dropped(self) -> None:
        result = filt.filter_capture(capture(C3, C4))
        self.assertEqual(result.status, filt.STATUS_NOT_OBSERVED)
        self.assertGreaterEqual(result.dropped, 2)

    def test_c5_c6_c7_are_dropped(self) -> None:
        result = filt.filter_capture(capture(C5, C6, C7))
        self.assertEqual(result.status, filt.STATUS_NOT_OBSERVED)

    def test_cr_skip_does_not_veto_the_rest(self) -> None:
        good = matching(("network", "filesystem"))
        result = filt.filter_capture(capture(good) + b"forged\r" + good.encode() + b"\n")
        self.assertEqual(result.status, filt.STATUS_OBSERVED)
        self.assertEqual(result.cr, 1)
        self.assertEqual(result.reconstructed, filt.reconstruct_line(("network", "filesystem")))

    def test_non_utf8_skip_does_not_veto_the_rest(self) -> None:
        good = matching(("env",))
        result = filt.filter_capture(capture(good) + b"\xff\xfe not utf8\n")
        self.assertEqual(result.status, filt.STATUS_OBSERVED)
        self.assertEqual(result.bad_utf8, 1)

    def test_overlong_line_is_skipped_even_if_it_looks_like_a_wrap(self) -> None:
        good = matching(("network",))
        over = (matching(("filesystem",)) + ("x" * 600)).encode("utf-8")
        self.assertGreater(len(over), filt.MAX_LINE)
        result = filt.filter_capture(capture(good) + over + b"\n")
        self.assertEqual(result.status, filt.STATUS_OBSERVED)
        self.assertEqual(result.tokens, frozenset({"network"}))
        self.assertGreaterEqual(result.overlong, 1)

    def test_ansi_csi_does_not_match_and_is_dropped(self) -> None:
        result = filt.filter_capture(capture(ANSI_WRAP))
        self.assertEqual(result.status, filt.STATUS_NOT_OBSERVED)
        self.assertGreaterEqual(result.dropped, 1)

    def test_line_starting_with_workflow_command_is_dropped(self) -> None:
        result = filt.filter_capture(capture(WORKFLOW_CMD, "::stop-commands::abc"))
        self.assertEqual(result.status, filt.STATUS_NOT_OBSERVED)
        self.assertFalse(any(line.startswith("::") for line in (result.reconstructed or "",)))

    def test_reconstruct_never_copies_ansi_or_payload(self) -> None:
        mixed = "\x1b[0m" + matching(("network", "env"))
        result = filt.filter_capture(capture(mixed, matching(("network", "env"))))
        self.assertEqual(result.status, filt.STATUS_OBSERVED)
        self.assertNotIn("\x1b", result.reconstructed or "")
        self.assertEqual(result.reconstructed, filt.reconstruct_line(("network", "env")))

    def test_empty_capture_is_not_observed(self) -> None:
        result = filt.filter_capture(b"")
        self.assertEqual(result.status, filt.STATUS_NOT_OBSERVED)
        self.assertIsNone(result.reconstructed)


class FilterAgreeTests(unittest.TestCase):
    def test_all_agree_upgrade_is_conflict(self) -> None:
        genuine = matching(("network", "filesystem"))
        upgraded = matching(("network", "filesystem", "env", "seccomp(unix-block)"))
        result = filt.filter_capture(capture(genuine, C2, upgraded))
        self.assertEqual(result.status, filt.STATUS_CONFLICT)
        self.assertIsNone(result.reconstructed)
        self.assertEqual(result.matches, 2)

    def test_lone_forged_full_match_stays_observed(self) -> None:
        # U2 residual: the filter does not detect injection of a lone full-match.
        forged = matching(("network", "filesystem", "env", "seccomp(unix-block)"))
        result = filt.filter_capture(capture(C2, C3, forged))
        self.assertEqual(result.status, filt.STATUS_OBSERVED)
        self.assertTrue(result.positive_evidence)
        self.assertEqual(
            result.reconstructed,
            filt.reconstruct_line(
                ("network", "filesystem", "env", "seccomp(unix-block)")
            ),
        )

    def test_empty_restrictions_is_observed_but_not_positive_evidence(self) -> None:
        empty = matching(())
        result = filt.filter_capture(capture(empty))
        self.assertEqual(result.status, filt.STATUS_OBSERVED)
        self.assertFalse(result.positive_evidence)
        self.assertEqual(result.reconstructed, filt.reconstruct_line(()))

    def test_wrong_token_order_is_not_a_match(self) -> None:
        line = filt.MATCH_PREFIX + "filesystem, network" + filt.MATCH_SUFFIX
        result = filt.filter_capture(capture(line))
        self.assertEqual(result.status, filt.STATUS_NOT_OBSERVED)

    def test_duplicate_token_is_not_a_match(self) -> None:
        line = filt.MATCH_PREFIX + "network, network" + filt.MATCH_SUFFIX
        result = filt.filter_capture(capture(line))
        self.assertEqual(result.status, filt.STATUS_NOT_OBSERVED)

    def test_sixteen_subsets_round_trip_from_constants(self) -> None:
        subsets = filt.all_subsets()
        self.assertEqual(len(subsets), 16)
        seen = set()
        for tokens in subsets:
            line = matching(tuple(name for name in filt.TOKEN_ORDER if name in tokens))
            result = filt.filter_capture(capture(line))
            self.assertEqual(result.status, filt.STATUS_OBSERVED, tokens)
            self.assertEqual(result.tokens, tokens)
            self.assertEqual(result.reconstructed, filt.reconstruct_line(tokens))
            self.assertFalse(result.reconstructed.startswith("::"))
            seen.add(result.reconstructed)
        self.assertEqual(len(seen), 16)

    def test_nul_padded_cap_does_not_copy_into_output(self) -> None:
        result = filt.filter_capture(capture(matching(("env",))))
        self.assertEqual(result.status, filt.STATUS_OBSERVED)
        self.assertNotIn("\0", result.reconstructed or "")
        self.assertEqual(len(filt.capture_buffer(b"abc")), filt.CAP_BYTES)


class FilterDoesNotWireDebugTests(unittest.TestCase):
    def test_workflow_does_not_set_srt_debug(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("\n          SRT_DEBUG:", text)
        self.assertNotIn("show_full_output: true", text)


if __name__ == "__main__":
    unittest.main()
