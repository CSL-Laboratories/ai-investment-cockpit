"""B15: faulted runs and tripped connections refused at the OrderGateway, refusals audited.

PRODUCT.md 4.2 (055cc7a5): "an order intent whose proposal came from a run on a route the
engine breaker had tripped, or from a run that ended in a fault, is refused at the
`OrderGateway`." Fleet briefing B15: typed code, n>=5, healthy run passes, refusal audited.

Two layers, each a real loop of N cells:

* ``FakeCoreCells`` drive the gateway against the ``csl-core/v0`` test double.
* ``RealCoreCells`` drive the developer-signed plugin, loaded by the real Recursion core
  (``recursion_core.plugin_security``, recursion #328), with the faults set on the core's
  own ``ConnectionBreaker`` (``fault_run``, typed ``report``), the object behind the core's
  host operations ``run.fault`` and ``breaker.report``. Skipped unless the core and
  ``cryptography`` are importable.

Run: ``PYTHONPATH=engine:/path/to/recursion/core python3 -B -m unittest discover -s
engine/cockpit/paper/tests -t engine -v``. Paper only: no provider, venue, network,
credential or keyring is touched.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import unittest
from dataclasses import replace

from cockpit.paper.core_client import CoreClient, canonical
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused
from cockpit.paper.tests import test_e2e_recursion_core as e2e
from cockpit.paper.tests.fakecore import AUTHORITY, FakeCore
from cockpit.paper.tests.test_gateway import _book_path

N = 10  # cells per refusal kind; B15 asks for n >= 5
BASE = Intent(intent_id="b15", account="paper-acct", instrument="AAPL", side="buy", quantity=1,
              limit_price_minor=100, run_id="run-ok", connection="conn-ok")


def _audit_codes(book: PaperBook) -> list[tuple]:
    return [(r["event"], r["stage"], r["code"], r["intent_id"], r["run_id"], r["connection"])
            for r in book.audit()]


class FakeCoreCells(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _book_path(self)
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**9)
        self.gw = OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(self.core),
                               caps=Caps(10**9, 10**6))

    def _approved(self, intent: Intent) -> str:
        approval = self.gw.request_approval(intent)["approval_id"]
        self.core.operator_approves(approval)
        return approval

    def test_faulted_tripped_and_healthy_cells(self) -> None:
        results = {"faulted_at_submit": [], "faulted_at_request": [], "tripped_at_submit": [],
                   "tripped_at_request": [], "healthy": []}
        expected_audit = []
        for i in range(N):
            # A run that ends in a fault after its proposal was approved: refused at submit.
            run, conn = "run-f%d" % i, "conn-f%d" % i
            intent = replace(BASE, intent_id="f%d" % i, run_id=run, connection=conn)
            approval = self._approved(intent)
            self.core.faulted_runs.add(run)
            with self.assertRaises(Refused) as ctx:
                self.gw.submit(intent, approval, AUTHORITY)
            results["faulted_at_submit"].append(ctx.exception.code)
            self.assertIsInstance(ctx.exception.audit_seq, int)
            expected_audit.append(("refused", "authorize", "core:run_faulted", intent.intent_id, run, conn))
            # A new proposal from that faulted run: refused before any approval is drawn.
            again = replace(intent, intent_id="f%d-again" % i)
            with self.assertRaises(Refused) as ctx:
                self.gw.request_approval(again)
            results["faulted_at_request"].append(ctx.exception.code)
            expected_audit.append(("refused", "request", "core:run_faulted", again.intent_id, run, conn))
            # A run on a connection the engine breaker trips after approval.
            run, conn = "run-t%d" % i, "conn-t%d" % i
            intent = replace(BASE, intent_id="t%d" % i, run_id=run, connection=conn)
            approval = self._approved(intent)
            self.core.tripped_connections.add(conn)
            with self.assertRaises(Refused) as ctx:
                self.gw.submit(intent, approval, AUTHORITY)
            results["tripped_at_submit"].append(ctx.exception.code)
            expected_audit.append(("refused", "authorize", "core:connection_tripped", intent.intent_id, run, conn))
            again = replace(intent, intent_id="t%d-again" % i, run_id="run-t%d-b" % i)
            with self.assertRaises(Refused) as ctx:
                self.gw.request_approval(again)
            results["tripped_at_request"].append(ctx.exception.code)
            expected_audit.append(("refused", "request", "core:connection_tripped", again.intent_id,
                                   again.run_id, conn))
            # A healthy run in the same loop still books.
            intent = replace(BASE, intent_id="ok%d" % i)
            out = self.gw.submit(intent, self._approved(intent), AUTHORITY)
            results["healthy"].append(out["status"])
            expected_audit.append(("booked", "book", None, intent.intent_id, "run-ok", "conn-ok"))
        self.assertEqual(results, {
            "faulted_at_submit": ["core:run_faulted"] * N, "faulted_at_request": ["core:run_faulted"] * N,
            "tripped_at_submit": ["core:connection_tripped"] * N,
            "tripped_at_request": ["core:connection_tripped"] * N, "healthy": ["paper_working"] * N})
        self.assertEqual(_audit_codes(self.book), expected_audit)
        self.assertEqual([o[0] for o in self.book.orders()], ["ok%d" % i for i in range(N)])
        # The audit is in the shared book file, readable by any process that opens it.
        other = PaperBook(self.path)
        self.addCleanup(other.close)
        self.assertEqual(_audit_codes(other), expected_audit)

    def test_refused_audit_rows_carry_effect_digest_and_halt_seq(self) -> None:
        intent = replace(BASE, intent_id="d1", run_id="run-d", connection="conn-d")
        approval = self._approved(intent)
        self.core.faulted_runs.add("run-d")
        with self.assertRaises(Refused):
            self.gw.submit(intent, approval, AUTHORITY)
        row = self.book.audit()[-1]
        self.assertEqual(row["effect_sha256"], hashlib.sha256(canonical(self.gw.risk_pass(intent))).hexdigest())
        self.assertEqual((row["plugin_id"], row["halt_seq"]), ("csl.cockpit.paper", 0))

    def test_risk_and_halt_refusals_are_audited_too(self) -> None:
        with self.assertRaises(Refused):
            self.gw.request_approval(replace(BASE, intent_id="big", quantity=10**7))
        self.gw.halt()
        with self.assertRaises(Refused):
            self.gw.submit(replace(BASE, intent_id="h1"), "apr_x", AUTHORITY)
        with self.assertRaises(Refused):
            self.gw.request_approval("not an intent")
        self.assertEqual([(r["stage"], r["code"], r["intent_id"]) for r in self.book.audit()], [
            ("risk", "size_cap_quantity", "big"), ("risk", "order_halted", "h1"), ("risk", "intent_invalid", None)])
        self.assertEqual(self.book.audit()[1]["halt_seq"], self.book.halt_seq())

    def test_audit_is_append_only(self) -> None:
        with self.assertRaises(Refused):
            self.gw.request_approval(replace(BASE, intent_id="x", side="hold"))
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        for sql in ("UPDATE audit SET code = 'ok'", "DELETE FROM audit"):
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute(sql)
        self.assertEqual([r["code"] for r in self.book.audit()], ["side_invalid"])

    def test_audit_write_failure_never_turns_refusal_into_pass(self) -> None:
        self.core.faulted_runs.add("run-ok")

        def broken(row):
            raise sqlite3.OperationalError("disk I/O error")
        self.book.record_refusal = broken
        with self.assertRaises(Refused) as ctx:
            self.gw.request_approval(replace(BASE, intent_id="w1"))
        self.assertEqual((ctx.exception.code, ctx.exception.audit_seq), ("core:run_faulted", None))
        self.assertEqual(self.book.orders(), [])

    def test_book_without_audit_table_refused(self) -> None:
        path = os.path.join(os.path.dirname(self.path), "old.db")
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE state (k TEXT PRIMARY KEY, v INTEGER NOT NULL)")
        db.close()
        with self.assertRaises(Refused) as ctx:
            PaperBook(path)
        self.assertEqual(ctx.exception.code, "book_without_audit")


@unittest.skipUnless(e2e.CORE, "Recursion core or cryptography not importable")
class RealCoreCells(unittest.TestCase):
    def test_real_core_faulted_tripped_and_healthy_cells(self) -> None:
        world = e2e.EndToEndPaperTests("test_paper_order_through_permission_approval_profile_gateway")
        world.addCleanup = self.addCleanup  # share cleanup with this test
        gateway, profile, decide, _, breaker, book, _ = world._world()
        self.addCleanup(book.close)
        codes = {"faulted_at_submit": [], "faulted_at_request": [], "tripped_at_submit": [],
                 "tripped_at_request": [], "healthy": []}
        for i in range(N):
            run, conn = "run-f%d" % i, "conn-f%d" % i
            breaker.bind_run(run, conn)
            intent = replace(e2e.INTENT, intent_id="rf%d" % i, run_id=run, connection=conn, quantity=1)
            approval = gateway.request_approval(intent)
            self.assertTrue(decide(approval)["ok"])
            breaker.fault_run(run)
            with self.assertRaises(Refused) as ctx:
                gateway.submit(intent, approval["approval_id"], profile)
            codes["faulted_at_submit"].append(ctx.exception.code)
            with self.assertRaises(Refused) as ctx:
                gateway.request_approval(replace(intent, intent_id="rf%d-again" % i))
            codes["faulted_at_request"].append(ctx.exception.code)

            run, conn = "run-t%d" % i, "conn-t%d" % i
            breaker.bind_run(run, conn)
            breaker.bind_run(run + "-b", conn)
            intent = replace(e2e.INTENT, intent_id="rt%d" % i, run_id=run, connection=conn, quantity=1)
            approval = gateway.request_approval(intent)
            self.assertTrue(decide(approval)["ok"])
            self.assertTrue(breaker.report(conn, ("plan_limit", "rate_limit", "usage_window_exhausted")[i % 3]))
            with self.assertRaises(Refused) as ctx:
                gateway.submit(intent, approval["approval_id"], profile)
            codes["tripped_at_submit"].append(ctx.exception.code)
            with self.assertRaises(Refused) as ctx:
                gateway.request_approval(replace(intent, intent_id="rt%d-again" % i, run_id=run + "-b"))
            codes["tripped_at_request"].append(ctx.exception.code)

            intent = replace(e2e.INTENT, intent_id="rok%d" % i, quantity=1)
            approval = gateway.request_approval(intent)
            self.assertTrue(decide(approval)["ok"])
            codes["healthy"].append(gateway.submit(intent, approval["approval_id"], profile)["status"])
        self.assertEqual(codes, {
            "faulted_at_submit": ["core:run_faulted"] * N, "faulted_at_request": ["core:run_faulted"] * N,
            "tripped_at_submit": ["core:connection_tripped"] * N,
            "tripped_at_request": ["core:connection_tripped"] * N, "healthy": ["paper_working"] * N})
        audit = book.audit()
        refused = [r for r in audit if r["event"] == "refused"]
        self.assertEqual(len(refused), 4 * N)
        self.assertEqual(sorted({r["code"] for r in refused}), ["core:connection_tripped", "core:run_faulted"])
        self.assertTrue(all(r["effect_sha256"] and len(r["effect_sha256"]) == 64 for r in refused))
        self.assertEqual([r["intent_id"] for r in audit if r["event"] == "booked"], ["rok%d" % i for i in range(N)])
        self.assertEqual([o[0] for o in book.orders()], ["rok%d" % i for i in range(N)])
        # A probe that succeeds clears the trip; the next run on that connection books again.
        self.assertTrue(breaker.reset_after_probe("conn-t0", True))
        breaker.bind_run("run-t0-c", "conn-t0")
        intent = replace(e2e.INTENT, intent_id="after-probe", run_id="run-t0-c", connection="conn-t0", quantity=1)
        approval = gateway.request_approval(intent)
        decide(approval)
        self.assertEqual(gateway.submit(intent, approval["approval_id"], profile)["status"], "paper_working")


if __name__ == "__main__":
    unittest.main()
