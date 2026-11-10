"""Paper OrderGateway: risk pass, cap, money, halt, cancels, core order of checks, one path.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/paper/tests -t engine -v``.
No provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import multiprocessing
import os
import re
import shutil
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from cockpit.paper.tests.fakecore import AUTHORITY, FakeCore
from cockpit.paper.plugin import build

PAPER_DIR = Path(__file__).resolve().parents[1]
INTENT = Intent(intent_id="i-1", account="paper-acct", instrument="AAPL", side="buy", quantity=10,
                limit_price_minor=10_000, run_id="run-1", connection="conn-a")


def _book_path(test: unittest.TestCase) -> str:
    d = tempfile.mkdtemp(prefix="cockpit-paper-")
    test.addCleanup(shutil.rmtree, d, True)
    path = os.path.join(d, "book.db")
    create_book(path, cash_minor={"paper-acct": 10**15})
    return path


def _submitter(path: str, prefix: str, count: int, out) -> None:
    core = FakeCore(budget_minor=10**12)
    book = PaperBook(path)
    gw = OrderGateway(plugin_id="csl.cockpit.paper", book=book, core=CoreClient(core),
                      caps=Caps(10**9, 10**6))
    done = 0
    for i in range(count):
        intent = replace(INTENT, intent_id="%s-%d" % (prefix, i))
        try:
            approval = gw.request_approval(intent)["approval_id"]
            core.operator_approves(approval)
            gw.submit(intent, approval, AUTHORITY)
            done += 1
        except Refused:
            pass
    book.close()
    out.put(done)


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _book_path(self)
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**9)
        self.gw = OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(self.core),
                               caps=Caps(max_notional_minor=500_000, max_quantity=100))

    def _approved(self, intent: Intent) -> str:
        approval = self.gw.request_approval(intent)["approval_id"]
        self.core.operator_approves(approval)
        return approval

    def _refused(self, code: str, fn, *args) -> None:
        with self.assertRaises(Refused) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.code, code)

    def test_happy_path(self) -> None:
        out = self.gw.submit(INTENT, self._approved(INTENT), AUTHORITY)
        self.assertEqual((out["status"], out["notional_minor"]), ("paper_working", 100_000))
        self.assertEqual(self.book.orders(), [("i-1", 1, "working")])

    def test_core_refusals_stop_the_order(self) -> None:
        self._refused("core:approval_not_granted", self.gw.submit, INTENT,
                      self.gw.request_approval(INTENT)["approval_id"], AUTHORITY)
        approval = self._approved(INTENT)
        self._refused("core:profile_signature_invalid", self.gw.submit, INTENT, approval, {"x": 1})
        self._refused("core:approval_replayed", self.gw.submit, INTENT, approval, AUTHORITY)
        changed = replace(INTENT, quantity=11)
        self._refused("core:approval_digest_mismatch", self.gw.submit, changed, self._approved(INTENT), AUTHORITY)
        self.assertEqual(self.book.orders(), [])

    def test_permission_is_checked_by_the_core_first(self) -> None:
        self.core.permissions = {"read", "propose"}
        self._refused("core:permission_required", self.gw.request_approval, INTENT)
        self._refused("core:permission_required", self.gw.submit, INTENT, "apr_0", AUTHORITY)
        self.assertEqual(self.book.orders(), [])

    def test_split_orders_cannot_dodge_the_profile_budget(self) -> None:
        self.core.budget = 250_000
        results = []
        for i in range(4):
            intent = replace(INTENT, intent_id="s-%d" % i)
            try:
                results.append(self.gw.submit(intent, self._approved(intent), AUTHORITY)["status"])
            except Refused as exc:
                results.append(exc.code)
        self.assertEqual(results, ["paper_working", "paper_working", "core:budget_total_exceeded",
                                   "core:budget_total_exceeded"])

    def test_malformed_money_and_size_rejected_never_clamped(self) -> None:
        bad = {
            "money_invalid": [dict(quantity=q) for q in (0, -5, 1.0, 1e9, float("nan"), float("inf"), True,
                                                       "10", None, 2**60)]
                             + [dict(limit_price_minor=p) for p in (0, -1, 99.5, "100", False, 2**53)],
            "size_cap_quantity": [dict(quantity=101)],
            "size_cap_notional": [dict(quantity=100, limit_price_minor=5_001)],
            "side_invalid": [dict(side="short"), dict(side="BUY")],
            "instrument_invalid": [dict(instrument=""), dict(instrument="aapl"), dict(instrument="A" * 40)],
            "intent_invalid": [dict(intent_id=""), dict(account="a b")],
        }
        for code, changes in bad.items():
            for change in changes:
                with self.subTest(code=code, change=repr(change)):
                    self._refused(code, self.gw.request_approval, replace(INTENT, **change))
        self.assertEqual(self.core.calls, [])  # nothing reached the core
        self._refused("intent_invalid", self.gw.request_approval, {"intent_id": "x"})
        for cap in ((0, 1), (1.5, 1), (10, True), (-1, 5)):
            with self.subTest(cap=cap):
                self.assertRaises(Refused, Caps, *cap)

    def test_in_process_plugin_refuses_non_paper_orders_before_core(self) -> None:
        gateway = build(self.core, self.path, Caps(500_000, 100))
        self.addCleanup(gateway._book.close)
        for change in (dict(venue="kalshi"), dict(venue="kalshi-demo"), dict(venue="coinbase"),
                       dict(venue=""), dict(venue=None), dict(mode="live"), dict(mode="demo")):
            intent = replace(INTENT, **change)
            for stage in ("request", "submit"):
                with self.subTest(change=change, stage=stage):
                    with self.assertRaises(Refused) as ctx:
                        if stage == "request":
                            gateway.request_approval(intent)
                        else:
                            gateway.submit(intent, "not-an-approval", AUTHORITY)
                    self.assertEqual(ctx.exception.code, "paper_only")
                    row = self.book.audit()[-1]
                    self.assertEqual((row["seq"], row["stage"], row["code"]),
                                     (ctx.exception.audit_seq, "risk", "paper_only"))
                    self.assertEqual((row["intent_id"], row["run_id"], row["connection"]),
                                     (intent.intent_id, intent.run_id, intent.connection))
        self.assertEqual(self.core.calls, [])
        self.assertEqual(self.book.orders(), [])
        self.assertEqual(self.book.available_cash_minor(INTENT.account), 10**15)
        out = gateway.submit(INTENT, self._approved(INTENT), AUTHORITY)
        self.assertEqual(out["status"], "paper_working")

    def test_halt_blocks_new_risk_and_allows_cancels(self) -> None:
        self.gw.submit(INTENT, self._approved(INTENT), AUTHORITY)
        second = replace(INTENT, intent_id="i-2")
        approval = self._approved(second)
        self.gw.halt()
        self._refused("order_halted", self.gw.submit, second, approval, AUTHORITY)
        self._refused("order_halted", self.gw.request_approval, replace(INTENT, intent_id="i-3"))
        self.assertEqual(self.gw.cancel_all(), 1)
        self.assertEqual(self.book.orders(), [("i-1", 1, "cancelled")])

    def test_halt_committed_between_risk_pass_and_booking_wins(self) -> None:
        second = replace(INTENT, intent_id="i-2")
        approval = self._approved(second)
        original = self.core.__call__

        def halt_during_core_call(raw: bytes) -> bytes:
            other = PaperBook(self.path)  # another connection, as another process would
            other.halt()
            other.close()
            return original(raw)
        self.gw._core = CoreClient(halt_during_core_call)
        # The halt landed after the charge: a typed leftover charge, never a clean refusal.
        with self.assertRaises(Refused) as ctx:
            self.gw.submit(second, approval, AUTHORITY)
        self.assertEqual((ctx.exception.code, ctx.exception.cause), ("leftover_charge", "order_halted"))
        self.assertEqual(str(ctx.exception), "leftover_charge:order_halted")
        self.assertEqual(self.book.orders(), [])
        self.assertEqual(self.core.spent, 100_000)  # the core charged, and nothing reversed it
        self.assertEqual(self.core.calls.count("effect.authorize"), 1)

    def test_same_intent_booked_during_the_core_call_is_a_leftover_charge(self) -> None:
        approval = self._approved(INTENT)
        original = self.core.__call__
        other_core = FakeCore(budget_minor=10**9)

        def book_same_intent_during_core_call(raw: bytes) -> bytes:
            other = PaperBook(self.path)  # another gateway on the same book, as another process would
            gw = OrderGateway(plugin_id="csl.cockpit.paper", book=other, core=CoreClient(other_core),
                              caps=Caps(max_notional_minor=500_000, max_quantity=100))
            other_approval = gw.request_approval(INTENT)["approval_id"]
            other_core.operator_approves(other_approval)
            gw.submit(INTENT, other_approval, AUTHORITY)
            other.close()
            return original(raw)
        self.gw._core = CoreClient(book_same_intent_during_core_call)
        with self.assertRaises(Refused) as ctx:
            self.gw.submit(INTENT, approval, AUTHORITY)
        self.assertEqual((ctx.exception.code, ctx.exception.cause), ("leftover_charge", "intent_duplicate"))
        self.assertIn("intent_duplicate", str(ctx.exception))
        self.assertEqual(self.book.orders(), [("i-1", 1, "working")])  # only the other gateway's order
        self.assertEqual(self.core.spent, 100_000)  # this core charged for an order that never booked

    def test_clean_refusals_carry_no_cause(self) -> None:
        with self.assertRaises(Refused) as ctx:
            self.gw.request_approval(replace(INTENT, side="short"))
        self.assertEqual((ctx.exception.code, ctx.exception.cause, str(ctx.exception)),
                         ("side_invalid", None, "side_invalid"))

    def test_halt_race_across_processes(self) -> None:
        """Three processes submit while this one halts: nothing books after the halt commits."""
        ctx = multiprocessing.get_context("spawn")
        out = ctx.Queue()
        per_process = 200
        procs = [ctx.Process(target=_submitter, args=(self.path, "p%d" % n, per_process, out)) for n in range(3)]
        for p in procs:
            p.start()
        deadline = time.monotonic() + 60
        while len(self.book.orders()) < 20 and time.monotonic() < deadline:
            time.sleep(0.002)
        halt_seq = self.gw.halt()
        for p in procs:
            p.join(120)
        booked = sum(out.get(timeout=10) for _ in procs)
        orders = self.book.orders()
        self.assertEqual(len(orders), booked)
        self.assertGreaterEqual(booked, 20)
        self.assertLess(booked, 3 * per_process)  # submissions after the halt were refused
        self.assertTrue(all(seq < halt_seq for _, seq, _ in orders), (halt_seq, orders[-3:]))

    def test_duplicate_intent_refused(self) -> None:
        self.gw.submit(INTENT, self._approved(INTENT), AUTHORITY)
        approval = self._approved(INTENT)
        spent, calls = self.core.spent, list(self.core.calls)
        self._refused("intent_duplicate", self.gw.submit, INTENT, approval, AUTHORITY)
        # Caught before the core: no second charge, and the approval is not used up.
        self.assertEqual((self.core.spent, self.core.calls), (spent, calls))
        self.assertEqual(spent, 100_000)
        self.assertEqual(self.core.approvals[approval]["state"], "approved")
        self.assertEqual(self.book.orders(), [("i-1", 1, "working")])

    def test_only_the_gateway_books_orders(self) -> None:
        """Static check: no other file in engine/ (plugin and loader callers included) writes the
        order book or calls a venue."""
        engine = PAPER_DIR.parents[1]
        writers, venue_io = [], []
        for path in engine.rglob("*.py"):
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if (re.search(r"INSERT INTO orders|UPDATE orders SET status = 'working'|\._book\(|\.book\(|_GATEWAY_ONLY",
                          text) and path != PAPER_DIR / "gateway.py"):
                writers.append(str(path))
            if re.search(r"^\s*(import|from)\s+(urllib|http|requests|httpx|socket|ssl|keyring|subprocess)\b",
                         text, re.MULTILINE):
                venue_io.append(str(path))
        self.assertEqual(writers, [])
        self.assertEqual(venue_io, [])

    def test_paper_book_has_no_public_booking_path(self) -> None:
        """Red-team round 2 finding 6: holding a PaperBook (or the gateway's) books nothing."""
        public = {name for name in vars(PaperBook) if not name.startswith("_")}
        # B15/B31 (#320) add read-only cash/holding reads, the funds check and the refusal audit
        # write; none of them writes the orders table.
        self.assertEqual(public, {"halt", "halted", "cancel_all", "working", "orders", "halt_seq", "close",
                                  "audit", "available_cash_minor", "held_shares", "check_funds", "record_refusal"})
        self.assertFalse(hasattr(self.book, "book"))
        for token in (None, object(), "gateway", True):
            with self.subTest(token=token):
                self._refused("book_direct_refused", self.book._book, INTENT, token)
                self._refused("book_direct_refused", self.gw._book._book, INTENT, token)
        self.assertEqual(self.book.orders(), [])
        import cockpit.paper.gateway as gateway_module
        self.assertNotIn("_GATEWAY_ONLY", gateway_module.__all__)
        self.gw.submit(INTENT, self._approved(INTENT), AUTHORITY)  # the gateway still books
        self.assertEqual(self.book.orders(), [("i-1", 1, "working")])


if __name__ == "__main__":
    unittest.main()
