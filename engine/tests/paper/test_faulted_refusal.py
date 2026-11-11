"""B15: faulted runs and tripped connections refused at the OrderGateway, refusals audited.

PRODUCT.md 4.2 (d84cdd26a4de2366f3c5d34c4aec5215f822a494, CSLR-D-012): "an order intent whose proposal came from a run on a route the
engine breaker had tripped, or from a run that ended in a fault, is refused at the
`OrderGateway`." Fleet briefing B15: typed code, n>=5, healthy run passes, refusal audited.

Two layers, each a real loop of N cells:

* ``FakeCoreCells`` drive the gateway against the ``csl-core/v0`` test double, which mirrors
  the core breaker's run binding (``run_unknown``, ``run_connection_mismatch``).
* ``RealCoreCells`` drive the developer-signed plugin, loaded by the real Recursion core
  (``recursion_core.plugin_security``, recursion #328), with the faults set on the core's
  own ``ConnectionBreaker`` (``fault_run``, typed ``report``), the object behind the core's
  host operations ``run.fault`` and ``breaker.report``. They skip only when ``recursion_core``
  or ``recursion_core.plugin_security`` itself is absent from the path; any other import
  failure, a missing ``cryptography`` included, is an error.

Both layers also cover the booking point: a fault, a trip or a changed run binding that lands
after ``effect.authorize`` and before the booking is refused ``leftover_charge`` with the
core's code as cause (the gateway's ``run_check``, asked inside the booking transaction).

Run: ``PYTHONPATH=engine:/path/to/recursion/core python3 -B -m unittest discover -s
engine/tests/paper -t engine -v``. Paper only: no provider, venue, network,
credential or keyring is touched.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from dataclasses import replace

import cockpit.paper.gateway as gateway_module
from cockpit.paper import plugin
from cockpit.paper.core_client import CoreClient, canonical
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused
from tests.paper import test_e2e_recursion_core as e2e
from tests.paper.fakecore import ALLOWLIST, AUTHORITY, FakeCore, sized
from tests.paper.test_gateway import _book_path

N = 10  # cells per refusal kind; B15 asks for n >= 5
REAL_N = 5  # booking-point cells per kind on the real core: each one charges its 120_000 profile
BASE = Intent(intent_id="b15", account="paper-acct", instrument="AAPL", side="buy", quantity=1,
              limit_price_minor=100, run_id="run-ok", connection="conn-ok")


def _audit_codes(book: PaperBook) -> list[tuple]:
    return [(r["event"], r["stage"], r["code"], r["intent_id"], r["run_id"], r["connection"])
            for r in book.audit()]


def _after_authorize(transport, land, charged: list):
    """Wrap a core transport: once the core has authorized an effect, run ``land`` before the
    reply reaches the gateway, so it falls between the charge and the booking."""
    def wrapped(raw: bytes) -> bytes:
        out = transport(raw)
        if json.loads(raw)["op"] == "effect.authorize" and json.loads(out)["ok"] is True:
            charged.append(out)
            land()
        return out
    return wrapped


class FakeCoreCells(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _book_path(self)
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**9)
        self.gw = OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(self.core),
                               caps=Caps(10**9, 10**6), allowlist=ALLOWLIST, run_check=self.core.check)

    def _approved(self, intent: Intent) -> str:
        approval = self.gw.request_approval(sized(self.gw, intent))["approval_id"]
        self.core.operator_approves(approval)
        return approval

    def test_faulted_tripped_and_healthy_cells(self) -> None:
        results = {"faulted_at_submit": [], "faulted_at_request": [], "tripped_at_submit": [],
                   "tripped_at_request": [], "healthy": []}
        expected_audit = []
        for i in range(N):
            # A run that ends in a fault after its proposal was approved: refused at submit.
            run, conn = "run-f%d" % i, "conn-f%d" % i
            self.core.bind_run(run, conn)
            intent = replace(BASE, intent_id="f%d" % i, run_id=run, connection=conn)
            approval = self._approved(intent)
            self.core.faulted_runs.add(run)
            with self.assertRaises(Refused) as ctx:
                self.gw.submit(intent, approval, AUTHORITY)
            results["faulted_at_submit"].append(ctx.exception.code)
            self.assertIsInstance(ctx.exception.audit_seq, int)
            expected_audit.append(("refused", "authorize", "core:run_faulted", intent.intent_id, run, conn))
            # A new proposal from that faulted run: refused before any approval is drawn.
            again = sized(self.gw, replace(intent, intent_id="f%d-again" % i))
            with self.assertRaises(Refused) as ctx:
                self.gw.request_approval(again)
            results["faulted_at_request"].append(ctx.exception.code)
            expected_audit.append(("refused", "request", "core:run_faulted", again.intent_id, run, conn))
            # A run on a connection the engine breaker trips after approval.
            run, conn = "run-t%d" % i, "conn-t%d" % i
            self.core.bind_run(run, conn)
            self.core.bind_run(run + "-b", conn)
            intent = replace(BASE, intent_id="t%d" % i, run_id=run, connection=conn)
            approval = self._approved(intent)
            self.core.tripped_connections.add(conn)
            with self.assertRaises(Refused) as ctx:
                self.gw.submit(intent, approval, AUTHORITY)
            results["tripped_at_submit"].append(ctx.exception.code)
            expected_audit.append(("refused", "authorize", "core:connection_tripped", intent.intent_id, run, conn))
            again = sized(self.gw, replace(intent, intent_id="t%d-again" % i, run_id="run-t%d-b" % i))
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
        self.core.bind_run("run-d", "conn-d")
        intent = replace(BASE, intent_id="d1", run_id="run-d", connection="conn-d")
        approval = self._approved(intent)
        self.core.faulted_runs.add("run-d")
        with self.assertRaises(Refused):
            self.gw.submit(intent, approval, AUTHORITY)
        row = self.book.audit()[-1]
        self.assertEqual(row["effect_sha256"], hashlib.sha256(canonical(self.gw.risk_pass(intent))).hexdigest())
        self.assertEqual((row["plugin_id"], row["halt_seq"]), ("csl.cockpit.paper", 0))

    def test_unbound_and_relabelled_runs_refused_and_audited(self) -> None:
        """Finding 2 (round 1): the core's run binding, surfaced and audited by the gateway.

        A run the host never bound is ``run_unknown``; a run sent on a connection other than
        the one it is bound to is ``run_connection_mismatch``. Both at request (no approval
        is drawn) and at submit (an approved order sized again under another run or
        connection). A relabel onto a healthy run of this plugin on that run's own connection
        is not caught by the run check: that is the proposal-to-run binding C5 still has to
        carry (finding 1, left for Lucas).
        """
        results = {"unbound_at_request": [], "moved_at_request": [], "relabel_unbound_at_submit": [],
                   "relabel_moved_at_submit": [], "healthy": []}
        expected_audit = []
        for i in range(N):
            # An intent naming a run the host never bound: refused before any approval.
            ghost = sized(self.gw, replace(BASE, intent_id="u%d" % i, run_id="run-ghost-%d" % i,
                                           connection="conn-ok"))
            drawn = len(self.core.approvals)
            with self.assertRaises(Refused) as ctx:
                self.gw.request_approval(ghost)
            results["unbound_at_request"].append(ctx.exception.code)
            self.assertIsInstance(ctx.exception.audit_seq, int)
            self.assertEqual(len(self.core.approvals), drawn)
            expected_audit.append(("refused", "request", "core:run_unknown", ghost.intent_id,
                                   ghost.run_id, "conn-ok"))
            # A bound run sent on another connection (here a healthy one): refused.
            run, conn = "run-m%d" % i, "conn-m%d" % i
            self.core.bind_run(run, conn)
            moved = sized(self.gw, replace(BASE, intent_id="m%d" % i, run_id=run, connection="conn-ok"))
            with self.assertRaises(Refused) as ctx:
                self.gw.request_approval(moved)
            results["moved_at_request"].append(ctx.exception.code)
            self.assertEqual(len(self.core.approvals), drawn)
            expected_audit.append(("refused", "request", "core:run_connection_mismatch", moved.intent_id,
                                   run, "conn-ok"))
            # An approved intent from a run that then faults, re-sent with a fresh run id.
            intent = replace(BASE, intent_id="r%d" % i, run_id=run, connection=conn)
            approval = self._approved(intent)
            self.core.faulted_runs.add(run)
            fresh = sized(self.gw, replace(intent, run_id="run-fresh-%d" % i))
            with self.assertRaises(Refused) as ctx:
                self.gw.submit(fresh, approval, AUTHORITY)
            results["relabel_unbound_at_submit"].append(ctx.exception.code)
            expected_audit.append(("refused", "authorize", "core:run_unknown", fresh.intent_id,
                                   fresh.run_id, conn))
            # The same approved intent re-sent on a healthy connection.
            onto = sized(self.gw, replace(intent, connection="conn-ok"))
            with self.assertRaises(Refused) as ctx:
                self.gw.submit(onto, approval, AUTHORITY)
            results["relabel_moved_at_submit"].append(ctx.exception.code)
            expected_audit.append(("refused", "authorize", "core:run_connection_mismatch", onto.intent_id,
                                   run, "conn-ok"))
            ok = replace(BASE, intent_id="ok%d" % i)
            results["healthy"].append(self.gw.submit(ok, self._approved(ok), AUTHORITY)["status"])
            expected_audit.append(("booked", "book", None, ok.intent_id, "run-ok", "conn-ok"))
        self.assertEqual(results, {
            "unbound_at_request": ["core:run_unknown"] * N,
            "moved_at_request": ["core:run_connection_mismatch"] * N,
            "relabel_unbound_at_submit": ["core:run_unknown"] * N,
            "relabel_moved_at_submit": ["core:run_connection_mismatch"] * N,
            "healthy": ["paper_working"] * N})
        self.assertEqual(_audit_codes(self.book), expected_audit)
        self.assertTrue(all(len(r["effect_sha256"]) == 64 for r in self.book.audit() if r["event"] == "refused"))
        self.assertEqual([o[0] for o in self.book.orders()], ["ok%d" % i for i in range(N)])
        # The double, like the core, never lets a run move connections.
        with self.assertRaises(ValueError):
            self.core.bind_run("run-ok", "conn-other")

    def test_fault_or_trip_after_authorize_is_refused_at_the_booking_point(self) -> None:
        """PRODUCT.md 4.2 at the booking point (was finding 3, round 1, a documented gap).

        A ``run.fault`` or a breaker trip that lands after ``effect.authorize`` returned and
        before the booking is seen by the run check inside the booking transaction. The core
        has charged by then, so the refusal is ``leftover_charge`` with the core's code as
        cause, audited at stage ``book``, and nothing is booked.
        """
        core = self.core
        kinds = (("run_faulted", lambda run, conn: core.faulted_runs.add(run)),
                 ("connection_tripped", lambda run, conn: core.tripped_connections.add(conn)))
        expected_audit, results = [], {code: [] for code, _ in kinds}
        for i in range(N):
            for code, land in kinds:
                run, conn = "run-%s-%d" % (code, i), "conn-%s-%d" % (code, i)
                core.bind_run(run, conn)
                intent = replace(BASE, intent_id="%s-%d" % (code, i), run_id=run, connection=conn)
                approval = self._approved(intent)
                digest = hashlib.sha256(canonical(self.gw.risk_pass(intent))).hexdigest()
                charged, spent = [], core.spent
                self.gw._core = CoreClient(_after_authorize(core, lambda: land(run, conn), charged))
                with self.assertRaises(Refused) as ctx:
                    self.gw.submit(intent, approval, AUTHORITY)
                self.gw._core = CoreClient(core)
                exc = ctx.exception
                results[code].append((exc.code, exc.cause, str(exc)))
                self.assertEqual((len(charged), core.spent), (1, spent + 100))  # charged, not reversed
                row = self.book.audit()[-1]
                self.assertEqual((row["seq"], row["effect_sha256"]), (exc.audit_seq, digest))
                expected_audit.append(("refused", "book", "leftover_charge:core:" + code, intent.intent_id, run, conn))
            # Control: a healthy run through the same wrapped transport still books.
            ok = replace(BASE, intent_id="ok%d" % i)
            approval = self._approved(ok)
            self.gw._core = CoreClient(_after_authorize(core, lambda: None, []))
            self.assertEqual(self.gw.submit(ok, approval, AUTHORITY)["status"], "paper_working")
            self.gw._core = CoreClient(core)
            expected_audit.append(("booked", "book", None, ok.intent_id, "run-ok", "conn-ok"))
        self.assertEqual(results, {code: [("leftover_charge", "core:" + code, "leftover_charge:core:" + code)] * N
                                   for code, _ in kinds})
        self.assertEqual(_audit_codes(self.book), expected_audit)
        self.assertEqual([o[0] for o in self.book.orders()], ["ok%d" % i for i in range(N)])

    def test_run_binding_is_checked_at_request_authorize_and_the_booking_point(self) -> None:
        """Proposal-to-run binding: the run must be this plugin's and on the intent's connection.

        Each of ``run_not_owned`` and ``run_connection_mismatch`` is refused at request (no
        approval drawn), at authorize (binding changed after the approval, no charge) and at
        the booking point (binding changed after the charge: ``leftover_charge``). The double's
        binding is changed by writing its tables, standing for a rewritten breaker store.
        """
        core = self.core
        kinds = (("run_not_owned", lambda run: core.run_owner.__setitem__(run, "other.plugin")),
                 ("run_connection_mismatch", lambda run: core.run_connection.__setitem__(run, "conn-moved")))
        expected_audit = []
        for i in range(N):
            for code, change in kinds:
                tag = "%s-%d" % (code, i)
                # At request: the run is already bound elsewhere.
                run, conn = "run-q-" + tag, "conn-q-" + tag
                core.bind_run(run, conn)
                change(run)
                intent = replace(BASE, intent_id="q-" + tag, run_id=run, connection=conn)
                drawn = len(core.approvals)
                with self.assertRaises(Refused) as ctx:
                    self.gw.request_approval(sized(self.gw, intent))
                self.assertEqual((ctx.exception.code, len(core.approvals)), ("core:" + code, drawn))
                expected_audit.append(("refused", "request", "core:" + code, intent.intent_id, run, conn))
                # At authorize: the binding changes after the approval.
                run, conn = "run-a-" + tag, "conn-a-" + tag
                core.bind_run(run, conn)
                intent = replace(BASE, intent_id="a-" + tag, run_id=run, connection=conn)
                approval, spent = self._approved(intent), core.spent
                change(run)
                with self.assertRaises(Refused) as ctx:
                    self.gw.submit(intent, approval, AUTHORITY)
                self.assertEqual((ctx.exception.code, ctx.exception.cause, core.spent), ("core:" + code, None, spent))
                expected_audit.append(("refused", "authorize", "core:" + code, intent.intent_id, run, conn))
                # At the booking point: the binding changes after the charge.
                run, conn = "run-b-" + tag, "conn-b-" + tag
                core.bind_run(run, conn)
                intent = replace(BASE, intent_id="b-" + tag, run_id=run, connection=conn)
                approval, charged = self._approved(intent), []
                self.gw._core = CoreClient(_after_authorize(core, lambda: change(run), charged))
                with self.assertRaises(Refused) as ctx:
                    self.gw.submit(intent, approval, AUTHORITY)
                self.gw._core = CoreClient(core)
                self.assertEqual((ctx.exception.code, ctx.exception.cause, len(charged)),
                                 ("leftover_charge", "core:" + code, 1))
                expected_audit.append(("refused", "book", "leftover_charge:core:" + code, intent.intent_id, run, conn))
            ok = replace(BASE, intent_id="ok%d" % i)
            self.assertEqual(self.gw.submit(ok, self._approved(ok), AUTHORITY)["status"], "paper_working")
            expected_audit.append(("booked", "book", None, ok.intent_id, "run-ok", "conn-ok"))
        self.assertEqual(_audit_codes(self.book), expected_audit)
        self.assertTrue(all(len(r["effect_sha256"]) == 64 for r in self.book.audit()))
        self.assertEqual([o[0] for o in self.book.orders()], ["ok%d" % i for i in range(N)])

    def test_gateway_without_a_run_check_cannot_book(self) -> None:
        gw = OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(self.core),
                          caps=Caps(10**9, 10**6), allowlist=ALLOWLIST)
        intent = sized(gw, replace(BASE, intent_id="nc"))
        approval = self._approved(sized(self.gw, intent))  # a real approval, drawn by the checked gateway
        calls, spent = list(self.core.calls), self.core.spent
        for attempt in (lambda: gw.request_approval(intent), lambda: gw.submit(intent, approval, AUTHORITY)):
            with self.assertRaises(Refused) as ctx:
                attempt()
            self.assertEqual((ctx.exception.code, ctx.exception.cause), ("run_check_missing", None))
            row = self.book.audit()[-1]
            self.assertEqual((row["seq"], row["event"], row["stage"], row["code"], row["intent_id"]),
                             (ctx.exception.audit_seq, "refused", "risk", "run_check_missing", "nc"))
        self.assertEqual((self.core.calls, self.core.spent, self.book.orders()), (calls, spent, []))
        # The booking itself refuses too, reached with a grant forged for that gateway (test-only).
        grant = gateway_module._Grant(gateway_module._GATEWAY_ONLY, gateway=gw, book=self.book, intent=intent,
                                      effect=gw.risk_pass(intent))
        with self.assertRaises(Refused) as ctx:
            self.book._book(intent, grant)
        self.assertEqual(ctx.exception.code, "run_check_missing")
        self.assertEqual(self.book.orders(), [])
        with self.assertRaises(Refused) as ctx:
            OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(self.core),
                         caps=Caps(10**9, 10**6), allowlist=ALLOWLIST, run_check="breaker.check")
        self.assertEqual(ctx.exception.code, "invalid_run_check")
        # The production entry point cannot be built without one.
        for kwargs in ({}, {"run_check": None}, {"run_check": "breaker.check"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(TypeError):
                    plugin.compose(self.core, self.path, Caps(10**9, 10**6), allowlist=ALLOWLIST, **kwargs)
                with self.assertRaises(TypeError):
                    plugin.build(self.core, self.path, Caps(10**9, 10**6), allowlist=ALLOWLIST, **kwargs)

    def test_run_check_that_fails_untyped_refuses_closed(self) -> None:
        """A run check that raises without a typed code, or answers with no dict, books nothing."""
        def raises(*args: object) -> dict:
            raise RuntimeError("breaker store unreadable")

        class Rigged(Exception):
            code = "RUN OK"  # not a typed code

        def rigged(*args: object) -> dict:
            raise Rigged()
        for n, check in enumerate((raises, rigged, lambda *args: None, lambda *args: True)):
            with self.subTest(check=n):
                gw = OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(self.core),
                                  caps=Caps(10**9, 10**6), allowlist=ALLOWLIST, run_check=check)
                intent = sized(gw, replace(BASE, intent_id="bad-check-%d" % n))
                approval = gw.request_approval(intent)["approval_id"]
                self.core.operator_approves(approval)
                with self.assertRaises(Refused) as ctx:
                    gw.submit(intent, approval, AUTHORITY)
                self.assertEqual((ctx.exception.code, ctx.exception.cause), ("leftover_charge", "run_check_error"))
                self.assertEqual(self.book.audit()[-1]["code"], "leftover_charge:run_check_error")
        self.assertEqual(self.book.orders(), [])

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
            self.gw.request_approval(sized(self.gw, replace(BASE, intent_id="w1")))
        self.assertEqual((ctx.exception.code, ctx.exception.audit_seq), ("core:run_faulted", None))
        self.assertEqual(self.book.orders(), [])

    def test_book_without_audit_table_refused(self) -> None:
        """A real book whose only fault is the missing audit table (from before B15)."""
        path = _book_path(self)
        db = sqlite3.connect(path)
        db.execute("DROP TABLE audit")
        db.commit()
        db.close()
        with self.assertRaises(Refused) as ctx:
            PaperBook(path)
        self.assertEqual(ctx.exception.code, "book_without_audit")


def _rewrite_binding(w: e2e.World, run: str, column: str, value: str) -> None:
    """Test-only: the breaker's API never moves a binding, so write its store the way same-uid
    code could (the limit the core documents as B17). ``column`` is a fixed test literal."""
    db = sqlite3.connect(w.breaker_path)
    try:
        db.execute("UPDATE runs SET %s = ? WHERE run_id = ?" % column, (value, run))
        db.commit()
    finally:
        db.close()


@unittest.skipIf(e2e.CORE_SKIP is not None, e2e.AWAITING_328)
class RealCoreCells(unittest.TestCase):
    def _bind(self, w: e2e.World, run: str, conn: str) -> None:
        w.breaker.bind_run(run, conn, e2e.PLUGIN_ID, e2e.PROVIDER, e2e.MODEL_VERSION)

    def _approved(self, w: e2e.World, intent: Intent) -> str:
        approval = w.gateway.request_approval(intent)
        self.assertTrue(w.decide(approval)["ok"])
        return approval["approval_id"]

    def test_real_core_faulted_tripped_and_healthy_cells(self) -> None:
        w = e2e.build_world(self)
        gateway, profile, breaker, book = w.gateway, w.profile, w.breaker, w.book
        codes = {"faulted_at_submit": [], "faulted_at_request": [], "tripped_at_submit": [],
                 "tripped_at_request": [], "healthy": []}
        for i in range(N):
            run, conn = "run-f%d" % i, "conn-f%d" % i
            self._bind(w, run, conn)
            intent = e2e.sized_buy(w, "rf%d" % i, quantity=1, run_id=run, connection=conn)
            approval = self._approved(w, intent)
            breaker.fault_run(run)
            with self.assertRaises(Refused) as ctx:
                gateway.submit(intent, approval, profile)
            codes["faulted_at_submit"].append(ctx.exception.code)
            with self.assertRaises(Refused) as ctx:
                gateway.request_approval(e2e.sized_buy(w, "rf%d-again" % i, quantity=1, run_id=run, connection=conn))
            codes["faulted_at_request"].append(ctx.exception.code)

            run, conn = "run-t%d" % i, "conn-t%d" % i
            self._bind(w, run, conn)
            self._bind(w, run + "-b", conn)
            intent = e2e.sized_buy(w, "rt%d" % i, quantity=1, run_id=run, connection=conn)
            approval = self._approved(w, intent)
            self.assertTrue(breaker.report(conn, ("plan_limit", "rate_limit", "usage_window_exhausted")[i % 3]))
            with self.assertRaises(Refused) as ctx:
                gateway.submit(intent, approval, profile)
            codes["tripped_at_submit"].append(ctx.exception.code)
            with self.assertRaises(Refused) as ctx:
                gateway.request_approval(e2e.sized_buy(w, "rt%d-again" % i, quantity=1, run_id=run + "-b",
                                                       connection=conn))
            codes["tripped_at_request"].append(ctx.exception.code)

            intent = e2e.sized_buy(w, "rok%d" % i, quantity=1)
            codes["healthy"].append(gateway.submit(intent, self._approved(w, intent), profile)["status"])
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
        # A leased probe that succeeds clears the trip; the next run on that connection books again.
        lease = breaker.begin_probe("conn-t0")
        self.assertIs(breaker.reset_after_probe("conn-t0", lease, True), True)
        self._bind(w, "run-t0-c", "conn-t0")
        intent = e2e.sized_buy(w, "after-probe", quantity=1, run_id="run-t0-c", connection="conn-t0")
        self.assertEqual(gateway.submit(intent, self._approved(w, intent), profile)["status"], "paper_working")

    def test_real_core_unbound_and_relabelled_runs_refused(self) -> None:
        w = e2e.build_world(self)
        gateway, profile, drawn, book = w.gateway, w.profile, w.drawn, w.book
        codes = {"unbound_at_request": [], "moved_at_request": [], "relabel_unbound_at_submit": [],
                 "relabel_moved_at_submit": []}
        for i in range(N):
            before = len(drawn)
            with self.assertRaises(Refused) as ctx:
                gateway.request_approval(e2e.sized_buy(w, "ru%d" % i, quantity=1, run_id="run-ghost-%d" % i))
            codes["unbound_at_request"].append(ctx.exception.code)
            run, conn = "run-m%d" % i, "conn-m%d" % i
            self._bind(w, run, conn)
            with self.assertRaises(Refused) as ctx:
                gateway.request_approval(e2e.sized_buy(w, "rm%d" % i, quantity=1, run_id=run))
            codes["moved_at_request"].append(ctx.exception.code)
            self.assertEqual(len(drawn), before)  # no approval drawn for either
            intent = e2e.sized_buy(w, "rr%d" % i, quantity=1, run_id=run, connection=conn)
            approval = self._approved(w, intent)
            w.breaker.fault_run(run)
            # The approved order, sized again under a fresh run id and then on a healthy connection.
            fresh = e2e.sized_buy(w, "rr%d" % i, quantity=1, run_id="run-fresh-%d" % i, connection=conn)
            with self.assertRaises(Refused) as ctx:
                gateway.submit(fresh, approval, profile)
            codes["relabel_unbound_at_submit"].append(ctx.exception.code)
            onto = e2e.sized_buy(w, "rr%d" % i, quantity=1, run_id=run)
            with self.assertRaises(Refused) as ctx:
                gateway.submit(onto, approval, profile)
            codes["relabel_moved_at_submit"].append(ctx.exception.code)
        self.assertEqual(codes, {
            "unbound_at_request": ["core:run_unknown"] * N,
            "moved_at_request": ["core:run_connection_mismatch"] * N,
            "relabel_unbound_at_submit": ["core:run_unknown"] * N,
            "relabel_moved_at_submit": ["core:run_connection_mismatch"] * N})
        refused = [r for r in book.audit() if r["event"] == "refused"]
        self.assertEqual([(r["stage"], r["code"]) for r in refused],
                         [("request", "core:run_unknown"), ("request", "core:run_connection_mismatch"),
                          ("authorize", "core:run_unknown"), ("authorize", "core:run_connection_mismatch")] * N)
        self.assertTrue(all(len(r["effect_sha256"]) == 64 for r in refused))
        self.assertEqual(book.orders(), [])

    def _booking_point(self, w: e2e.World, intent: Intent, approval: str, land) -> Refused:
        """Submit with ``land`` run right after the core authorized (and charged) the effect."""
        charged: list = []
        w.gateway._core = CoreClient(_after_authorize(w.plugin_transport, land, charged))
        try:
            with self.assertRaises(Refused) as ctx:
                w.gateway.submit(intent, approval, w.profile)
        finally:
            w.gateway._core = CoreClient(w.plugin_transport)
        self.assertEqual(len(charged), 1)  # the core authorized: its ledger is charged
        return ctx.exception

    def test_real_core_fault_or_trip_after_authorize_is_refused_at_the_booking_point(self) -> None:
        w = e2e.build_world(self)
        kinds = (("run_faulted", lambda run, conn: w.host("run.fault", run_id=run)),
                 ("connection_tripped",
                  lambda run, conn: w.host("breaker.report", connection=conn, event_type="rate_limit")))
        expected_audit = []
        for i in range(REAL_N):
            for code, land in kinds:
                run, conn = "run-%s-%d" % (code, i), "conn-%s-%d" % (code, i)
                self._bind(w, run, conn)
                intent = e2e.sized_buy(w, "%s-%d" % (code, i), quantity=1, run_id=run, connection=conn)
                digest = hashlib.sha256(canonical(w.gateway.risk_pass(intent))).hexdigest()
                exc = self._booking_point(w, intent, self._approved(w, intent), lambda: land(run, conn))
                self.assertEqual((exc.code, exc.cause, str(exc)),
                                 ("leftover_charge", "core:" + code, "leftover_charge:core:" + code))
                row = w.book.audit()[-1]
                self.assertEqual((row["seq"], row["effect_sha256"]), (exc.audit_seq, digest))
                expected_audit.append(("refused", "book", "leftover_charge:core:" + code, intent.intent_id, run, conn))
                self.assertEqual(w.book.orders(), [])
        # Control: a healthy run through the same wrapped transport books.
        ok = e2e.sized_buy(w, "bp-ok", quantity=1)
        approval, charged = self._approved(w, ok), []
        w.gateway._core = CoreClient(_after_authorize(w.plugin_transport, lambda: None, charged))
        self.assertEqual(w.gateway.submit(ok, approval, w.profile)["status"], "paper_working")
        expected_audit.append(("booked", "book", None, "bp-ok", e2e.RUN, e2e.CONNECTION))
        self.assertEqual(_audit_codes(w.book), expected_audit)
        self.assertEqual((len(charged), [o[0] for o in w.book.orders()]), (1, ["bp-ok"]))

    def test_real_core_run_binding_checked_at_request_authorize_and_the_booking_point(self) -> None:
        w = e2e.build_world(self)
        kinds = (("run_not_owned", "plugin_id", e2e.OTHER_PLUGIN_ID),
                 ("run_connection_mismatch", "connection", "conn-moved"))
        expected_audit = []
        for code, column, value in kinds:
            # At request: a run bound to another plugin, and a run cited on another connection.
            run, conn = "run-q-" + code, "conn-q-" + code
            if code == "run_not_owned":
                w.breaker.bind_run(run, conn, e2e.OTHER_PLUGIN_ID, e2e.PROVIDER, e2e.MODEL_VERSION)
                intent = e2e.sized_buy(w, "q-" + code, quantity=1, run_id=run, connection=conn)
            else:
                self._bind(w, run, conn)
                intent = e2e.sized_buy(w, "q-" + code, quantity=1, run_id=run)  # on conn-a, not its own
            drawn = len(w.drawn)
            with self.assertRaises(Refused) as ctx:
                w.gateway.request_approval(intent)
            self.assertEqual((ctx.exception.code, len(w.drawn)), ("core:" + code, drawn))
            expected_audit.append(("refused", "request", "core:" + code, intent.intent_id, run, intent.connection))
            # At authorize: the stored binding changes after the approval. No charge.
            run, conn = "run-a-" + code, "conn-a-" + code
            self._bind(w, run, conn)
            intent = e2e.sized_buy(w, "a-" + code, quantity=1, run_id=run, connection=conn)
            approval = self._approved(w, intent)
            _rewrite_binding(w, run, column, value)
            with self.assertRaises(Refused) as ctx:
                w.gateway.submit(intent, approval, w.profile)
            self.assertEqual((ctx.exception.code, ctx.exception.cause), ("core:" + code, None))
            expected_audit.append(("refused", "authorize", "core:" + code, intent.intent_id, run, conn))
            # At the booking point: the stored binding changes after the charge.
            run, conn = "run-b-" + code, "conn-b-" + code
            self._bind(w, run, conn)
            intent = e2e.sized_buy(w, "b-" + code, quantity=1, run_id=run, connection=conn)
            exc = self._booking_point(w, intent, self._approved(w, intent),
                                      lambda: _rewrite_binding(w, run, column, value))
            self.assertEqual((exc.code, exc.cause), ("leftover_charge", "core:" + code))
            expected_audit.append(("refused", "book", "leftover_charge:core:" + code, intent.intent_id, run, conn))
        ok = e2e.sized_buy(w, "bind-ok", quantity=1)
        self.assertEqual(w.gateway.submit(ok, self._approved(w, ok), w.profile)["status"], "paper_working")
        expected_audit.append(("booked", "book", None, "bind-ok", e2e.RUN, e2e.CONNECTION))
        self.assertEqual(_audit_codes(w.book), expected_audit)
        self.assertEqual([o[0] for o in w.book.orders()], ["bind-ok"])


if __name__ == "__main__":
    unittest.main()
