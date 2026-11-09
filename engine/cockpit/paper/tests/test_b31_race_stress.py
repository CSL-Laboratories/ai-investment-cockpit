"""B31 race and stress: cash refused at send time, never clamped, under many threads (CSL-801).

Lane CSL-801 (3.14t race and stress), measured on this branch's base, Cockpit #320 head
31aa87a6e935dd8bceeaa7fa7b4097b24b0366a4. B31 (Lucas t1414u; PRODUCT.md 055cc7a5, 4.2): "size to cash, refuse at the gateway
... it never clamps or resizes."

Each iteration gives every thread its own ``PaperBook`` connection to one book file (as
separate processes would have) and runs sized buys, fixed-size buys that may exceed cash,
held-share sells, ``cancel_all`` and, sometimes, ``halt``. The approval step yields, so other
threads spend cash between the early funds read and the booking. Checks:

* the gateway refuses every over-cash order with a typed ``Refused``; the database
  ``CHECK (available_minor >= 0)`` never has to stop one (no ``sqlite3`` error escapes);
* never clamps: each booked order has exactly the submitted quantity, and the returned
  ``notional_minor`` is quantity times limit price;
* conservation per account: starting cash == available cash + working buy notional, and the
  same for held shares and working sells;
* halt wins: no order is booked with a sequence number after the halt's;
* every booking has its ``booked`` audit row and every refusal its ``refused`` audit row.

A sensitivity test drops the send-time check inside the booking transaction (B31's forbidden
shape) and requires the run to catch it, so a clean run means something.

Run: ``PYTHONPATH=engine python3 -B -m unittest engine/cockpit/paper/tests/test_b31_race_stress.py -v``,
and on the free-threaded build with ``PYTHON_GIL=0 python3.14t -X gil=0``.
``COCKPIT_RACE_ITERS`` (default 10) and ``COCKPIT_RACE_SEED`` tune the run. Paper only: the
Recursion core is a duck-typed fake that approves every effect; no venue, network or credential.
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

ACCOUNTS = ("acct-a", "acct-b")
INSTRUMENT = "AAPL"
START_CASH = 1_000_000
START_SHARES = 40
THREADS = 8
EXPECTED_CODES = {"cash_insufficient", "holding_insufficient", "order_halted", "size_zero",
                  "size_cap_notional", "size_cap_quantity"}  # caps are Q25 risk refusals, also never clamped


class ApprovingCore:
    """Approves and authorizes every effect. Thread-safe; ids are unique per call."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._n = 0

    def request_approval(self, effect: dict) -> dict:
        with self._lock:
            self._n += 1
            return {"approval_id": f"appr-{self._n}"}

    def authorize(self, effect: dict, approval_id: str, authority) -> dict:
        return {"authorized": True, "digest": hashlib.sha256(repr(sorted(effect)).encode()).hexdigest()}


def hammer(workers, timeout: float = 300.0) -> list[str]:
    """Run each callable on its own thread behind one barrier; report escapes and hangs."""
    barrier = threading.Barrier(len(workers))
    problems: list[str] = []
    lock = threading.Lock()

    def wrap(fn):
        def run():
            try:
                barrier.wait()
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


def one_iteration(rng: random.Random, violations: list[str], counters: dict) -> None:
    tmp = tempfile.mkdtemp(prefix="cockpit-b31-race-")
    try:
        _iteration(os.path.join(tmp, "book.db"), rng, violations, counters)
    finally:
        shutil.rmtree(tmp, True)


def _iteration(path: str, rng: random.Random, violations: list[str], counters: dict) -> None:
    create_book(path, cash_minor={a: START_CASH for a in ACCOUNTS},
                holdings={(a, INSTRUMENT): START_SHARES for a in ACCOUNTS})
    caps = Caps(max_notional_minor=400_000, max_quantity=50)
    core = ApprovingCore()
    submitted: dict[str, int] = {}
    halt_seqs: list[int] = []
    outcomes = {"booked": 0, "refused": 0}
    lock = threading.Lock()

    def flag(message: str) -> None:
        with lock:
            violations.append(message)

    def worker(seed: int, n: int) -> None:
        wrng = random.Random(seed)
        book = PaperBook(path)
        gw = OrderGateway(plugin_id="csl.cockpit.paper", book=book, core=core, caps=caps)
        try:
            for i in range(25):
                intent_id = f"t{n}-{i}"
                account = wrng.choice(ACCOUNTS)
                price = wrng.choice((5_000, 10_000, 25_000))
                roll = wrng.random()
                try:
                    if roll < 0.08:
                        gw.cancel_all()
                        continue
                    if roll < 0.45:
                        intent = gw.size_buy(intent_id=intent_id, account=account, instrument=INSTRUMENT,
                                             limit_price_minor=price, conviction_bps=wrng.randint(1, 10_000),
                                             run_id="run-1", connection="conn-a")
                    else:
                        side = "buy" if roll < 0.8 else "sell"
                        intent = Intent(intent_id=intent_id, account=account, instrument=INSTRUMENT, side=side,
                                        quantity=wrng.randint(1, 40), limit_price_minor=price,
                                        run_id="run-1", connection="conn-a")
                    approval = gw.request_approval(intent)["approval_id"]
                    time.sleep(0)  # the operator approves; other threads spend cash meanwhile
                    out = gw.submit(intent, approval, None)
                    with lock:
                        submitted[intent_id] = intent.quantity
                        outcomes["booked"] += 1
                    if out["notional_minor"] != intent.quantity * intent.limit_price_minor:
                        flag(f"{intent_id}: notional {out['notional_minor']} != submitted quantity x price")
                except Refused as exc:
                    with lock:
                        outcomes["refused"] += 1
                    if exc.code not in EXPECTED_CODES:
                        flag(f"{intent_id}: unexpected refusal {exc.code}")
                    if exc.audit_seq is None:
                        flag(f"{intent_id}: refusal {exc.code} has no audit row")
                except sqlite3.Error as exc:
                    flag(f"{intent_id}: the database stopped it, not the gateway: {type(exc).__name__}: {exc}")
        finally:
            book.close()

    def halter() -> None:
        time.sleep(rng.random() * 0.02)
        book = PaperBook(path)
        try:
            halt_seqs.append(book.halt())
        finally:
            book.close()

    workers = [lambda s=rng.randrange(2**32), n=n: worker(s, n) for n in range(THREADS)]
    if rng.random() < 0.3:
        workers.append(halter)
    violations.extend(hammer(workers))

    db = sqlite3.connect(path)
    try:
        orders = db.execute("SELECT intent_id, seq, account, side, quantity, limit_price_minor, status"
                            " FROM orders").fetchall()
        cash = dict(db.execute("SELECT account, available_minor FROM cash").fetchall())
        held = {a: n for a, _, n in db.execute("SELECT account, instrument, available FROM holdings")}
        audit_booked = db.execute("SELECT COUNT(*) FROM audit WHERE event = 'booked'").fetchone()[0]
        send_time = db.execute("SELECT COUNT(*) FROM audit WHERE event = 'refused' AND stage = 'book'"
                               " AND code = 'cash_insufficient'").fetchone()[0]
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
    counters["booked"] += outcomes["booked"]
    counters["refused"] += outcomes["refused"]
    counters["send_time_cash_refusals"] += send_time
    counters["halts"] += len(halt_seqs)


def run(iters: int, seed: int) -> tuple[list[str], dict]:
    rng = random.Random(seed)
    violations: list[str] = []
    counters = {"booked": 0, "refused": 0, "send_time_cash_refusals": 0, "halts": 0}
    for _ in range(iters):
        one_iteration(rng, violations, counters)
    return violations, counters


def _runtime() -> str:
    check = getattr(sys, "_is_gil_enabled", None)
    gil = True if check is None else check()
    return f"{sys.version.split()[0]} gil_enabled={gil}"


class B31RaceStress(unittest.TestCase):
    iters = int(os.environ.get("COCKPIT_RACE_ITERS", "10"))
    seed = int(os.environ.get("COCKPIT_RACE_SEED") or random.SystemRandom().randrange(2**32))

    def test_cash_refused_at_send_time_never_clamped(self) -> None:
        violations, counters = run(self.iters, self.seed)
        print(f"\nB31 race: {_runtime()} iters={self.iters} seed={self.seed} {counters} "
              f"violations={len(violations)}", file=sys.stderr)
        self.assertEqual(violations, [], f"seed {self.seed}")
        self.assertGreater(counters["booked"], 0)

    def test_sensitivity_dropped_send_time_check_is_caught(self) -> None:
        original = PaperBook._check_funds
        original_book = PaperBook._book  # capability-gated since #317 (merged into #320 at 31aa87a6)
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

        with mock.patch.object(gw_mod.PaperBook, "_check_funds", classmethod(check_outside_booking)), \
                mock.patch.object(gw_mod.PaperBook, "_book", book_without_send_check):
            violations, _ = run(max(3, self.iters // 2), self.seed)
        # B31: the mutant must be caught by the database CHECK, not by any other flag.
        caught = [item for item in violations if "CHECK constraint failed" in item]
        other = [item for item in violations if "CHECK constraint failed" not in item]
        self.assertTrue(caught, f"mutant not caught as a CHECK violation, seed {self.seed}: {violations}")
        self.assertEqual(other, [], f"seed {self.seed}")


if __name__ == "__main__":
    unittest.main()
