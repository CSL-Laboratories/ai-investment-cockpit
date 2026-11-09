"""B31 forced interleavings: the send-time window held open on purpose (CSL-801, session 2).

Companion to ``test_b31_race_stress.py``, which races many threads and hopes to land in the
window between the gateway's early funds read and the booking. Here the window is forced: the
core double blocks inside ``effect.authorize`` for one intent (after its early funds read has
passed) while a second ``PaperBook`` connection, as a second process would have, spends the cash
or shares. B31 (Lucas t1414u; PRODUCT.md 055cc7a5, 4.2): the booking re-reads tracked cash in
its own ``BEGIN IMMEDIATE`` transaction and refuses with ``cash_insufficient``; it never clamps
or resizes.

Checks: the refusal is typed and audited at stage ``book``; nothing is booked for the paused
intent; available cash plus working buy notional still equals starting cash; one intent id
books once across connections.

Run: ``PYTHONPATH=engine python3 -B -m unittest engine/cockpit/paper/tests/test_b31_interleave.py -v``,
and with ``PYTHON_GIL=0 python3.14t -X gil=0``. ``COCKPIT_RACE_ITERS`` (default 10) repeats
each forced window. Paper only: the core is a local double that approves every effect.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest

from cockpit.paper.core_client import CoreClient, canonical
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book

ITERS = int(os.environ.get("COCKPIT_RACE_ITERS", "10"))
THREADS = 8
ACCT = "paper-acct"
PRICE = 10_000
AUTHORITY = {"profile": "fake", "signature": "fake"}


class Gate:
    """The thread that reaches ``hit`` blocks until the test calls ``release``."""

    def __init__(self) -> None:
        self.reached = threading.Event()
        self._go = threading.Event()

    def hit(self) -> None:
        self.reached.set()
        if not self._go.wait(30):
            raise TimeoutError("gate never released")

    def wait_reached(self) -> None:
        if not self.reached.wait(30):
            raise TimeoutError("gate never reached")

    def release(self) -> None:
        self._go.set()


class PausingCore:
    """``csl-core/v0`` double: approves every effect; blocks one intent inside authorize."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._n = 0
        self.pause: dict[str, Gate] = {}

    def __call__(self, raw: bytes) -> bytes:
        msg = json.loads(raw)
        effect = msg["args"]["effect"]
        digest = hashlib.sha256(canonical(effect)).hexdigest()
        if msg["op"] == "effect.request":
            with self._lock:
                self._n += 1
                body = {"approval_id": f"apr_{self._n}", "digest": digest}
        else:
            gate = self.pause.get(effect["payload"]["intent_id"])
            if gate is not None:
                gate.hit()
            body = {"authorized": True, "digest": digest, "charge": None}
        return canonical({"protocol": "csl-core/v0", "ok": True, "result": body})


class InterleaveCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="cockpit-b31-interleave-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.n = 0

    def new_book(self, cash: int, holdings=None) -> str:
        self.n += 1
        path = os.path.join(self.dir, f"book{self.n}.db")
        create_book(path, cash_minor={ACCT: cash}, holdings=holdings)
        return path

    def gateway(self, path: str):
        """One gateway on its own PaperBook connection."""
        book = PaperBook(path)
        self.addCleanup(book.close)
        core = PausingCore()
        gw = OrderGateway(plugin_id="csl.cockpit.paper", book=book, core=CoreClient(core), caps=Caps(10**12, 10**6))
        return gw, book, core

    @staticmethod
    def intent(intent_id: str, qty: int, side: str = "buy") -> Intent:
        return Intent(intent_id=intent_id, account=ACCT, instrument="AAPL", side=side, quantity=qty,
                      limit_price_minor=PRICE, run_id="run-1", connection="conn-a")

    @staticmethod
    def submit(gw, intent):
        return gw.submit(intent, gw.request_approval(intent)["approval_id"], AUTHORITY)

    def paused_submit(self, gw, core, intent):
        """Start ``submit`` and return once it is blocked inside authorize, past the early funds read."""
        approval = gw.request_approval(intent)["approval_id"]
        gate = Gate()
        core.pause[intent.intent_id] = gate
        out: dict = {}

        def run():
            try:
                out["result"] = gw.submit(intent, approval, AUTHORITY)
            except Refused as exc:
                out["result"] = exc
            except BaseException as exc:  # noqa: BLE001 - anything untyped is the finding
                out["result"] = exc

        t = threading.Thread(target=run)
        t.start()
        gate.wait_reached()
        return gate, t, out

    def assert_cash_conserved(self, book, start: int) -> None:
        working = book._db.execute("SELECT quantity, limit_price_minor FROM orders"
                                   " WHERE side = 'buy' AND status = 'working'").fetchall()
        available = book.available_cash_minor(ACCT)
        self.assertGreaterEqual(available, 0)
        self.assertEqual(available + sum(q * p for q, p in working), start)


class SendTimeWindowTests(InterleaveCase):
    def test_cash_spent_between_early_read_and_booking_is_refused_not_clamped(self) -> None:
        for i in range(ITERS):
            start = 50_000
            path = self.new_book(start)
            gw_a, book_a, core_a = self.gateway(path)
            gw_b, _, _ = self.gateway(path)
            sized = gw_a.size_buy(intent_id=f"a{i}", account=ACCT, instrument="AAPL", limit_price_minor=PRICE,
                                  conviction_bps=10_000, run_id="run-1", connection="conn-a")
            self.assertEqual(sized.quantity, 5)  # sized to all of the cash
            gate, t, out = self.paused_submit(gw_a, core_a, sized)
            self.submit(gw_b, self.intent(f"b{i}", 1))  # another connection spends one share's cash
            gate.release()
            t.join(30)
            exc = out["result"]
            self.assertIsInstance(exc, Refused, f"iteration {i}: booked over cash or untyped error: {exc!r}")
            self.assertEqual(exc.code, "cash_insufficient")
            rows = [(r["event"], r["stage"], r["code"]) for r in book_a.audit() if r["intent_id"] == sized.intent_id]
            self.assertEqual(rows, [("refused", "book", "cash_insufficient")])
            self.assertEqual([o[0] for o in book_a.orders()], [f"b{i}"])  # nothing booked for A, not 4 shares
            self.assertEqual(book_a.available_cash_minor(ACCT), start - PRICE)
            self.assert_cash_conserved(book_a, start)

    def test_exact_fit_after_the_window_still_books_whole(self) -> None:
        """Control: if the other connection leaves exactly enough, the paused order books unchanged."""
        for i in range(ITERS):
            path = self.new_book(6 * PRICE)
            gw_a, book_a, core_a = self.gateway(path)
            gw_b, _, _ = self.gateway(path)
            gate, t, out = self.paused_submit(gw_a, core_a, self.intent(f"a{i}", 5))
            self.submit(gw_b, self.intent(f"b{i}", 1))
            gate.release()
            t.join(30)
            self.assertEqual(out["result"]["status"], "paper_working", out["result"])
            self.assertEqual(out["result"]["notional_minor"], 5 * PRICE)
            self.assertEqual(book_a.available_cash_minor(ACCT), 0)

    def test_held_shares_sold_between_early_read_and_booking_are_refused(self) -> None:
        for i in range(ITERS):
            path = self.new_book(0, holdings={(ACCT, "AAPL"): 3})
            gw_a, book_a, core_a = self.gateway(path)
            gw_b, _, _ = self.gateway(path)
            gate, t, out = self.paused_submit(gw_a, core_a, self.intent(f"s-a{i}", 3, "sell"))
            self.submit(gw_b, self.intent(f"s-b{i}", 1, "sell"))
            gate.release()
            t.join(30)
            self.assertEqual(getattr(out["result"], "code", out["result"]), "holding_insufficient")
            self.assertEqual(book_a.held_shares(ACCT, "AAPL"), 2)
            self.assertEqual([o[0] for o in book_a.orders()], [f"s-b{i}"])

    def test_halt_inside_the_window_wins(self) -> None:
        for i in range(ITERS):
            path = self.new_book(10 * PRICE)
            gw_a, book_a, core_a = self.gateway(path)
            gw_b, _, _ = self.gateway(path)
            gate, t, out = self.paused_submit(gw_a, core_a, self.intent(f"a{i}", 1))
            gw_b.halt()  # the operator halts from another process while A is being authorized
            gate.release()
            t.join(30)
            self.assertEqual(getattr(out["result"], "code", out["result"]), "order_halted")
            self.assertEqual(book_a.orders(), [])
            self.assertEqual(book_a.available_cash_minor(ACCT), 10 * PRICE)


class DuplicateIntentTests(InterleaveCase):
    def test_one_intent_id_books_once_across_connections(self) -> None:
        for i in range(ITERS):
            path = self.new_book(10**9)
            gws = [self.gateway(path) for _ in range(THREADS)]
            barrier = threading.Barrier(THREADS)
            codes: list = []
            lock = threading.Lock()

            def go(n):
                barrier.wait()
                try:
                    self.submit(gws[n][0], self.intent(f"dup{i}", 1))
                    code = None
                except Refused as exc:
                    code = exc.code
                except BaseException as exc:  # noqa: BLE001
                    code = repr(exc)
                with lock:
                    codes.append(code)

            threads = [threading.Thread(target=go, args=(n,)) for n in range(THREADS)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
            self.assertEqual(codes.count(None), 1, codes)
            self.assertTrue(all(c == "intent_duplicate" for c in codes if c is not None), codes)
            book = gws[0][1]
            self.assertEqual(len(book.orders()), 1)
            self.assert_cash_conserved(book, 10**9)


if __name__ == "__main__":
    unittest.main()
