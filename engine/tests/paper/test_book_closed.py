"""A closed paper book refuses, typed, and closing it under load never crashes the process.

``PaperBook.close`` takes the book's lock and marks the book closed; every later book call,
and every gateway call that reaches the book, is ``Refused("book_closed")`` and never a raw
``sqlite3.ProgrammingError``. Closing while other threads book must not close the SQLite
connection under a statement in flight (that crashed the interpreter).

The load test runs in a child interpreter so a crash is an exit code here, not the end of
the test run. No provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace

from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, OrderGateway, PaperBook, Refused, create_book
from cockpit.paper.gemini import GeminiKill
from cockpit.paper.killswitch import KillSwitch
from tests.paper.fakecore import ALLOWLIST, AUTHORITY, FakeCore, sized
from tests.paper.test_gateway import INTENT
from tests.paper.test_gemini import MockGemini

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAPS = Caps(10**9, 10**6)

# Three threads book through their own gateway on one shared PaperBook while the main thread
# closes it. Prints what each thread ended on; a crash never reaches the print.
CLOSE_UNDER_LOAD = r"""
import json, sys, threading, time
from dataclasses import replace
from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, OrderGateway, PaperBook, Refused
from tests.paper.fakecore import ALLOWLIST, AUTHORITY, FakeCore, sized
from tests.paper.test_gateway import INTENT

book = PaperBook(sys.argv[1])
ended, booked, lock = {}, [], threading.Lock()
started = threading.Barrier(4)

def worker(n):
    core = FakeCore(budget_minor=10**12)
    gw = OrderGateway(plugin_id="csl.cockpit.paper", book=book, core=CoreClient(core), caps=Caps(10**9, 10**6),
                      allowlist=ALLOWLIST, run_check=core.check)
    started.wait(10)
    end = "ran_out"
    for i in range(20000):
        intent = sized(gw, replace(INTENT, intent_id="c%d-%d" % (n, i)))
        try:
            approval = gw.request_approval(intent)["approval_id"]
            core.operator_approves(approval)
            gw.submit(intent, approval, AUTHORITY)
        except Refused as exc:
            end = "refused:" + str(exc)
            break
        except BaseException as exc:
            end = "raw:" + type(exc).__name__
            break
        with lock:
            booked.append(intent.intent_id)
    with lock:
        ended[n] = end

threads = [threading.Thread(target=worker, args=(n,), daemon=True) for n in range(3)]
for t in threads:
    t.start()
started.wait(10)
time.sleep(0.05)
book.close()
book.close()  # a second close is a no-op
for t in threads:
    t.join(20)
print(json.dumps({"ended": [ended.get(n, "hung") for n in range(3)], "booked": len(booked)}))
"""


def _new_book(case: unittest.TestCase) -> str:
    d = tempfile.mkdtemp(prefix="cockpit-closed-")
    case.addCleanup(shutil.rmtree, d, True)
    path = os.path.join(d, "book.db")
    create_book(path, cash_minor={"paper-acct": 10**15})
    return path


class ClosedBookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _new_book(self)
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**9)
        self.gw = OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(self.core), caps=CAPS,
                               allowlist=ALLOWLIST, run_check=self.core.check)

    def _book_one(self, intent_id: str = "w-1") -> None:
        intent = sized(self.gw, replace(INTENT, intent_id=intent_id))
        approval = self.gw.request_approval(intent)["approval_id"]
        self.core.operator_approves(approval)
        self.gw.submit(intent, approval, AUTHORITY)

    def _refused(self, call, code: str = "book_closed", cause: str | None = None) -> None:
        with self.assertRaises(Refused) as ctx:
            call()
        self.assertIs(type(ctx.exception), Refused)
        self.assertEqual((ctx.exception.code, ctx.exception.cause), (code, cause))

    def test_every_book_call_on_a_closed_book_is_refused_typed(self) -> None:
        self._book_one()
        self.book.close()
        for name in ("halt", "halted", "cancel_all", "working", "orders", "halt_seq"):
            with self.subTest(call=name):
                self._refused(getattr(self.book, name))
        row = {"stage": "risk", "code": "x", "plugin_id": "csl.cockpit.paper"}
        for name, call in (("available_cash_minor", lambda: self.book.available_cash_minor("paper-acct")),
                           ("held_shares", lambda: self.book.held_shares("paper-acct", "AAPL")),
                           ("check_funds", lambda: self.book.check_funds(INTENT)),
                           ("audit", self.book.audit),
                           ("record_refusal", lambda: self.book.record_refusal(row)),
                           ("_has_intent", lambda: self.book._has_intent("w-1"))):
            with self.subTest(call=name):
                self._refused(call)
        self.book.close()  # closing again neither raises nor reopens
        self._refused(self.book.halted)
        # Nothing was written by the refused calls: a fresh connection sees the order still working.
        reader = PaperBook(self.path)
        self.addCleanup(reader.close)
        self.assertEqual((reader.halted(), reader.orders()), (False, [("w-1", 1, "working")]))

    def test_gateway_on_a_closed_book_refuses_typed_before_the_core(self) -> None:
        self._book_one()
        fresh = sized(self.gw, replace(INTENT, intent_id="closed-1"))
        approval = self.gw.request_approval(fresh)["approval_id"]
        self.core.operator_approves(approval)
        self.book.close()
        calls, spent = list(self.core.calls), self.core.spent
        for name, call in (("request_approval", lambda: self.gw.request_approval(fresh)),
                           ("submit", lambda: self.gw.submit(fresh, approval, AUTHORITY)),
                           ("risk_pass", lambda: self.gw.risk_pass(fresh)),
                           ("size_buy", lambda: self.gw.size_buy(
                               intent_id="closed-2", account="paper-acct", instrument="AAPL",
                               limit_price_minor=10_000, conviction_bps=1, run_id="run-1", connection="conn-a")),
                           ("halt", self.gw.halt), ("cancel_all", self.gw.cancel_all)):
            with self.subTest(call=name):
                self._refused(call)
        self.assertEqual((self.core.calls, self.core.spent), (calls, spent))  # the core was never asked
        self.assertEqual(self.core.approvals[approval]["state"], "approved")  # and the approval not used

    def test_book_closed_during_the_core_call_is_a_leftover_charge_not_a_raw_error(self) -> None:
        intent = replace(INTENT, intent_id="mid-1")
        book = self.book

        class ClosesOnAuthorize(FakeCore):
            def __call__(self, raw: bytes) -> bytes:
                reply = super().__call__(raw)
                if json.loads(raw)["op"] == "effect.authorize":
                    book.close()
                return reply
        core = ClosesOnAuthorize(budget_minor=10**9)
        gw = OrderGateway(plugin_id="csl.cockpit.paper", book=book, core=CoreClient(core), caps=CAPS,
                          allowlist=ALLOWLIST, run_check=core.check)
        sized(gw, intent)
        approval = gw.request_approval(intent)["approval_id"]
        core.operator_approves(approval)
        self._refused(lambda: gw.submit(intent, approval, AUTHORITY), "leftover_charge", "book_closed")
        reader = PaperBook(self.path)
        self.addCleanup(reader.close)
        self.assertEqual(reader.orders(), [])

    def test_kill_on_a_closed_book_is_pending_with_the_refusal_and_still_reaches_venues(self) -> None:
        self._book_one()
        self.book.close()
        reader = PaperBook(self.path)
        self.addCleanup(reader.close)
        venue = MockGemini(orders=["gm-1"])
        switch = KillSwitch(self.gw.halt, [GeminiKill(reader.halt, venue, halted=reader.halted)],
                            paper_book=self.book)
        for revokes in (1, 2):
            out = switch.kill()
            self.assertEqual((out["status"], out["halt"]), ("pending", "refused:book_closed"))
            paper, gemini = out["venues"]
            self.assertEqual(paper, {"venue": "paper", "status": "pending", "cancelled": 0,
                                     "detail": {"cancel_orders": "refused:book_closed"}})
            self.assertEqual((gemini["venue"], gemini["status"]), ("gemini", "confirmed"))
            self.assertEqual((venue.open, venue.log.count("revoke")), ([], revokes))
        # The venue's own halt landed on its open connection; the order the closed connection
        # could not cancel is still working and is reported pending, not hidden.
        self.assertEqual((reader.halted(), reader.working()), (True, 1))
        # With no venue at all it is pending too, never paper_only.
        out = KillSwitch(self.gw.halt, [], paper_book=self.book).kill()
        self.assertEqual((out["status"], out["halt"]), ("pending", "refused:book_closed"))


class CloseUnderLoadTests(unittest.TestCase):
    def test_close_while_three_threads_book_does_not_crash_the_process(self) -> None:
        env = dict(os.environ)
        inherited = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
        env["PYTHONPATH"] = os.pathsep.join([ENGINE_DIR] + inherited)
        for attempt in range(3):  # three short tries; one crash is enough
            with self.subTest(attempt=attempt):
                path = _new_book(self)
                proc = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", CLOSE_UNDER_LOAD, path],
                                      env=env, capture_output=True, text=True, timeout=120)
                self.assertEqual(proc.returncode, 0, "the child died (-11 is SIGSEGV): close() raced a booking "
                                                     "thread\n" + proc.stderr[-2000:])
                report = json.loads(proc.stdout.strip().splitlines()[-1])
                # Every booking thread ended on the typed refusal: at the book, or after the charge.
                for end in report["ended"]:
                    self.assertIn(end, ("refused:book_closed", "refused:leftover_charge:book_closed"))
                # The file is still a consistent book holding exactly the orders that were booked.
                book = PaperBook(path)
                try:
                    orders = book.orders()
                    self.assertEqual((book.halted(), book.working()), (False, len(orders)))
                finally:
                    book.close()
                self.assertEqual(len(orders), report["booked"])
                self.assertEqual([seq for _, seq, _ in orders], list(range(1, len(orders) + 1)))
                self.assertEqual(len({intent_id for intent_id, _, _ in orders}), len(orders))


if __name__ == "__main__":
    unittest.main()
