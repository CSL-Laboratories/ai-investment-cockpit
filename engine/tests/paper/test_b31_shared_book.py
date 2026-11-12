"""B31 on one shared ``PaperBook`` and a halt landing just before the booking transaction.

Companion to ``test_b31_race_stress.py`` and ``test_b31_interleave.py``. Those give every
thread its own ``PaperBook`` and halt while the core is authorizing, so two lines of
``gateway.py`` were never what made them pass:

- the in-process ``PaperBook._lock`` (one sqlite connection shared by several threads);
- the halt re-read inside the booking's ``BEGIN IMMEDIATE`` transaction (the booking-time
  risk pass outside the transaction refused first).

Here four threads share one gateway and one ``PaperBook``, and a second connection halts
after the booking-time risk pass has returned and before the transaction starts, so only the
in-transaction re-read can refuse. Each scenario returns its problems as a list; the
``MutantSelfCheck`` tests keep these checks non-vacuous: the lock check safely records
competing entrants while still serialising the connection; the halt check neutralises the
re-read in-process and requires the scenario to report a booking after the halt.

Run: ``PYTHONPATH=engine python3 -B -m unittest engine/tests/paper/test_b31_shared_book.py -v``,
and with ``python3.14t -X gil=0``. Paper only: the core is a local double that approves
every effect and every run.
"""

from __future__ import annotations

import random
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from tests.paper.fakecore import ALLOWLIST, sized
from tests.paper.test_b31_interleave import AUTHORITY, PausingCore

ACCT = "paper-acct"
PRICE = 10_000
WAIT = 30.0
THREADS = 4
ROUNDS = 40


class _RecordingLock:
    """Record entrants that would overlap without ``PaperBook._lock``, safely.

    The first entrant holds the real lock until a contender arrives, making the
    overlap deterministic. No two threads ever enter sqlite through this stand-in.
    """

    def __init__(self) -> None:
        self._serial = threading.Lock()
        self.contended = threading.Event()

    def __enter__(self):
        if not self._serial.acquire(blocking=False):
            self.contended.set()
            if not self._serial.acquire(timeout=WAIT):
                raise AssertionError("recording lock contender timed out")
        elif not self.contended.wait(WAIT):
            self._serial.release()
            raise AssertionError("shared-book stress no longer contends for PaperBook._lock")
        return self

    def __exit__(self, *exc):
        self._serial.release()
        return False


class _NoHaltRereadDb:
    """Mutant stand-in for a book connection: inside a transaction the halt flag reads 0."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def execute(self, sql, *args):
        if self._db.in_transaction and sql == "SELECT v FROM state WHERE k = 'halted'":
            return self._db.execute("SELECT 0")
        return self._db.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._db, name)


def _intent(intent_id: str, qty: int, side: str) -> Intent:
    return Intent(intent_id=intent_id, account=ACCT, instrument="AAPL", side=side, quantity=qty,
                  limit_price_minor=PRICE, run_id="run-1", connection="conn-a")


def _submit(gw: OrderGateway, intent: Intent):
    try:
        return gw.submit(intent, gw.request_approval(intent)["approval_id"], AUTHORITY)
    except Refused as exc:
        return (exc.code, exc.cause)


def _conservation(path: str, start_cash: int, start_held: int) -> list[str]:
    db = sqlite3.connect(path, isolation_level=None, timeout=10)
    try:
        db.execute("BEGIN")
        cash = db.execute("SELECT available_minor FROM cash WHERE account = ?", (ACCT,)).fetchone()[0]
        row = db.execute("SELECT available FROM holdings WHERE account = ? AND instrument = 'AAPL'",
                         (ACCT,)).fetchone()
        held = 0 if row is None else row[0]
        buys = db.execute("SELECT COALESCE(SUM(quantity * limit_price_minor), 0) FROM orders"
                          " WHERE side = 'buy' AND status = 'working'").fetchone()[0]
        sells = db.execute("SELECT COALESCE(SUM(quantity), 0) FROM orders"
                           " WHERE side = 'sell' AND status = 'working'").fetchone()[0]
        db.execute("COMMIT")
    finally:
        db.close()
    problems = []
    if cash < 0 or cash + buys != start_cash:
        problems.append(f"cash {cash} + working buys {buys} != {start_cash}")
    if held < 0 or held + sells != start_held:
        problems.append(f"held {held} + working sells {sells} != {start_held}")
    return problems


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="cockpit-b31-shared-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.n = 0

    def new_book(self, cash: int, held: int = 0) -> str:
        self.n += 1
        path = f"{self.dir}/book{self.n}.db"
        create_book(path, cash_minor={ACCT: cash}, holdings={(ACCT, "AAPL"): held} if held else None)
        return path

    def gateway(self, book: PaperBook) -> OrderGateway:
        core = PausingCore()
        return OrderGateway(plugin_id="csl.cockpit.paper", book=book, core=CoreClient(core),
                            caps=Caps(10**12, 10**6), allowlist=ALLOWLIST, run_check=core.check)

    def shared_book_stress(self, mutate=None) -> list[str]:
        """Four threads, one gateway, one ``PaperBook``: buys, sells and cancels at random."""
        start_cash, start_held = 30 * PRICE, 12
        path = self.new_book(start_cash, start_held)
        book = PaperBook(path)
        if mutate is not None:
            mutate(book)
        gw = self.gateway(book)
        problems: list[str] = []
        booked: list[str] = []
        lock = threading.Lock()
        allowed = {("cash_insufficient", None), ("holding_insufficient", None),
                   ("leftover_charge", "cash_insufficient"), ("leftover_charge", "holding_insufficient")}
        barrier = threading.Barrier(THREADS)

        def worker(n: int) -> None:
            rng = random.Random(n)
            try:
                barrier.wait(WAIT)
                for i in range(ROUNDS):
                    roll = rng.random()
                    if roll < 0.1:
                        gw.cancel_all()
                        continue
                    side = "buy" if roll < 0.6 else "sell"
                    intent = _intent(f"t{n}-{i}", rng.randint(1, 6), side)
                    res = _submit(gw, sized(gw, intent))
                    if isinstance(res, dict):
                        with lock:
                            booked.append(intent.intent_id)
                    elif isinstance(res, dict) and res not in allowed:
                        with lock:
                            problems.append(f"{intent.intent_id}: {res}")
            except BaseException as exc:  # noqa: BLE001 - an untyped error is the finding
                with lock:
                    problems.append(f"t{n}: {exc!r}")

        threads = [threading.Thread(target=worker, args=(n,), daemon=True) for n in range(THREADS)]
        for t in threads:
            t.start()
        deadline = time.monotonic() + WAIT
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
        if any(t.is_alive() for t in threads):
            return problems + ["worker thread(s) hung"]
        try:
            if book._db.in_transaction:
                problems.append("connection left inside a transaction")
            book.close()
        except BaseException as exc:  # noqa: BLE001
            problems.append(f"close: {exc!r}")
        if not booked:
            problems.append("nothing booked: the race never ran")
        return problems + _conservation(path, start_cash, start_held)

    def halt_before_begin(self, mutate=None) -> list[str]:
        """A second connection halts after the booking-time risk pass and before ``BEGIN IMMEDIATE``."""
        path = self.new_book(10 * PRICE)
        book_a, book_b = PaperBook(path), PaperBook(path)
        self.addCleanup(book_a.close)
        self.addCleanup(book_b.close)
        if mutate is not None:
            mutate(book_a)
        gw = self.gateway(book_a)
        intent = sized(gw, _intent("late-halt", 1, "buy"))
        approval = gw.request_approval(intent)["approval_id"]
        real, calls = gw.risk_pass, []

        def risk_then_halt(candidate):
            effect = real(candidate)
            calls.append(len(calls))
            if len(calls) == 2:  # the booking-time re-run, after the core has charged
                book_b.halt()
            return effect

        gw.risk_pass = risk_then_halt  # instance attribute on this test's own gateway
        problems = []
        try:
            res = gw.submit(intent, approval, AUTHORITY)
            problems.append(f"booked after the halt: {res!r}")
        except Refused as exc:
            if (exc.code, exc.cause) != ("leftover_charge", "order_halted") or exc.audit_seq is None:
                problems.append(f"refused {(exc.code, exc.cause, exc.audit_seq)!r}")
        if len(calls) != 2:
            problems.append(f"risk pass ran {len(calls)} times, expected submit's and the booking's")
        if book_b.orders():
            problems.append(f"orders after the halt: {book_b.orders()!r}")
        if book_b.available_cash_minor(ACCT) != 10 * PRICE:
            problems.append("cash moved")
        return problems


class SharedBookTests(Case):
    def test_four_threads_on_one_paperbook_keep_cash_and_shares_exact(self) -> None:
        self.assertEqual(self.shared_book_stress(), [])

    def test_halt_between_booking_time_risk_pass_and_begin_immediate_wins(self) -> None:
        for _ in range(5):
            self.assertEqual(self.halt_before_begin(), [])


class MutantSelfCheck(Case):
    """The scenarios above still depend on the lock and the halt re-read."""

    def test_removing_the_in_process_lock_is_noticed(self) -> None:
        recording = _RecordingLock()

        def record_lock(book):
            book._lock = recording

        self.assertEqual(self.shared_book_stress(record_lock), [])
        self.assertTrue(recording.contended.is_set(),
                        "shared-book stress no longer contends for PaperBook._lock")

    def test_removing_the_in_transaction_halt_reread_is_noticed(self) -> None:
        def no_reread(book):
            book._db = _NoHaltRereadDb(book._db)

        self.assertIn("booked after the halt", " ".join(self.halt_before_begin(no_reread)))


if __name__ == "__main__":
    unittest.main()
