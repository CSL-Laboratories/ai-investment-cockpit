"""The kill switch reaches every venue (F44): one halt first, Kalshi, Coinbase and Gemini mocks."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from dataclasses import replace

from cockpit.paper.coinbase import CoinbaseKill
from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, OrderGateway, PaperBook, create_book
from cockpit.paper.gemini import GeminiKill
from cockpit.paper.kalshi import KalshiKill
from cockpit.paper.killswitch import KillSwitch
from cockpit.paper.tests.fakecore import AUTHORITY, FakeCore
from cockpit.paper.tests.test_coinbase import MockCoinbase
from cockpit.paper.tests.test_gateway import INTENT
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

    def _working_orders(self, count: int) -> None:
        core = FakeCore(budget_minor=10**9)
        gw = OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(core),
                          caps=Caps(10**9, 10**6))
        for i in range(count):
            intent = replace(INTENT, intent_id=f"w-{i}")
            approval = gw.request_approval(intent)["approval_id"]
            core.operator_approves(approval)
            gw.submit(intent, approval, AUTHORITY)
        self.assertEqual(self.book.working(), count)

    def _switch(self, halt, venues):
        return KillSwitch(halt, venues, paper_book=self.book)

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
        out = self._switch(halt, venues).kill()
        self.assertEqual(log[0], "halt")
        self.assertTrue(self.book.halted())
        self.assertEqual(out["status"], "confirmed")
        self.assertEqual([v["venue"] for v in out["venues"]], ["paper", "kalshi", "coinbase", "gemini"])
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
        out = self._switch(halt, venues).kill()
        self.assertEqual(out["status"], "pending")
        self.assertEqual([v["status"] for v in out["venues"]],
                         ["confirmed", "pending", "pending", "confirmed", "confirmed"])
        self.assertEqual(out["venues"][1]["detail"]["kill"], "error:RuntimeError")
        self.assertIn("cancel:c1", log)
        self.assertIn("cancel_all", log)

    def test_unreadable_venue_result_is_pending(self) -> None:
        class Odd:
            def kill(self):
                return {"venue": "odd", "status": "ok"}
        out = self._switch(self.book.halt, [Odd()]).kill()
        self.assertEqual((out["status"], out["venues"][1]["status"]), ("pending", "pending"))

    def test_halt_failure_is_pending_and_still_reaches_venues(self) -> None:
        log = []
        _, venues = self._venues(log)
        def broken():
            raise OSError("disk")
        out = self._switch(broken, venues).kill()
        self.assertEqual((out["status"], out["halt"]), ("pending", "error:OSError"))
        self.assertEqual(len(out["venues"]), 4)

    def test_cancel_timeout_with_confirmed_revoke_keeps_the_switch_pending(self) -> None:
        """O70: a venue whose cancel timed out is pending, and so is the switch, whatever its revoke says."""
        cases = (
            ("kalshi", "cancel_all", "revoke_key", "error:TimeoutError",
             lambda halt, log: KalshiKill(halt, FakeTransport(cancel=TimeoutError(), log=log))),
            ("coinbase", "cancel_orders", "revoke_grant", "unconfirmed:1",
             lambda halt, log: CoinbaseKill(halt, self._coinbase_timing_out(log))),
            ("gemini", "cancel_all", "revoke_grant", "error:TimeoutError",
             lambda halt, log: GeminiKill(halt, self._gemini_timing_out(log))),
        )
        for index, (name, cancel_key, revoke_key, detail, make) in enumerate(cases, start=1):
            with self.subTest(venue=name):
                log = []
                halt, venues = self._venues(log)
                venues[index - 1] = make(halt, log)
                out = self._switch(halt, venues).kill()
                venue = out["venues"][index]
                self.assertEqual((venue["venue"], venue["status"], venue[cancel_key], venue[revoke_key]),
                                 (name, "pending", "pending", "confirmed"))
                self.assertEqual(venue["detail"], {cancel_key: detail, "revoke_probe": "unauthorized"})
                self.assertEqual((out["status"], out["halt"]), ("pending", "confirmed"))
                others = [v["status"] for i, v in enumerate(out["venues"]) if i != index]
                self.assertEqual(others, ["confirmed"] * 3)

    @staticmethod
    def _coinbase_timing_out(log):
        venue = MockCoinbase(orders=["c1", "c2"], log=log)
        venue.fail_cancel["c2"] = TimeoutError()
        return venue

    @staticmethod
    def _gemini_timing_out(log):
        venue = MockGemini(orders=["g1"], log=log)
        venue.cancel_error = TimeoutError()
        return venue

    def test_gemini_order_listed_after_a_pending_revoke_keeps_the_switch_pending(self) -> None:
        log = []
        venue = MockGemini(orders=["g1"], log=log)
        venue.revoke_works = False
        switch = self._switch(self.book.halt, [GeminiKill(self.book.halt, venue)])
        out = switch.kill()
        gemini = out["venues"][1]
        self.assertEqual((out["status"], out["halt"], gemini["cancel_all"], gemini["revoke_grant"]),
                         ("pending", "confirmed", "confirmed", "pending"))
        # The grant still worked, so an order could be placed after the first listing.
        venue.open = ["g-new"]
        venue.cancel_leaves = 1  # the venue still lists it after the cancel
        venue.revoke_works = True
        log.clear()
        out = switch.kill()
        gemini = out["venues"][1]
        self.assertEqual(log, ["cancel_all", "list", "revoke", "probe"])
        self.assertEqual(venue.open, ["g-new"])
        self.assertEqual((out["status"], gemini["status"], gemini["cancel_all"], gemini["revoke_grant"]),
                         ("pending", "pending", "pending", "confirmed"))
        self.assertEqual(gemini["detail"], {"cancel_all": "remaining:1", "revoke_probe": "unauthorized"})

    def test_kill_cancels_working_paper_orders(self) -> None:
        """Red-team round 2 finding 1: a confirmed kill leaves no working paper order (O42, K1)."""
        self._working_orders(3)
        log = []
        halt, venues = self._venues(log)
        out = self._switch(halt, venues).kill()
        self.assertEqual(out["status"], "confirmed")
        self.assertEqual(out["venues"][0], {"venue": "paper", "status": "confirmed", "cancelled": 3, "detail": {}})
        self.assertEqual(self.book.working(), 0)
        self.assertEqual([status for *_, status in self.book.orders()], ["cancelled"] * 3)

    def test_paper_cancel_unconfirmed_keeps_the_kill_pending(self) -> None:
        self._working_orders(2)
        book = self.book

        class Stuck:  # cancel reports success but the book still lists working orders
            def cancel_all(self):
                return 2
            def working(self):
                return book.working()
            def halted(self):
                return True

        class Broken:
            def cancel_all(self):
                raise OSError("disk")
            def working(self):
                raise OSError("disk")
            def halted(self):
                return True
        for paper, detail in ((Stuck(), "remaining:2"), (Broken(), "error:OSError")):
            with self.subTest(detail=detail):
                out = KillSwitch(self.book.halt, [], paper_book=paper).kill()
                self.assertEqual((out["status"], out["venues"][0]["status"]), ("pending", "pending"))
                self.assertEqual(out["venues"][0]["detail"]["cancel_orders"], detail)
        # A retry against the real book cancels them and confirms.
        out = self._switch(self.book.halt, []).kill()
        self.assertEqual((out["status"], out["venues"][0]["cancelled"]), ("confirmed", 2))

    def test_paper_book_is_required(self) -> None:
        with self.assertRaises(TypeError):
            KillSwitch(self.book.halt, [])

    def test_halt_confirms_only_when_this_book_reads_halted(self) -> None:
        """A halt callable that returns is not confirmation. The book read is."""
        class Quiet:
            def __init__(self, halted):
                self._halted = halted
            def cancel_all(self):
                return 0
            def working(self):
                return 0
            def halted(self):
                return self._halted

        for read, halt in ((False, "not_halted"), (1, "not_halted"), (True, "confirmed")):
            with self.subTest(read=read):
                out = KillSwitch(lambda: None, [], paper_book=Quiet(read)).kill()
                status = "confirmed" if halt == "confirmed" else "pending"
                self.assertEqual((out["status"], out["halt"]), (status, halt))

        other_dir = tempfile.mkdtemp(prefix="cockpit-kill-other-")
        self.addCleanup(shutil.rmtree, other_dir, True)
        other_path = os.path.join(other_dir, "other.db")
        create_book(other_path)
        other = PaperBook(other_path)
        self.addCleanup(other.close)
        out = KillSwitch(other.halt, [], paper_book=self.book).kill()
        self.assertTrue(other.halted())
        self.assertFalse(self.book.halted())
        self.assertEqual((out["status"], out["halt"]), ("pending", "not_halted"))

        out = KillSwitch(self.book.halt, [], paper_book=self.book).kill()
        self.assertTrue(self.book.halted())
        self.assertEqual((out["status"], out["halt"]), ("confirmed", "confirmed"))


if __name__ == "__main__":
    unittest.main()
