"""The durable halt flag is the source of truth (Lucas, Nov 11, relayed 12:52am PST): "Keep the
durable halt flag as the source of truth, /dev/shm only as a fast copy".

Whether a book is halted is read only from its committed ``state`` row, on every check. The
file in ``/dev/shm`` (``_HALT_FLAG_DIR``; here a temporary directory) only orders writers
behind a halt in progress and is never read as halt state. So:

* deleting that copy, or its whole directory, leaves the book halted, for this connection,
  a new connection and another process;
* a stale, future-dated, corrupt or wrong-type copy never lifts a halt (and never stalls a
  writer past the yield bound);
* a durable flag that cannot be read (missing row, a value other than exactly 0 or 1, an
  I/O error) fails closed: nothing is approved or booked (``halt_unreadable``) and a kill
  cannot confirm its halt on it; ``halt()`` repairs a missing or corrupt row to halted.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/tests/paper -t engine -v``.
No provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from cockpit.paper import gateway as gateway_module
from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, OrderGateway, PaperBook, Refused, create_book
from cockpit.paper.killswitch import KillSwitch
from tests.paper.fakecore import AUTHORITY, FakeCore

ACCT = "paper-acct"
UNREADABLE_VALUES = ("'garbage'", "2", "-1", "0.5", "X'01'")


class _FailingHaltRead:
    """A connection whose reads of the ``halted`` row raise an I/O error; everything else passes."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def execute(self, sql: str, *args: object):
        if "k = 'halted'" in sql and sql.lstrip().upper().startswith("SELECT"):
            raise sqlite3.OperationalError("disk I/O error")
        return self._db.execute(sql, *args)

    def __getattr__(self, name: str) -> object:
        return getattr(self._db, name)


class HaltDurableTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-halt-durable-")
        self.addCleanup(shutil.rmtree, d, True)
        self.dir = d
        self.fast = os.path.join(d, "shm")  # stands in for /dev/shm
        os.mkdir(self.fast)
        patcher = mock.patch.object(gateway_module, "_HALT_FLAG_DIR", self.fast)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = os.path.join(d, "book.db")
        create_book(self.path, cash_minor={ACCT: 10**9})
        self.gw, self.core, self.book = self._gateway()

    def _gateway(self) -> tuple:
        book = PaperBook(self.path)
        self.addCleanup(book.close)
        core = FakeCore(budget_minor=10**12)
        gw = OrderGateway(plugin_id="csl.cockpit.paper", book=book, core=CoreClient(core), caps=Caps(10**9, 10**6),
                          allowlist=frozenset({"AAPL"}), run_check=core.check)
        gw.kill_switch = KillSwitch(gw.halt, (), paper_book=book)
        return gw, core, book

    def _buy(self, gw: OrderGateway, intent_id: str):
        return gw.size_buy(intent_id=intent_id, account=ACCT, instrument="AAPL", limit_price_minor=10_000,
                           conviction_bps=100, run_id="run-1", connection="conn-a")

    def _approved(self, gw: OrderGateway, core: FakeCore, intent_id: str) -> tuple:
        intent = self._buy(gw, intent_id)
        approval = gw.request_approval(intent)["approval_id"]
        core.operator_approves(approval)
        return intent, approval

    def _raw(self, sql: str) -> None:
        db = sqlite3.connect(self.path)  # another connection, as another process would
        try:
            db.execute(sql)
            db.commit()
        finally:
            db.close()

    def _assert_refused(self, code: str, gw: OrderGateway, core: FakeCore, intent, approval: str = "apr_none") -> None:
        calls, orders = list(core.calls), gw._book.orders()
        for attempt in (lambda: gw.request_approval(intent), lambda: gw.submit(intent, approval, AUTHORITY)):
            with self.assertRaises(Refused) as ctx:
                attempt()
            self.assertEqual((ctx.exception.code, ctx.exception.cause), (code, None))
        self.assertEqual((core.calls, gw._book.orders()), (calls, orders))

    # -- the fast copy is never halt state ------------------------------------------------------

    def test_deleting_the_fast_copy_leaves_the_book_halted(self) -> None:
        intent, approval = self._approved(self.gw, self.core, "before-halt")
        self.assertEqual(self.gw.kill_switch.kill()["status"], "paper_only")
        flag = self.book._halt_flag
        self.assertEqual(os.path.dirname(flag), self.fast)
        open(flag, "w").close()  # a copy left behind, then deleted, then its whole directory
        os.unlink(flag)
        shutil.rmtree(self.fast)
        self.assertIs(self.book.halted(), True)
        self._assert_refused("order_halted", self.gw, self.core, intent, approval)
        # A new connection that finds no fast copy (and no directory for one) reads the durable flag.
        gw, core, book = self._gateway()
        self.assertIs(book.halted(), True)
        self._assert_refused("order_halted", gw, core, self._buy(gw, "after-halt"))
        # Halting again with nowhere to put the copy still halts and confirms on the durable read.
        out = gw.kill_switch.kill()
        self.assertEqual((out["status"], out["halt"]), ("paper_only", "confirmed"))

    def test_another_process_without_the_fast_copy_reads_halted(self) -> None:
        self.gw.halt()
        shutil.rmtree(self.fast)
        code = (
            "import json, sys\n"
            "from cockpit.paper import gateway as g\n"
            "g._HALT_FLAG_DIR = sys.argv[2]\n"
            "book = g.PaperBook(sys.argv[1])\n"
            "gw = g.OrderGateway(plugin_id='p', book=book, core=None, caps=g.Caps(10**9, 10**6),\n"
            "                    allowlist=frozenset({'AAPL'}))\n"
            "intent = g.Intent(intent_id='x', account='paper-acct', instrument='AAPL', side='buy', quantity=1,\n"
            "                  limit_price_minor=1, run_id='r', connection='c')\n"
            "try:\n"
            "    gw.risk_pass(intent)\n"
            "    code = None\n"
            "except g.Refused as exc:\n"
            "    code = exc.code\n"
            "print(json.dumps([book.halted(), code, g.os.path.exists(sys.argv[2])]))\n"
        )
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
        out = subprocess.run([sys.executable, "-B", "-c", code, self.path, self.fast], env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [True, "order_halted", False])

    def test_fast_copy_deleted_while_a_halt_waits_for_the_lock_still_halts(self) -> None:
        holder = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")  # another writer holds the lock, so the halt spins
        done: list = []
        halter = threading.Thread(target=lambda: done.append(self.book.halt()), daemon=True)
        halter.start()
        deadline = time.monotonic() + 10
        while not os.path.exists(self.book._halt_flag):
            self.assertLess(time.monotonic(), deadline, "the halt never made its fast copy")
            time.sleep(0.001)
        os.unlink(self.book._halt_flag)  # the copy is gone mid-halt
        holder.execute("ROLLBACK")
        halter.join(10)
        self.assertEqual(len(done), 1)
        self.assertIs(self.book.halted(), True)
        self._assert_refused("order_halted", self.gw, self.core, self._buy(self.gw, "mid-halt"))

    def test_stale_or_corrupt_fast_copy_never_lifts_a_halt(self) -> None:
        self.gw.halt()
        flag = self.book._halt_flag
        now = time.time()

        def garbage(mtime: float):
            def make() -> None:
                with open(flag, "wb") as f:
                    f.write(b"\x00not-a-flag\xff" * 64)
                os.utime(flag, (mtime, mtime))
            return make

        def directory() -> None:
            os.mkdir(flag)
            os.utime(flag, (now - 3600, now - 3600))

        def dangling() -> None:
            os.symlink(os.path.join(self.dir, "nowhere"), flag)

        variants = {"stale": garbage(now - 3600), "future": garbage(now + 3600), "directory": directory,
                    "dangling_symlink": dangling, "empty_stale": garbage(now - gateway_module.HALT_YIELD_S - 1)}
        for n, (name, make) in enumerate(variants.items()):
            with self.subTest(copy=name):
                make()
                try:
                    t = time.monotonic()
                    self._assert_refused("order_halted", self.gw, self.core, self._buy(self.gw, "c-%d" % n))
                    self.assertLess(time.monotonic() - t, gateway_module.HALT_YIELD_S)
                    self.assertIs(self.book.halted(), True)
                    self.assertIs(PaperBook(self.path).halted(), True)
                finally:
                    if os.path.isdir(flag) and not os.path.islink(flag):
                        os.rmdir(flag)
                    elif os.path.lexists(flag):
                        os.unlink(flag)

    def test_halt_never_follows_a_symlinked_fast_copy(self) -> None:
        target = os.path.join(self.dir, "victim")
        with open(target, "wb") as f:
            f.write(b"keep")
        os.utime(target, (1, 1))
        os.symlink(target, self.book._halt_flag)
        self.gw.halt()
        self.assertIs(self.book.halted(), True)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"keep")
        self.assertEqual(os.stat(target).st_mtime, 1)  # never opened or touched through the link

    # -- the durable flag fails closed -----------------------------------------------------------

    def test_corrupt_durable_flag_fails_closed_and_halt_repairs_it(self) -> None:
        for n, value in enumerate(UNREADABLE_VALUES + ("missing",)):
            with self.subTest(value=value):
                if value == "missing":
                    self._raw("DELETE FROM state WHERE k = 'halted'")
                else:
                    self._raw("UPDATE state SET v = %s WHERE k = 'halted'" % value)
                with self.assertRaises(Refused) as ctx:
                    self.book.halted()
                self.assertEqual(ctx.exception.code, "halt_unreadable")
                self._assert_refused("halt_unreadable", self.gw, self.core, self._buy(self.gw, "u-%d" % n))
                # A kill repairs the row to halted and confirms on reading it back.
                out = self.gw.kill_switch.kill()
                self.assertEqual((out["status"], out["halt"]), ("paper_only", "confirmed"))
                self.assertIs(self.book.halted(), True)
                self._raw("UPDATE state SET v = 0 WHERE k = 'halted'")  # test-only reset for the next value

    def test_halt_does_not_depend_on_the_sequence_counter(self) -> None:
        """A damaged ``seq`` row never stops the halted write (it is written first)."""
        for n, damage in enumerate(("DELETE FROM state WHERE k = 'seq'", "UPDATE state SET v = 0.5 WHERE k = 'seq'")):
            with self.subTest(damage=damage):
                self._raw("UPDATE state SET v = 0 WHERE k = 'halted'")  # test-only reset
                self._raw(damage)
                out = self.gw.kill_switch.kill()
                self.assertEqual((out["status"], out["halt"]), ("paper_only", "confirmed"))
                self.assertIs(self.book.halted(), True)
                self._assert_refused("order_halted", self.gw, self.core, self._buy(self.gw, "seq-%d" % n))
                with self.assertRaises(Refused) as ctx:  # the counter itself is a typed refusal, not a TypeError
                    self.book._tx(self.book._next_seq)
                self.assertEqual(ctx.exception.code, "book_invalid")
                self._raw("INSERT INTO state VALUES ('seq', 5) ON CONFLICT (k) DO UPDATE SET v = 5")

    def test_durable_flag_read_error_fails_closed_and_stays_halted(self) -> None:
        intent, approval = self._approved(self.gw, self.core, "io")
        self.gw.halt()
        real = self.book._db
        self.book._open_db = lambda: _FailingHaltRead(real)  # the halted row cannot be read
        try:
            with self.assertRaises(Refused) as ctx:
                self.book.halted()
            self.assertEqual(ctx.exception.code, "halt_unreadable")
            self._assert_refused("halt_unreadable", self.gw, self.core, intent, approval)
            out = self.gw.kill_switch.kill()  # the halt is written, but cannot be read back: not confirmed
            self.assertEqual((out["status"], out["halt"]), ("pending", "refused:halt_unreadable"))
        finally:
            del self.book._open_db
        self.assertIs(self.book.halted(), True)  # still halted once the read works again
        self._assert_refused("order_halted", self.gw, self.core, intent, approval)

    def test_durable_flag_read_error_on_an_unhalted_book_still_refuses(self) -> None:
        intent, approval = self._approved(self.gw, self.core, "io-open")
        real = self.book._db
        self.book._open_db = lambda: _FailingHaltRead(real)
        try:
            self._assert_refused("halt_unreadable", self.gw, self.core, intent, approval)
        finally:
            del self.book._open_db
        self.assertIs(self.book.halted(), False)
        self.assertEqual(self.gw.submit(intent, approval, AUTHORITY)["status"], "paper_working")  # control

    def test_flag_corrupted_after_the_charge_is_a_leftover_charge(self) -> None:
        intent, approval = self._approved(self.gw, self.core, "late")
        core = self.core

        def corrupt_during_core_call(raw: bytes) -> bytes:
            out = core(raw)
            self._raw("UPDATE state SET v = 'garbage' WHERE k = 'halted'")
            return out
        self.gw._core = CoreClient(corrupt_during_core_call)
        with self.assertRaises(Refused) as ctx:
            self.gw.submit(intent, approval, AUTHORITY)
        self.assertEqual((ctx.exception.code, ctx.exception.cause), ("leftover_charge", "halt_unreadable"))
        self.assertEqual(self.book.orders(), [])


if __name__ == "__main__":
    unittest.main()
