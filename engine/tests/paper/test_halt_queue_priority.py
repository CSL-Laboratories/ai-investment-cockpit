"""A halt goes ahead of the book's other writers from the moment it is called.

Inside one process a halt counts itself in as its first act (``_HALTS``, one count per book
file for every handle the process has open on it), and every writer waits while that count is
above zero: before it takes its handle's lock (``_yield_to_halt``) and again once it holds it
(``_give_way``). Across processes the halt's flag file does the same job, best effort. A
writer that was already waiting on the lock when the halt began is past the first wait. If it
then took the lock ahead of the halt, every booking queued that way would run its whole
transaction first, and one whose re-verify hangs holds the lock for ``RUN_RECHECK_TIMEOUT_S``:
measured at 4287d19a, a halt behind 1, 2, 3, 4 and 8 such bookings (the one in its re-verify
included) took 0.96, 1.96, 2.96, 3.96 and 7.96 s. ``submit``'s probe (``_probe_writable``)
queues the same way: while another connection held the write lock, a halt behind three
queued probes waited one ``BOOK_LOCK_PROBE_S`` for each. Pinned here:

* with bookings queued on the book's lock behind one in progress, a halt or a kill commits
  right after that one: one re-verify is asked, the booking in progress books, every queued
  one finds the book halted (``leftover_charge`` with cause ``order_halted``), and a kill
  leaves nothing working;
* the same holds with the book's own lock, whatever order it hands itself over in;
* inside the process that priority does not hang on the flag file: it holds when the flag
  has aged past ``HALT_YIELD_S``, has been removed, or could never be made; a writer that
  arrives after the halt began waits before the lock on the count alone; a writer on another
  handle of the same book waits too, to its cap and no longer;
* a writer waiting on the lock when a halt begins does not write until the halt's flag goes;
* probes queued on the lock while another connection holds the write lock do not try for it
  ahead of a halt: the halt is the first to try; and a probe puts the connection's busy
  timeout back, whether it answers or refuses;
* only a live flag is given way to (not a missing, stale, future-dated or non-regular one),
  and it is never removed while the lock is held;
* a flag dated within the clock's grain ahead (``_HALT_FLAG_GRAIN_S``) is a halt in progress,
  before the lock and under it: on Windows a flag looked at in the clock tick it was made
  in reads one float step in the future about one look in seven. The clock is held still
  for this, so a slow machine cannot hide the rule;
* a writer's waits all end at one cap, ``HALT_YIELD_TOTAL_S`` after its first look at a halt
  in progress. The cap is carried from look to look and never started again, in a
  transaction and in a probe; a flag left by a halt that died does not cost a retried halt
  its turn, and the wait already spent on it is carried over; and a flag that stays live
  cannot hold a writer for ever;
* a writer that judged a flag stale looks again before it removes it: a flag made or dated
  since is left, and so is any entry other than the one it judged; and a writer's removal
  that lands just after a halt began still leaves that halt ahead of the queued bookings;
* where the flag is kept: under ``XDG_RUNTIME_DIR`` in a folder of the user's own, made 0700,
  when that variable names a usable folder; beside the book for every unusable one (unset,
  relative, a link, writable by group or others, another user's, or with a ``csl-cockpit``
  that fails the same check), and always on Windows; ``/dev/shm`` itself is no such folder,
  and the flag is under it only where the user's own runtime folder or the book is;
* making or dating the flag never waits and goes through no link: a FIFO at the path, put
  there before the halt or between its look and its open, does not hold a halt or a kill;
  what is opened is dated only if it is a regular file; a link at the path, put there before
  or after the look, is removed and never followed; a stale flag is replaced by a new file
  and never dated in place; a live flag is dated in place and never replaced; a flag that
  goes between the halt's look and its dating is made again; and an interrupt raised before
  any one instruction of the flag's making leaves no descriptor open, so that the halt that
  follows can take the flag away; no tool that watches calls (``sys.setprofile``,
  ``sys.monitoring``) is handed anything that leads to the flag's open file; and an interrupt
  that such a tool raises at any call or return of a halt, and that is kept with what the
  tool was handed, does not keep the halt that follows from taking the flag away;
* a halt that is still retrying for the write lock keeps its flag live: it dates the flag
  again, and makes it again at once when a writer removed it; and it takes the flag away
  only after its commit, when another connection already reads the book as halted;
* halts that overlap or fail: a halt is counted in before it touches its flag; a halt on a
  closed book touches neither the count nor the flag; a halt on another handle that is
  refused, times out or commits leaves the flag to the halt still in progress, which keeps
  its place ahead of queued bookings and probes; a halt that begins as another is ending is
  counted in at once and keeps its new flag; an interrupt as a halt begins leaves no flag
  and no count behind, and one that lands before the halt is counted in touches no flag; one
  that lands inside a halt's counting out still leaves it out of the count, with its flag
  left for a writer to remove once stale; two in a row leave an entry that is no longer
  counted once the call is over, though the interrupt is kept with its traceback and the
  call's frames, and that does not keep a halt that follows from taking the flag away; the
  same holds for an interrupt raised by a trace function at any line of the counting out, on
  top of those or alone, and for one raised before any one instruction of the halt, called
  directly or by a kill; such an entry stays counted only while something holds the object
  the call was counted by; the
  count goes by a halt's own token and never by an address, so an ``end`` or ``forget`` that
  never began changes nothing, whatever else is counted; the count drops an entry whose
  halt is over; a forked child starts with no halt in progress;
* a kill whose halt was interrupted twice in its counting out does not find that halt still
  counted when it cancels its own paper orders, though it keeps the interrupt it caught, and
  with it that halt's frame, until it has tried every venue;
* the bounds as shipped are in a workable order (every other test here replaces them);
* the mutants without the look under the lock are caught: every queued booking books before
  the halt, and every queued probe tries for the write lock before it.

No test here passes or fails on how fast the machine is. ``setUp`` raises
``RUN_RECHECK_TIMEOUT_S``, ``HALT_YIELD_S`` and ``HALT_YIELD_TOTAL_S`` well past ``WAIT``, so
a stalled machine cannot time out the booking in progress or age out a halt's flag before
the test moves on. Where a test must know that a writer has looked for a halt, or has given
way, it watches that look itself rather than sleeping; where something must happen between
two steps of the gateway (between a writer's look and its removal, between a halt's look and
its open), the test does it from inside that step. The queue order is the worst one for the
halt (it is last in line), fixed by a first-in first-out stand-in for the book's lock, so the
result does not depend on how the platform's lock hands itself over. The booking in progress
is not interrupted: it is the one transaction a halt still waits for. Time decides nothing
except the two tests of the cap itself, which wait a short ``HALT_YIELD_TOTAL_S`` out; there a
slow machine only makes the writer later, and the test checks that the halt was still in
progress, or the flag still live, when the writer went on.

Every flag these tests make or cause is under the test's own temporary folder (``setUp``
points ``XDG_RUNTIME_DIR`` at a folder inside it; on Windows the flag is beside the book),
which is removed afterwards: nothing is left in the real runtime folder or beside a book.
Tests that need a FIFO or a symbolic link skip where the platform or the account cannot make
one.

Not covered, and not fixed (see "Halt priority" in the gateway's module doc): a writer already
inside ``BEGIN IMMEDIATE`` waiting for another connection's transaction; a writer that has
waited out its whole cap (it gives way to nothing more, a later halt included); a lock left
held or a transaction left open by an interrupt (the tests that raise one before every
instruction of a halt give the lock back and roll the transaction back themselves); and
everything that happens between two processes, where a halt has only its flag: a flag aged
out while its halt waits on its own connection's lock, a flag removed by another process's
halt as it ends or by another process's writer between its last look and its removal, and
processes that work out different flag paths. The Windows case of a link put at the flag's
path between the look and the dating is not covered either: Windows has no call that would
refuse it.

Fake core; paper only: no provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import ast
import collections
import contextlib
import dataclasses
import dis
import gc
import hashlib
import inspect
import io
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
import warnings
import weakref
from unittest import mock

from cockpit.paper import gateway as gateway_module
from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from cockpit.paper.killswitch import KillSwitch
from tests.paper.fakecore import ALLOWLIST, AUTHORITY, DEFAULT_PLUGIN, FakeCore, sized

ACCT, CASH = "paper-acct", 1_000_000
RUN, CONN = "run-hq", "conn-hq"
BASE = Intent(intent_id="hq", account=ACCT, instrument="AAPL", side="buy", quantity=3,
              limit_price_minor=100, run_id=RUN, connection=CONN)
BOOKING = "booking-"  # the submitting threads' name prefix
PROBE = "probe-"
QUEUED = 3  # writers waiting on the book's lock behind the one in progress
WAIT = 10.0  # a synchronisation point taking longer than this fails the test
HALTED, BOOKED = "leftover_charge:order_halted", "paper_working"
REFUSAL = {"stage": "risk", "code": "paper_only", "plugin_id": DEFAULT_PLUGIN}
# The bounds as the module ships them, read before any test replaces them.
SHIPPED = {name: getattr(gateway_module, name) for name in (
    "HALT_YIELD_S", "HALT_YIELD_TOTAL_S", "_HALT_FLAG_GRAIN_S", "_HALT_FLAG_REFRESH_S",
    "RUN_RECHECK_TIMEOUT_S", "BOOK_LOCK_PROBE_S")}


class _FifoLock:
    """Stands in for ``PaperBook._lock``: handed over strictly in arrival order, and it says
    how many threads are waiting on it."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._line: collections.deque = collections.deque()
        self._held = False

    def acquire(self) -> bool:
        with self._guard:
            if not self._held:
                self._held = True
                return True
            turn = threading.Event()
            self._line.append(turn)
        turn.wait()
        return True

    def release(self) -> None:
        with self._guard:
            if not self._held:
                raise RuntimeError("release unlocked lock")  # as threading.Lock does
            if self._line:
                self._line.popleft().set()  # the lock passes straight to the next in line
            else:
                self._held = False

    def locked(self) -> bool:
        return self._held

    def waiting(self) -> int:
        with self._guard:
            return len(self._line)

    def __enter__(self) -> _FifoLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class _Forced(KeyboardInterrupt):
    """The interrupt a test's trace function raises, to tell it from any other."""


class _Retaken:
    """Stands in for a lock in the tests that force an interrupt before every instruction of a
    halt. Such an interrupt can leave a lock held (a limit the gateway's module doc states; it
    is not what those tests are about), and the next use of the real lock would then wait for
    good. This one its holder can take again, and ``left`` gives it back and says whether it
    had been left held."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._taken = 0

    def __enter__(self) -> _Retaken:
        self._lock.acquire()
        self._taken += 1
        return self

    def __exit__(self, *exc: object) -> None:
        self._taken -= 1
        self._lock.release()

    def left(self) -> bool:
        held = self._taken > 0
        while self._taken:
            self.__exit__()
        return held


class _Watched:
    """Stands in for the book's connection: notes which thread tries for the write lock, in
    order (a thread's tries in a row count once)."""

    def __init__(self, db: sqlite3.Connection, tried: list) -> None:
        self._db, self._tried = db, tried

    def execute(self, sql: str, *args: object):
        if sql == "BEGIN IMMEDIATE":
            name = threading.current_thread().name
            if not self._tried or self._tried[-1] != name:
                self._tried.append(name)
        return self._db.execute(sql, *args)

    def __getattr__(self, name: str):
        return getattr(self._db, name)


class HaltQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-halt-queue-")
        self.addCleanup(shutil.rmtree, d, True)
        self.dir = d
        self.path = os.path.join(d, "book.db")
        create_book(self.path, cash_minor={ACCT: CASH})
        # The flag goes under a runtime folder of the test's own (POSIX) or beside the book
        # (Windows), both inside ``d``: never into the real runtime folder.
        self.runtime = os.path.join(d, "runtime")
        os.mkdir(self.runtime, 0o700)
        self.enterContext(mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.runtime}))
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.addCleanup(self._remove_flag)  # a writer still waiting on it is let go before the book closes
        # No bound in the gateway may run out while a test is between two of its steps, however
        # slow the machine: the booking in progress waits for its answer, and a flag stays live.
        # A test may spend ``WAIT`` at each of several steps, so each bound is well past one.
        for name, seconds in (("RUN_RECHECK_TIMEOUT_S", 3 * WAIT), ("HALT_YIELD_S", 3 * WAIT),
                              ("HALT_YIELD_TOTAL_S", 6 * WAIT)):
            self.enterContext(mock.patch.object(gateway_module, name, seconds))
        self.core = FakeCore(budget_minor=10**9, runs={})
        self.core.bind_run(RUN, CONN)
        self.gw = OrderGateway(plugin_id=DEFAULT_PLUGIN, book=self.book, core=CoreClient(self.core),
                               caps=Caps(10**9, 10**6), allowlist=ALLOWLIST, run_check=self._check)
        self.switch = KillSwitch(self.gw.halt, (), paper_book=self.book, venue_timeout_s=5.0)
        self.reverifies: list = []  # one entry per re-verify asked
        self.first_reverify = threading.Event()
        self.answer = threading.Event()  # a re-verify answers once this is set
        self.addCleanup(self.answer.set)
        # Every booking stops at the door of its transaction (pre-checks, the charge and the
        # outside run check done) until all of them are there; then they go in together, so
        # all but one are waiting on the book's lock, past ``_yield_to_halt``.
        self.at_the_door = threading.Semaphore(0)
        self.go = threading.Event()
        self.addCleanup(self.go.set)
        seen = threading.local()
        tx = self.book._tx

        def gated_tx(fn, *args, **kwargs):
            if threading.current_thread().name.startswith(BOOKING) and not getattr(seen, "once", False):
                seen.once = True
                self.at_the_door.release()
                if not self.go.wait(WAIT):
                    raise RuntimeError("the test never let the bookings into their transactions")
            return tx(fn, *args, **kwargs)
        self.book._tx = gated_tx

    def _remove_flag(self) -> None:
        flag = self.book._halt_flag
        try:
            if os.path.isdir(flag) and not os.path.islink(flag):
                os.rmdir(flag)
            elif os.path.lexists(flag):
                os.unlink(flag)
        except OSError:
            pass

    def _flag_live(self) -> bool:
        try:
            return gateway_module._halt_flag_live(os.lstat(self.book._halt_flag))
        except OSError:
            return False

    def _halts(self) -> int:
        """How many halts this process counts as in progress on the book."""
        return gateway_module._HALTS.count(self.book._halt_key)

    def _nothing_else_holds_it(self) -> None:
        """Run the collector. An object the test has just let go of, or that a call has, is then
        gone on any interpreter, not only on one that frees an object with its last reference."""
        gc.collect()

    def _hold_what_halts_are_counted_by(self) -> list:
        """Keep every object a halt is counted by from now on (``_Halt``), as a debugger that
        copied the call's local variables would. Returns the list that keeps them."""
        held: list = []
        begin = gateway_module._HALTS.begin

        def begin_and_hold(book: str, token) -> None:
            held.append(token())
            begin(book, token)
        self.enterContext(mock.patch.object(gateway_module._HALTS, "begin", begin_and_hold))
        return held

    def _entries(self) -> int:
        """How many entries the count holds for the book, counted or not. Not a look at the
        count: it drops none of them."""
        return list(gateway_module._HALTS._halts.values()).count(self.book._halt_key)

    def _a_halt_interrupted_twice_in_its_counting_out(self, call=None) -> BaseException:
        """Call ``halt`` (or ``call``) with an interrupt in ``end`` and another in ``forget``, so
        that neither takes the halt's entry out and it is left in the count. Returns the
        interrupt the call ends with: its traceback keeps the frame of the ``halt`` call."""
        halts = gateway_module._HALTS
        landed: list = []

        def interrupted_in(step: str):
            def interrupted(*args) -> None:
                landed.append(step)
                raise KeyboardInterrupt
            return interrupted
        with mock.patch.object(halts, "end", interrupted_in("end")), \
                mock.patch.object(halts, "forget", interrupted_in("forget")):
            try:
                (call or self.book.halt)()
            except KeyboardInterrupt as exc:
                self.assertEqual(landed, ["end", "forget"], "the interrupts did not land where the test means them to")
                return exc
        self.fail("the interrupts did not land where the test means them to")

    def _keeps_the_frame_of_a_halt(self, exc: BaseException) -> bool:
        """Whether the interrupt's traceback keeps the frames of a ``halt`` call: the frame of
        ``halt`` and the frame its body runs in."""
        kept = set()
        tb = exc.__traceback__
        while tb is not None:
            kept.add(tb.tb_frame.f_code)
            tb = tb.tb_next
        return {PaperBook.halt.__code__, PaperBook._halt_counted.__code__} <= kept

    def _age(self, flag: str) -> float:
        """Date ``flag`` further back than a halt in progress can leave its flag. Returns that date."""
        old = time.time() - gateway_module.HALT_YIELD_S - 5
        os.utime(flag, (old, old))
        return old

    def _fifo(self) -> str:
        """A FIFO with no reader, under a name of its own next to the flag (so the flag's path
        can be made a second name for it). Whatever opened it for writing and is still waiting
        is let go at clean-up, through that name, which stays even if the flag's path is
        cleared: a test that fails that way fails instead of hanging."""
        side = self.book._halt_flag + ".fifo"
        os.mkfifo(side, 0o600)

        def let_an_opener_go() -> None:
            try:
                os.close(os.open(side, os.O_RDONLY | os.O_NONBLOCK))
                os.unlink(side)
            except OSError:
                pass
        self.addCleanup(let_an_opener_go)
        return side

    def _link(self, target: str, link: str) -> None:
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("this platform or account cannot make a symbolic link: %s" % type(exc).__name__)

    def _check(self, run_id: str, connection: str, plugin_id: str) -> dict:
        # The outside check runs in the submitting thread; the re-verify runs on another one,
        # inside the booking transaction, and answers when the test says so.
        if not threading.current_thread().name.startswith(BOOKING):
            self.reverifies.append(run_id)
            self.first_reverify.set()
            self.answer.wait(3 * WAIT)
        return self.core.check(run_id, connection, plugin_id)

    def _until(self, condition, why: str) -> None:
        deadline = time.monotonic() + WAIT
        while not condition():
            self.assertLess(time.monotonic(), deadline, why)
            time.sleep(0.001)

    def _back(self, call, why: str):
        """What ``call`` returns. It runs on its own thread, so one that never comes back fails
        the test instead of hanging it; what it raises is raised here."""
        out, raised = [], []

        def run() -> None:
            try:
                out.append(call())
            except BaseException as exc:  # noqa: BLE001 - raised again below, in the test's thread
                raised.append(exc)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(WAIT)
        self.assertFalse(thread.is_alive(), why)
        if raised:
            raise raised[0]
        return out[0]

    def _busy_timeout_is_put_back(self) -> None:
        self.assertEqual(self.book._db.execute("PRAGMA busy_timeout").fetchone()[0],
                         gateway_module._BUSY_TIMEOUT_MS, "a probe left its own short wait on the connection")

    def _take(self, lock: _FifoLock):
        """Take the book's lock for the test. Returns what lets go of it. A test that fails
        first lets go of it at clean-up, so closing the book never waits on a lock that
        nobody will free."""
        lock.acquire()
        held = [True]

        def let_go() -> None:
            if held:
                held.clear()
                lock.release()
        self.addCleanup(let_go)
        return let_go

    def _approved(self, intent_id: str) -> tuple:
        intent = sized(self.gw, dataclasses.replace(BASE, intent_id=intent_id))
        approval = self.gw.request_approval(intent)["approval_id"]
        self.core.operator_approves(approval)
        return intent, approval

    # -- watching a writer's looks at the flag -----------------------------------------------

    def _watch_judgements(self) -> list:
        """Every judgement of the flag from here on, as ``(thread name, live or not)``."""
        judged: list = []
        real = gateway_module._halt_flag_live

        def watched(st: os.stat_result) -> bool:
            live = real(st)
            judged.append((threading.current_thread().name, live))
            return live
        self.enterContext(mock.patch.object(gateway_module, "_halt_flag_live", watched))
        return judged

    def _watch_looks(self, book: PaperBook | None = None) -> tuple:
        """Every finished wait before the lock and every look under it (on ``book``, else on
        the test's book), each as ``(thread name, cap handed in, cap handed back)``."""
        book = book or self.book
        before, under = [], []
        yield_to_halt, give_way = book._yield_to_halt, book._give_way

        def watched_yield(cap=None):
            out = yield_to_halt(cap)
            before.append((threading.current_thread().name, cap, out))
            return out

        def watched_give_way(cap):
            out = give_way(cap)
            under.append((threading.current_thread().name, cap, out))
            return out
        book._yield_to_halt, book._give_way = watched_yield, watched_give_way
        return before, under

    def _watch_count(self) -> list:
        """Every reading of the count of halts in progress from here on, as
        ``(thread name, count)``."""
        read: list = []
        count = gateway_module._HALTS.count

        def watched(book: str) -> int:
            halts = count(book)
            read.append((threading.current_thread().name, halts))
            return halts
        self.enterContext(mock.patch.object(gateway_module._HALTS, "count", watched))
        return read

    def _after_the_writers_first_judgement(self, then) -> None:
        """Run ``then`` once, in the writer's own thread, right after its first judgement of
        the flag: what another process does between a writer's look and its removal."""
        real = gateway_module._halt_flag_live
        judged: list = []

        def watched(st: os.stat_result) -> bool:
            live = real(st)
            if threading.current_thread().name == "writer":
                judged.append(live)
                if len(judged) == 1:
                    then()
            return live
        self.enterContext(mock.patch.object(gateway_module, "_halt_flag_live", watched))

    def _after_the_halts_look_at_its_flag(self, swap) -> None:
        """Run ``swap`` once, just before the gateway first opens the flag's path or dates it
        by path: after its look at what is there, and before it acts on what it saw."""
        flag, swapped = self.book._halt_flag, []

        def watching(real):
            def watched(path, *args, **kwargs):
                if path == flag and not swapped:
                    swapped.append(True)
                    swap()
                return real(path, *args, **kwargs)
            return watched
        self.enterContext(mock.patch.object(gateway_module.os, "open", watching(os.open)))
        if os.utime not in os.supports_fd:  # dated by path there; elsewhere through the open above
            self.enterContext(mock.patch.object(gateway_module.os, "utime", watching(os.utime)))

    # -- a halt behind a queue of bookings ---------------------------------------------------

    def _halt_behind_a_queue(self, action: str, fifo: bool = True, first=None, meanwhile=None) -> tuple:
        """One booking inside its transaction (its re-verify not yet answered), ``QUEUED`` more
        bookings behind it, then a halt or a kill; the booking in progress is then answered.
        ``first`` runs once the bookings are queued, before the halt is called; ``meanwhile``
        runs while the halt is in progress and last in line, before the answer. Returns the
        bookings' outcomes, sorted, and what the halt or kill returned."""
        lock = None
        if fifo:
            lock = self.book._lock = _FifoLock()
        before, _ = self._watch_looks()
        orders = [self._approved("hq%d" % i) for i in range(1 + QUEUED)]
        outcomes: dict = {}

        def submit(i: int, intent: Intent, approval: str) -> None:
            try:
                outcomes[i] = self.gw.submit(intent, approval, AUTHORITY)["status"]
            except Refused as exc:
                outcomes[i] = "%s:%s" % (exc.code, exc.cause)
        bookings = [threading.Thread(target=submit, args=(i, intent, approval), daemon=True, name=BOOKING + str(i))
                    for i, (intent, approval) in enumerate(orders)]
        for thread in bookings:
            thread.start()
        for _ in bookings:
            self.assertTrue(self.at_the_door.acquire(timeout=WAIT), "a booking never reached its transaction")
        del before[:]  # the waits so far were the probes'; from here on they are the transactions'
        self.go.set()
        self.assertTrue(self.first_reverify.wait(WAIT), "no booking reached its re-verify")
        # Every booking is past its wait for a halt, and none found one: the one in progress
        # holds the book's lock and the others are at it.
        self._until(lambda: len(before) == 1 + QUEUED, "a booking never got past its wait for a halt")
        self.assertEqual([cap for _, _, cap in before], [None] * (1 + QUEUED))
        if fifo:
            self._until(lambda: lock.waiting() == QUEUED, "the other bookings never queued on the book's lock")
        if first is not None:
            first()
        returned: list = []
        halter = threading.Thread(
            target=lambda: returned.append(self.switch.kill() if action == "kill" else self.gw.halt()),
            daemon=True, name="halter")
        halter.start()
        if fifo:  # the halt is last in line: every queued booking gets the lock before it does
            self._until(lambda: lock.waiting() == QUEUED + 1, "the halt never queued on the book's lock")
        else:
            self._until(lambda: os.path.exists(self.book._halt_flag), "the halt never made its flag")
        if meanwhile is not None:
            meanwhile()
        self.answer.set()  # the booking in progress is answered, commits and lets go of the lock
        for thread in [halter] + bookings:
            thread.join(WAIT)
            self.assertFalse(thread.is_alive(), thread.name + " never finished")
        self.assertEqual(len(outcomes), 1 + QUEUED)
        return sorted(outcomes.values()), returned[0]

    def test_a_halt_goes_ahead_of_bookings_already_waiting_on_the_lock(self) -> None:
        outcomes, _ = self._halt_behind_a_queue("halt")
        self.assertEqual(outcomes, [HALTED] * QUEUED + [BOOKED])
        self.assertEqual(len(self.reverifies), 1, "a queued booking ran its transaction ahead of the halt")
        self.assertTrue(self.book.halted())
        self.assertEqual([status for _, _, status in self.book.orders()], ["working"])
        self.assertEqual(self.book.available_cash_minor(ACCT), CASH - 300)
        self.assertFalse(os.path.exists(self.book._halt_flag), "the halt flag was left behind")

    def test_a_kill_goes_ahead_of_bookings_already_waiting_on_the_lock(self) -> None:
        outcomes, killed = self._halt_behind_a_queue("kill")
        self.assertEqual((killed["status"], killed["halt"]), ("paper_only", "confirmed"))
        self.assertEqual(outcomes, [HALTED] * QUEUED + [BOOKED])
        self.assertEqual(len(self.reverifies), 1, "a queued booking ran its transaction ahead of the kill")
        self.assertEqual(self.book.working(), 0)  # the one booked ahead of the halt is cancelled by the kill
        self.assertEqual([status for _, _, status in self.book.orders()], ["cancelled"])
        self.assertEqual(self.book.available_cash_minor(ACCT), CASH)

    def test_the_same_holds_with_the_books_own_lock(self) -> None:
        outcomes, _ = self._halt_behind_a_queue("halt", fifo=False)
        self.assertEqual(outcomes, [HALTED] * QUEUED + [BOOKED])
        self.assertEqual(len(self.reverifies), 1)
        self.assertTrue(self.book.halted())
        self.assertEqual([status for _, _, status in self.book.orders()], ["working"])

    # -- inside the process, the halt's priority does not hang on its flag ---------------------

    def _the_halt_went_first(self, outcomes: list) -> None:
        self.assertEqual(outcomes, [HALTED] * QUEUED + [BOOKED])
        self.assertEqual(len(self.reverifies), 1, "a queued booking ran its transaction ahead of the halt")
        self.assertTrue(self.book.halted())
        self.assertEqual([status for _, _, status in self.book.orders()], ["working"])
        self.assertEqual(self._halts(), 0, "a halt that has ended is still counted as in progress")

    def test_a_halt_whose_flag_aged_out_still_goes_ahead_of_the_queued_bookings(self) -> None:
        # The halt waits on its own connection's lock, behind the queue, for longer than its flag
        # stays live (it dates the flag again only once it retries for the write lock). The
        # queued bookings have not used up their cap: they still give way.
        flag = self.book._halt_flag

        def the_wait_outlasts_the_flag() -> None:
            self._age(flag)
            self.assertFalse(self._flag_live())
        outcomes, _ = self._halt_behind_a_queue("halt", meanwhile=the_wait_outlasts_the_flag)
        self._the_halt_went_first(outcomes)
        self.assertFalse(os.path.lexists(flag), "the halt flag was left behind")

    def test_a_halt_whose_flag_was_removed_still_goes_ahead_of_the_queued_bookings(self) -> None:
        # As a writer in another process does once it takes the flag for a dead halt's, or
        # another process's halt as it ends.
        flag = self.book._halt_flag
        outcomes, killed = self._halt_behind_a_queue("kill", meanwhile=lambda: os.unlink(flag))
        self.assertEqual((killed["status"], killed["halt"]), ("paper_only", "confirmed"))
        self.assertEqual(outcomes, [HALTED] * QUEUED + [BOOKED])
        self.assertEqual(len(self.reverifies), 1, "a queued booking ran its transaction ahead of the kill")
        self.assertEqual([status for _, _, status in self.book.orders()], ["cancelled"])
        self.assertEqual(self._halts(), 0)

    def test_a_halt_whose_flag_cannot_be_made_still_goes_ahead_of_the_queued_bookings(self) -> None:
        flag = self.book._halt_flag = os.path.join(self.dir, "no-such-folder", "flag")
        outcomes, _ = self._halt_behind_a_queue(
            "halt", meanwhile=lambda: self.assertFalse(os.path.lexists(flag)))
        self._the_halt_went_first(outcomes)

    def test_a_writer_that_arrives_after_a_halt_began_waits_before_the_lock_on_the_count_alone(self) -> None:
        lock = self.book._lock = _FifoLock()
        flag = self.book._halt_flag
        read = self._watch_count()
        before, under = self._watch_looks()
        let_go = self._take(lock)  # something else holds the book's lock: the halt waits for it
        halter = threading.Thread(target=self.book.halt, daemon=True, name="halter")
        halter.start()
        self._until(lambda: lock.waiting() == 1, "the halt never queued on the book's lock")
        os.unlink(flag)  # nothing but the count says a halt is in progress
        writer, done = self._writer()
        writer.start()
        # The writer has looked twice and found the halt both times: it is waiting, not in line.
        self._until(lambda: read.count(("writer", 1)) >= 2 or lock.waiting() > 1,
                    "the writer never looked for a halt in progress")
        self.assertEqual(lock.waiting(), 1, "a writer went for the book's lock ahead of a halt in progress")
        self.assertEqual((before, under), ([], []))
        self.assertFalse(done.is_set())
        let_go()
        halter.join(WAIT)
        self.assertFalse(halter.is_alive(), "the halt never finished")
        self.assertTrue(done.wait(WAIT), "the writer did not go on after the halt ended")
        writer.join(WAIT)
        cap = before[0][2]
        self.assertIsNotNone(cap, "the wait on the halt in progress was not counted towards the writer's cap")
        self.assertEqual((before, under), ([("writer", None, cap)], [("writer", cap, None)]))
        self.assertTrue(self.book.halted())
        self.assertEqual([row["code"] for row in self.book.audit()], ["paper_only"])
        self.assertEqual(self._halts(), 0)

    def test_a_halt_in_progress_holds_a_writer_on_another_handle_to_its_cap_and_no_longer(self) -> None:
        # The count is the book file's, not the handle's: a writer on a second handle waits for a
        # halt called on the first. And the cap still bounds it: this halt never gets its lock.
        lock = self.book._lock = _FifoLock()
        other = PaperBook(self.path)
        self.addCleanup(other.close)
        before, under = self._watch_looks(other)
        let_go = self._take(lock)
        halter = threading.Thread(target=self.book.halt, daemon=True, name="halter")
        halter.start()
        self._until(lambda: lock.waiting() == 1, "the halt never queued on the book's lock")
        os.unlink(self.book._halt_flag)  # the count alone
        writer, done = self._writer(lambda: other.record_refusal(REFUSAL))
        with mock.patch.object(gateway_module, "HALT_YIELD_TOTAL_S", 0.3):
            writer.start()
            self.assertTrue(done.wait(WAIT), "a halt in progress held a writer for ever")
            finished = time.monotonic()
        writer.join(WAIT)
        self.assertEqual(self._halts(), 1, "the writer went on because the halt ended, not at its cap")
        cap = before[0][2]
        self.assertIsNotNone(cap, "a writer on another handle of the same book did not wait for the halt")
        self.assertGreaterEqual(finished, cap, "the writer went on before its cap")
        self.assertEqual((before, under), ([("writer", None, cap)], [("writer", cap, None)]))
        self.assertEqual([row["code"] for row in other.audit()], ["paper_only"])
        self.assertFalse(other.halted())
        let_go()
        halter.join(WAIT)
        self.assertFalse(halter.is_alive(), "the halt never finished")
        self.assertTrue(other.halted())
        self.assertEqual(self._halts(), 0)

    # -- a halt behind a queue of submit probes ----------------------------------------------

    def _halt_behind_queued_probes(self, meanwhile=None) -> tuple:
        """Another connection holds the write lock. ``QUEUED`` probes wait on the book's lock,
        then a halt begins behind them, and the lock is let go. Once the halt has tried for
        the write lock the other connection lets go of it. ``meanwhile`` runs while the halt
        is in progress and last in line, with the write lock free for as long as it runs.
        Returns who tried for the write lock, in order, and the probes' outcomes, sorted."""
        lock = self.book._lock = _FifoLock()
        tried: list = []
        self.book._db = _Watched(self.book._db, tried)
        holder = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        outcomes: dict = {}

        def probe(i: int) -> None:
            try:
                self.book._probe_writable()
                outcomes[i] = "writable"
            except Refused as exc:
                outcomes[i] = exc.code
        let_go = self._take(lock)  # something else holds the book's lock while the queue forms
        probes = [threading.Thread(target=probe, args=(i,), daemon=True, name=PROBE + str(i)) for i in range(QUEUED)]
        for i, thread in enumerate(probes):
            thread.start()
            self._until(lambda: lock.waiting() == i + 1, "a probe never queued on the book's lock")
        halter = threading.Thread(target=self.book.halt, daemon=True, name="halter")
        halter.start()
        self._until(lambda: lock.waiting() == QUEUED + 1, "the halt never queued on the book's lock")
        if meanwhile is not None:
            holder.execute("ROLLBACK")
            meanwhile()
            holder.execute("BEGIN IMMEDIATE")
        # A probe that does try waits this long for the write lock, holding the book's lock.
        with mock.patch.object(gateway_module, "BOOK_LOCK_PROBE_S", 0.05):
            let_go()
            self._until(lambda: "halter" in tried, "the halt never tried for the write lock")
            holder.execute("ROLLBACK")
            for thread in [halter] + probes:
                thread.join(WAIT)
                self.assertFalse(thread.is_alive(), thread.name + " never finished")
        self.assertEqual(len(outcomes), QUEUED)
        return tried, sorted(outcomes.values())

    def test_a_halt_goes_ahead_of_probes_already_waiting_on_the_lock(self) -> None:
        tried, outcomes = self._halt_behind_queued_probes()
        self.assertEqual(tried[0], "halter", "a queued probe tried for the write lock ahead of the halt")
        self.assertEqual(sorted(tried[1:]), [PROBE + str(i) for i in range(QUEUED)])  # each once, afterwards
        self.assertEqual(outcomes, ["writable"] * QUEUED)  # halted, but nothing holds the write lock
        self._busy_timeout_is_put_back()
        self.assertTrue(self.book.halted())
        self.assertFalse(os.path.exists(self.book._halt_flag), "the halt flag was left behind")

    # -- halts that overlap, on two handles of one book ---------------------------------------

    def _another_handle(self) -> PaperBook:
        other = PaperBook(self.path)
        self.addCleanup(other.close)
        return other

    def test_a_halt_on_a_closed_book_touches_nothing(self) -> None:
        flag = self.book._halt_flag
        other = self._another_handle()
        other.close()
        open(flag, "w").close()  # a halt is in progress in another process
        stamp = time.time() - 2
        os.utime(flag, (stamp, stamp))  # still live, and dated so that any dating of it would show
        was = os.lstat(flag)
        with self.assertRaises(Refused) as ctx:
            other.halt()
        self.assertEqual(ctx.exception.code, "book_closed")
        self.assertTrue(os.path.lexists(flag), "a halt on a closed book removed another halt's flag")
        now = os.lstat(flag)
        self.assertEqual((now.st_ino, now.st_mtime_ns), (was.st_ino, was.st_mtime_ns),
                         "a halt on a closed book made or dated the flag")
        self.assertFalse(self.book.halted())
        self.assertEqual(self._halts(), 0)

    def _a_kill_waits_while_another_handles_halt_ends(self, others_halt) -> None:
        """A kill waits behind queued bookings. Meanwhile a halt is called on another handle of
        the same book and ends without committing. The kill keeps its flag and its place."""
        flag = self.book._halt_flag
        other = self._another_handle()

        def meanwhile() -> None:
            others_halt(other)
            self.assertTrue(self._flag_live(),
                            "a halt that ended on another handle took the flag of the one in progress")
            self.assertEqual(self._halts(), 1)
        outcomes, killed = self._halt_behind_a_queue("kill", meanwhile=meanwhile)
        self.assertEqual((killed["status"], killed["halt"]), ("paper_only", "confirmed"))
        self.assertEqual(outcomes, [HALTED] * QUEUED + [BOOKED])
        self.assertEqual(len(self.reverifies), 1, "a queued booking ran its transaction ahead of the kill")
        self.assertEqual(self.book.working(), 0)
        self.assertFalse(os.path.lexists(flag), "the halt flag was left behind")
        self.assertEqual(self._halts(), 0)

    def test_a_halt_refused_on_a_closed_handle_leaves_a_kill_in_progress_its_flag_and_its_place(self) -> None:
        def refused(other: PaperBook) -> None:
            other.close()
            with self.assertRaises(Refused) as ctx:
                other.halt()
            self.assertEqual(ctx.exception.code, "book_closed")
        self._a_kill_waits_while_another_handles_halt_ends(refused)

    def test_a_halt_that_times_out_on_another_handle_leaves_a_kill_in_progress_its_flag_and_its_place(self) -> None:
        def timed_out(other: PaperBook) -> None:
            # The booking in progress holds the write lock, so this halt retries until it gives up.
            with mock.patch.object(gateway_module, "_BUSY_TIMEOUT_MS", 10):
                with self.assertRaises(sqlite3.OperationalError):
                    other.halt()
            self.assertFalse(other.halted())
        self._a_kill_waits_while_another_handles_halt_ends(timed_out)

    def test_a_halt_that_commits_on_another_handle_leaves_a_queued_halt_ahead_of_the_probes(self) -> None:
        other = self._another_handle()

        def meanwhile() -> None:
            other.halt()  # commits on its own connection while the first halt is still in line
            self.assertTrue(other.halted())
            self.assertTrue(self._flag_live(),
                            "a halt that committed on another handle took the flag of the one in progress")
            self.assertEqual(self._halts(), 1)
        tried, outcomes = self._halt_behind_queued_probes(meanwhile)
        self.assertEqual(tried[0], "halter", "a queued probe tried for the write lock ahead of the halt in progress")
        self.assertEqual(sorted(tried[1:]), [PROBE + str(i) for i in range(QUEUED)])
        self.assertEqual(outcomes, ["writable"] * QUEUED)
        self._busy_timeout_is_put_back()
        self.assertFalse(os.path.lexists(self.book._halt_flag), "the halt flag was left behind")
        self.assertEqual(self._halts(), 0)

    def test_a_halt_that_begins_as_another_is_ending_keeps_its_flag(self) -> None:
        # One halt has committed, found itself the last in progress and is about to take the
        # flag away. Another begins on a second handle. It is counted in at once, and makes its
        # flag only once the first has taken the old one away: it never loses its own to it.
        flag, key = self.book._halt_flag, self.book._halt_key
        order = gateway_module._HALTS._flag_locks[key] = _FifoLock()  # stands in for the book's flag lock
        other = self._another_handle()
        lock = other._lock = _FifoLock()
        let_go = self._take(lock)  # the second halt stays in progress, waiting for its handle's lock
        at_the_removal, go_on = threading.Event(), threading.Event()
        self.addCleanup(go_on.set)
        unlink = os.unlink

        def held_up(path, *args, **kwargs):
            if path == flag and threading.current_thread().name == "ending":
                at_the_removal.set()
                go_on.wait(WAIT)
            return unlink(path, *args, **kwargs)
        self.enterContext(mock.patch.object(gateway_module.os, "unlink", held_up))
        ending = threading.Thread(target=self.book.halt, daemon=True, name="ending")
        ending.start()
        self.assertTrue(at_the_removal.wait(WAIT), "the first halt never went to take its flag away")
        self.assertTrue(self.book.halted())
        beginning = threading.Thread(target=other.halt, daemon=True, name="beginning")
        beginning.start()
        self._until(lambda: order.waiting() == 1,
                    "the halt that began did not wait for the one ending to take the flag away")
        # It is counted in already, and the count is read without waiting for the removal: the
        # count's lock is not held across it.
        self.assertEqual(self._back(self._halts, "reading the count waited for a halt to take its flag away"), 1)
        go_on.set()
        ending.join(WAIT)
        self.assertFalse(ending.is_alive(), "the first halt never finished")
        self._until(lambda: lock.waiting() == 1, "the second halt never went for its handle's lock")
        self.assertTrue(self._flag_live(), "a halt that was ending took away the flag of one that had begun")
        let_go()
        beginning.join(WAIT)
        self.assertFalse(beginning.is_alive(), "the second halt never finished")
        self.assertFalse(os.path.lexists(flag), "the halt flag was left behind")
        self.assertEqual(self._halts(), 0)

    def test_an_interrupt_as_a_halt_begins_leaves_no_flag_and_no_halt_in_progress(self) -> None:
        # The halt's first reading of the clock comes right after it has made its flag.
        flag = self.book._halt_flag
        monotonic = time.monotonic
        flag_made: list = []

        def interrupted_once() -> float:
            flag_made.append(os.path.lexists(flag))
            if len(flag_made) == 1:
                raise KeyboardInterrupt
            return monotonic()
        with mock.patch.object(gateway_module.time, "monotonic", interrupted_once):
            with self.assertRaises(KeyboardInterrupt):
                self.book.halt()
        self.assertEqual(flag_made[:1], [True], "the interrupt did not land where the test means it to")
        self.assertFalse(os.path.lexists(flag), "an interrupted halt left its flag behind")
        self.assertFalse(self.book.halted())
        self.assertEqual(self._halts(), 0, "an interrupted halt is still counted as in progress")
        before, _ = self._watch_looks()
        self._back(lambda: self.book.record_refusal(REFUSAL), "a writer waited on a halt that was interrupted")
        self.assertEqual([look[1:] for look in before], [(None, None)])

    def test_a_halt_is_counted_in_before_it_touches_its_flag(self) -> None:
        # Its priority inside the process starts with the call, not once the flag is made.
        counted: list = []
        raise_flag = self.book._raise_halt_flag
        self.book._raise_halt_flag = lambda: (counted.append(self._halts()), raise_flag())[1]
        self.book.halt()
        self.assertEqual(counted, [1], "a halt went to its flag before it was counted as in progress")
        self.assertEqual(self._halts(), 0)

    def test_a_halt_interrupted_before_it_is_counted_in_touches_no_flag(self) -> None:
        # It never took its place, so it made no flag: the one there is some other halt's.
        flag = self.book._halt_flag
        open(flag, "w").close()  # a halt is in progress in another process
        was = os.lstat(flag)

        def interrupted(book: str, halt: object) -> None:
            raise KeyboardInterrupt
        with mock.patch.object(gateway_module._HALTS, "begin", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.book.halt()
        self.assertTrue(os.path.lexists(flag), "a halt that was never counted in removed another halt's flag")
        self.assertEqual(os.lstat(flag).st_ino, was.st_ino)
        self.assertEqual(self._halts(), 0)
        self.assertFalse(self.book.halted())

    def test_an_interrupt_inside_a_halts_counting_out_still_leaves_it_out_of_the_count(self) -> None:
        # Counting out is not one step: ``end`` takes the book's flag lock before it takes the halt
        # out, so an interrupt can land inside it. The halt is then taken out by ``forget``; its
        # flag is left, and a writer treats it like any flag whose halt has gone.
        halts, flag = gateway_module._HALTS, self.book._halt_flag
        flag_lock = halts.flag_lock
        uses: list = []
        # The test keeps the object each halt is counted by, so an entry cannot stop being
        # counted because that object went: only ``forget`` can have taken it out.
        held = self._hold_what_halts_are_counted_by()

        def interrupted_end(book: str, halt: object, flag: str) -> None:
            raise KeyboardInterrupt

        def interrupted_at_its_second_use(book: str):
            uses.append(book)
            if len(uses) == 2:  # the first was the halt making its flag; this one is ``end``
                raise KeyboardInterrupt
            return flag_lock(book)
        cases = {"as end is entered": ("end", interrupted_end),
                 "as end goes for the flag lock": ("flag_lock", interrupted_at_its_second_use)}
        for where, (name, interrupted) in cases.items():
            with self.subTest(interrupt=where):
                landed = False
                with mock.patch.object(halts, name, interrupted):
                    try:
                        self.book.halt()
                    except KeyboardInterrupt:
                        landed = True
                self.assertTrue(landed, "the interrupt did not land where the test means it to")
                self.assertTrue(held and all(type(one) is gateway_module._Halt for one in held),
                                "the test does not hold what the halt was counted by")
                self.assertEqual(self._halts(), 0, "an interrupted halt is still counted as in progress")
                self.assertTrue(self.book.halted(), "the halt had committed before it was interrupted")
                self.assertTrue(os.path.lexists(flag), "the interrupt was meant to land before the flag's removal")
                self._age(flag)  # no halt dates it any more: the writer finds it stale, removes it and goes on
                self._back(lambda: self.book.record_refusal(REFUSAL), "a writer waited on a halt that had ended")
                self.assertFalse(os.path.lexists(flag), "the flag an interrupted halt left was not removed")

    def test_a_halt_whose_counting_out_was_interrupted_twice_is_not_counted_though_its_frame_is_kept(self) -> None:
        # Two interrupts in a row skip both ``end`` and ``forget``, and the entry is left in the
        # count. It is counted only while the object the call was counted by is alive, and that
        # object is held by the call's evaluation stack and by no variable. Keeping the
        # interrupt, and with it the traceback and the call's frames, therefore keeps nothing
        # counted.
        kept = self._a_halt_interrupted_twice_in_its_counting_out()
        self.assertTrue(self._keeps_the_frame_of_a_halt(kept), "the test does not keep the frame of the halt call")
        self._nothing_else_holds_it()
        self.assertEqual(self._halts(), 0, "a halt whose call is over is still counted: its frame was kept")
        self.assertTrue(self.book.halted())
        # No writer waits on it either, once the flag it left has gone stale.
        self._age(self.book._halt_flag)
        before, _ = self._watch_looks()
        self._back(lambda: self.book.record_refusal(REFUSAL), "a writer waited on a halt whose call was over")
        self.assertEqual([look[1:] for look in before], [(None, None)])
        self.assertTrue(self._keeps_the_frame_of_a_halt(kept))  # kept to the end

    def test_a_halt_that_follows_one_interrupted_twice_ends_alone_and_takes_the_flag_away(self) -> None:
        # The entry the interrupted halt left is in the count when the next halt begins and when
        # it ends: nothing has looked at the count in between. It belongs to no running halt, so
        # the halt that follows is the only one in progress, and when it ends it takes the flag
        # away, which it would not do while another were still counted. That holds whether the
        # interrupt is kept or let go, and wherever the next halt's own object happens to be made.
        flag = self.book._halt_flag
        for keeping in ("kept", "let go"):
            with self.subTest(interrupt=keeping):
                kept = self._a_halt_interrupted_twice_in_its_counting_out()
                if keeping == "let go":
                    kept = None
                self._nothing_else_holds_it()
                self.assertTrue(os.path.lexists(flag))
                self.book.halt()
                self.assertFalse(os.path.lexists(flag),
                                 "a halt that ended alone left the flag: an entry of a halt that was over was counted")
                self.assertEqual((self._halts(), self._entries()), (0, 0))
                del kept

    def test_what_holds_the_object_a_halt_was_counted_by_keeps_a_twice_interrupted_halt_counted(self) -> None:
        # A limit that remains (see "Limits that remain" in the gateway's module doc). An entry
        # left by two interrupts is counted for exactly as long as that object is alive. Nothing
        # holds it but the call, unless something called the halt's token while the call ran and
        # kept the result, as this test does.
        flag = self.book._halt_flag
        held = self._hold_what_halts_are_counted_by()
        kept = self._a_halt_interrupted_twice_in_its_counting_out()
        del kept  # neither the interrupt nor its traceback nor the call's frame is kept
        self._nothing_else_holds_it()
        self.assertEqual([type(one) for one in held], [gateway_module._Halt])
        self.assertEqual(self._halts(), 1, "held by something else, the object should still keep the entry counted")
        held.clear()
        self._nothing_else_holds_it()
        self.assertEqual(self._halts(), 0, "an entry was counted after the object it was counted by had gone")
        self.assertTrue(os.path.lexists(flag))
        self.book.halt()  # and the halts that follow are counted as usual
        self.assertFalse(os.path.lexists(flag), "the halt flag was left behind")
        held.clear()
        self._nothing_else_holds_it()
        self.assertEqual(self._halts(), 0)

    # -- an interrupt raised where no call is ---------------------------------------------------

    def _counting_out_lines(self) -> list:
        """The lines of the ``finally`` in which a halt counts itself out, in order."""
        source, first = inspect.getsourcelines(PaperBook._halt_counted)
        body = ast.parse(textwrap.dedent("".join(source))).body[0]
        outer = [node for node in body.body if isinstance(node, ast.Try) and node.finalbody]
        self.assertEqual(len(outer), 1, "the test no longer finds where a halt counts itself out")
        return sorted({first + node.lineno - 1 for part in outer[0].finalbody for node in ast.walk(part)
                       if isinstance(node, ast.stmt)})

    def test_an_interrupt_at_any_line_of_a_halts_counting_out_leaves_it_out_of_the_count(self) -> None:
        # A halt counts itself out in a ``finally``: ``end``, with ``forget`` in a ``finally``
        # around it. An interrupt can be raised at each line of that, by a trace function if by
        # nothing else, and on top of interrupts in ``end`` and in ``forget``. Raised at the line
        # of the ``try:`` it skips every handler of the function (the gateway's module doc says
        # why), so that neither ``end`` nor ``forget`` runs. Whatever is skipped, the halt is not
        # counted once its call is over, though the interrupt is kept with its traceback and the
        # call's frames, and the halt that follows ends alone and takes the flag away. The trace
        # function is a stand-in: the test does not show that an interrupt raised by a signal
        # handler can land at these lines.
        halts, flag, body = gateway_module._HALTS, self.book._halt_flag, PaperBook._halt_counted.__code__
        lines = self._counting_out_lines()
        self.assertEqual(len(lines), 3, "the counting out is no longer a try, an end and a forget")
        names = dict(zip(lines, ("the try", "the call of end", "the call of forget")))

        def interrupted(*args) -> None:
            raise KeyboardInterrupt
        for in_calls in ((), ("end",), ("end", "forget")):
            for line in lines:
                with self.subTest(interrupts_in=in_calls, and_one_at=names[line]):
                    landed: list = []

                    def at_the_line(frame, event, arg):
                        if frame.f_code is not body:
                            return None
                        if event == "line" and frame.f_lineno == line:
                            landed.append(line)
                            raise _Forced("at a line of the counting out")  # which also ends the tracing
                        return at_the_line
                    kept = None
                    with contextlib.ExitStack() as patches:
                        for name in in_calls:
                            patches.enter_context(mock.patch.object(halts, name, interrupted))
                        before = sys.gettrace()
                        sys.settrace(at_the_line)
                        try:
                            self.book.halt()
                        except KeyboardInterrupt as exc:
                            kept = exc
                        finally:
                            sys.settrace(before)
                    self.assertEqual(landed, [line], "the interrupt did not land where the test means it to")
                    self.assertTrue(kept is not None and self._keeps_the_frame_of_a_halt(kept),
                                    "the test does not keep the frames of the halt call")
                    self._nothing_else_holds_it()
                    self.assertEqual(self._halts(), 0, "a halt whose call is over is still counted")
                    self.assertTrue(self.book.halted())
                    self.book.halt()  # a halt that follows, the interrupt still kept
                    self.assertFalse(os.path.lexists(flag), "the halt that followed left its flag behind")
                    self.assertEqual((self._halts(), self._entries()), (0, 0))
                    kept = None

    def _counted_once_collected(self) -> int:
        """The count for the book. If it is above zero it is read again after a collection, so
        that the answer does not hang on how this interpreter frees objects."""
        if self._halts():
            self._nothing_else_holds_it()
        return self._halts()

    def _interrupted_before(self, nth, call, codes) -> tuple:
        """Run ``call`` (which calls ``halt`` once) with a trace function raising one ``_Forced``
        just before the ``nth`` instruction executed in the functions whose code is in ``codes``
        (``None``: none is raised). Returns the instruction it was raised before (or None), how
        many instructions were counted, and what the call ended with: an exception, or None
        where it returned."""
        seen = {"n": 0, "at": None}

        def trace(frame, event, arg):
            if frame.f_code not in codes:
                return None
            frame.f_trace_opcodes = True
            if event == "opcode":
                seen["n"] += 1
                if seen["n"] == nth:
                    seen["at"] = next(step for step in dis.get_instructions(frame.f_code)
                                      if step.offset == frame.f_lasti)
                    raise _Forced("before instruction %d" % nth)  # which also ends the tracing
            return trace
        here, before = sys._getframe(), sys.gettrace()
        asked = here.f_trace_opcodes
        # On 3.12 and later "opcode" events are delivered only if a frame had asked for them
        # before ``sys.settrace`` was called.
        here.f_trace_opcodes = True
        sys.settrace(trace)
        try:
            call()
        except BaseException as exc:  # noqa: BLE001 - the interrupt, or what was raised on top of it
            return seen["at"], seen["n"], exc
        finally:
            sys.settrace(before)
            here.f_trace_opcodes = asked
        return seen["at"], seen["n"], None

    def _interrupted_before_each_instruction(self, through_a_kill: bool) -> None:
        """Call ``halt`` once for every instruction it executes in its own two functions, with a
        trace function raising one interrupt just before that instruction, and check each time
        that the halt is not counted once its call is over (the interrupt, its traceback and the
        call's frames kept) and that the halt that follows ends alone and takes the flag away.
        With ``through_a_kill`` the halt is called by ``KillSwitch.kill``, which keeps the
        interrupt until it has cancelled the paper orders, and the count is read from inside
        that cancel as well."""
        halts, book = gateway_module._HALTS, self.book
        key, flag = book._halt_key, book._halt_flag
        codes = (PaperBook.halt.__code__, PaperBook._halt_counted.__code__)
        # Not what this test is about, and stated in the gateway's module doc: an interrupt forced
        # at some instructions leaves a lock held or a transaction open. Stand-ins that their
        # holder can take again keep the halt, the kill and this test from waiting on a lock left
        # held; the test gives the lock back and rolls the transaction back before it goes on.
        book_lock = book._lock = _Retaken()
        flag_lock = halts._flag_locks[key] = _Retaken()
        self.addCleanup(halts._flag_locks.pop, key, None)
        at_the_cancel: list = []
        call = book.halt
        if through_a_kill:
            cancel_all = book.cancel_all

            def watched() -> int:
                at_the_cancel.append(self._counted_once_collected())
                if os.path.lexists(flag):
                    self._age(flag)  # a flag the interrupted halt left: stale, so the cancel does not wait on it
                return cancel_all()
            book.cancel_all = watched
            # A build that still counts the halt waits this cap out and then fails below, instead
            # of waiting the cap ``setUp`` sets.
            self.enterContext(mock.patch.object(gateway_module, "HALT_YIELD_TOTAL_S", 0.3))
            call = self.switch.kill
        _, instructions, _ = self._interrupted_before(None, call, codes)
        self.assertGreater(instructions, 100, "the test did not see the instructions of a halt")
        seen_at = set()
        for nth in range(1, instructions + 1):
            book._db.execute("UPDATE state SET v = 0 WHERE k = 'halted'")  # every call is a first halt
            self._remove_flag()
            del at_the_cancel[:]
            step, _, kept = self._interrupted_before(nth, call, codes)
            self.assertIsNotNone(step, "no instruction %d in this call" % nth)
            where = "before instruction %d: %s, line %s" % (nth, step.opname, step.positions.lineno)
            seen_at.add((step.opname, step.positions.lineno))
            book_lock.left()
            flag_lock.left()
            if book._db.in_transaction:
                book._db.execute("ROLLBACK")
            if through_a_kill:
                self.assertEqual(at_the_cancel, [0], "the kill's paper cancel found its halt counted, " + where)
            else:
                # The halt ends with the interrupt, or with an error raised while it was handling
                # the interrupt (after the commit its rollback finds nothing to roll back and
                # fails: see the limits in the gateway's module doc).
                raised = kept
                while raised is not None and not isinstance(raised, _Forced):
                    raised = raised.__context__
                self.assertIsNotNone(raised, "the halt did not end with the interrupt, " + where)
            self.assertEqual(self._counted_once_collected(), 0, "a halt whose call is over is still counted, " + where)
            book.halt()  # a halt that follows, the interrupt still kept
            self.assertFalse(os.path.lexists(flag), "the halt that followed left its flag behind, " + where)
            self.assertEqual((self._halts(), self._entries()), (0, 0), where)
            kept = None
        # The line at which one interrupt skips the whole counting out was among them.
        self.assertIn(("NOP", self._counting_out_lines()[0]), seen_at)

    def test_a_halt_interrupted_before_any_one_of_its_instructions_is_not_counted_afterwards(self) -> None:
        self._interrupted_before_each_instruction(through_a_kill=False)

    def test_a_kill_whose_halt_was_interrupted_before_any_one_instruction_does_not_find_it_counted(self) -> None:
        self._interrupted_before_each_instruction(through_a_kill=True)

    def test_the_count_follows_a_halts_token_and_drops_what_it_no_longer_counts(self) -> None:
        halts, key = gateway_module._HALTS, self.book._halt_key
        call = gateway_module._Halt()
        token = weakref.ref(call)
        halts.begin(key, token)
        self.assertEqual(self._halts(), 1)
        other = gateway_module._Halt()
        halts.forget(weakref.ref(other))  # never counted in: changes nothing
        self.assertEqual(self._halts(), 1)
        del call
        self._nothing_else_holds_it()
        self.assertEqual(self._halts(), 0, "an entry of a halt that is over was counted")
        self.assertEqual(self._entries(), 0, "an entry of a halt that is over was kept")
        call = gateway_module._Halt()
        token = weakref.ref(call)
        halts.begin(key, token)
        halts.forget(token)
        self.assertEqual((self._halts(), self._entries()), (0, 0))

    def test_an_end_or_forget_that_never_began_changes_nothing_whatever_else_is_counted(self) -> None:
        # The count goes by a halt's own token. One that was never counted in finds no entry: not
        # the entry of a halt that is over (though its own object may well have been made where
        # that halt's used to be), and not that of a halt still in progress.
        halts, key, flag = gateway_module._HALTS, self.book._halt_key, self.book._halt_flag
        open(flag, "w").close()  # a halt is in progress in another process
        was = os.lstat(flag)
        over = gateway_module._Halt()
        left = weakref.ref(over)
        halts.begin(key, left)
        del over
        self._nothing_else_holds_it()
        self.assertIsNone(left(), "the test's own stand-in for a halt that is over is still alive")
        # With nothing in the count but the entry of the halt that is over.
        call = gateway_module._Halt()
        never = weakref.ref(call)
        halts.end(key, never, flag)
        halts.forget(never)
        self.assertIn(left, halts._halts, "a halt that never began took out another halt's entry")
        self.assertTrue(os.path.lexists(flag), "a halt that never began took the flag away")
        self.assertEqual(os.lstat(flag).st_ino, was.st_ino)
        # And with a halt in progress on the book as well.
        running = gateway_module._Halt()
        token = weakref.ref(running)
        halts.begin(key, token)
        again = gateway_module._Halt()
        never = weakref.ref(again)
        halts.end(key, never, flag)
        halts.forget(never)
        self.assertTrue(left in halts._halts and token in halts._halts and self._entries() == 2,
                        "a halt that never began took out another halt's entry")
        self.assertTrue(os.path.lexists(flag), "a halt that never began took the flag away")
        self.assertEqual(os.lstat(flag).st_ino, was.st_ino)
        # A token whose object had gone before it was ever handed in cannot be looked up at all
        # (``halt`` cannot make one: it holds its object until it has counted itself out).
        gone = weakref.ref(gateway_module._Halt())
        self._nothing_else_holds_it()
        self.assertIsNone(gone())
        for take_out in (lambda: halts.end(key, gone, flag), lambda: halts.forget(gone)):
            with self.assertRaises(TypeError):
                take_out()
        self.assertTrue(left in halts._halts and token in halts._halts and self._entries() == 2)
        self.assertEqual(os.lstat(flag).st_ino, was.st_ino)
        self.assertEqual(self._halts(), 1, "a halt that never began took a halt in progress out of the count")
        halts.forget(token)
        self.assertEqual((self._halts(), self._entries()), (0, 0))

    def test_the_count_does_not_go_by_where_an_object_is_in_memory(self) -> None:
        # A count kept under a number taken from an object's address would take one halt's entry
        # for another's whenever an object is made where one that is gone used to be. Here every
        # ``id`` the gateway could ask for is the same number. A halt interrupted twice leaves its
        # entry in the count. The halt that follows is interrupted before it is counted in, so it
        # made no flag and must touch none: the one there is some other halt's.
        halts, flag = gateway_module._HALTS, self.book._halt_flag
        self.enterContext(mock.patch.object(gateway_module, "id", lambda thing: 1, create=True))
        kept = self._a_halt_interrupted_twice_in_its_counting_out()
        del kept
        self._nothing_else_holds_it()
        self.assertTrue(os.path.lexists(flag))  # stands for the flag of a halt in another process
        was = os.lstat(flag)

        def interrupted(*args) -> None:
            raise KeyboardInterrupt
        with mock.patch.object(halts, "begin", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.book.halt()
        self.assertTrue(os.path.lexists(flag), "a halt that was never counted in removed another halt's flag")
        self.assertEqual(os.lstat(flag).st_ino, was.st_ino)
        self.assertEqual(self._halts(), 0)

    @unittest.skipUnless(hasattr(os, "fork"), "no fork on this platform")
    def test_a_forked_child_starts_with_no_halt_in_progress(self) -> None:
        lock = self.book._lock = _FifoLock()
        key = self.book._halt_key
        let_go = self._take(lock)  # the halt stays in progress while the process forks
        halter = threading.Thread(target=self.book.halt, daemon=True, name="halter")
        halter.start()
        self._until(lambda: lock.waiting() == 1, "the halt never queued on the book's lock")
        self.assertEqual(self._halts(), 1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # fork with threads running
            pid = os.fork()
        if pid == 0:  # child: never return into the test runner
            code = 1
            try:
                halts, me = gateway_module._HALTS, gateway_module._Halt()
                mine = weakref.ref(me)
                fresh = halts.count(key) == 0  # the parent's halt is not running here
                halts.begin(key, mine)  # and the count works: its lock was not left held
                counted = halts.count(key) == 1
                halts.end(key, mine, os.path.join(self.dir, "no-flag-of-the-childs"))
                code = 0 if fresh and counted and halts.count(key) == 0 else 2
            finally:
                os._exit(code)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        self.assertEqual(self._halts(), 1)  # the parent's count is its own
        let_go()
        halter.join(WAIT)
        self.assertFalse(halter.is_alive(), "the halt never finished")
        self.assertEqual(self._halts(), 0)

    # -- a kill whose halt was interrupted ------------------------------------------------------

    def test_a_kill_whose_halt_was_interrupted_twice_does_not_find_that_halt_still_counted(self) -> None:
        # The kill's own paper cancel is a transaction on the book: it waits its whole cap while
        # the count is above zero. So the halt the kill called must be out of the count by then,
        # even when two interrupts skipped both ``end`` and ``forget``. The kill does nothing
        # for that itself: it keeps the interrupt it caught, and with it the traceback and the
        # frame of the ``halt`` call, until it has tried every venue, and then raises it again.
        flag = self.book._halt_flag
        counted: list = []
        cancel_all = self.book.cancel_all

        def watched() -> int:
            self._nothing_else_holds_it()
            counted.append((self._halts(), os.path.lexists(flag)))
            self._age(flag)  # the flag the interrupted halt left: stale, so the cancel does not wait on it
            return cancel_all()
        self.book.cancel_all = watched
        # A build that still counts the halt waits this cap out and then fails below, instead of
        # waiting the cap ``setUp`` sets.
        self.enterContext(mock.patch.object(gateway_module, "HALT_YIELD_TOTAL_S", 0.3))
        raised = self._a_halt_interrupted_twice_in_its_counting_out(self.switch.kill)
        self.assertIs(type(raised), KeyboardInterrupt)  # raised again by the kill, after its paper cancel
        self.assertTrue(self._keeps_the_frame_of_a_halt(raised),
                        "the kill did not keep the frame of its halt: this test then shows nothing about a kept frame")
        self.assertEqual(counted, [(0, True)], "the kill's paper cancel found the kill's own halt still counted")
        self.assertTrue(self.book.halted())
        self.assertEqual(self.book.working(), 0)
        self.assertFalse(os.path.lexists(flag), "the stale flag was not removed by the kill's paper cancel")
        self.assertEqual(self._halts(), 0)

    # -- any writer, and the flag it gives way to --------------------------------------------

    def _writer(self, write=None) -> tuple:
        done = threading.Event()
        write = write or (lambda: self.book.record_refusal(REFUSAL))
        writer = threading.Thread(target=lambda: (write(), done.set()), daemon=True, name="writer")
        return writer, done

    def test_a_writer_waiting_on_the_lock_gives_way_to_a_halt_that_begins_meanwhile(self) -> None:
        lock = self.book._lock = _FifoLock()
        flag = self.book._halt_flag
        _, under = self._watch_looks()
        writer, done = self._writer()
        let_go = self._take(lock)  # something else holds the book's lock
        writer.start()
        self._until(lambda: lock.waiting() == 1, "the writer never queued on the book's lock")
        open(flag, "w").close()  # a halt begins while the writer waits on the lock
        let_go()
        self._until(lambda: under, "the writer never looked for a halt under the lock")
        self.assertIsNotNone(under[0][2], "a writer waiting on the lock went ahead of a halt in progress")
        self.assertEqual(self.book.audit(), [])
        self.assertFalse(done.is_set())
        os.unlink(flag)
        self.assertTrue(done.wait(WAIT), "the writer did not go on after the halt flag was removed")
        writer.join(WAIT)
        self.assertEqual([row["code"] for row in self.book.audit()], ["paper_only"])

    def test_a_retried_halt_is_given_way_to_after_a_dead_halts_flag_was_waited_on(self) -> None:
        # A halt dies and leaves its flag. A writer waits on it until it is too old to be a halt
        # in progress, removes it and queues on the book's lock. The kill is retried: the writer
        # gives way to the new halt too, because its cap has not passed. And the cap it gives way
        # under is the one its wait on the dead halt's flag started: that wait is not forgotten.
        lock = self.book._lock = _FifoLock()
        flag = self.book._halt_flag
        judged = self._watch_judgements()
        before, under = self._watch_looks()
        writer, done = self._writer()
        let_go = self._take(lock)  # something else holds the book's lock throughout
        open(flag, "w").close()  # the dead halt's flag: nobody removes it or dates it again
        writer.start()
        self._until(lambda: ("writer", True) in judged, "the writer never waited on the dead halt's flag")
        old = time.time() - gateway_module.HALT_YIELD_S - 5
        os.utime(flag, (old, old))  # older than a halt in progress can leave its flag
        self._until(lambda: lock.waiting() == 1, "the writer never got past the dead halt's flag")
        self.assertFalse(os.path.exists(flag), "the dead halt's flag was not removed")
        cap = before[0][2]
        self.assertEqual(before, [("writer", None, cap)])
        self.assertIsNotNone(cap, "the wait on the dead halt's flag was dropped when the flag was removed")
        open(flag, "w").close()  # the retried halt
        let_go()
        self._until(lambda: under, "the writer never looked for a halt under the lock")
        self.assertEqual(under[0], ("writer", cap, cap), "a writer went ahead of a retried halt, or on a new cap")
        self.assertEqual(self.book.audit(), [])
        self.assertFalse(done.is_set())
        os.unlink(flag)
        self.assertTrue(done.wait(WAIT), "the writer did not go on after the retried halt's flag was removed")
        writer.join(WAIT)
        self.assertEqual([row["code"] for row in self.book.audit()], ["paper_only"])

    # -- a writer and the flag it judged stale -------------------------------------------------

    def _a_writer_judges_the_flag_stale_and_then(self, then) -> tuple:
        """A stale flag; a writer judges it; ``then`` runs before the writer can remove it.
        Comes back once the writer is past its wait and queued on the book's lock, with what
        lets go of that lock, the writer's event, and the writer's looks before and under the
        lock."""
        lock = self.book._lock = _FifoLock()
        flag = self.book._halt_flag
        open(flag, "w").close()
        self._age(flag)
        self._after_the_writers_first_judgement(then)
        before, under = self._watch_looks()
        writer, done = self._writer()
        self.addCleanup(writer.join, WAIT)  # clean-up runs backwards: the lock is let go, the flag
        self.addCleanup(self._remove_flag)  # removed, and then the writer has nothing left to wait on
        let_go = self._take(lock)  # something else holds the book's lock
        writer.start()
        self._until(lambda: lock.waiting() == 1, "the writer never got past the flag it judged stale")
        return let_go, done, before, under

    def _a_flag_made_live_since_the_writer_judged_it_is_left(self, a_halt_begins) -> None:
        flag = self.book._halt_flag
        let_go, done, before, under = self._a_writer_judges_the_flag_stale_and_then(a_halt_begins)
        self.assertTrue(self._flag_live(), "a writer removed a flag that a halt made or dated after it judged it")
        self.assertEqual(before, [("writer", None, None)])
        let_go()
        self._until(lambda: under, "the writer never looked for a halt under the lock")
        self.assertIsNotNone(under[0][2], "a writer went ahead of the halt whose flag it had nearly removed")
        self.assertEqual(self.book.audit(), [])
        self.assertFalse(done.is_set())
        os.unlink(flag)  # that halt is done
        self.assertTrue(done.wait(WAIT), "the writer did not go on after the halt's flag was removed")
        self.assertEqual([row["code"] for row in self.book.audit()], ["paper_only"])

    def test_a_writer_looks_again_and_leaves_a_flag_that_was_dated_since_it_judged_it(self) -> None:
        flag = self.book._halt_flag
        self._a_flag_made_live_since_the_writer_judged_it_is_left(lambda: os.utime(flag))

    def test_a_writer_looks_again_and_leaves_a_flag_that_was_made_since_it_judged_it(self) -> None:
        flag = self.book._halt_flag

        def a_halt_in_another_process_makes_its_flag() -> None:
            os.unlink(flag)
            open(flag, "w").close()
        self._a_flag_made_live_since_the_writer_judged_it_is_left(a_halt_in_another_process_makes_its_flag)

    def test_a_writer_removes_only_the_entry_it_judged(self) -> None:
        # Another stale entry has taken the place of the one the writer judged. It is not this
        # writer's to remove: the next look at it, by any writer, judges it for itself.
        flag = self.book._halt_flag
        aside = flag + ".judged"
        self.addCleanup(lambda: os.path.lexists(aside) and os.unlink(aside))

        def another_stale_entry_takes_its_place() -> None:
            os.rename(flag, aside)  # kept, so that the new entry cannot be given its inode
            open(flag, "w").close()
            self._age(flag)
        let_go, done, before, under = self._a_writer_judges_the_flag_stale_and_then(
            another_stale_entry_takes_its_place)
        self.assertTrue(os.path.lexists(flag), "a writer removed an entry it had not judged")
        self.assertNotEqual(os.lstat(flag).st_ino, os.lstat(aside).st_ino)
        self.assertEqual(before, [("writer", None, None)])
        let_go()
        self.assertTrue(done.wait(WAIT), "a stale entry held a writer")
        self.assertEqual(under, [("writer", None, None)])  # stale: nothing to give way to

    def test_a_writers_removal_that_lands_after_a_halt_began_does_not_let_queued_bookings_ahead(self) -> None:
        # A writer has judged a dead halt's flag stale, twice, and is about to remove it. A kill
        # begins and puts a flag of its own in its place. The writer's removal then takes the
        # kill's flag: its last look and its removal are two system calls. The count still
        # holds the queued bookings back.
        flag = self.book._halt_flag
        at_the_removal, go_on = threading.Event(), threading.Event()
        self.addCleanup(go_on.set)
        unlink = os.unlink

        def held_up(path, *args, **kwargs):
            if path == flag and threading.current_thread().name == "cleaner":
                at_the_removal.set()
                go_on.wait(WAIT)
            return unlink(path, *args, **kwargs)
        cleaner = threading.Thread(target=lambda: self.book.record_refusal(REFUSAL), daemon=True, name="cleaner")

        def a_writer_is_about_to_remove_a_dead_halts_flag() -> None:
            open(flag, "w").close()
            self._age(flag)
            self.enterContext(mock.patch.object(gateway_module.os, "unlink", held_up))
            cleaner.start()
            self.assertTrue(at_the_removal.wait(WAIT), "the writer never went to remove the dead halt's flag")

        def the_removal_lands() -> None:
            self.assertTrue(self._flag_live(), "the kill did not put a flag of its own in the dead halt's place")
            go_on.set()
            self._until(lambda: self.book._lock.waiting() == QUEUED + 2, "the writer never got past its removal")
            self.assertFalse(os.path.lexists(flag))  # the removal took the kill's flag
        outcomes, killed = self._halt_behind_a_queue("kill", first=a_writer_is_about_to_remove_a_dead_halts_flag,
                                                     meanwhile=the_removal_lands)
        cleaner.join(WAIT)
        self.assertFalse(cleaner.is_alive(), "the writer never finished")
        self.assertEqual((killed["status"], killed["halt"]), ("paper_only", "confirmed"))
        self.assertEqual(outcomes, [HALTED] * QUEUED + [BOOKED])
        self.assertEqual(len(self.reverifies), 1, "a queued booking ran its transaction ahead of the kill")
        self.assertEqual(self.book.working(), 0)
        self.assertEqual(self._halts(), 0)

    def test_only_a_live_flag_is_given_way_to_and_it_is_never_removed_under_the_lock(self) -> None:
        flag = self.book._halt_flag
        now = time.time()

        def dated(mtime: float):
            def make() -> None:
                open(flag, "w").close()
                os.utime(flag, (mtime, mtime))
            return make
        variants = {"missing": lambda: None, "stale": dated(now - gateway_module.HALT_YIELD_S - 5),
                    "future": dated(now + 3600), "directory": lambda: os.mkdir(flag)}
        for name, make in variants.items():
            with self.subTest(flag=name):
                make()
                try:
                    with self.book._lock:
                        self.assertIsNone(self.book._give_way(None))
                    # Left as it is: ``_yield_to_halt`` removes it (a directory stays there too).
                    self.assertEqual(os.path.lexists(flag), name != "missing")
                finally:
                    self._remove_flag()
        open(flag, "w").close()  # a live one
        before = time.monotonic()
        with self.book._lock:
            cap = self.book._give_way(None)
        after = time.monotonic()
        self.assertIsNotNone(cap)
        # The writer's cap starts at this look: ``HALT_YIELD_TOTAL_S`` after it, no less and no more.
        self.assertGreaterEqual(cap, before + gateway_module.HALT_YIELD_TOTAL_S)
        self.assertLessEqual(cap, after + gateway_module.HALT_YIELD_TOTAL_S)
        self.assertTrue(os.path.exists(flag))

    def test_a_flag_dated_within_the_clocks_grain_ahead_is_a_halt_in_progress(self) -> None:
        # The flag's date and time.time() are not one clock reading: on Windows a flag looked at
        # in the clock tick it was made in reads a float step in the future about one look in
        # seven. Judged "dated in the future", a halt's own flag was removed and not waited on.
        flag = self.book._halt_flag
        grain = gateway_module._HALT_FLAG_GRAIN_S
        for name, ahead, live in (("the same reading", 0.0, True), ("a clock tick", 0.0156, True),
                                  ("most of the grain", 0.9 * grain, True), ("past the grain", 1.5 * grain, False),
                                  ("a minute", 60.0, False), ("an hour", 3600.0, False)):
            with self.subTest(ahead=name):
                open(flag, "w").close()
                dated = os.lstat(flag).st_mtime
                try:
                    # The clock stands still ``ahead`` before the flag's date, so the judgement
                    # does not depend on how long the machine takes to get from one line to the next.
                    with mock.patch.object(gateway_module.time, "time", return_value=dated - ahead):
                        with self.book._lock:
                            self.assertEqual(self.book._give_way(None) is not None, live)
                        # The wait before the lock judges it the same way: a live flag is left alone
                        # (the cap has passed here, so nothing waits), anything else is removed.
                        spent = time.monotonic() - 1
                        self._back(lambda: self.book._yield_to_halt(spent), "a writer waited past its cap")
                    self.assertEqual(os.path.exists(flag), live)
                finally:
                    self._remove_flag()

    # -- the cap on a writer's waits ----------------------------------------------------------

    def test_a_writers_waits_all_end_at_one_cap(self) -> None:
        flag = self.book._halt_flag
        open(flag, "w").close()  # a halt that never finishes while this test looks
        now = time.monotonic()
        with self.book._lock:
            self.assertEqual(self.book._give_way(now + 60), now + 60)  # a cap is kept, never pushed out
            self.assertIsNone(self.book._give_way(now - 0.5))  # the cap has passed: go on
        # The wait before the lock agrees: nothing waits once the cap has passed.
        self.assertEqual(self._back(lambda: self.book._yield_to_halt(now - 0.5), "a writer waited past its cap"),
                         now - 0.5)
        self.assertTrue(os.path.exists(flag))  # a live flag is never removed by a writer
        # End to end: a flag that stays live holds a writer to its cap, not for ever. Nothing here
        # shortens the flag's life: it stays live for ``HALT_YIELD_S`` as ``setUp`` set it, well
        # past ``WAIT``, and it is still there and live once the writer is done. So the writer
        # went on because its cap passed, and for no other reason, however slow the machine.
        before, under = self._watch_looks()
        writer, done = self._writer()
        self.addCleanup(writer.join, WAIT)  # clean-up runs backwards: the flag is removed first,
        self.addCleanup(self._remove_flag)  # so a writer still waiting on it ends before the join
        with mock.patch.object(gateway_module, "HALT_YIELD_TOTAL_S", 0.3):
            started = time.monotonic()
            writer.start()
            self.assertTrue(done.wait(WAIT), "a flag that stays live held a writer for ever")
            finished = time.monotonic()
        writer.join(WAIT)
        self.assertTrue(self._flag_live(), "the writer went on because the flag aged out or went, not at its cap")
        cap = before[0][2]
        self.assertGreaterEqual(cap, started + 0.3)
        self.assertGreaterEqual(finished, cap, "the writer went on before its cap")
        self.assertEqual((before, under), ([("writer", None, cap)], [("writer", cap, None)]))
        self.assertEqual([row["code"] for row in self.book.audit()], ["paper_only"])

    def _cap_is_carried(self, write) -> None:
        """A writer waits on one halt's flag before the lock, then gives way to a second halt
        under the lock and waits again: every look after the first is handed the cap the
        first one set."""
        lock = self.book._lock = _FifoLock()
        flag = self.book._halt_flag
        judged = self._watch_judgements()
        before, under = self._watch_looks()
        writer, done = self._writer(write)
        let_go = self._take(lock)  # something else holds the book's lock
        open(flag, "w").close()  # the first halt
        started = time.monotonic()
        writer.start()
        self._until(lambda: ("writer", True) in judged, "the writer never waited on the first halt's flag")
        os.unlink(flag)  # the first halt is done
        self._until(lambda: lock.waiting() == 1, "the writer never queued on the book's lock")
        latest = time.monotonic()  # the writer's first look at a live flag came before this
        cap = before[0][2]
        self.assertEqual(before, [("writer", None, cap)])
        self.assertGreaterEqual(cap, started + gateway_module.HALT_YIELD_TOTAL_S)
        self.assertLessEqual(cap, latest + gateway_module.HALT_YIELD_TOTAL_S)
        open(flag, "w").close()  # a second halt begins while the writer waits on the lock
        let_go()
        self._until(lambda: under, "the writer never looked for a halt under the lock")
        self.assertEqual(under, [("writer", cap, cap)])  # gives way, on the same cap
        self.assertFalse(done.is_set())
        os.unlink(flag)  # the second halt is done
        self.assertTrue(done.wait(WAIT), "the writer did not go on after the second halt")
        writer.join(WAIT)
        self.assertEqual(before, [("writer", None, cap), ("writer", cap, cap)])  # never started again
        self.assertEqual(under, [("writer", cap, cap), ("writer", cap, None)])

    def test_a_transaction_carries_its_cap_from_look_to_look(self) -> None:
        self._cap_is_carried(lambda: self.book.record_refusal(REFUSAL))
        self.assertEqual([row["code"] for row in self.book.audit()], ["paper_only"])

    def test_a_probe_carries_its_cap_from_look_to_look(self) -> None:
        self._cap_is_carried(self.book._probe_writable)

    # -- where the flag is kept ----------------------------------------------------------------

    def test_the_flag_is_kept_in_a_folder_of_the_users_own_or_beside_the_book(self) -> None:
        real = os.path.realpath(self.path)
        flag = self.book._halt_flag
        if os.name != "posix":
            # Always beside the book on Windows, though ``setUp`` names a usable runtime folder.
            folder = os.path.dirname(real)
            self.assertEqual(flag, real + "-halt")
            self.assertEqual(os.listdir(self.runtime), [])
        elif not gateway_module._private_dir(self.runtime):
            self.skipTest("the temporary folder does not keep an owner-only mode")
        else:
            folder = os.path.join(self.runtime, "csl-cockpit")
            name = "csl-paper-halt-" + hashlib.sha256(real.encode()).hexdigest()[:32]
            self.assertEqual(flag, os.path.join(folder, name))
            made = os.lstat(folder)
            self.assertTrue(stat.S_ISDIR(made.st_mode))
            self.assertEqual((made.st_uid, stat.S_IMODE(made.st_mode) & 0o077), (os.geteuid(), 0))
        # Every handle on the book shares the one flag and the one count, by whatever path it
        # was opened; another book has others.
        again = PaperBook(os.path.join(self.dir, ".", "book.db"))
        self.addCleanup(again.close)
        self.assertEqual((again._halt_flag, again._halt_key), (flag, real))
        other = os.path.join(self.dir, "other.db")
        create_book(other, cash_minor={ACCT: 1})
        another = PaperBook(other)
        self.addCleanup(another.close)
        self.assertEqual(os.path.dirname(another._halt_flag), folder)
        self.assertNotEqual(another._halt_flag, flag)
        self.assertNotEqual(another._halt_key, real)
        # A halt takes its flag away again: nothing is left in the folder it was kept in.
        self.book.halt()
        self.assertFalse(os.path.lexists(flag))
        if os.name == "posix":
            self.assertEqual(os.listdir(folder), [])

    @unittest.skipUnless(os.name == "posix", "on Windows the flag is always beside the book")
    def test_an_unusable_runtime_folder_puts_the_flag_beside_the_book(self) -> None:
        if not gateway_module._private_dir(self.runtime):
            self.skipTest("the temporary folder does not keep an owner-only mode")
        beside = os.path.realpath(self.path) + "-halt"

        def folder(name: str, mode: int = 0o700) -> str:
            path = os.path.join(self.dir, name)
            os.mkdir(path)
            os.chmod(path, mode)
            return path

        def holding(name: str, make) -> str:
            base = folder(name)
            make(os.path.join(base, "csl-cockpit"))
            return base
        a_file = os.path.join(self.dir, "a-file")
        open(a_file, "w").close()
        a_link = os.path.join(self.dir, "a-link")
        os.symlink(self.runtime, a_link)
        elsewhere = folder("elsewhere")
        unusable = {
            "unset": None,
            "empty": "",
            "relative": os.path.relpath(self.runtime),
            "missing": os.path.join(self.dir, "missing"),
            "a file": a_file,
            "a link to a usable folder": a_link,
            "its group can write it": folder("group", 0o770),
            "others can write it": folder("others", 0o707),
            "made like /dev/shm": folder("shm", 0o1777),
            "its csl-cockpit is a file": holding("sub-file", lambda sub: open(sub, "w").close()),
            "its csl-cockpit is a link": holding("sub-link", lambda sub: os.symlink(elsewhere, sub)),
            "its csl-cockpit can be written by its group": holding(
                "sub-group", lambda sub: (os.mkdir(sub), os.chmod(sub, 0o770))),
        }
        for why, value in unusable.items():
            with self.subTest(runtime_folder=why):
                with mock.patch.dict(os.environ):
                    if value is None:
                        del os.environ["XDG_RUNTIME_DIR"]
                    else:
                        os.environ["XDG_RUNTIME_DIR"] = value
                    self.assertEqual(gateway_module._halt_flag_path(self.path), beside)
        with self.subTest(runtime_folder="another user's"):
            mine = os.geteuid()
            with mock.patch.object(gateway_module.os, "geteuid", lambda: mine + 1):
                self.assertEqual(gateway_module._halt_flag_path(self.path), beside)
        # The control: with none of those faults the flag does go under the runtime folder, so
        # each case above was refused for its fault and not for the test's own doing.
        self.assertEqual(os.path.dirname(gateway_module._halt_flag_path(self.path)),
                         os.path.join(self.runtime, "csl-cockpit"))
        self.assertEqual(os.listdir(elsewhere), [])  # nothing was made through the link

    @unittest.skipUnless(os.name == "posix", "on Windows the flag is always beside the book")
    def test_the_flag_is_under_dev_shm_only_where_the_users_runtime_folder_or_the_book_is(self) -> None:
        shm = "/dev/shm"
        if not os.path.isdir(shm) or not os.access(shm, os.W_OK | os.X_OK):
            self.skipTest("no /dev/shm here that this user can write")
        beside = os.path.realpath(self.path) + "-halt"
        name = "csl-paper-halt-" + hashlib.sha256(os.path.realpath(self.path).encode()).hexdigest()[:32]
        # ``/dev/shm`` itself is no runtime folder where anyone can write it: beside the book.
        if not gateway_module._private_dir(shm):
            was_there = os.path.lexists(os.path.join(shm, "csl-cockpit"))
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": shm}):
                self.assertEqual(gateway_module._halt_flag_path(self.path), beside)
            self.assertEqual(os.path.lexists(os.path.join(shm, "csl-cockpit")), was_there,
                             "a csl-cockpit folder was made in /dev/shm itself")
        # A folder of the user's own inside it is a runtime folder like any other: where a folder
        # is, is not looked at.
        private = tempfile.mkdtemp(prefix="cockpit-halt-queue-", dir=shm)
        self.addCleanup(shutil.rmtree, private, True)
        if not gateway_module._private_dir(private):
            self.skipTest("/dev/shm does not keep an owner-only mode")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": private}):
            self.assertEqual(gateway_module._halt_flag_path(self.path), os.path.join(private, "csl-cockpit", name))
        # And a book that is kept there, with no usable runtime folder, has its flag beside it.
        there = os.path.join(private, "book.db")
        create_book(there, cash_minor={ACCT: 1})
        with mock.patch.dict(os.environ):
            del os.environ["XDG_RUNTIME_DIR"]
            self.assertEqual(gateway_module._halt_flag_path(there), os.path.realpath(there) + "-halt")

    # -- making and dating the flag ------------------------------------------------------------

    def _open_descriptors(self) -> int:
        """How many of the first 2048 descriptor numbers are open in this process."""
        found = 0
        for fd in range(2048):
            try:
                os.fstat(fd)
            except OSError:
                continue
            found += 1
        return found

    def test_an_interrupt_before_any_instruction_of_the_flags_making_leaves_no_descriptor_open(self) -> None:
        # The flag is made through a file object, which closes its descriptor when it goes. Made
        # with a bare descriptor, an interrupt between the call that opened it and the call that
        # closed it left the descriptor open for good; and on Windows an open one keeps the flag
        # from being removed, by that halt and by every halt after it. The interrupt is raised
        # by a trace function before each instruction of ``_raise_halt_flag`` in turn; one raised
        # by a signal handler can land after the call that makes the file as well.
        book, flag, codes = self.book, self.book._halt_flag, (PaperBook._raise_halt_flag.__code__,)
        book.halt()  # whatever the book keeps open for good is open from here on
        self.assertFalse(os.path.lexists(flag))
        _, instructions, _ = self._interrupted_before(None, book.halt, codes)
        self.assertGreater(instructions, 10, "the test did not see the instructions that make the flag")
        open_before = self._open_descriptors()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)  # a file object let go of unclosed says so
            for nth in range(1, instructions + 1):
                self._remove_flag()  # so that the halt makes its flag, and does not date one
                step, _, kept = self._interrupted_before(nth, book.halt, codes)
                self.assertIsNotNone(step, "no instruction %d in this call" % nth)
                where = "before instruction %d: %s, line %s" % (nth, step.opname, step.positions.lineno)
                self.assertIsInstance(kept, _Forced, "the halt did not end with the interrupt, " + where)
                self._nothing_else_holds_it()
                self.assertEqual(self._open_descriptors(), open_before, "a descriptor was left open, " + where)
                self.assertEqual(self._halts(), 0, where)
                book.halt()  # a halt that follows, the interrupt still kept
                self.assertFalse(os.path.lexists(flag), "the halt that followed could not take its flag away, " + where)
                self.assertEqual((self._halts(), self._entries()), (0, 0), where)
                kept = None

    def _leads_to_an_open_file(self, start: object, depth: int = 5) -> bool:
        """Whether an open file object can be reached from ``start`` through at most ``depth``
        references, never through a module, a class, a function, a code object or a frame."""
        opaque = (types.ModuleType, type, types.FunctionType, types.CodeType, types.FrameType)
        seen: set = set()
        layer = [start]
        for _ in range(depth + 1):
            following: list = []
            for thing in layer:
                if id(thing) in seen:
                    continue
                seen.add(id(thing))
                if isinstance(thing, io.IOBase):
                    if not thing.closed:
                        return True
                elif not isinstance(thing, opaque):
                    following.extend(gc.get_referents(thing))
            layer = following
        return False

    def test_no_tool_that_watches_calls_is_handed_the_flags_file_object(self) -> None:
        # A tool that watches calls is handed the callable of each call: a function set with
        # ``sys.setprofile`` gets a C function bound to its object, a ``sys.monitoring`` callback
        # the callable and the call's first argument. What it was handed stays in its frame, so
        # if it raises an interrupt there and the interrupt is kept, the object is kept too. The
        # flag was once made with ``open(...).close()``: the tool was handed the bound ``close``
        # of the flag's open file, and one profile function that raised at that call kept the
        # descriptor open for as long as the interrupt was kept. On Windows the halt that
        # followed, and every later one, then could not take its flag away. So nothing a tool
        # is handed during a halt may lead to an open file object: the flag's is closed from C,
        # in the statement that makes it. Each tool looks at what it is handed then and there,
        # and keeps none of it.
        book, flag = self.book, self.book._halt_flag
        gateway_file = PaperBook._raise_halt_flag.__code__.co_filename
        told: list = []
        handed: list = []

        def named(thing: object) -> str:
            return str(getattr(thing, "__qualname__", None) or getattr(thing, "__name__", None) or type(thing).__name__)

        def profile(frame, event, arg) -> None:
            if frame.f_code.co_filename != gateway_file or arg is None:
                return
            what = (frame.f_code.co_name, event, named(arg) if event.startswith("c_") else "")
            told.append(what)
            if self._leads_to_an_open_file(arg):
                handed.append(("sys.setprofile",) + what)
        before = sys.getprofile()
        sys.setprofile(profile)
        try:
            book.halt()
        finally:
            sys.setprofile(before)
        self.assertIn(("_raise_halt_flag", "c_call", "open"), told, "the profile function did not see the flag made")
        self.assertEqual(handed, [], "a profile function was handed something that leads to an open file")
        self.assertFalse(os.path.lexists(flag))
        monitoring = getattr(sys, "monitoring", None)
        if monitoring is None:
            return  # before 3.12 there is only the profile function
        tool = next(number for number in range(6) if monitoring.get_tool(number) is None)
        monitoring.use_tool_id(tool, "test_halt_queue_priority")
        self.addCleanup(monitoring.free_tool_id, tool)
        del told[:]

        def watching(event: str):
            def callback(code, offset, *given) -> None:
                if code.co_filename != gateway_file:
                    return
                what = (code.co_name, event, named(given[0]) if event != "PY_RETURN" else "")
                told.append(what)
                if any(self._leads_to_an_open_file(thing) for thing in given):
                    handed.append(("sys.monitoring",) + what)
            return callback
        events = monitoring.events
        for event, bit in (("CALL", events.CALL), ("C_RETURN", events.C_RETURN), ("C_RAISE", events.C_RAISE),
                           ("PY_RETURN", events.PY_RETURN)):
            monitoring.register_callback(tool, bit, watching(event))
        monitoring.set_events(tool, events.CALL | events.PY_RETURN)
        try:
            book.halt()
        finally:
            monitoring.set_events(tool, 0)
        self.assertIn(("_raise_halt_flag", "CALL", "open"), told, "the monitoring callback did not see the flag made")
        self.assertEqual(handed, [], "a monitoring callback was handed something that leads to an open file")
        self.assertFalse(os.path.lexists(flag))

    def _interrupted_at(self, nth, call, in_file: str) -> tuple:
        """Run ``call`` (which calls ``halt`` once) with a function set by ``sys.setprofile``
        raising one ``_Forced`` at the ``nth`` event it is told of inside that ``halt``, in the
        functions of ``in_file`` (``None``: none is raised). It raises with what it was handed
        still in its frame, as a tool that watches calls does. Returns the event it was raised
        at (or None), how many events were counted, and what the call ended with: an exception,
        or None where it returned."""
        seen = {"n": 0, "at": None, "in_a_halt": 0}
        halt = PaperBook.halt.__code__

        def profile(frame, event, arg) -> None:
            code = frame.f_code
            if code is halt and event == "call":
                seen["in_a_halt"] += 1
            if seen["in_a_halt"] and code.co_filename == in_file:
                seen["n"] += 1
                if seen["n"] == nth:
                    seen["at"] = (code.co_name, event, getattr(arg, "__name__", "") if event.startswith("c_") else "")
                    raise _Forced("at event %d" % nth)
            if code is halt and event == "return":
                seen["in_a_halt"] -= 1
        before = sys.getprofile()
        sys.setprofile(profile)
        try:
            call()
        except BaseException as exc:  # noqa: BLE001 - the interrupt, or what was raised on top of it
            return seen["at"], seen["n"], exc
        finally:
            sys.setprofile(before)
        return seen["at"], seen["n"], None

    def _interrupted_at_each_call_and_return(self, through_a_kill: bool) -> None:
        """Call ``halt`` once for every event a profile function is told of inside it (each call
        of a function of the gateway and each return from one, each call into C and each return
        from it), with that function raising one interrupt at the event, and check each time
        that the halt is not counted once its call is over (the interrupt kept, and with it the
        tool's frame and what the tool was handed) and that the halt that follows ends alone and
        takes the flag away. With ``through_a_kill`` the halt is called by ``KillSwitch.kill``."""
        halts, book = gateway_module._HALTS, self.book
        key, flag = book._halt_key, book._halt_flag
        gateway_file = PaperBook.halt.__code__.co_filename
        # As in the test that raises before every instruction: a lock an interrupt leaves held is
        # a stated limit and not what this test is about, so each lock a halt takes is a
        # stand-in that its holder can take again. The count's own lock is one of them here,
        # because the functions that take it are swept too.
        book_lock = book._lock = _Retaken()
        flag_lock = halts._flag_locks[key] = _Retaken()
        self.addCleanup(halts._flag_locks.pop, key, None)
        count_lock = _Retaken()
        self.enterContext(mock.patch.object(halts, "_lock", count_lock))
        at_the_cancel: list = []
        call = book.halt
        if through_a_kill:
            cancel_all = book.cancel_all

            def watched() -> int:
                at_the_cancel.append(self._counted_once_collected())
                if os.path.lexists(flag):
                    self._age(flag)  # a flag the interrupted halt left: stale, so the cancel does not wait on it
                return cancel_all()
            book.cancel_all = watched
            self.enterContext(mock.patch.object(gateway_module, "HALT_YIELD_TOTAL_S", 0.3))
            call = self.switch.kill
        _, events, _ = self._interrupted_at(None, call, gateway_file)
        self.assertGreater(events, 40, "the test did not see the calls of a halt")
        seen_at = set()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)  # a file object let go of unclosed says so
            for nth in range(1, events + 1):
                book._db.execute("UPDATE state SET v = 0 WHERE k = 'halted'")  # every call is a first halt
                self._remove_flag()
                del at_the_cancel[:]
                at, _, kept = self._interrupted_at(nth, call, gateway_file)
                self.assertIsNotNone(at, "no event %d in this call" % nth)
                where = "at event %d: %s in %s %s" % (nth, at[1], at[0], at[2])
                seen_at.add(at)
                for lock in (count_lock, flag_lock, book_lock):
                    lock.left()
                if book._db.in_transaction:
                    book._db.execute("ROLLBACK")
                if through_a_kill:
                    self.assertEqual(at_the_cancel, [0], "the kill's paper cancel found its halt counted, " + where)
                else:
                    raised = kept
                    while raised is not None and not isinstance(raised, _Forced):
                        raised = raised.__context__
                    self.assertIsNotNone(raised, "the halt did not end with the interrupt, " + where)
                self.assertEqual(self._counted_once_collected(), 0, "a halt whose call is over is still counted, " + where)
                book.halt()  # a halt that follows, the interrupt still kept
                self.assertFalse(os.path.lexists(flag), "the halt that followed left its flag behind, " + where)
                self.assertEqual((self._halts(), self._entries()), (0, 0), where)
                kept = None
        # The making of the flag was among them, at the call and at the return.
        self.assertLessEqual({("_raise_halt_flag", "c_call", "open"), ("_raise_halt_flag", "c_return", "open")}, seen_at)

    def test_an_interrupt_a_tool_raises_at_any_call_or_return_of_a_halt_leaves_no_flag_to_the_next(self) -> None:
        self._interrupted_at_each_call_and_return(through_a_kill=False)

    def test_a_kill_whose_halt_a_tool_interrupted_at_any_call_or_return_leaves_no_flag_to_the_next(self) -> None:
        self._interrupted_at_each_call_and_return(through_a_kill=True)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "this platform cannot make a FIFO")
    def test_a_fifo_at_the_flags_path_does_not_hold_a_halt_or_a_kill(self) -> None:
        # Opened for writing in the plain way, a FIFO with no reader holds the opener for good.
        flag = self.book._halt_flag
        os.link(self._fifo(), flag)
        killed = self._back(self.switch.kill, "a FIFO at the flag's path held the kill")
        self.assertEqual((killed["status"], killed["halt"]), ("paper_only", "confirmed"))
        self.assertTrue(self.book.halted())
        self.assertFalse(os.path.lexists(flag), "the FIFO was left at the flag's path")
        self.assertEqual(self._halts(), 0)

    @unittest.skipUnless(hasattr(os, "mkfifo") and os.utime in os.supports_fd,
                         "needs a FIFO and a platform that dates the flag through an open")
    def test_a_fifo_put_at_the_path_after_the_halts_look_does_not_hold_it(self) -> None:
        flag = self.book._halt_flag
        fifo = self._fifo()
        open(flag, "w").close()  # live: the halt goes on to date it in place

        def a_fifo_takes_the_flags_place() -> None:
            os.unlink(flag)
            os.link(fifo, flag)
        self._after_the_halts_look_at_its_flag(a_fifo_takes_the_flags_place)
        self._back(self.book._raise_halt_flag, "the open of a FIFO put at the flag's path held the halt")
        self.assertTrue(stat.S_ISREG(os.lstat(flag).st_mode), "the FIFO was not replaced by a flag")
        self.assertTrue(self._flag_live())

    @unittest.skipUnless(hasattr(os, "mkfifo") and os.utime in os.supports_fd,
                         "needs a FIFO and a platform that dates the flag through an open")
    def test_what_the_halt_opened_is_dated_only_if_it_is_a_regular_file(self) -> None:
        # With a reader on it, a FIFO put at the path after the look does open. It is not dated.
        flag = self.book._halt_flag
        fifo = self._fifo()
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, reader)
        os.utime(fifo, (1000, 1000))
        open(flag, "w").close()

        def a_fifo_takes_the_flags_place() -> None:
            os.unlink(flag)
            os.link(fifo, flag)
        self._after_the_halts_look_at_its_flag(a_fifo_takes_the_flags_place)
        self._back(self.book._raise_halt_flag, "the halt never came back from making its flag")
        self.assertEqual(os.fstat(reader).st_mtime, 1000, "the halt dated something that is not a flag")
        self.assertTrue(stat.S_ISREG(os.lstat(flag).st_mode), "the FIFO was not replaced by a flag")
        self.assertTrue(self._flag_live())

    def _a_victim(self) -> str:
        victim = os.path.join(self.dir, "victim")
        with open(victim, "wb") as f:
            f.write(b"keep")
        os.utime(victim, (1000, 1000))
        return victim

    def _victim_untouched(self, victim: str) -> None:
        with open(victim, "rb") as f:
            self.assertEqual(f.read(), b"keep")
        self.assertEqual(os.stat(victim).st_mtime, 1000, "the halt dated the file a link at its flag's path names")

    def test_the_flag_is_never_made_or_dated_through_a_link(self) -> None:
        flag = self.book._halt_flag
        victim = self._a_victim()
        nowhere = os.path.join(self.dir, "made-through-a-link")
        self._link(victim, flag)
        os.unlink(flag)
        # The second pass is a platform without ``O_NOFOLLOW`` and without dating through an
        # open: Windows, stood in for here, where links can be made.
        for platform, nofollow, by_open in (("as it is", gateway_module._NOFOLLOW, os.supports_fd),
                                            ("without O_NOFOLLOW or dating through an open", 0, frozenset())):
            with self.subTest(platform=platform), \
                    mock.patch.object(gateway_module, "_NOFOLLOW", nofollow), \
                    mock.patch.object(gateway_module.os, "supports_fd", by_open):
                os.symlink(victim, flag)
                self.book._raise_halt_flag()
                self._victim_untouched(victim)
                self.assertTrue(stat.S_ISREG(os.lstat(flag).st_mode), "the link was not replaced by a flag")
                os.unlink(flag)
                os.symlink(nowhere, flag)  # a link to nothing: followed, it would make the file
                self.book.halt()
                self.assertFalse(os.path.lexists(nowhere), "the halt made a file through a link")
                self.assertFalse(os.path.lexists(flag), "the halt flag was left behind")
                self.assertTrue(self.book.halted())

    def test_a_link_put_at_the_path_as_the_halt_makes_its_flag_is_not_followed(self) -> None:
        # Nothing is at the path when the halt looks, and a link to nothing is there when it
        # makes its flag: made without ``O_EXCL``, the flag would be made through the link.
        flag = self.book._halt_flag
        nowhere = os.path.join(self.dir, "made-through-a-link")
        self._link(nowhere, flag)
        os.unlink(flag)
        for platform, nofollow in (("as it is", gateway_module._NOFOLLOW), ("without O_NOFOLLOW", 0)):
            with self.subTest(platform=platform), mock.patch.object(gateway_module, "_NOFOLLOW", nofollow):
                self._after_the_halts_look_at_its_flag(lambda: os.symlink(nowhere, flag))
                self.book._raise_halt_flag()
                self.assertFalse(os.path.lexists(nowhere), "the halt made its flag through a link")
                self.assertTrue(os.path.islink(flag))  # the test's link: the halt made nothing
                os.unlink(flag)

    @unittest.skipUnless(gateway_module._NOFOLLOW and os.utime in os.supports_fd,
                         "needs O_NOFOLLOW and a platform that dates the flag through an open")
    def test_a_link_put_at_the_path_after_the_halts_look_is_not_followed(self) -> None:
        flag = self.book._halt_flag
        victim = self._a_victim()
        self._link(victim, flag)
        os.unlink(flag)
        open(flag, "w").close()  # live: the halt goes on to date it in place

        def a_link_takes_the_flags_place() -> None:
            os.unlink(flag)
            os.symlink(victim, flag)
        self._after_the_halts_look_at_its_flag(a_link_takes_the_flags_place)
        self.book._raise_halt_flag()
        self._victim_untouched(victim)
        self.assertTrue(stat.S_ISREG(os.lstat(flag).st_mode), "the link was not replaced by a flag")
        self.assertTrue(self._flag_live())

    def test_a_stale_flag_is_replaced_by_a_new_file_and_never_dated_in_place(self) -> None:
        # Dated in place, a stale flag would be a file that a writer may already have judged
        # stale and be about to remove.
        flag = self.book._halt_flag
        kept = flag + ".stale"
        self.addCleanup(lambda: os.path.lexists(kept) and os.unlink(kept))
        open(flag, "w").close()
        old = self._age(flag)
        try:
            os.link(flag, kept)  # a second name for the stale file, so it can be looked at afterwards
        except (OSError, NotImplementedError) as exc:
            self.skipTest("this file system cannot give a file a second name: %s" % type(exc).__name__)
        self.book._raise_halt_flag()
        self.assertTrue(self._flag_live(), "a stale flag was not replaced by a live one")
        self.assertAlmostEqual(os.lstat(kept).st_mtime, old, delta=1, msg="the stale flag was dated in place")
        self.assertNotEqual(os.lstat(flag).st_ino, os.lstat(kept).st_ino, "the flag is still the stale file")

    def test_a_live_flag_is_dated_in_place_and_not_replaced(self) -> None:
        # A halt never takes a live flag away to put another there: no moment is left without one.
        flag = self.book._halt_flag
        kept = flag + ".live"
        self.addCleanup(lambda: os.path.lexists(kept) and os.unlink(kept))
        open(flag, "w").close()
        stamp = time.time() - 2
        os.utime(flag, (stamp, stamp))  # still live, and dated so that a new date shows
        try:
            os.link(flag, kept)  # a second name for the file, so a replacement cannot pass for it
        except (OSError, NotImplementedError) as exc:
            self.skipTest("this file system cannot give a file a second name: %s" % type(exc).__name__)
        self.book._raise_halt_flag()
        self.assertTrue(os.path.samestat(os.lstat(flag), os.lstat(kept)), "a live flag was replaced, not dated")
        self.assertGreater(os.lstat(flag).st_mtime, stamp + 1, "a live flag was not dated again")

    def test_a_flag_that_goes_between_the_halts_look_and_its_dating_is_made_again(self) -> None:
        # Taken by a writer in another process, or by another process's halt as it ends.
        flag = self.book._halt_flag
        open(flag, "w").close()  # live: the halt goes on to date it in place
        self._after_the_halts_look_at_its_flag(lambda: os.unlink(flag))
        self.book._raise_halt_flag()
        self.assertTrue(self._flag_live(), "the halt was left without a flag")

    # -- the halt's side ----------------------------------------------------------------------

    def test_a_halt_still_retrying_for_the_write_lock_keeps_its_flag_live(self) -> None:
        # Another connection holds the write lock, so the halt retries. Its flag is made too old
        # to be a halt in progress, and then removed, as a writer in another process does once
        # it takes the halt for one that died. Each time the halt puts it right.
        flag = self.book._halt_flag
        holder = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        self.enterContext(mock.patch.object(gateway_module, "_HALT_FLAG_REFRESH_S", 0.01))
        halter = threading.Thread(target=self.book.halt, daemon=True, name="halter")
        halter.start()
        self._until(lambda: os.path.exists(flag), "the halt never made its flag")
        old = time.time() - gateway_module.HALT_YIELD_S - 5
        os.utime(flag, (old, old))
        self._until(self._flag_live, "a halt still retrying left its flag to age out")

        def removed() -> bool:
            try:
                os.unlink(flag)
            except FileNotFoundError:
                pass
            except OSError:  # Windows: the halt has the file open this instant
                return False
            return True
        self._until(removed, "the flag could not be removed")
        self._until(self._flag_live, "a halt still retrying did not make its flag again")
        self.assertTrue(halter.is_alive())
        holder.execute("ROLLBACK")
        halter.join(WAIT)
        self.assertFalse(halter.is_alive(), "the halt never finished")
        self.assertTrue(self.book.halted())
        self.assertFalse(os.path.exists(flag), "the halt flag was left behind")

    def test_a_halt_that_finds_its_flag_gone_makes_it_again_at_once(self) -> None:
        # Not at its next re-dating: that is put out of reach here, so only the halt noticing
        # that its flag has gone can bring the flag back.
        flag = self.book._halt_flag
        tried: list = []
        self.book._db = _Watched(self.book._db, tried)
        holder = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        self.enterContext(mock.patch.object(gateway_module, "_HALT_FLAG_REFRESH_S", 6 * WAIT))
        halter = threading.Thread(target=self.book.halt, daemon=True, name="halter")
        halter.start()
        self._until(lambda: "halter" in tried, "the halt never tried for the write lock")
        self.assertTrue(self._flag_live())

        def removed() -> bool:
            try:
                os.unlink(flag)
            except FileNotFoundError:
                pass
            except OSError:  # Windows: the halt has the file open this instant
                return False
            return True
        for _ in range(3):  # each time it goes, as a writer in another process may take it more than once
            self._until(removed, "the flag could not be removed")
            self._until(self._flag_live, "a halt still retrying did not make its flag again when it found it gone")
        self.assertTrue(halter.is_alive())
        holder.execute("ROLLBACK")
        halter.join(WAIT)
        self.assertFalse(halter.is_alive(), "the halt never finished")
        self.assertTrue(self.book.halted())
        self.assertFalse(os.path.exists(flag), "the halt flag was left behind")

    def test_a_halt_takes_its_flag_away_only_after_its_commit(self) -> None:
        # Other writers stop waiting the moment the flag goes. Taken away before the commit, it
        # would let them write to a book that does not read halted yet.
        flag = self.book._halt_flag
        other = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(other.close)
        halted_when_it_went: list = []
        unlink = os.unlink

        def watched(path, *args, **kwargs):
            if path == flag:  # what another connection reads at that moment
                halted_when_it_went.append(other.execute("SELECT v FROM state WHERE k = 'halted'").fetchone()[0])
            return unlink(path, *args, **kwargs)
        with mock.patch.object(gateway_module.os, "unlink", watched):
            self.book.halt()
        self.assertEqual(halted_when_it_went, [1], "the halt's flag went before its commit could be read")
        self.assertFalse(os.path.exists(flag))

    def test_the_bounds_as_shipped_are_in_a_workable_order(self) -> None:
        grain, refresh = SHIPPED["_HALT_FLAG_GRAIN_S"], SHIPPED["_HALT_FLAG_REFRESH_S"]
        live, cap = SHIPPED["HALT_YIELD_S"], SHIPPED["HALT_YIELD_TOTAL_S"]
        self.assertGreater(grain, 0.0156)  # a Windows clock tick still reads as a halt in progress
        self.assertLess(grain, refresh)  # and a planted date gains less than one re-dating
        self.assertLessEqual(2 * refresh, live)  # a halt that misses a re-dating is not taken for dead
        # A halt waits for the one transaction or probe in progress on its connection without
        # re-dating its flag: the flag has to stay live that long.
        self.assertGreater(live, max(SHIPPED["RUN_RECHECK_TIMEOUT_S"], SHIPPED["BOOK_LOCK_PROBE_S"]))
        self.assertLess(live, cap)  # a dead halt's flag leaves a retried halt part of the cap

    # -- mutants -----------------------------------------------------------------------------

    def test_mutant_without_the_look_under_the_lock_is_caught(self) -> None:
        with mock.patch.object(PaperBook, "_give_way", lambda book, cap: None):
            outcomes, _ = self._halt_behind_a_queue("halt")
        # Every queued booking ran its whole transaction, re-verify included, ahead of the halt.
        self.assertEqual(outcomes, [BOOKED] * (1 + QUEUED))
        self.assertEqual(len(self.reverifies), 1 + QUEUED)
        self.assertEqual(len(self.book.orders()), 1 + QUEUED)
        self.assertTrue(self.book.halted())

    def test_mutant_probe_without_the_look_under_the_lock_is_caught(self) -> None:
        with mock.patch.object(PaperBook, "_give_way", lambda book, cap: None):
            tried, outcomes = self._halt_behind_queued_probes()
        # Every queued probe waited out its try for the write lock ahead of the halt.
        self.assertEqual(tried, [PROBE + str(i) for i in range(QUEUED)] + ["halter"])
        self.assertEqual(outcomes, ["book_locked"] * QUEUED)
        self._busy_timeout_is_put_back()
        self.assertTrue(self.book.halted())


if __name__ == "__main__":
    unittest.main()
