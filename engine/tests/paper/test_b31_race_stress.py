"""B31 race and stress: cash refused at send time, never clamped, under many threads (CSL-801).

Lane CSL-801 (3.14t race and stress), measured on this branch's base, Cockpit #320.
B31 (Lucas t1414u; PRODUCT.md d84cdd26a4de2366f3c5d34c4aec5215f822a494 (CSLR-D-012), 4.2): "size to cash, refuse at the gateway
... it never clamps or resizes."

Each iteration gives every thread its own ``PaperBook`` connection to one book file (as
separate processes would have) and runs sized buys, fixed-size buys that may exceed cash,
held-share sells, ``cancel_all`` and, sometimes, ``halt``. The approval step yields, so other
threads spend cash between the early funds read and the booking. Fixed-size buys go through
the ``fakecore.sized`` test seam, since the gateway refuses a buy it did not size. Checks:

* the gateway refuses every over-cash order with a typed ``Refused``: a plain code before the
  core charge, ``leftover_charge`` with the booking refusal in ``.cause`` after it; the database
  ``CHECK (available_minor >= 0)`` never has to stop one (no ``sqlite3`` error escapes and no
  ``leftover_charge`` carries ``booking_error:IntegrityError``);
* never clamps: each booked order has exactly the submitted quantity, and the returned
  ``notional_minor`` is quantity times limit price;
* conservation per account: starting cash == available cash + working buy notional, and the
  same for held shares and working sells;
* halt wins: no order is booked with a sequence number after the halt's, and once the halt is
  committed every thread sends one more order, which must be refused ``order_halted``;
* every booking has its ``booked`` audit row and every refusal its ``refused`` audit row,
  except a bounded pre-charge ``book_locked`` refusal, whose audit write may also time out.
  That refusal is skipped only before the halt and before the charge: a ``book_locked`` for an
  order sent or submitted after the committed halt, or for an intent the core has authorized,
  is still a violation.

A sensitivity test drops the send-time check inside the booking transaction (B31's forbidden
shape) and holds the send-time window open (``forced_window``: one buy blocked inside the core's
authorize while another connection spends cash), so the mutant is caught every time as the
database CHECK firing (``leftover_charge`` with cause ``booking_error:IntegrityError``). A clean
run therefore means something. The same window without the mutant is the control.

Run: ``PYTHONPATH=engine python3 -B -m unittest engine/tests/paper/test_b31_race_stress.py -v``,
and on the free-threaded build with ``PYTHON_GIL=0 python3.14t -X gil=0``.
``COCKPIT_RACE_ITERS`` (default 10) and ``COCKPIT_RACE_SEED`` (default ``DEFAULT_SEED``, printed
with every run) tune the run. Paper only: the Recursion core is a duck-typed fake that approves
every effect and every run; no venue, network or credential.
"""

from __future__ import annotations

import hashlib
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
import unittest
from unittest import mock

from cockpit.paper import gateway as gw_mod
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from tests.paper.fakecore import ALLOWLIST, sized
from tests.paper.test_b31_interleave import Gate

ACCOUNTS = ("acct-a", "acct-b")
INSTRUMENT = "AAPL"
START_CASH = 1_000_000
START_SHARES = 40
THREADS = 8
ORDERS = 25  # orders each worker sends per iteration
DEFAULT_SEED = 801  # fixed so a run repeats; COCKPIT_RACE_SEED overrides it
WAIT = 30  # seconds any barrier, event or forced-window join may take before the test fails
HALT_CODES = ("order_halted", "leftover_charge:order_halted")
EXPECTED_CODES = {"cash_insufficient", "holding_insufficient", "order_halted", "size_zero",
                  "size_cap_notional", "size_cap_quantity"}  # caps are Q25 risk refusals, also never clamped
# After the core charge every booking refusal is ``leftover_charge`` with one of these in ``.cause``.
EXPECTED_CAUSES = {"cash_insufficient", "holding_insufficient", "order_halted", "intent_duplicate"}


class ApprovingCore:
    """Approves and authorizes every effect. Thread-safe; ids are unique per call.

    ``check`` is the host's run check: every run is healthy. ``pause`` blocks the named intent
    inside ``authorize``, after the gateway's early funds read and before its booking.
    ``charged`` says whether ``authorize`` (the charge) was ever called for an intent id.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._n = 0
        self._authorized: set[str] = set()
        self.pause: dict[str, Gate] = {}

    def request_approval(self, effect: dict) -> dict:
        with self._lock:
            self._n += 1
            return {"approval_id": f"appr-{self._n}"}

    def charged(self, intent_id: str) -> bool:
        with self._lock:
            return intent_id in self._authorized

    def authorize(self, effect: dict, approval_id: str, authority) -> dict:
        with self._lock:
            self._authorized.add(effect["payload"]["intent_id"])
        gate = self.pause.get(effect["payload"]["intent_id"])
        if gate is not None:
            gate.hit()
        return {"authorized": True, "digest": hashlib.sha256(repr(sorted(effect)).encode()).hexdigest()}

    def check(self, run_id: str, connection: str, plugin_id: str) -> dict:
        return {}


def new_gateway(book: PaperBook, core: ApprovingCore, caps: Caps) -> OrderGateway:
    return OrderGateway(plugin_id="csl.cockpit.paper", book=book, core=core, caps=caps, allowlist=ALLOWLIST,
                        run_check=core.check)


def hammer(workers, timeout: float = 120.0) -> list[str]:
    """Run each callable on its own thread behind one barrier; report escapes and hangs.

    A thread that never reaches the barrier breaks it for all after ``WAIT`` seconds, and one
    still running at ``timeout`` is reported and left behind (daemon), so a hang fails fast.
    """
    barrier = threading.Barrier(len(workers))
    problems: list[str] = []
    lock = threading.Lock()

    def wrap(fn):
        def run():
            try:
                barrier.wait(WAIT)
                fn()
            except BaseException:  # noqa: BLE001 - every escape is a finding
                with lock:
                    problems.append("".join(traceback.format_exc().splitlines(True)[-3:]))
        return run

    threads = [threading.Thread(target=wrap(fn), daemon=True) for fn in workers]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    hung = sum(thread.is_alive() for thread in threads)
    if hung:
        problems.append(f"{hung} thread(s) still running after {timeout}s")
    return problems


def one_iteration(rng: random.Random, violations: list[str], counters: dict, halt: bool | None = None) -> None:
    tmp = tempfile.mkdtemp(prefix="cockpit-b31-race-")
    try:
        _iteration(os.path.join(tmp, "book.db"), rng, violations, counters, halt)
    finally:
        shutil.rmtree(tmp, True)


def _iteration(path: str, rng: random.Random, violations: list[str], counters: dict,
               halt: bool | None = None) -> None:
    """``halt`` forces (True) or forbids (False) the halter thread; None keeps the 30% roll.

    With a halter, every worker waits for the committed halt after its ``ORDERS`` racing orders
    and sends one more fixed sell, which must be refused ``order_halted``.
    """
    create_book(path, cash_minor={a: START_CASH for a in ACCOUNTS},
                holdings={(a, INSTRUMENT): START_SHARES for a in ACCOUNTS})
    caps = Caps(max_notional_minor=400_000, max_quantity=50)
    core = ApprovingCore()
    submitted: dict[str, int] = {}
    halt_seqs: list[int] = []
    halt_done = threading.Event()
    outcomes = {"booked": 0, "refused": 0, "book_locked_skipped": 0,
                "after_halt_refused": 0, "after_halt_book_locked": 0}
    lock = threading.Lock()
    worker_seeds = [rng.randrange(2**32) for _ in range(THREADS)]
    roll = rng.random() < 0.3  # always drawn, so a forced iteration keeps the seed's later draws
    with_halter = roll if halt is None else halt
    approvals_ready = threading.Barrier(THREADS + 1) if with_halter else None

    def flag(message: str) -> None:
        with lock:
            violations.append(message)

    def worker(seed: int, n: int) -> None:
        wrng = random.Random(seed)
        book = PaperBook(path)
        gw = new_gateway(book, core, caps)
        try:
            if with_halter:
                # Approve before the halt so the guaranteed after-halt order reaches submit.
                halted_intent = sized(gw, Intent(
                    intent_id=f"t{n}-after-halt", account=ACCOUNTS[n % len(ACCOUNTS)],
                    instrument=INSTRUMENT, side="sell", quantity=1, limit_price_minor=5_000,
                    run_id="run-1", connection="conn-a"))
                halted_approval = gw.request_approval(halted_intent)["approval_id"]
                approvals_ready.wait(WAIT)
            for i in range(ORDERS + with_halter):
                after_halt = i == ORDERS
                if after_halt:
                    if not halt_done.wait(WAIT):
                        raise TimeoutError("halt never committed")
                    intent_id, account, price, roll = f"t{n}-after-halt", ACCOUNTS[n % len(ACCOUNTS)], 5_000, 1.0
                else:
                    intent_id = f"t{n}-{i}"
                    account = wrng.choice(ACCOUNTS)
                    price = wrng.choice((5_000, 10_000, 25_000))
                    roll = wrng.random()
                submit_after_halt = False  # set just before submit: whether the halt had committed by then
                try:
                    if roll < 0.08:
                        gw.cancel_all()
                        continue
                    if after_halt:
                        intent = halted_intent
                    elif roll < 0.45:
                        intent = gw.size_buy(intent_id=intent_id, account=account, instrument=INSTRUMENT,
                                             limit_price_minor=price, conviction_bps=wrng.randint(1, 10_000),
                                             run_id="run-1", connection="conn-a")
                    else:
                        side = "buy" if roll < 0.8 else "sell"
                        intent = sized(gw, Intent(intent_id=intent_id, account=account, instrument=INSTRUMENT,
                                                  side=side, quantity=wrng.randint(1, 40), limit_price_minor=price,
                                                  run_id="run-1", connection="conn-a"))
                    approval = halted_approval if after_halt else gw.request_approval(intent)["approval_id"]
                    time.sleep(0)  # the operator approves; other threads spend cash meanwhile
                    # On a successful halt, halt_done is set after commit. A submit starting once it
                    # is set must refuse order_halted; a failed halt separately fails the iteration.
                    submit_after_halt = halt_done.is_set()
                    out = gw.submit(intent, approval, None)
                    with lock:
                        submitted[intent_id] = intent.quantity
                        outcomes["booked"] += 1
                    if out["notional_minor"] != intent.quantity * intent.limit_price_minor:
                        flag(f"{intent_id}: notional {out['notional_minor']} != submitted quantity x price")
                    if after_halt or submit_after_halt:
                        flag(f"{intent_id}: booked after the halt was committed")
                except Refused as exc:
                    halted_order = after_halt or submit_after_halt
                    with lock:
                        outcomes["refused"] += 1
                        if after_halt:
                            outcomes["after_halt_refused"] += 1
                            outcomes["after_halt_book_locked"] += exc.code == "book_locked"
                    if exc.code == "book_locked" and not halted_order and not core.charged(intent_id):
                        # Pre-charge probe bounded by BOOK_LOCK_PROBE_S: nothing is charged or
                        # booked. Its audit write shares that bound, so the row may be missing.
                        # Skipped only as that refusal: not for an order sent or submitted after
                        # the halt committed (that is order_halted), and not once the core has
                        # charged the intent (after the charge a refusal is leftover_charge).
                        with lock:
                            outcomes["book_locked_skipped"] += 1
                        continue
                    if exc.code == "book_locked" and core.charged(intent_id):
                        flag(f"{intent_id}: refused {exc} after the core charge, not leftover_charge")
                    if halted_order and (exc.code, exc.cause) != ("order_halted", None):
                        flag(f"{intent_id}: sent after the halt, refused {exc} and not order_halted")
                    if exc.code == "leftover_charge":
                        if exc.cause == "booking_error:IntegrityError" \
                                and isinstance(exc.__cause__, sqlite3.IntegrityError):
                            flag(f"{intent_id}: the database stopped it, not the gateway: "
                                 f"{type(exc.__cause__).__name__}: {exc.__cause__}")
                        elif exc.cause not in EXPECTED_CAUSES:
                            flag(f"{intent_id}: unexpected refusal {exc}")
                    elif exc.code not in EXPECTED_CODES or exc.cause is not None:
                        flag(f"{intent_id}: unexpected refusal {exc}")
                    if exc.audit_seq is None:
                        flag(f"{intent_id}: refusal {exc} has no audit row")
                except sqlite3.Error as exc:
                    flag(f"{intent_id}: the database stopped it, not the gateway: {type(exc).__name__}: {exc}")
        finally:
            book.close()

    def halter() -> None:
        approvals_ready.wait(WAIT)
        time.sleep(rng.random() * 0.02)
        book = PaperBook(path)
        try:
            halt_seqs.append(book.halt())
        finally:
            halt_done.set()  # also on a failed halt: the workers' last orders then book and are flagged
            book.close()

    workers = [lambda s=s, n=n: worker(s, n) for n, s in enumerate(worker_seeds)]
    if with_halter:
        workers.append(halter)
    violations.extend(hammer(workers))
    if with_halter and len(halt_seqs) != 1:
        violations.append(f"halter iteration recorded {len(halt_seqs)} halt seq(s), expected 1")
    if not with_halter and halt_seqs:
        violations.append(f"no-halter iteration recorded halt seqs {halt_seqs}")

    db = sqlite3.connect(path)
    try:
        orders = db.execute("SELECT intent_id, seq, account, side, quantity, limit_price_minor, status"
                            " FROM orders").fetchall()
        cash = dict(db.execute("SELECT account, available_minor FROM cash").fetchall())
        held = {a: n for a, _, n in db.execute("SELECT account, instrument, available FROM holdings")}
        audit_booked = db.execute("SELECT COUNT(*) FROM audit WHERE event = 'booked'").fetchone()[0]
        send_time = db.execute("SELECT COUNT(*) FROM audit WHERE event = 'refused' AND stage = 'book'"
                               " AND code = 'leftover_charge:cash_insufficient'").fetchone()[0]
        halt_refusals = db.execute("SELECT COUNT(*) FROM audit WHERE event = 'refused' AND code IN (?, ?)",
                                   HALT_CODES).fetchone()[0]
    finally:
        db.close()
    if not len(orders) == outcomes["booked"] == audit_booked:
        violations.append(f"orders {len(orders)}, booked {outcomes['booked']}, booked audit rows {audit_booked}")
    for intent_id, seq, _account, _side, quantity, _price, _status in orders:
        if submitted.get(intent_id) != quantity:
            violations.append(f"{intent_id}: booked quantity {quantity} != submitted {submitted.get(intent_id)}")
        if halt_seqs and seq > halt_seqs[0]:
            violations.append(f"{intent_id}: booked at seq {seq} after halt seq {halt_seqs[0]}")
    for account in ACCOUNTS:
        buys = sum(q * p for _, _, a, s, q, p, st in orders if a == account and s == "buy" and st == "working")
        sells = sum(q for _, _, a, s, q, _, st in orders if a == account and s == "sell" and st == "working")
        if cash[account] < 0 or cash[account] + buys != START_CASH:
            violations.append(f"{account}: cash {cash[account]} + working buys {buys} != {START_CASH}")
        if held[account] + sells != START_SHARES:
            violations.append(f"{account}: shares {held[account]} + working sells {sells} != {START_SHARES}")
    # At most 10% per iteration, excluding the guaranteed after-halt refusals: allow
    # occasional slow-disk probe timeouts, but require >=90% of racing order outcomes
    # to exercise the normal paths. Checking each iteration prevents dilution by others.
    racing_outcomes = outcomes["booked"] + outcomes["refused"] - outcomes["after_halt_refused"]
    if outcomes["book_locked_skipped"] * 10 > racing_outcomes:
        violations.append(f"book_locked became common: {outcomes['book_locked_skipped']} skipped"
                          f" / {racing_outcomes} racing outcomes (maximum 10%)")
    counters["book_locked_skipped"] += outcomes["book_locked_skipped"]
    counters["after_halt_book_locked"] += outcomes["after_halt_book_locked"]
    counters["booked"] += outcomes["booked"]
    counters["refused"] += outcomes["refused"]
    counters["send_time_cash_refusals"] += send_time
    counters["halts"] += len(halt_seqs)
    counters["halt_refusals"] += halt_refusals
    counters["halter_iters" if with_halter else "no_halter_iters"] += 1
    if with_halter:
        counters["orders_checked_against_halt"] += len(orders)


def run(iters: int, seed: int) -> tuple[list[str], dict]:
    """Iteration 0 always has a halter and iteration 1 never does; the rest keep the 30% roll."""
    rng = random.Random(seed)
    violations: list[str] = []
    counters = new_counters()
    for i in range(max(2, iters)):
        one_iteration(rng, violations, counters, {0: True, 1: False}.get(i))
    return violations, counters


def new_counters() -> dict:
    return {"booked": 0, "refused": 0, "send_time_cash_refusals": 0, "halts": 0, "halt_refusals": 0,
            "halter_iters": 0, "no_halter_iters": 0, "orders_checked_against_halt": 0,
            "book_locked_skipped": 0, "after_halt_book_locked": 0}


def forced_window() -> tuple[object, list[str], int]:
    """Hold the send-time window open: A's buy of all the cash is blocked inside authorize, past its
    early funds read, while B on a second connection books one share; then A goes on to its booking.

    Returns A's outcome (the submit result or what it raised), the booked intent ids and the cash left.
    """
    account, price, start = ACCOUNTS[0], 10_000, 50_000
    caps = Caps(max_notional_minor=400_000, max_quantity=50)
    core = ApprovingCore()
    tmp = tempfile.mkdtemp(prefix="cockpit-b31-window-")
    books: list[PaperBook] = []
    try:
        path = os.path.join(tmp, "book.db")
        create_book(path, cash_minor={account: start})
        books += [PaperBook(path), PaperBook(path)]
        gw_a, gw_b = (new_gateway(book, core, caps) for book in books)
        a = gw_a.size_buy(intent_id="win-a", account=account, instrument=INSTRUMENT, limit_price_minor=price,
                          conviction_bps=10_000, run_id="run-1", connection="conn-a")
        b = gw_b.size_buy(intent_id="win-b", account=account, instrument=INSTRUMENT, limit_price_minor=price,
                          conviction_bps=250, run_id="run-1", connection="conn-a")
        if (a.quantity, b.quantity) != (5, 1):  # A is sized to all of the cash, B to one share
            raise AssertionError(f"window sizing changed: {a.quantity}, {b.quantity}")
        approval_a = gw_a.request_approval(a)["approval_id"]
        approval_b = gw_b.request_approval(b)["approval_id"]
        gate = core.pause[a.intent_id] = Gate()
        out: list = []

        def submit_a() -> None:
            try:
                out.append(gw_a.submit(a, approval_a, None))
            except BaseException as exc:  # noqa: BLE001 - the caller asserts on whatever came out
                out.append(exc)

        thread = threading.Thread(target=submit_a, daemon=True)
        thread.start()
        try:
            gate.wait_reached()
            gw_b.submit(b, approval_b, None)
        finally:
            gate.release()
        thread.join(WAIT)
        if thread.is_alive() or not out:
            raise AssertionError(f"paused submit still running after {WAIT}s (hung)")
        return out[0], [order[0] for order in books[1].orders()], books[1].available_cash_minor(account)
    finally:
        for book in books:
            book.close()
        shutil.rmtree(tmp, True)


def _runtime() -> str:
    check = getattr(sys, "_is_gil_enabled", None)
    gil = True if check is None else check()
    return f"{sys.version.split()[0]} gil_enabled={gil}"


class B31RaceStress(unittest.TestCase):
    iters = int(os.environ.get("COCKPIT_RACE_ITERS", "10"))
    seed = int(os.environ.get("COCKPIT_RACE_SEED") or DEFAULT_SEED)

    def test_cash_refused_at_send_time_never_clamped(self) -> None:
        violations, counters = run(self.iters, self.seed)
        print(f"\nB31 race: {_runtime()} iters={self.iters} seed={self.seed} {counters} "
              f"violations={len(violations)}", file=sys.stderr)
        self.assertEqual(violations, [], f"seed {self.seed}")
        self.assertGreater(counters["booked"], 0)
        # Both shapes run every time, not by a 30% roll: the halt-wins check and the no-halter path.
        self.assertGreaterEqual(counters["halter_iters"], 1, f"seed {self.seed}: {counters}")
        self.assertGreaterEqual(counters["no_halter_iters"], 1, f"seed {self.seed}: {counters}")
        self.assertGreaterEqual(counters["halts"], 1, f"seed {self.seed}: {counters}")
        self.assertEqual(counters["halts"], counters["halter_iters"], f"seed {self.seed}: {counters}")
        # Halt wins is not vacuous: after each committed halt every thread had an order refused for it.
        self.assertGreaterEqual(counters["halt_refusals"], THREADS * counters["halter_iters"],
                                f"seed {self.seed}: {counters}")

    def test_halt_and_no_halt_shapes_are_forced(self) -> None:
        """``_iteration(halt=...)`` overrides the roll both ways, whatever the seed draws."""
        for halt in (True, False):
            for seed in range(5):
                violations: list[str] = []
                counters = new_counters()
                one_iteration(random.Random(seed), violations, counters, halt)
                self.assertEqual(violations, [], f"halt={halt} seed={seed}")
                self.assertEqual(counters["halts"], int(halt), f"halt={halt} seed={seed}: {counters}")
                self.assertEqual(counters["halter_iters"], int(halt), counters)
                self.assertEqual(counters["no_halter_iters"], int(not halt), counters)
                if halt:  # every thread's order after the committed halt was refused order_halted
                    self.assertGreaterEqual(counters["halt_refusals"], THREADS, f"seed={seed}: {counters}")
                else:
                    self.assertEqual(counters["halt_refusals"], 0, f"seed={seed}: {counters}")

    def test_sensitivity_dropped_send_time_check_is_caught(self) -> None:
        original = PaperBook._check_funds
        original_book = PaperBook._book  # capability-gated since #317
        in_book = threading.local()

        def check_outside_booking(cls, db, intent):
            if not getattr(in_book, "on", False):
                original(db, intent)

        def book_without_send_check(self, intent, token, audit=None):
            in_book.on = True
            try:
                return original_book(self, intent, token, audit)
            finally:
                in_book.on = False

        # Control: with the send-time check in place the forced window is a gateway refusal.
        result, booked, cash = forced_window()
        self.assertIsInstance(result, Refused, repr(result))
        self.assertEqual((result.code, result.cause), ("leftover_charge", "cash_insufficient"))
        self.assertEqual((booked, cash), (["win-b"], 40_000))

        with mock.patch.object(gw_mod.PaperBook, "_check_funds", classmethod(check_outside_booking)), \
                mock.patch.object(gw_mod.PaperBook, "_book", book_without_send_check):
            result, booked, cash = forced_window()
            violations, _ = run(max(3, self.iters // 2), self.seed)
        # B31: the mutant must be caught by the database CHECK, every time. The CHECK's IntegrityError
        # follows the charge, so it surfaces as leftover_charge / booking_error:IntegrityError.
        self.assertIsInstance(result, Refused, f"mutant not caught in the forced window: {result!r}")
        self.assertEqual((result.code, result.cause), ("leftover_charge", "booking_error:IntegrityError"))
        self.assertIsInstance(result.__cause__, sqlite3.IntegrityError)
        self.assertIn("CHECK constraint failed", str(result.__cause__))
        self.assertEqual((booked, cash), (["win-b"], 40_000))  # the CHECK held: nothing booked for A
        # The racing run under the mutant may or may not land in the window; whatever it reports
        # must be that same CHECK and no other flag.
        caught = [item for item in violations if "CHECK constraint failed" in item]
        other = [item for item in violations if "CHECK constraint failed" not in item]
        print(f"\nB31 mutant: {_runtime()} seed={self.seed} forced window caught, "
              f"racing run CHECK violations={len(caught)}", file=sys.stderr)
        self.assertEqual(other, [], f"seed {self.seed}")


if __name__ == "__main__":
    unittest.main()
