"""Devin adversarial: a venue kill must not report halt "confirmed" from a no-op halt callable.

GeminiKill / CoinbaseKill / KalshiKill set ``halt = "confirmed"`` when the halt callable merely
returns; there is no verified read-back (KillSwitch, by contrast, confirms on
``paper_book.halted()``). Here the halt callable does nothing and cancel + revoke confirm.
Mocks only, reusing the suite's existing venue mocks.
"""

from __future__ import annotations

import unittest

from cockpit.paper.coinbase import CoinbaseKill
from cockpit.paper.gemini import GeminiKill
from cockpit.paper.kalshi import KalshiKill
from tests.paper.test_coinbase import MockCoinbase
from tests.paper.test_gemini import MockGemini
from tests.paper.test_kalshi import FakeTransport

VENUES = (
    ("gemini", lambda halt: GeminiKill(halt, MockGemini(orders=["g1"])), "cancel_all", "revoke_grant"),
    ("coinbase", lambda halt: CoinbaseKill(halt, MockCoinbase(orders=["c1", "c2"])), "cancel_orders", "revoke_grant"),
    ("kalshi", lambda halt: KalshiKill(halt, FakeTransport()), "cancel_all", "revoke_key"),
)


class HaltConfirmedWithoutEvidenceTests(unittest.TestCase):
    def test_noop_halt_is_not_confirmed(self) -> None:
        for name, make, cancel_key, revoke_key in VENUES:
            with self.subTest(venue=name):
                out = make(lambda: None).kill()
                # Precondition: the other legs confirm, so only the halt leg is in question.
                self.assertEqual((out[cancel_key], out[revoke_key]), ("confirmed", "confirmed"))
                self.assertNotEqual(out["halt"], "confirmed",
                                    "%s reports halt confirmed after a no-op halt callable "
                                    "(no verified halted() read)" % name)
                self.assertNotEqual(out["status"], "confirmed",
                                    "%s reports overall confirmed while nothing was halted" % name)

    def test_noop_halt_stays_unconfirmed_on_retry(self) -> None:
        for name, make, _, _ in VENUES:
            with self.subTest(venue=name):
                kill = make(lambda: None)
                kill.kill()
                out = kill.kill()
                self.assertNotEqual(out["halt"], "confirmed")
                self.assertNotEqual(out["status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
