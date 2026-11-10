"""Typed revoke reply and probe classification (F44), shared by the Coinbase and Gemini kills."""

from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path

from cockpit.paper import revoke
from cockpit.paper.revoke import PROBE_VALUES, RevokeReply, classify_probe

PAPER_DIR = Path(__file__).resolve().parents[2] / "cockpit" / "paper"
NO_AMBIENT = (r"(?m)os\.environ|getenv|\bopen\(|^\s*(import|from)\s+"
              r"(os|socket|ssl|urllib|http|requests|keyring|subprocess)\b")


class RevokeReplyTests(unittest.TestCase):
    def test_reply_is_frozen(self) -> None:
        reply = RevokeReply(200)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            reply.http_status = 401
        self.assertEqual(reply, RevokeReply(http_status=200))

    def test_answered_200_only_for_the_int_200(self) -> None:
        for status, answered in ((200, True), (204, False), (400, False), (401, False), (503, False),
                                 (True, False), ("200", False), (200.0, False), (None, False)):
            with self.subTest(status=status):
                self.assertIs(RevokeReply(status).answered_200, answered)

    def test_the_docstring_says_a_200_is_not_proof(self) -> None:
        doc = " ".join(RevokeReply.__doc__.split())
        self.assertIn("answering 200 is NOT proof that access is gone", doc)
        self.assertIn("RFC 7009", doc)


class ClassifyProbeTests(unittest.TestCase):
    def test_declared_replies_are_returned_as_is(self) -> None:
        self.assertEqual(PROBE_VALUES, ("unauthorized", "authorized"))
        for value in PROBE_VALUES:
            self.assertEqual(classify_probe(lambda value=value: value), value)

    def test_any_other_reply_is_unreadable_and_no_part_of_it_is_stored(self) -> None:
        class Shouty(str):
            pass
        for reply in ("UNAUTHORIZED", "unauthorized ", " unauthorized", "confirmed", "cancelled", "x" * 40, "",
                      Shouty("unauthorized"), b"unauthorized", None, True, 401, {"unauthorized": True},
                      ["unauthorized"], ("unauthorized", "authorized")):
            with self.subTest(reply=reply):
                self.assertEqual(classify_probe(lambda reply=reply: reply), "unreadable_probe")

    def test_an_exception_is_reported_by_its_class_name_only(self) -> None:
        def raises(exc):
            def call():
                raise exc
            return call
        self.assertEqual(classify_probe(raises(TimeoutError("unauthorized"))), "error:TimeoutError")
        self.assertEqual(classify_probe(raises(PermissionError())), "error:PermissionError")
        # Not an Exception: it is not swallowed.
        with self.assertRaises(KeyboardInterrupt):
            classify_probe(raises(KeyboardInterrupt()))

    def test_exports_and_no_ambient_or_network_code(self) -> None:
        self.assertEqual(sorted(revoke.__all__), ["PROBE_VALUES", "RevokeReply", "classify_probe"])
        source = PAPER_DIR.joinpath("revoke.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, NO_AMBIENT)


if __name__ == "__main__":
    unittest.main()
