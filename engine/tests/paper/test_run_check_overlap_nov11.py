"""Lucas, Nov 11 (12:00pm PST): "Yes, run the re-check during the database writes on #320".

The bounded re-verify of the host's run check still starts inside both of the book's locks,
right after the halt re-read, but the booking's statements (duplicate, funds, cash/holdings
update, seq, insert, audit) now run while it does, and it is waited for before the commit.
Pinned here, on top of ``test_run_check_outside_lock_nov11``:

* the statements really run while the re-verify is in flight;
* a re-verify that refuses, or answers differently, after the statements (through the
  INSERT and the booked audit row) have run rolls the booking back: nothing booked, no
  cash taken, the seq unchanged;
* when a statement fails, the re-verify is still waited for and its refusal wins (as when it
  ran before the statements); when it agrees, the statement's own refusal stands;
* the wait is bounded from the re-verify's start, not from the end of the statements, so the
  book's locks are let go at the bound even when the statements are slow;
* a kill or halt arriving while the re-verify hangs under the lock completes in under 2 s;
* an interrupt raised by a statement waits for the re-verify (at most to the bound) and is
  then raised as it is, never replaced by a refusal or by the check's own interrupt, and
  nothing is booked;
* mutants (comparison ignored, unbounded wait, statement error wins over the re-verify,
  deadline taken at the join, interrupt skips the wait) are each caught.

Fake core; paper only: no provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import shutil
import sqlite3
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock

from cockpit.paper import gateway as gateway_module
from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from cockpit.paper.killswitch import KillSwitch
from tests.paper.fakecore import ALLOWLIST, AUTHORITY, DEFAULT_PLUGIN, MODEL_VERSION, FakeCore, sized

ACCT, CASH = "paper-acct", 1_000_000
RUN, CONN = "run-ov", "conn-ov"
BASE = Intent(intent_id="ov1", account=ACCT, instrument="AAPL", side="buy", quantity=3,
              limit_price_minor=100, run_id=RUN, connection=CONN)
WAIT = 10.0  # a synchronisation point taking longer than this fails the test


class _Check:
    """The host's run check over the fake core. Call 0 is the outside check, call 1 the
    re-verify; ``hooks[1]`` runs on the re-verify's worker before the core is asked and may
    return a replacement answer (``None`` keeps the core's)."""

    def __init__(self, core: FakeCore) -> None:
        self.core = core
        self.calls = 0
        self.hooks: dict = {}
        self.entered = threading.Event()  # the re-verify was asked
        self.answered = threading.Event()  # the re-verify returned (or raised)

    def __call__(self, run_id: str, connection: str, plugin_id: str) -> dict:
        index, self.calls = self.calls, self.calls + 1
        if index == 1:
            self.entered.set()
        try:
            hook = self.hooks.get(index)
            replaced = hook() if hook is not None else None
            answer = self.core.check(run_id, connection, plugin_id)
            return answer if replaced is None else replaced
        finally:
            if index == 1:
                self.answered.set()


@contextlib.contextmanager
def _mutant(old: str, new: str):
    """``PaperBook._book`` with one source edit, compiled against the live module globals (so
    patched module constants still apply), for the duration of the block."""
    src = textwrap.dedent(inspect.getsource(PaperBook._book))
    assert src.count(old) == 1, "mutant anchor not found: %r" % old
    src = src.replace(old, new).replace("def _book(", "def _mutant_book(", 1)
    g = vars(gateway_module)
    exec(compile(src, "<mutant _book>", "exec"), g)  # noqa: S102 - test-only source mutant
    fn = g.pop("_mutant_book")
    with mock.patch.object(PaperBook, "_book", fn):
        yield


# Anchors as in the dedented source of ``PaperBook._book`` (its ``run`` body at 8 spaces).
COMPARE = 'if gateway._join_run_check(pending) != checked:\n            raise Refused("run_check_changed")\n        return seq'
COMPARE_IGNORED = 'gateway._join_run_check(pending)\n        return seq'
BOUNDED_START = "pending = gateway._start_run_check(order, RUN_RECHECK_TIMEOUT_S)"
UNBOUNDED_START = "pending = gateway._start_run_check(order, 3600.0)"
ERROR_JOIN = ('            if gateway._join_run_check(pending) != checked:\n'
              '                raise Refused("run_check_changed") from None\n'
              '            raise\n'
              '        except BaseException:')
ERROR_WINS = '            raise\n        except BaseException:'
INTERRUPT_JOIN = ('            try:\n'
                  '                gateway._join_run_check(pending)\n'
                  '            except BaseException:  # noqa: BLE001 - the interrupt below is what is raised\n'
                  '                pass\n'
                  '            raise\n')
INTERRUPT_NO_WAIT = '            raise\n'


class RunCheckOverlapTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-run-check-ov-")
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, "book.db")
        create_book(self.path, cash_minor={ACCT: CASH})
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**9, runs={})
        self.core.bind_run(RUN, CONN)
        self.check = _Check(self.core)
        self.gw = OrderGateway(plugin_id=DEFAULT_PLUGIN, book=self.book, core=CoreClient(self.core),
                               caps=Caps(10**9, 10**6), allowlist=ALLOWLIST, run_check=self.check)
        self.switch = KillSwitch(self.gw.halt, (), paper_book=self.book, venue_timeout_s=5.0)
        self.intent = sized(self.gw, BASE)
        self.approval = self.gw.request_approval(self.intent)["approval_id"]
        self.core.operator_approves(self.approval)
        self.check.calls = 0  # booking-time calls only from here
        # Set in the booking thread once the re-verify has been handed to its worker (the
        # worker may not have started it yet).
        self.started = threading.Event()
        start = self.gw._start_run_check

        def start_run_check(order, timeout_s):
            self.t_start = time.monotonic()  # the deadline is fixed in here
            pending = start(order, timeout_s)
            self.started.set()
            return pending
        self.gw._start_run_check = start_run_check
        # When the booking transaction let go of the book's locks (after COMMIT or ROLLBACK).
        self.tx_ends: list = []
        tx = self.book._tx

        def timed_tx(fn, *args, **kwargs):
            try:
                return tx(fn, *args, **kwargs)
            finally:
                if self.started.is_set():
                    self.tx_ends.append(time.monotonic())
        self.book._tx = timed_tx
        self.release = threading.Event()
        self.addCleanup(self.release.set)

    def _seq(self) -> int:
        with self.book._lock:
            return PaperBook._state_int(self.book._open_db(), "seq")

    def _in_booking(self, fn):
        """Patch the book's funds check with ``fn`` only for the booking transaction's call
        (the one made after the re-verify started); the pre-charge funds check is the real one."""
        real = PaperBook._check_funds

        def funds(db, order):
            return fn(db, order) if self.started.is_set() else real(db, order)
        self.enterContext(mock.patch.object(self.book, "_check_funds", funds))

    def _submit(self) -> object:
        try:
            return self.gw.submit(self.intent, self.approval, AUTHORITY)
        except Refused as exc:
            return exc

    def _assert_nothing_booked(self, outcome: object, cause: str) -> None:
        self.assertIsInstance(outcome, Refused, outcome)
        self.assertEqual((outcome.code, outcome.cause), ("leftover_charge", cause))
        self.assertEqual(self.book.orders(), [])
        self.assertEqual(self.book.available_cash_minor(ACCT), CASH)
        self.assertEqual(self.book.audit()[-1]["code"], "leftover_charge:" + cause)
        self.assertFalse(self.book._lock.locked())
        self.assertFalse(self.book._db.in_transaction)

    def _held(self) -> float:
        """How long the booking transaction held the book's locks, from the re-verify's start."""
        return self.tx_ends[0] - self.t_start

    def _after_statements(self):
        """An event set once the booking's statements have all run (the order INSERT and then
        its ``booked`` audit row), with the re-verify still unanswered at that moment recorded
        in ``overlapped``."""
        done, overlapped = threading.Event(), []
        real = PaperBook._audit_row

        def audit_row(db, row):
            seq = real(db, row)
            if self.started.is_set() and row.get("event") == "booked" and not done.is_set():
                overlapped.append(not self.check.answered.is_set())
                done.set()
            return seq
        self.enterContext(mock.patch.object(self.book, "_audit_row", audit_row))
        return done, overlapped

    # -- the overlap itself -------------------------------------------------------------

    def test_the_statements_run_while_the_reverify_is_in_flight_and_it_books(self) -> None:
        inserted, overlapped = self._after_statements()
        self.check.hooks[1] = lambda: (inserted.wait(WAIT), None)[1]  # answers only after the statements
        booked = self._submit()
        self.assertEqual(booked["status"], "paper_working", booked)
        self.assertEqual(overlapped, [True])
        self.assertEqual([row[0] for row in self.book.orders()], ["ov1"])
        self.assertEqual(self.book.available_cash_minor(ACCT), CASH - 300)

    def test_a_changed_answer_after_the_statements_ran_rolls_back(self) -> None:
        for name, land in {
            "binding": lambda: {"provider": "p", "model_version": MODEL_VERSION + "-2"},
            "run_faulted": lambda: (self.core.faulted_runs.add(RUN), None)[1],
            "connection_tripped": lambda: (self.core.tripped_connections.add(CONN), None)[1],
        }.items():
            with self.subTest(changed=name):
                self.setUp()
                seq0 = self._seq()
                inserted, overlapped = self._after_statements()
                self.check.hooks[1] = lambda land=land: (inserted.wait(WAIT), land())[1]
                cause = "run_check_changed" if name == "binding" else "core:" + name
                self._assert_nothing_booked(self._submit(), cause)
                self.assertEqual(overlapped, [True])  # the INSERT and audit row had run when it answered
                self.assertEqual(self._seq(), seq0)

    # -- refusal priority when a statement fails ----------------------------------------

    def test_the_reverify_refusal_wins_over_a_statement_error(self) -> None:
        errors = {
            "cash_insufficient": Refused("cash_insufficient"),
            "database": sqlite3.OperationalError("disk I/O error"),
        }
        answers = {
            "run_check_changed": lambda: {"provider": "other"},
            "core:run_faulted": lambda: (self.core.faulted_runs.add(RUN), None)[1],
        }
        for err_name, error in errors.items():
            for cause, answer in answers.items():
                with self.subTest(statement=err_name, reverify=cause):
                    self.setUp()
                    raised = threading.Event()

                    def funds(db, order, error=error):
                        raised.set()
                        raise error
                    self._in_booking(funds)
                    # The re-verify answers only after the statement has failed.
                    self.check.hooks[1] = lambda answer=answer: (raised.wait(WAIT), answer())[1]
                    self._assert_nothing_booked(self._submit(), cause)

    def test_a_statement_error_stands_when_the_reverify_agrees(self) -> None:
        self._in_booking(lambda db, order: (_ for _ in ()).throw(Refused("cash_insufficient")))
        self._assert_nothing_booked(self._submit(), "cash_insufficient")
        self.setUp()
        self._in_booking(lambda db, order: (_ for _ in ()).throw(sqlite3.OperationalError("x")))
        self._assert_nothing_booked(self._submit(), "booking_error:OperationalError")

    def test_a_statement_error_waits_for_the_reverify_within_the_bound(self) -> None:
        self._in_booking(lambda db, order: (_ for _ in ()).throw(Refused("cash_insufficient")))
        self.check.hooks[1] = lambda: (self.release.wait(WAIT), None)[1]
        with mock.patch.object(gateway_module, "RUN_RECHECK_TIMEOUT_S", 0.3):
            t = time.monotonic()
            outcome = self._submit()
            took = time.monotonic() - t
        self._assert_nothing_booked(outcome, "run_check_timeout")
        self.assertLess(took, 2.0)

    # -- the bound ----------------------------------------------------------------------

    def _slow_statements(self, seconds: float) -> None:
        real = PaperBook._check_funds

        def funds(db, order):
            time.sleep(seconds)
            return real(db, order)
        self._in_booking(funds)

    def test_the_bound_runs_from_the_reverify_start_even_with_slow_statements(self) -> None:
        self._slow_statements(1.0)
        self.check.hooks[1] = lambda: (self.release.wait(WAIT), None)[1]  # hangs
        with mock.patch.object(gateway_module, "RUN_RECHECK_TIMEOUT_S", 0.5):
            outcome = self._submit()
        self._assert_nothing_booked(outcome, "run_check_timeout")
        # Deadline at the start: the locks go once the 1 s of statements end (the 0.5 s bound
        # has passed by then); with the deadline taken at the join it would be ~1.5 s.
        self.assertGreaterEqual(self._held(), 0.95)
        self.assertLess(self._held(), 1.25)

    def test_a_hung_reverify_holds_the_locks_at_most_the_default_bound(self) -> None:
        self.assertEqual(gateway_module.RUN_RECHECK_TIMEOUT_S, 1.0)
        self.check.hooks[1] = lambda: (self.release.wait(WAIT), None)[1]
        outcome = self._submit()
        self._assert_nothing_booked(outcome, "run_check_timeout")
        self.assertGreaterEqual(self._held(), 0.95)
        self.assertLess(self._held(), 1.0 + 1.0)  # slack for a loaded runner; the bound is the 1 s
        t = time.monotonic()
        self.gw.halt()
        self.assertLess(time.monotonic() - t, 1.0)

    def test_a_kill_or_halt_during_a_hung_reverify_finishes_in_under_two_seconds(self) -> None:
        for action in ("kill", "halt"):
            with self.subTest(action=action):
                self.setUp()
                inserted, overlapped = self._after_statements()
                self.check.hooks[1] = lambda: (self.release.wait(WAIT), None)[1]
                result: list = []
                worker = threading.Thread(target=lambda: result.append(self._submit()), daemon=True)
                worker.start()
                self.assertTrue(inserted.wait(WAIT), "the booking's statements never ran")
                t = time.monotonic()
                if action == "kill":
                    killed = self.switch.kill()
                    self.assertEqual((killed["status"], killed["halt"]), ("paper_only", "confirmed"))
                else:
                    self.gw.halt()
                took = time.monotonic() - t
                self.assertLess(took, 2.0)
                self.assertTrue(self.book.halted())
                worker.join(WAIT)
                self.assertFalse(worker.is_alive())
                self.assertEqual(overlapped, [True])
                self._assert_nothing_booked(result[0], "run_check_timeout")

    # -- interrupts in the booking thread -----------------------------------------------

    def test_an_interrupt_in_a_statement_is_raised_as_it_is_after_the_bounded_wait(self) -> None:
        for reverify in ("refuses", "hangs", "raises_interrupt"):
            for interrupt in (KeyboardInterrupt, SystemExit):
                with self.subTest(reverify=reverify, interrupt=interrupt.__name__):
                    self.setUp()
                    raised = threading.Event()

                    def funds(db, order, interrupt=interrupt):
                        raised.set()
                        raise interrupt("from the statement")
                    self._in_booking(funds)
                    if reverify == "refuses":
                        self.check.hooks[1] = lambda: (raised.wait(WAIT), self.core.faulted_runs.add(RUN), None)[2]
                    elif reverify == "raises_interrupt":  # the check's own interrupt does not replace it
                        self.check.hooks[1] = lambda: (raised.wait(WAIT), (_ for _ in ()).throw(SystemExit("from the check")))[1]
                    else:
                        self.check.hooks[1] = lambda: (self.release.wait(WAIT), None)[1]
                    with mock.patch.object(gateway_module, "RUN_RECHECK_TIMEOUT_S", 0.3):
                        with self.assertRaises(interrupt) as caught:
                            self.gw.submit(self.intent, self.approval, AUTHORITY)
                    self.assertEqual(caught.exception.args, ("from the statement",))
                    if reverify != "hangs":
                        self.assertTrue(self.check.answered.is_set())  # it was waited for
                    else:
                        self.assertGreaterEqual(self._held(), 0.28)  # waited to the bound
                    self.assertLess(self._held(), 2.0)  # and no longer
                    self.assertEqual(self.book.orders(), [])
                    self.assertEqual(self.book.available_cash_minor(ACCT), CASH)
                    self.assertFalse(self.book._lock.locked())
                    self.assertFalse(self.book._db.in_transaction)

    # -- mutants ------------------------------------------------------------------------

    def test_mutant_comparison_ignored_is_caught(self) -> None:
        inserted, _ = self._after_statements()
        self.check.hooks[1] = lambda: (inserted.wait(WAIT), {"provider": "other"})[1]
        with _mutant(COMPARE, COMPARE_IGNORED):
            outcome = self._submit()
        self.assertEqual(outcome["status"], "paper_working")  # the mutant books the changed run
        self.assertEqual(len(self.book.orders()), 1)

    def test_a_slow_reverify_is_cut_off_at_the_bound(self) -> None:  # the mutant below's twin
        self.check.hooks[1] = lambda: (self.release.wait(1.5), None)[1]
        with mock.patch.object(gateway_module, "RUN_RECHECK_TIMEOUT_S", 0.3):
            outcome = self._submit()
        self._assert_nothing_booked(outcome, "run_check_timeout")
        self.assertLess(self._held(), 1.2)

    def test_mutant_unbounded_wait_is_caught(self) -> None:
        self.check.hooks[1] = lambda: (self.release.wait(1.5), None)[1]
        with _mutant(BOUNDED_START, UNBOUNDED_START), \
                mock.patch.object(gateway_module, "RUN_RECHECK_TIMEOUT_S", 0.3):
            t = time.monotonic()
            outcome = self._submit()
            took = time.monotonic() - t
        self.assertGreaterEqual(took, 1.4)  # held the locks past the bound
        self.assertEqual(outcome["status"], "paper_working")

    def test_mutant_statement_error_wins_is_caught(self) -> None:
        raised = threading.Event()

        def funds(db, order):
            raised.set()
            raise Refused("cash_insufficient")
        self._in_booking(funds)
        self.check.hooks[1] = lambda: (raised.wait(WAIT), self.core.faulted_runs.add(RUN), None)[2]
        with _mutant(ERROR_JOIN, ERROR_WINS):
            outcome = self._submit()
        self.assertEqual((outcome.code, outcome.cause), ("leftover_charge", "cash_insufficient"))

    def test_mutant_deadline_at_the_join_is_caught(self) -> None:
        self._slow_statements(1.0)
        self.check.hooks[1] = lambda: (self.release.wait(WAIT), None)[1]
        real_join = gateway_module._BoundedCaller.join

        def join_from_now(pool, pending):
            done, box, _ = pending
            return real_join(pool, (done, box, time.monotonic() + gateway_module.RUN_RECHECK_TIMEOUT_S))
        with mock.patch.object(gateway_module._BoundedCaller, "join", join_from_now), \
                mock.patch.object(gateway_module, "RUN_RECHECK_TIMEOUT_S", 0.5):
            outcome = self._submit()
        self.assertEqual((outcome.code, outcome.cause), ("leftover_charge", "run_check_timeout"))
        self.assertGreaterEqual(self._held(), 1.4)  # what the bound test above refuses

    def test_mutant_interrupt_skips_the_wait_is_caught(self) -> None:
        raised = threading.Event()

        def funds(db, order):
            raised.set()
            raise KeyboardInterrupt
        self._in_booking(funds)
        self.check.hooks[1] = lambda: (raised.wait(WAIT), time.sleep(0.3), None)[2]
        with _mutant(INTERRUPT_JOIN, INTERRUPT_NO_WAIT):
            with self.assertRaises(KeyboardInterrupt):
                self.gw.submit(self.intent, self.approval, AUTHORITY)
        self.assertFalse(self.check.answered.is_set())  # the mutant did not wait for it
        self.assertLess(self._held(), 0.25)


if __name__ == "__main__":
    unittest.main()
