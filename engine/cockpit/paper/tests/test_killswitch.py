"""The kill switch reaches every venue (F44): one halt first, Kalshi, Coinbase and Gemini mocks."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from cockpit.paper.coinbase import CoinbaseKill
from cockpit.paper.gateway import PaperBook, create_book
from cockpit.paper.gemini import GeminiKill
from cockpit.paper.kalshi import KalshiKill
from cockpit.paper.killswitch import KillSwitch
from cockpit.paper.tests.test_coinbase import MockCoinbase
from cockpit.paper.tests.test_gemini import MockGemini
from cockpit.paper.tests.test_kalshi import FakeTransport


class KillSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-kill-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "book.db")
        create_book(path)
        self.book = PaperBook(path)
        self.addCleanup(self.book.close)

    def _venues(self, log):
        def halt():
            log.append("halt")
            self.book.halt()
        kalshi = KalshiKill(halt, FakeTransport(log=log))
        coinbase = CoinbaseKill(halt, MockCoinbase(orders=["c1", "c2"], log=log))
        gemini = GeminiKill(halt, MockGemini(orders=["g1"], log=log))
        return halt, [kalshi, coinbase, gemini]

    def test_halt_first_then_every_venue_confirmed(self) -> None:
        log = []
        halt, venues = self._venues(log)
        out = KillSwitch(halt, venues).kill()
        self.assertEqual(log[0], "halt")
        self.assertTrue(self.book.halted())
        self.assertEqual(out["status"], "confirmed")
        self.assertEqual([v["venue"] for v in out["venues"]], ["kalshi", "coinbase", "gemini"])
        self.assertTrue(all(v["status"] == "confirmed" for v in out["venues"]))
        for op in ("cancel_all", "revoke_api_key", "cancel:c1", "cancel:c2", "revoke"):
            self.assertIn(op, log)

    def test_one_venue_failing_never_skips_the_others(self) -> None:
        log = []
        halt, venues = self._venues(log)

        class Boom:
            def kill(self):
                raise RuntimeError("venue down")
        venues.insert(0, Boom())
        venues[1] = KalshiKill(halt, FakeTransport(cancel=TimeoutError(), log=log))
        out = KillSwitch(halt, venues).kill()
        self.assertEqual(out["status"], "pending")
        self.assertEqual([v["status"] for v in out["venues"]], ["pending", "pending", "confirmed", "confirmed"])
        self.assertEqual(out["venues"][0]["detail"]["kill"], "error:RuntimeError")
        self.assertIn("cancel:c1", log)
        self.assertIn("cancel_all", log)

    def test_unreadable_venue_result_is_pending(self) -> None:
        class Odd:
            def kill(self):
                return {"venue": "odd", "status": "ok"}
        out = KillSwitch(self.book.halt, [Odd()]).kill()
        self.assertEqual((out["status"], out["venues"][0]["status"]), ("pending", "pending"))

    def test_halt_failure_is_pending_and_still_reaches_venues(self) -> None:
        log = []
        _, venues = self._venues(log)
        def broken():
            raise OSError("disk")
        out = KillSwitch(broken, venues).kill()
        self.assertEqual((out["status"], out["halt"]), ("pending", "error:OSError"))
        self.assertEqual(len(out["venues"]), 3)


if __name__ == "__main__":
    unittest.main()
