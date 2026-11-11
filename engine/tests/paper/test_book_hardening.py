"""Paper book hardening on the order path (#320): lock, halt priority, open check, kill path.

- A book another connection holds write-locked is refused ``book_locked`` before the core
  charges, and the unused approval still books once the lock is gone.
- ``halt`` is not starved by refusal commits on other connections: it returns within a bound.
- ``PaperBook(path)`` refuses a book without the cash ``CHECK`` constraints, the append-only
  audit triggers or the working-order index, and reads ``sqlite_master`` once on open.
- ``cancel_all`` runs the same statements however many orders are working, releases cash and
  shares exactly, and ``working()`` reads the partial index, not the cancelled history.

Paper only, fake core: no provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from unittest import mock

from cockpit.paper import gateway as gw_mod
from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, OrderGateway, PaperBook, Refused, create_book
from tests.paper.fakecore import ALLOWLIST, AUTHORITY, FakeCore, sized
from tests.paper.test_gateway import INTENT

CAPS = Caps(10**9, 10**6)
WAIT = 10  # seconds any synchronisation point may take before the test fails


def _tmp(test: unittest.TestCase) -> str:
    d = tempfile.mkdtemp(prefix="cockpit-book-hardening-")
    test.addCleanup(shutil.rmtree, d, True)
    return d


def _gateway(path: str, core: FakeCore | None = None) -> tuple[OrderGateway, FakeCore, PaperBook]:
    core = core or FakeCore(budget_minor=10**15)
    book = PaperBook(path)
    gw = OrderGateway(plugin_id="csl.cockpit.paper", book=book, core=CoreClient(core), caps=CAPS,
                      allowlist=ALLOWLIST, run_check=core.check)
    return gw, core, book


def _book_one(gw: OrderGateway, core: FakeCore, intent) -> dict:
    intent = sized(gw, intent)
    approval = gw.request_approval(intent)["approval_id"]
    core.operator_approves(approval)
    return gw.submit(intent, approval, AUTHORITY)


class LockedBookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = os.path.join(_tmp(self), "book.db")
        create_book(self.path, cash_minor={"paper-acct": 10**12})
        self.gw, self.core, self.book = _gateway(self.path)
        self.addCleanup(self.book.close)

    def test_a_write_locked_book_is_refused_before_the_core_charges(self) -> None:
        intent = sized(self.gw, replace(INTENT, intent_id="lk-1"))
        approval = self.gw.request_approval(intent)["approval_id"]
        self.core.operator_approves(approval)
        blocker = sqlite3.connect(self.path, isolation_level=None, timeout=WAIT)
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with mock.patch.object(gw_mod, "BOOK_LOCK_PROBE_S", 0.05):
                started = time.monotonic()
                with self.assertRaises(Refused) as ctx:
                    self.gw.submit(intent, approval, AUTHORITY)
                took = time.monotonic() - started
        finally:
            blocker.execute("ROLLBACK")
        self.assertEqual((ctx.exception.code, ctx.exception.cause), ("book_locked", None))
        self.assertLess(took, 5, "the locked book was waited on for the full busy timeout")
        self.assertNotIn("effect.authorize", self.core.calls, "the core was asked to charge a locked book")
        self.assertEqual(self.core.spent, 0)
        self.assertEqual(self.book.orders(), [])
        # The approval was never taken, so once the lock is gone the same order books.
        booked = self.gw.submit(intent, approval, AUTHORITY)
        self.assertEqual(booked["status"], "paper_working")
        self.assertEqual(self.core.spent, booked["notional_minor"])

    def test_an_unlocked_book_takes_no_extra_commit(self) -> None:
        """The probe takes the write lock and lets it go: no write, so no extra commit."""
        statements: list[str] = []
        self.book._db.set_trace_callback(statements.append)
        _book_one(self.gw, self.core, replace(INTENT, intent_id="lk-2"))
        self.book._db.set_trace_callback(None)
        self.assertEqual(statements.count("COMMIT"), 1, statements)
        self.assertEqual(statements.count("ROLLBACK"), 1, statements)


def _refusal_storm(path: str, stop: threading.Event, counts: list, ready: threading.Barrier) -> None:
    book = PaperBook(path)
    row = {"stage": "risk", "code": "paper_only", "plugin_id": "csl.cockpit.paper"}
    n = 0
    try:
        ready.wait(WAIT)
        while not stop.is_set():
            book.record_refusal(row)
            n += 1
    finally:
        counts.append(n)
        book.close()


class HaltPriorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = os.path.join(_tmp(self), "book.db")
        create_book(self.path, cash_minor={"paper-acct": 10**12})

    def test_halt_is_not_starved_by_refusal_commits_on_other_connections(self) -> None:
        stop, counts = threading.Event(), []
        ready = threading.Barrier(4)
        threads = [threading.Thread(target=_refusal_storm, args=(self.path, stop, counts, ready), daemon=True)
                   for _ in range(3)]
        for t in threads:
            t.start()
        halter = PaperBook(self.path)
        self.addCleanup(halter.close)
        try:
            ready.wait(WAIT)
            time.sleep(0.2)  # let the storm reach full rate
            started = time.monotonic()
            seq = halter.halt()
            took = time.monotonic() - started
        finally:
            stop.set()
            for t in threads:
                t.join(WAIT)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertLess(took, 2.0, "halt took %.3f s under a refusal storm" % took)
        self.assertTrue(halter.halted())
        self.assertEqual(halter.halt_seq(), seq)
        self.assertGreater(sum(counts), 0, "the storm never committed: the test proves nothing")
        self.assertFalse(os.path.exists(halter._halt_flag), "the halt flag was left behind")

    def test_a_writer_waits_while_a_halt_flag_is_fresh_and_goes_on_once_it_is_gone(self) -> None:
        book = PaperBook(self.path)
        self.addCleanup(book.close)
        flag = book._halt_flag
        open(flag, "w").close()
        done = threading.Event()
        row = {"stage": "risk", "code": "paper_only", "plugin_id": "csl.cockpit.paper"}
        writer = threading.Thread(target=lambda: (book.record_refusal(row), done.set()), daemon=True)
        writer.start()
        self.assertFalse(done.wait(0.3), "a writer went ahead of a halt in progress")
        os.unlink(flag)
        self.assertTrue(done.wait(WAIT), "the writer did not go on after the halt flag was removed")
        writer.join(WAIT)

    def test_a_stale_halt_flag_is_removed_and_does_not_slow_writers(self) -> None:
        book = PaperBook(self.path)
        self.addCleanup(book.close)
        flag = book._halt_flag
        open(flag, "w").close()
        old = time.time() - gw_mod.HALT_YIELD_S - 5
        os.utime(flag, (old, old))
        started = time.monotonic()
        book.record_refusal({"stage": "risk", "code": "paper_only", "plugin_id": "csl.cockpit.paper"})
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(os.path.exists(flag))

    def test_every_connection_to_one_book_shares_one_flag(self) -> None:
        a, b = PaperBook(self.path), PaperBook(os.path.join(os.path.dirname(self.path), ".", "book.db"))
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        self.assertEqual(a._halt_flag, b._halt_flag)
        other = os.path.join(os.path.dirname(self.path), "other.db")
        create_book(other, cash_minor={"paper-acct": 1})
        c = PaperBook(other)
        self.addCleanup(c.close)
        self.assertNotEqual(a._halt_flag, c._halt_flag)

    def test_a_halt_flag_that_cannot_be_made_still_halts(self) -> None:
        book = PaperBook(self.path)
        self.addCleanup(book.close)
        book._halt_flag = os.path.join(self.path + ".missing-dir", "x-halt")
        book.halt()
        self.assertTrue(book.halted())


def _raw_book(path: str, tables, triggers, indexes) -> None:
    """A book made like ``create_book`` but from the given statements."""
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("BEGIN IMMEDIATE")
        for schema in (*tables, *triggers, *indexes):
            db.execute(schema)
        db.executemany("INSERT INTO state VALUES (?, 0)", [(k,) for k in gw_mod._STATE_KEYS])
        db.execute("INSERT INTO cash VALUES ('paper-acct', 50000)")
        db.execute("COMMIT")
    finally:
        db.close()


class OpenValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = _tmp(self)

    def _refused(self, name: str, tables=None, triggers=None, indexes=None) -> None:
        path = os.path.join(self.dir, name + ".db")
        _raw_book(path, gw_mod._TABLE_SCHEMAS if tables is None else tables,
                  gw_mod._TRIGGER_SCHEMAS if triggers is None else triggers,
                  gw_mod._INDEX_SCHEMAS if indexes is None else indexes)
        with self.assertRaises(Refused) as ctx:
            PaperBook(path).close()
        self.assertEqual(ctx.exception.code, "book_invalid")

    def test_control_a_raw_book_made_from_the_same_statements_opens(self) -> None:
        path = os.path.join(self.dir, "control.db")
        _raw_book(path, gw_mod._TABLE_SCHEMAS, gw_mod._TRIGGER_SCHEMAS, gw_mod._INDEX_SCHEMAS)
        PaperBook(path).close()

    def test_a_book_without_the_cash_checks_is_refused(self) -> None:
        tables = tuple(t.replace(" CHECK (available_minor >= 0)", "").replace(" CHECK (available >= 0)", "")
                       for t in gw_mod._TABLE_SCHEMAS)
        self.assertNotEqual(tables, gw_mod._TABLE_SCHEMAS)
        self._refused("no-checks", tables=tables)

    def test_a_book_without_the_append_only_triggers_is_refused(self) -> None:
        self._refused("no-triggers", triggers=())
        self._refused("one-trigger", triggers=gw_mod._TRIGGER_SCHEMAS[:1])

    def test_a_book_without_the_working_index_is_refused(self) -> None:
        self._refused("no-index", indexes=())

    def test_a_book_with_an_extra_trigger_is_refused(self) -> None:
        extra = "CREATE TRIGGER cash_free AFTER INSERT ON orders BEGIN UPDATE cash SET available_minor = 0; END"
        self._refused("extra-trigger", triggers=(*gw_mod._TRIGGER_SCHEMAS, extra))

    def test_open_reads_the_schema_once(self) -> None:
        path = os.path.join(self.dir, "book.db")
        create_book(path, cash_minor={"paper-acct": 1})
        statements: list[str] = []
        real = sqlite3.connect

        def traced(*args, **kwargs):
            db = real(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db
        with mock.patch.object(gw_mod.sqlite3, "connect", traced):
            PaperBook(path).close()
        schema_reads = [s for s in statements if "sqlite_master" in s or "table_info" in s]
        self.assertEqual(len(schema_reads), 1, schema_reads)


class KillPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = os.path.join(_tmp(self), "book.db")
        self.cash = {"paper-acct": 10**12, "acct-b": 10**12}
        self.held = {("paper-acct", "MSFT"): 10**6, ("acct-b", "XYZ"): 10**6}
        create_book(self.path, cash_minor=dict(self.cash), holdings=dict(self.held))
        self.gw, self.core, self.book = _gateway(self.path)
        self.addCleanup(self.book.close)

    def _book_working(self, n: int, tag: str) -> None:
        for i in range(n):
            account = "paper-acct" if i % 2 else "acct-b"
            if i % 3 == 0:
                instrument = "MSFT" if account == "paper-acct" else "XYZ"
                intent = replace(INTENT, intent_id="%s-s%d" % (tag, i), account=account, instrument=instrument,
                                 side="sell", quantity=1 + i % 4)
            else:
                intent = replace(INTENT, intent_id="%s-b%d" % (tag, i), account=account, quantity=1 + i % 5,
                                 limit_price_minor=100 + i)
            self.assertEqual(_book_one(self.gw, self.core, intent)["status"], "paper_working")

    def _cancel_statements(self) -> tuple[int, int]:
        statements: list[str] = []
        self.book._db.set_trace_callback(statements.append)
        try:
            cancelled = self.book.cancel_all()
        finally:
            self.book._db.set_trace_callback(None)
        return cancelled, len(statements)

    def test_cancel_all_runs_the_same_statements_however_many_orders_work(self) -> None:
        self._book_working(10, "a")
        ten = self._cancel_statements()
        self._book_working(100, "b")
        hundred = self._cancel_statements()
        self.assertEqual((ten[0], hundred[0]), (10, 100))
        self.assertEqual(ten[1], hundred[1], "statements per cancel_all grew with working orders")

    def test_cancel_all_releases_cash_and_shares_exactly(self) -> None:
        self._book_working(37, "c")
        self.assertNotEqual([self.book.available_cash_minor(a) for a in self.cash], list(self.cash.values()))
        self.assertEqual(self.book.cancel_all(), 37)
        self.assertEqual(self.book.working(), 0)
        for account, minor in self.cash.items():
            self.assertEqual(self.book.available_cash_minor(account), minor, account)
        for (account, instrument), shares in self.held.items():
            self.assertEqual(self.book.held_shares(account, instrument), shares, (account, instrument))
        self.assertEqual(self.book.cancel_all(), 0)
        self.assertEqual([self.book.available_cash_minor(a) for a in self.cash], list(self.cash.values()))

    def test_working_orders_are_read_through_the_partial_index(self) -> None:
        self._book_working(5, "d")
        for sql in ("SELECT COUNT(*) FROM orders WHERE status = 'working'",
                    "UPDATE orders SET status = 'cancelled' WHERE status = 'working'"):
            with self.subTest(sql=sql):
                plan = " ".join(row[-1] for row in self.book._db.execute("EXPLAIN QUERY PLAN " + sql))
                self.assertIn("orders_working", plan)


if __name__ == "__main__":
    unittest.main()
