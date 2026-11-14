"""B15: a run fault or a connection trip at each point of the order path, on the fake core.

PRODUCT.md 4.2 "Two breakers, both in the way" (d84cdd26a4de2366f3c5d34c4aec5215f822a494):
"an order intent whose proposal came from a run on a route the engine breaker had
tripped, or from a run that ended in a fault, is refused at the `OrderGateway`."

Each test lands a run fault (the host's ``run.fault``) or a connection trip
(``breaker.report``) at one point and asserts the same outcome: ``Refused`` is raised,
nothing is booked, the account's cash is unchanged, and the refusal is the last row of the
append-only audit, committed and readable from a fresh connection.

Points, in order:

1. before ``request_approval``: ``core:<code>`` at stage ``request``, no approval drawn;
2. after the request and before the operator's decision: ``core:<code>`` at ``authorize``;
3. after the approval and before ``submit``: ``core:<code>`` at ``authorize``;
4. after ``effect.authorize`` returned and before the booking;
5. inside the booking's ``BEGIN IMMEDIATE`` transaction, before the gateway's checks run;
6. while the booking waits on the book's write lock, held by another connection.

At points 1 to 3 the core refuses and nothing is charged. At points 4 to 6 the core has
charged, so the refusal is ``leftover_charge`` with cause ``core:<code>`` at stage ``book``:
the gateway's run check, asked inside the booking transaction, sees the fault or trip.
``HealthyWindowTests`` runs the same paths with nothing landed, and they book.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/tests/paper -t engine -v``.
Paper only: no provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest

from cockpit.paper.core_client import CoreClient, canonical
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from tests.paper.fakecore import ALLOWLIST, AUTHORITY, DEFAULT_PLUGIN, FakeCore, sized

ACCT = "paper-acct"
CASH = 1_000_000
RUN, CONN = "run-w", "conn-w"
BASE = Intent(intent_id="w1", account=ACCT, instrument="AAPL", side="buy", quantity=2,
              limit_price_minor=100, run_id=RUN, connection=CONN)
NOTIONAL = 200
CODES = {"fault": "run_faulted", "trip": "connection_tripped"}
WAIT = 10  # seconds a synchronisation point may take before the test fails


class _SignallingDb:
    """The book's connection, setting ``about_to_begin`` right before each ``BEGIN IMMEDIATE``.

    With ``after_probe``, the first ``BEGIN IMMEDIATE`` is ``submit``'s write-lock probe
    before the charge: ``after_probe`` runs right after the probe lets the lock go, and only
    a later ``BEGIN IMMEDIATE`` (the booking's) sets ``about_to_begin``."""

    def __init__(self, db: sqlite3.Connection, about_to_begin: threading.Event, after_probe=None) -> None:
        self._db = db
        self._about_to_begin = about_to_begin
        self._after_probe = after_probe
        self._begins = 0

    def execute(self, sql: str, *args: object):
        if sql == "BEGIN IMMEDIATE":
            self._begins += 1
            if self._after_probe is None or self._begins > 1:
                self._about_to_begin.set()
        out = self._db.execute(sql, *args)
        if sql == "ROLLBACK" and self._after_probe is not None and self._begins == 1:
            self._after_probe()
        return out

    def close(self) -> None:
        self._db.close()


class _Window:
    """The fixture and the six landing points. ``kind`` is ``fault``, ``trip`` or ``none``."""

    kind = "none"

    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-fault-window-")
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, "book.db")
        create_book(self.path, cash_minor={ACCT: CASH})
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**9, runs={})
        self.core.bind_run(RUN, CONN)
        self.gw = OrderGateway(plugin_id=DEFAULT_PLUGIN, book=self.book, core=CoreClient(self.core),
                               caps=Caps(10**9, 10**6), allowlist=ALLOWLIST, run_check=self.core.check)
        self.intent = sized(self.gw, BASE)
        self.digest = hashlib.sha256(canonical(self.gw.risk_pass(self.intent))).hexdigest()

    def _land(self) -> None:
        if self.kind == "fault":
            self.core.faulted_runs.add(RUN)  # the host's run.fault
        elif self.kind == "trip":
            self.core.tripped_connections.add(CONN)  # breaker.report with a typed limit

    def _request(self) -> str:
        return self.gw.request_approval(self.intent)["approval_id"]

    def _approved(self) -> str:
        approval = self._request()
        self.core.operator_approves(approval)
        return approval

    def _submit(self, approval: str) -> object:
        try:
            return self.gw.submit(self.intent, approval, AUTHORITY)
        except Refused as exc:
            return exc

    # --- the landing points; each returns what ``request_approval`` or ``submit`` gave ---

    def _before_request(self) -> object:
        self._land()
        try:
            return self._request()
        except Refused as exc:
            return exc

    def _before_decision(self) -> object:
        approval = self._request()
        self._land()
        self.core.operator_approves(approval)
        return self._submit(approval)

    def _before_submit(self) -> object:
        approval = self._approved()
        self._land()
        return self._submit(approval)

    def _after_authorize(self) -> object:
        approval = self._approved()
        core, landed = self.core, []

        def transport(raw: bytes) -> bytes:
            out = core(raw)
            if json.loads(raw)["op"] == "effect.authorize" and json.loads(out)["ok"] is True:
                landed.append(True)
                self._land()  # after the core said yes, before the reply reaches the gateway
            return out
        self.gw._core = CoreClient(transport)
        outcome = self._submit(approval)
        self.assertEqual(landed, [True])
        return outcome

    def _in_booking_transaction(self) -> object:
        approval = self._approved()
        real_tx, landed = self.book._tx, []

        def tx(fn):
            def inner(db):
                if not landed:
                    landed.append((db.in_transaction, self.core.calls.count("effect.authorize")))
                    self._land()  # inside BEGIN IMMEDIATE, before the booking's own checks
                return fn(db)
            return real_tx(inner)
        self.book._tx = tx
        outcome = self._submit(approval)
        del self.book._tx
        self.assertEqual(landed, [(True, 1)], "the landing point was not inside the booking transaction")
        return outcome

    def _while_waiting_on_the_book_lock(self) -> object:
        """Time of check (``effect.authorize``) against time of use (the booking's commit).

        Another connection takes the book's write lock right after ``submit``'s pre-charge
        lock probe, as another process's booking or refusal audit would (the window the
        probe cannot close). ``submit`` passes the core and reaches the booking's
        ``BEGIN IMMEDIATE``, which cannot start while the lock is held; the fault or trip
        lands there, and only then is the lock released.
        """
        approval = self._approved()
        about_to_begin = threading.Event()
        blocker = sqlite3.connect(self.path, isolation_level=None, timeout=WAIT, check_same_thread=False)
        self.addCleanup(blocker.close)
        self.book._db = _SignallingDb(self.book._db, about_to_begin,
                                      after_probe=lambda: blocker.execute("BEGIN IMMEDIATE"))
        result = []
        worker = threading.Thread(target=lambda: result.append(self._submit(approval)), daemon=True)
        try:
            worker.start()
            self.assertTrue(about_to_begin.wait(WAIT), "submit never reached the booking transaction")
            # The second BEGIN IMMEDIATE of this submit is the booking's, and it follows the charge.
            self.assertEqual((self.core.calls.count("effect.authorize"), self.core.spent), (1, NOTIONAL))
            self._land()
            self.assertEqual(result, [], "submit finished while the book's write lock was held")
        finally:
            blocker.execute("ROLLBACK")
        worker.join(WAIT)
        self.assertFalse(worker.is_alive(), "submit did not return after the lock was released")
        return result[0]


class _RefusedAtEveryPoint(_Window):
    def _refused(self, outcome: object, stage: str, *, charged: bool) -> None:
        """``outcome`` is the typed refusal for this kind, audited at ``stage``; nothing booked."""
        core_code = "core:" + CODES[self.kind]
        self.assertIsInstance(outcome, Refused, "the %s landed before the booking committed, yet the gateway"
                              " returned %r" % (self.kind, outcome))
        expected = ("leftover_charge", core_code) if charged else (core_code, None)
        self.assertEqual((outcome.code, outcome.cause), expected)
        self.assertEqual(self.core.spent, NOTIONAL if charged else 0)
        if not charged:  # a refused authorize does not use the approval up
            self.assertNotIn("used", [item["state"] for item in self.core.approvals.values()])
        self.assertEqual(self.book.orders(), [], "an order row was written")
        self.assertEqual(self.book.working(), 0)
        self.assertEqual(self.book.available_cash_minor(ACCT), CASH, "cash was reserved for a refused intent")
        rows = self.book.audit()
        self.assertNotIn("booked", [r["event"] for r in rows])
        last = rows[-1]
        self.assertEqual((last["event"], last["stage"], last["code"]), ("refused", stage, str(outcome)))
        self.assertEqual(last["code"], "leftover_charge:" + core_code if charged else core_code)
        self.assertEqual((last["plugin_id"], last["intent_id"], last["run_id"], last["connection"]),
                         (DEFAULT_PLUGIN, BASE.intent_id, RUN, CONN))
        self.assertEqual(last["effect_sha256"], self.digest)
        self.assertEqual(outcome.audit_seq, last["seq"])
        other = PaperBook(self.path)  # committed in its own transaction: another connection reads it
        self.addCleanup(other.close)
        self.assertEqual(other.audit(), rows)
        self.assertEqual((other.orders(), other.available_cash_minor(ACCT)), ([], CASH))

    def test_before_request_is_refused(self) -> None:
        outcome = self._before_request()
        self.assertEqual(self.core.approvals, {}, "an approval was drawn for a faulted or tripped run")
        self._refused(outcome, "request", charged=False)

    def test_after_request_before_the_operator_decides_is_refused(self) -> None:
        self._refused(self._before_decision(), "authorize", charged=False)

    def test_after_approval_before_submit_is_refused(self) -> None:
        self._refused(self._before_submit(), "authorize", charged=False)

    def test_after_authorize_before_the_booking_is_refused(self) -> None:
        self._refused(self._after_authorize(), "book", charged=True)

    def test_inside_the_booking_transaction_is_refused(self) -> None:
        self._refused(self._in_booking_transaction(), "book", charged=True)

    def test_while_the_booking_waits_on_the_book_lock_is_refused(self) -> None:
        self._refused(self._while_waiting_on_the_book_lock(), "book", charged=True)


class FaultWindowTests(_RefusedAtEveryPoint, unittest.TestCase):
    """A run that ends in a fault at each point of the order path."""

    kind = "fault"


class TripWindowTests(_RefusedAtEveryPoint, unittest.TestCase):
    """The run's connection tripped by a typed limit at each point of the order path."""

    kind = "trip"


class HealthyWindowTests(_Window, unittest.TestCase):
    """Control: with nothing landed the same paths book, so the refusals above are not vacuous."""

    def _booked(self, outcome: object) -> None:
        self.assertIs(type(outcome), dict, "a healthy run was refused: %r" % (outcome,))
        self.assertEqual((outcome["status"], outcome["notional_minor"]), ("paper_working", NOTIONAL))
        self.assertEqual(self.book.orders(), [(BASE.intent_id, 1, "working")])
        self.assertEqual(self.book.available_cash_minor(ACCT), CASH - NOTIONAL)
        self.assertEqual([(r["event"], r["stage"], r["effect_sha256"]) for r in self.book.audit()],
                         [("booked", "book", self.digest)])

    def test_healthy_run_draws_an_approval(self) -> None:
        self.assertEqual([self._before_request()], list(self.core.approvals))

    def test_healthy_run_books_after_the_decision(self) -> None:
        self._booked(self._before_decision())

    def test_healthy_run_books_through_the_wrapped_transport(self) -> None:
        self._booked(self._after_authorize())

    def test_healthy_run_books_through_the_transaction_hook(self) -> None:
        self._booked(self._in_booking_transaction())

    def test_healthy_run_books_after_waiting_on_the_book_lock(self) -> None:
        self._booked(self._while_waiting_on_the_book_lock())


if __name__ == "__main__":
    unittest.main()
