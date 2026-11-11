"""Lucas, Nov 11 (2:15am PST): "Fix it now: move the breaker check outside the lock and re-verify".

At booking the gateway asks the host's run check in the calling thread before it takes the
book's lock and SQLite write lock, then re-verifies it inside the booking transaction,
waiting at most ``RUN_RECHECK_TIMEOUT_S``. Pinned here:

* the full check runs with neither lock held, and the re-verify runs with both held;
* a fault, a trip or a new binding landing between the two is refused after the charge
  (``leftover_charge`` with ``core:<code>`` or ``run_check_changed``), nothing booked
  (the fault-after-authorize guarantee, Devin finding 1, still holds);
* a re-verify that errors, answers a non-dict or hangs fails closed (``run_check_error``,
  ``run_check_timeout``), and the book's locks are let go within the bound;
* a check that hangs no longer keeps a halt or a kill in the same process waiting: both
  complete well under the halt's 30 s lock deadline, and the hung submit never books;
* the approved effect's values are still what is booked (D5) when the check passes.

Fake core first; the last class repeats the between-the-checks fault on the real Recursion
core's ``ConnectionBreaker`` (skipped only with the exact B5 reason when the core is absent).
Paper only: no provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import warnings
from unittest import mock

from cockpit.paper import gateway as gateway_module
from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from cockpit.paper.killswitch import KillSwitch
from tests.paper import test_e2e_recursion_core as e2e
from tests.paper.fakecore import ALLOWLIST, AUTHORITY, DEFAULT_PLUGIN, MODEL_VERSION, PROVIDER, FakeCore, sized

ACCT, CASH = "paper-acct", 1_000_000
RUN, CONN = "run-ol", "conn-ol"
BASE = Intent(intent_id="ol1", account=ACCT, instrument="AAPL", side="buy", quantity=3,
              limit_price_minor=100, run_id=RUN, connection=CONN)
WAIT = 10.0  # a synchronisation point taking longer than this fails the test
HALT_DEADLINE_S = 30.0  # PaperBook.halt's own write-lock deadline


class _Check:
    """The host's run check, delegating to the fake core, with a hook per booking-time call.

    Records, per call, whether the book's lock was held and whether its connection was inside
    a transaction, so the tests can tell the outside check from the re-verify."""

    def __init__(self, core: FakeCore, book: PaperBook) -> None:
        self.core, self.book = core, book
        self.hooks: dict = {}  # call index (0 = outside, 1 = re-verify) -> callable(answer) -> answer
        self.seen: list = []
        self.entered = [threading.Event(), threading.Event()]

    def __call__(self, run_id: str, connection: str, plugin_id: str) -> dict:
        index = len(self.seen)
        self.seen.append((self.book._lock.locked(), self.book._db.in_transaction))
        if index < 2:
            self.entered[index].set()
        hook = self.hooks.get(index)
        if hook is not None and getattr(hook, "before", False):
            hook()
        answer = self.core.check(run_id, connection, plugin_id)
        return hook(answer) if hook is not None and not getattr(hook, "before", False) else answer


def _before(fn):
    fn.before = True
    return fn


class RunCheckOutsideLockTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-run-check-")
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, "book.db")
        create_book(self.path, cash_minor={ACCT: CASH})
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**9, runs={})
        self.core.bind_run(RUN, CONN)
        self.check = _Check(self.core, self.book)
        self.gw = OrderGateway(plugin_id=DEFAULT_PLUGIN, book=self.book, core=CoreClient(self.core),
                               caps=Caps(10**9, 10**6), allowlist=ALLOWLIST, run_check=self.check)
        self.switch = KillSwitch(self.gw.halt, (), paper_book=self.book, venue_timeout_s=5.0)
        self.intent = sized(self.gw, BASE)
        approval = self.gw.request_approval(self.intent)["approval_id"]
        self.core.operator_approves(approval)
        self.approval = approval
        self.check.seen.clear()  # request_approval does not call it; booking-time calls only from here

    def _submit(self) -> object:
        try:
            return self.gw.submit(self.intent, self.approval, AUTHORITY)
        except Refused as exc:
            return exc

    def _assert_nothing_booked(self, outcome: object, cause: str) -> None:
        self.assertIsInstance(outcome, Refused)
        self.assertEqual((outcome.code, outcome.cause), ("leftover_charge", cause))
        self.assertEqual(self.book.orders(), [])
        self.assertEqual(self.book.available_cash_minor(ACCT), CASH)
        self.assertEqual(self.book.audit()[-1]["code"], "leftover_charge:" + cause)

    def test_full_check_runs_unlocked_and_the_reverify_runs_under_both_locks(self) -> None:
        booked = self._submit()
        self.assertEqual(booked["status"], "paper_working", booked)
        self.assertEqual(self.check.seen, [(False, False), (True, True)])
        # D5: what is booked is the approved effect's values.
        self.assertEqual([row[0] for row in self.book.orders()], ["ol1"])
        self.assertEqual(self.book.available_cash_minor(ACCT), CASH - 300)

    def test_a_fault_or_trip_between_the_check_and_the_booking_is_refused(self) -> None:
        lands = {
            "run_faulted": lambda: self.core.faulted_runs.add(RUN),
            "connection_tripped": lambda: self.core.tripped_connections.add(CONN),
        }
        for code, land in lands.items():
            with self.subTest(code=code):
                self.setUp()

                def after_outside(answer, land=land):
                    land()  # the outside check said healthy; the fault lands before the booking
                    return answer
                self.check.hooks[0] = after_outside
                self._assert_nothing_booked(self._submit(), "core:" + code)
                self.assertEqual(len(self.check.seen), 2)

    def test_a_binding_that_changed_between_the_checks_is_refused(self) -> None:
        self.check.hooks[1] = lambda answer: dict(answer, model_version=MODEL_VERSION + "-2")
        self._assert_nothing_booked(self._submit(), "run_check_changed")
        self.setUp()
        self.check.hooks[1] = lambda answer: dict(answer, probe="leased")  # any added field too
        self._assert_nothing_booked(self._submit(), "run_check_changed")

    def test_a_reverify_that_cannot_be_read_fails_closed(self) -> None:
        breaks = {
            "raises_untyped": _before(lambda: (_ for _ in ()).throw(RuntimeError("store unreadable"))),
            "not_a_dict": lambda answer: [answer],
            "non_str_key": lambda answer: {1: PROVIDER},
        }
        for name, hook in breaks.items():
            with self.subTest(broken=name):
                self.setUp()
                self.check.hooks[1] = hook
                self._assert_nothing_booked(self._submit(), "run_check_error")

    def test_a_reverify_that_hangs_is_bounded_and_lets_the_locks_go(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        self.check.hooks[1] = _before(lambda: release.wait(WAIT))
        with mock.patch.object(gateway_module, "RUN_RECHECK_TIMEOUT_S", 0.3):
            t = time.monotonic()
            outcome = self._submit()
            took = time.monotonic() - t
        self._assert_nothing_booked(outcome, "run_check_timeout")
        self.assertLess(took, 3.0)
        t = time.monotonic()
        self.gw.halt()  # the book's locks were let go at the bound
        self.assertLess(time.monotonic() - t, 1.0)
        self.assertTrue(self.book.halted())

    def _hung_submit(self, index: int):
        release = threading.Event()
        self.addCleanup(release.set)
        self.check.hooks[index] = _before(lambda: release.wait(WAIT))
        result: list = []
        worker = threading.Thread(target=lambda: result.append(self._submit()), daemon=True)
        worker.start()
        self.assertTrue(self.check.entered[index].wait(WAIT), "the run check was never asked")
        return release, worker, result

    def test_a_hung_check_does_not_block_a_kill_or_halt_in_this_process(self) -> None:
        release, worker, result = self._hung_submit(0)  # hangs before any lock is taken
        t = time.monotonic()
        killed = self.switch.kill()
        took = time.monotonic() - t
        self.assertEqual((killed["status"], killed["halt"]), ("paper_only", "confirmed"))
        self.assertLess(took, 2.0)
        self.assertLess(took, HALT_DEADLINE_S / 10)
        t = time.monotonic()
        self.gw.halt()
        self.assertLess(time.monotonic() - t, 1.0)
        self.assertEqual(result, [], "the hung submit returned before its check did")
        self.assertEqual(self.book.orders(), [])
        release.set()  # the check answers late: the halt committed meanwhile wins
        worker.join(WAIT)
        self.assertFalse(worker.is_alive())
        self._assert_nothing_booked(result[0], "order_halted")

    def test_a_reverify_hung_under_the_lock_holds_a_kill_only_to_the_bound(self) -> None:
        release, worker, result = self._hung_submit(1)  # hangs with the book's locks held
        t = time.monotonic()
        killed = self.switch.kill()
        took = time.monotonic() - t
        self.assertEqual((killed["status"], killed["halt"]), ("paper_only", "confirmed"))
        self.assertLess(took, gateway_module.RUN_RECHECK_TIMEOUT_S + 2.0)
        self.assertLess(took, HALT_DEADLINE_S / 10)
        worker.join(WAIT)
        self.assertFalse(worker.is_alive())
        self._assert_nothing_booked(result[0], "run_check_timeout")

    def test_hung_rechecks_cannot_pile_up_threads(self) -> None:
        pool, slots = gateway_module._BoundedCaller(), gateway_module._Slots()
        release = threading.Event()
        self.addCleanup(release.set)
        with mock.patch.object(gateway_module, "_RUN_CHECK_WORKERS_MAX", 2):
            for _ in range(2):
                with self.assertRaises(Refused) as caught:
                    pool.call(release.wait, (WAIT,), 0.05, slots)
                self.assertEqual(caught.exception.code, "run_check_timeout")
            t = time.monotonic()
            with self.assertRaises(Refused) as caught:
                pool.call(release.wait, (WAIT,), WAIT, slots)
            self.assertEqual(caught.exception.code, "run_check_busy")
            self.assertLess(time.monotonic() - t, 1.0)  # refused at once, never queued
            release.set()
            deadline = time.monotonic() + WAIT
            while (pool._running or slots.used) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(pool.call(lambda: {"ok": 1}, (), WAIT, slots), (True, {"ok": 1}))  # hung workers came back

    def test_the_process_wide_thread_cap_refuses_busy_at_once(self) -> None:
        pool = gateway_module._BoundedCaller()
        release = threading.Event()
        self.addCleanup(release.set)
        with mock.patch.object(gateway_module, "_RUN_CHECK_THREADS_MAX", 2):
            for _ in range(2):  # two gateways, one hung check each
                with self.assertRaises(Refused):
                    pool.call(release.wait, (WAIT,), 0.05, gateway_module._Slots())
            with self.assertRaises(Refused) as caught:
                pool.call(release.wait, (WAIT,), WAIT, gateway_module._Slots())
            self.assertEqual(caught.exception.code, "run_check_busy")

    def test_a_saturated_pool_is_refused_before_the_charge(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        with mock.patch.object(gateway_module, "_RUN_CHECK_WORKERS_MAX", 1):
            with self.assertRaises(Refused):
                gateway_module._RUN_CHECKS.call(release.wait, (WAIT,), 0.05, self.gw._run_check_slots)  # hung
            outcome = self._submit()
            self.assertEqual((outcome.code, outcome.cause), ("run_check_busy", None))
            self.assertEqual((self.core.spent, self.core.calls.count("effect.authorize")), (0, 0))
            # Another gateway's slots are its own: a hung check here does not refuse there.
            other = OrderGateway(plugin_id=DEFAULT_PLUGIN, book=self.book, core=CoreClient(self.core),
                                 caps=Caps(10**9, 10**6), allowlist=ALLOWLIST, run_check=self.core.check)
            intent = sized(other, Intent(**dict(BASE.__dict__, intent_id="ol-other")))
            approval = other.request_approval(intent)["approval_id"]
            self.core.operator_approves(approval)
            self.assertEqual(other.submit(intent, approval, AUTHORITY)["status"], "paper_working")
        self.assertEqual(self.book.orders()[0][0], "ol-other")

    def test_an_interrupt_from_the_check_is_raised_not_turned_into_a_refusal(self) -> None:
        for index in (0, 1):
            for interrupt in (KeyboardInterrupt, SystemExit):
                with self.subTest(call=index, interrupt=interrupt.__name__):
                    self.setUp()
                    self.check.hooks[index] = _before(lambda interrupt=interrupt: (_ for _ in ()).throw(interrupt()))
                    with self.assertRaises(interrupt):
                        self.gw.submit(self.intent, self.approval, AUTHORITY)
                    self.assertEqual(self.book.orders(), [])
                    self.assertFalse(self.book._lock.locked())
                    self.assertFalse(self.book._db.in_transaction)

    def test_a_check_raising_the_gateways_own_refusal_keeps_the_core_prefix(self) -> None:
        self.check.hooks[1] = _before(lambda: (_ for _ in ()).throw(Refused("order_halted")))
        self._assert_nothing_booked(self._submit(), "core:order_halted")

    def test_a_value_of_another_type_is_a_changed_answer(self) -> None:
        self.check.hooks[0] = lambda answer: dict(answer, probe=1)
        self.check.hooks[1] = lambda answer: dict(answer, probe=True)  # True == 1, but not the same answer
        self._assert_nothing_booked(self._submit(), "run_check_changed")

    @unittest.skipUnless(hasattr(os, "fork"), "no fork on this platform")
    def test_a_forked_child_starts_with_an_empty_pool(self) -> None:
        self.assertEqual(self._submit()["status"], "paper_working")  # leaves an idle worker behind
        self.assertTrue(gateway_module._RUN_CHECKS._idle)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # fork with threads running
            pid = os.fork()
        if pid == 0:  # child: never return into the test runner
            code = 1
            try:
                pool, slots = gateway_module._RUN_CHECKS, self.gw._run_check_slots
                fresh = (pool._idle, pool._running, slots.used) == ([], 0, 0)
                t = time.monotonic()
                answered = pool.call(lambda: 7, (), 5.0, slots) == (True, 7)
                code = 0 if fresh and answered and time.monotonic() - t < 1.0 else 2
            finally:
                os._exit(code)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)


@unittest.skipIf(e2e.CORE_SKIP is not None, e2e.AWAITING_328)
class RealCoreRunCheckOutsideLockTests(unittest.TestCase):
    """The same between-the-checks landing on the real core's ``ConnectionBreaker``."""

    def test_a_fault_or_trip_after_the_outside_check_is_refused_on_the_real_breaker(self) -> None:
        for code in ("run_faulted", "connection_tripped"):
            with self.subTest(code=code):
                w = e2e.build_world(self)
                run, conn = "run-ol-" + code, "conn-ol-" + code
                w.breaker.bind_run(run, conn, e2e.PLUGIN_ID, e2e.PROVIDER, e2e.MODEL_VERSION)
                intent = e2e.sized_buy(w, "ol-" + code, quantity=1, run_id=run, connection=conn)
                approval = w.gateway.request_approval(intent)
                self.assertTrue(w.decide(approval)["ok"])
                real, calls = w.breaker.check, []

                def check(run_id, connection, plugin_id):
                    calls.append(w.gateway._book._lock.locked())
                    answer = real(run_id, connection, plugin_id)
                    if len(calls) == 1:  # healthy outside the lock; the fault or trip lands now
                        if code == "run_faulted":
                            w.breaker.fault_run(run)
                        else:
                            w.breaker.report(conn, sorted(e2e.TYPED_LIMITS)[0])
                    return answer
                w.gateway._run_check = check
                with self.assertRaises(Refused) as caught:
                    w.gateway.submit(intent, approval["approval_id"], w.profile)
                self.assertEqual((caught.exception.code, caught.exception.cause), ("leftover_charge", "core:" + code))
                self.assertEqual(calls, [False, True])
                self.assertEqual(w.book.orders(), [])


if __name__ == "__main__":
    unittest.main()
