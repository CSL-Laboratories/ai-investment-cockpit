"""Paper-only crash integrity for the book audit, run faults and the charge key.

Temp SQLite only. A crash is the child process killing itself with SIGKILL between
two steps, so no ``finally`` runs and an uncommitted transaction is rolled back.
No provider, venue, network, credential or keyring is touched. Connection trips
stay on the core: a successful probe must be able to clear them, and this file
does not make a trip sticky in the book.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import cockpit.paper.gateway as gateway_module
from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from cockpit.paper.tests.fakecore import AUTHORITY, FakeCore

_ENGINE = str(Path(__file__).resolve().parents[3])
_CASH = 500_000
_PLUGIN = "csl.cockpit.paper"
_CAPS = Caps(500_000, 100)


def _intent(**change) -> Intent:
    base = Intent(intent_id="crash-1", account="paper-acct", instrument="AAPL", side="buy",
                  quantity=2, limit_price_minor=100, run_id="run-1", connection="conn-a")
    return base if not change else replace(base, **change)


def _open(path: str, core) -> tuple[PaperBook, OrderGateway]:
    book = PaperBook(path)
    return book, OrderGateway(plugin_id=_PLUGIN, book=book, core=CoreClient(core), caps=_CAPS)


def _ledger_hits(ledger: str) -> int:
    db = sqlite3.connect(ledger, isolation_level=None)
    try:
        row = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'hit'").fetchone()
        if row[0] == 0:
            return 0
        return db.execute("SELECT COUNT(*) FROM hit").fetchone()[0]
    finally:
        db.close()


def _status(path: str) -> str | None:
    db = sqlite3.connect(path, isolation_level=None)
    try:
        row = db.execute("SELECT status FROM idempotency").fetchone()
        return None if row is None else row[0]
    finally:
        db.close()


def _spawn(role: str, path: str, ledger: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": _ENGINE,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    cmd = [sys.executable, "-B", __file__, role, path]
    if ledger is not None:
        cmd.append(ledger)
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30, check=False)


def _record(ledger: str) -> None:
    db = sqlite3.connect(ledger, isolation_level=None)
    db.execute("CREATE TABLE IF NOT EXISTS hit (n INTEGER NOT NULL)")
    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT INTO hit VALUES (1)")
    db.execute("COMMIT")
    db.close()


def _recording_transport(fake: FakeCore, ledger: str, kill_after_charge: bool):
    def transport(raw: bytes) -> bytes:
        out = fake(raw)
        message = json.loads(raw)
        if message["op"] == "effect.authorize" and json.loads(out).get("ok") is True:
            _record(ledger)
            if kill_after_charge:
                os.kill(os.getpid(), signal.SIGKILL)
        return out
    return transport


def _child_fail(message: str) -> None:
    sys.stderr.write(message + "\n")
    raise SystemExit(1)


def _crash_fault(path: str) -> None:
    core = FakeCore()
    core.faulted_runs.add("run-1")
    _book, gw = _open(path, core)
    try:
        gw.request_approval(_intent())
    except Refused as exc:
        if exc.code != "core:run_faulted":
            _child_fail(exc.code)
    else:
        _child_fail("faulted run was approved")
    os.kill(os.getpid(), signal.SIGKILL)


def _crash_audit_insert(path: str) -> None:
    _book, gw = _open(path, FakeCore())
    try:
        gw.request_approval(_intent(side="hold"))
    except Refused as exc:
        if exc.code != "side_invalid":
            _child_fail(exc.code)
    else:
        _child_fail("bad side was approved")
    original = PaperBook._audit_row

    def die(db, row):
        original(db, row)  # inserted, not yet committed
        os.kill(os.getpid(), signal.SIGKILL)

    PaperBook._audit_row = staticmethod(die)
    try:
        gw.request_approval(_intent(intent_id="second", side="hold"))
    except Exception:
        pass
    _child_fail("second insert committed")


def _crash_default_insert(path: str) -> None:
    # The default connection's implicit transaction is not committed. SIGKILL drops it.
    db = sqlite3.connect(path)
    db.execute("INSERT INTO audit (event, stage, code, plugin_id, halt_seq)"
               " VALUES ('refused', 'risk', 'ghost', 'p', 0)")
    os.kill(os.getpid(), signal.SIGKILL)


def _crash_during_authorize(path: str, ledger: str) -> None:
    fake = FakeCore(budget_minor=10**9)
    book, gw = _open(path, None)
    gw._core = CoreClient(_recording_transport(fake, ledger, kill_after_charge=True))
    intent = _intent()
    approval = gw.request_approval(intent)["approval_id"]
    fake.operator_approves(approval)
    try:
        gw.submit(intent, approval, AUTHORITY)
    except Exception:
        pass
    book.close()
    _child_fail("authorize returned")


def _crash_after_charge(path: str, ledger: str) -> None:
    fake = FakeCore(budget_minor=10**9)
    book, gw = _open(path, None)
    gw._core = CoreClient(_recording_transport(fake, ledger, kill_after_charge=False))

    def die(self, *args, **kwargs):
        os.kill(os.getpid(), signal.SIGKILL)

    PaperBook._book = die
    intent = _intent()
    approval = gw.request_approval(intent)["approval_id"]
    fake.operator_approves(approval)
    try:
        gw.submit(intent, approval, AUTHORITY)
    except Exception:
        pass
    book.close()
    _child_fail("booking returned")


def _crash_main(argv: list[str]) -> None:
    role, path = argv[0], argv[1]
    ledger = argv[2] if len(argv) > 2 else ""
    if role == "crash-fault":
        _crash_fault(path)
    elif role == "crash-audit-insert":
        _crash_audit_insert(path)
    elif role == "crash-default-insert":
        _crash_default_insert(path)
    elif role == "crash-during-authorize":
        _crash_during_authorize(path, ledger)
    elif role == "crash-after-charge":
        _crash_after_charge(path, ledger)
    else:
        _child_fail("unknown role " + role)


class CrashIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="cockpit-crash-")
        self.addCleanup(self._rm)
        self.path = os.path.join(self.dir, "book.db")
        create_book(self.path, cash_minor={"paper-acct": _CASH})

    def _rm(self) -> None:
        import shutil
        shutil.rmtree(self.dir, True)

    def _killed(self, proc: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(proc.returncode, -signal.SIGKILL, proc.stderr or proc.stdout)

    def test_book_connection_is_not_the_default_and_replace_cannot_rewrite_audit(self) -> None:
        book, gw = _open(self.path, FakeCore())
        self.addCleanup(book.close)
        self.assertIsNone(book._db.isolation_level)
        with self.assertRaises(Refused):
            gw.request_approval(_intent(side="hold"))
        with self.assertRaises(Refused):
            gw.request_approval(_intent(intent_id="second", instrument="MSFT", side="hold"))
        before = book.audit()
        self.assertEqual([row["code"] for row in before], ["side_invalid", "side_invalid"])
        default = sqlite3.connect(self.path)  # isolation_level "" : implicit transaction
        self.addCleanup(default.close)
        with self.assertRaises(sqlite3.DatabaseError):
            default.execute("INSERT OR REPLACE INTO audit (seq, event, stage, code, plugin_id,"
                            " intent_id, run_id, connection, effect_sha256, halt_seq)"
                            " VALUES (?, 'refused', 'risk', 'rewritten', 'p', 'x', 'run-1', 'conn-a', NULL, 0)",
                            (before[0]["seq"],))
        default.close()
        fresh = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(fresh.close)
        codes = [row[0] for row in fresh.execute("SELECT code FROM audit ORDER BY seq")]
        self.assertEqual(codes, ["side_invalid", "side_invalid"])

        proc = _spawn("crash-default-insert", self.path)
        self._killed(proc)
        codes = [row[0] for row in fresh.execute("SELECT code FROM audit ORDER BY seq")]
        self.assertEqual(codes, ["side_invalid", "side_invalid"])  # the killed insert did not land

    def test_kill_during_audit_insert_keeps_the_committed_row(self) -> None:
        proc = _spawn("crash-audit-insert", self.path)
        self._killed(proc)
        book = PaperBook(self.path)
        self.addCleanup(book.close)
        rows = book.audit()
        self.assertEqual([(row["intent_id"], row["code"]) for row in rows], [("crash-1", "side_invalid")])

    def test_kill_after_fault_refusal_blocks_a_fresh_core(self) -> None:
        proc = _spawn("crash-fault", self.path)
        self._killed(proc)
        core = FakeCore()  # the dead process's fault set is gone
        book, gw = _open(self.path, core)
        self.addCleanup(book.close)
        with self.assertRaises(Refused) as ctx:
            gw.submit(_intent(), "apr_unused", AUTHORITY)
        self.assertEqual(ctx.exception.code, "core:run_faulted")
        self.assertEqual(core.calls, [])
        self.assertEqual(book.orders(), [])
        self.assertEqual(book.available_cash_minor("paper-acct"), _CASH)
        codes = [row["code"] for row in book.audit()]
        self.assertEqual(codes, ["core:run_faulted", "core:run_faulted"])
        default = sqlite3.connect(self.path)
        self.addCleanup(default.close)
        with self.assertRaises(sqlite3.DatabaseError):
            default.execute("UPDATE fault_state SET code = 'ok'")
        with self.assertRaises(sqlite3.DatabaseError):
            default.execute("INSERT OR REPLACE INTO fault_state (kind, subject, code)"
                            " VALUES ('run', 'run-1', 'rewritten')")
        fresh = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(fresh.close)
        self.assertEqual(fresh.execute("SELECT code FROM fault_state").fetchone()[0], "core:run_faulted")

    def test_kill_during_authorize_is_not_charged_again(self) -> None:
        ledger = os.path.join(self.dir, "charges.db")
        proc = _spawn("crash-during-authorize", self.path, ledger)
        self._killed(proc)
        self.assertEqual(_ledger_hits(ledger), 1)
        self.assertEqual(_status(self.path), "armed")
        dead = PaperBook(self.path)
        self.addCleanup(dead.close)
        self.assertEqual(dead.orders(), [])
        core = FakeCore(budget_minor=10**9)
        book, gw = _open(self.path, core)
        self.addCleanup(book.close)
        with self.assertRaises(Refused) as ctx:
            gw.submit(_intent(), "apr_retry", AUTHORITY)
        self.assertEqual(ctx.exception.code, "charge_uncertain")
        with self.assertRaises(Refused) as again:
            gw.submit(_intent(), "apr_retry_2", AUTHORITY)
        self.assertEqual(again.exception.code, "charge_uncertain")
        self.assertEqual(core.calls, [])
        self.assertEqual(core.spent, 0)
        self.assertEqual(_ledger_hits(ledger), 1)
        self.assertEqual(book.orders(), [])
        self.assertEqual(book.available_cash_minor("paper-acct"), _CASH)
        self.assertEqual(_status(self.path), "armed")

    def test_kill_after_recorded_charge_books_once_on_retry(self) -> None:
        ledger = os.path.join(self.dir, "charges.db")
        proc = _spawn("crash-after-charge", self.path, ledger)
        self._killed(proc)
        self.assertEqual(_ledger_hits(ledger), 1)
        self.assertEqual(_status(self.path), "charged")
        dead = PaperBook(self.path)
        self.assertEqual(dead.orders(), [])
        self.assertEqual(dead.available_cash_minor("paper-acct"), _CASH)
        dead.close()
        core = FakeCore(budget_minor=10**9)
        book, gw = _open(self.path, core)
        self.addCleanup(book.close)
        out = gw.submit(_intent(), "apr_retry", AUTHORITY)
        self.assertEqual(out["status"], "paper_working")
        self.assertEqual(out["notional_minor"], 200)
        self.assertEqual(book.orders(), [("crash-1", 1, "working")])
        self.assertEqual(book.available_cash_minor("paper-acct"), _CASH - 200)
        self.assertEqual(core.calls, [])
        self.assertEqual(core.spent, 0)
        self.assertEqual(_ledger_hits(ledger), 1)
        self.assertEqual(_status(self.path), "booked")
        default = sqlite3.connect(self.path)  # recursive_triggers stays off on this connection
        self.addCleanup(default.close)
        with self.assertRaises(sqlite3.DatabaseError):
            default.execute("INSERT OR REPLACE INTO idempotency (idempotency_key, intent_id,"
                            " effect_sha256, status) VALUES ('other-key', 'crash-1', 'other-key', 'void')")
        self.assertEqual(_status(self.path), "booked")
        with self.assertRaises(Refused) as ctx:
            gw.submit(_intent(), "apr_retry", AUTHORITY)
        self.assertEqual(ctx.exception.code, "intent_duplicate")
        self.assertEqual(core.calls, [])
        self.assertEqual(book.available_cash_minor("paper-acct"), _CASH - 200)

    def test_explicit_nonpositive_seq_is_refused(self) -> None:
        """seq -1 is the unassigned sentinel, so the BEFORE guard must not be the only check."""
        book, gw = _open(self.path, FakeCore())
        self.addCleanup(book.close)
        with self.assertRaises(Refused):
            gw.request_approval(_intent(side="hold"))
        self.assertGreaterEqual(book.audit()[0]["seq"], 1)
        db = sqlite3.connect(self.path)  # recursive_triggers stays off
        self.addCleanup(db.close)
        attempts = (
            "INSERT INTO audit (seq, event, stage, plugin_id, halt_seq) VALUES ({seq}, 'refused', 'risk', 'p', 0)",
            "INSERT OR REPLACE INTO audit (seq, event, stage, plugin_id, halt_seq)"
            " VALUES ({seq}, 'refused', 'risk', 'rewritten', 0)",
            "INSERT INTO fault_seen (seq, kind, subject) VALUES ({seq}, 'run', 'ghost')",
            "INSERT OR REPLACE INTO fault_seen (seq, kind, subject) VALUES ({seq}, 'run', 'ghost')",
            "INSERT INTO idempotency_seen (seq, intent_id) VALUES ({seq}, 'ghost-intent')",
            "INSERT OR REPLACE INTO idempotency_seen (seq, intent_id) VALUES ({seq}, 'ghost-intent')",
            "INSERT INTO idempotency_key_seen (seq, idempotency_key) VALUES ({seq}, 'ghost-key')",
            "INSERT OR REPLACE INTO idempotency_key_seen (seq, idempotency_key) VALUES ({seq}, 'ghost-key')",
        )
        for seq in (-1, 0):
            for sql in attempts:
                with self.assertRaises(sqlite3.DatabaseError):
                    db.execute(sql.format(seq=seq))
                db.rollback()
        fresh = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(fresh.close)
        for table in ("audit", "fault_seen", "idempotency_seen", "idempotency_key_seen"):
            self.assertEqual(fresh.execute(f"SELECT COUNT(*) FROM {table} WHERE seq < 1").fetchone()[0], 0)
        self.assertEqual([row["code"] for row in book.audit()], ["side_invalid"])

    def test_charged_key_replace_or_delete_leaves_the_charge(self) -> None:
        book = PaperBook(self.path)
        self.addCleanup(book.close)
        book._arm_charge("intent-1", "key-123", "apr-1")
        book._mark_charged("key-123", "apr-1", "core-digest-1", 1000)
        db = sqlite3.connect(self.path)  # recursive_triggers stays off, as a default connection
        self.addCleanup(db.close)
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("INSERT OR REPLACE INTO idempotency (idempotency_key, intent_id, effect_sha256, status)"
                       " VALUES ('key-123', 'intent-2', 'key-123', 'void')")
        db.rollback()
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("DELETE FROM idempotency")
        db.rollback()
        self.assertEqual(book._charge_state("intent-1", "key-123"), "resume")
        row = book._db.execute("SELECT intent_id, status FROM idempotency WHERE idempotency_key = 'key-123'").fetchone()
        self.assertEqual(row, ("intent-1", "charged"))

    def test_retargeted_charge_key_cannot_be_replaced(self) -> None:
        book = PaperBook(self.path)
        self.addCleanup(book.close)
        self.assertEqual(book._arm_charge("intent-1", "key-old", "apr-1"), "go")
        book._void_charge("key-old")
        self.assertEqual(book._arm_charge("intent-1", "key-new", "apr-2"), "go")
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("INSERT OR REPLACE INTO idempotency (idempotency_key, intent_id, effect_sha256, status)"
                       " VALUES ('key-new', 'intent-2', 'key-new', 'void')")
        db.rollback()
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("INSERT OR REPLACE INTO idempotency (idempotency_key, intent_id, effect_sha256, status)"
                       " VALUES ('key-old', 'intent-2', 'key-old', 'void')")
        db.rollback()
        row = book._db.execute("SELECT intent_id, idempotency_key, status FROM idempotency"
                               " WHERE intent_id = 'intent-1'").fetchone()
        self.assertEqual(row, ("intent-1", "key-new", "armed"))

    def test_reopen_installs_seq_and_key_guards_on_an_older_book(self) -> None:
        book = PaperBook(self.path)
        book._arm_charge("intent-1", "key-123", "apr-1")
        book._mark_charged("key-123", "apr-1", "core-digest-1", 1000)
        book.close()
        db = sqlite3.connect(self.path, isolation_level=None)
        db.execute("DROP TRIGGER IF EXISTS idempotency_no_replace")
        db.execute("CREATE TRIGGER idempotency_no_replace BEFORE INSERT ON idempotency"
                   " WHEN EXISTS (SELECT 1 FROM idempotency_seen WHERE intent_id = NEW.intent_id)"
                   " BEGIN SELECT RAISE(ABORT, 'idempotency key is already recorded'); END")
        for name in ("audit_no_nonpositive_seq", "fault_seen_no_nonpositive_seq",
                     "idempotency_seen_no_nonpositive_seq", "idempotency_key_seen_no_explicit_seq",
                     "idempotency_key_seen_no_nonpositive_seq", "idempotency_key_seen_no_update",
                     "idempotency_key_seen_no_delete", "idempotency_no_replace_key",
                     "idempotency_remember_key", "idempotency_remember_key_update",
                     "idempotency_no_delete", "idempotency_transitions"):
            db.execute(f"DROP TRIGGER IF EXISTS {name}")
        db.execute("DROP TABLE IF EXISTS idempotency_key_seen")
        db.close()
        book = PaperBook(self.path)
        self.addCleanup(book.close)
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("INSERT INTO audit (seq, event, stage, plugin_id, halt_seq)"
                       " VALUES (-1, 'refused', 'risk', 'p', 0)")
        db.rollback()
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("INSERT OR REPLACE INTO idempotency (idempotency_key, intent_id, effect_sha256, status)"
                       " VALUES ('key-123', 'intent-2', 'key-123', 'void')")
        db.rollback()
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("DELETE FROM idempotency")
        db.rollback()
        self.assertEqual(book._charge_state("intent-1", "key-123"), "resume")
        guard = db.execute("SELECT sql FROM sqlite_master WHERE name = 'idempotency_no_replace'").fetchone()[0]
        self.assertIn("idempotency_key_seen", guard)
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("UPDATE idempotency SET status = 'void' WHERE idempotency_key = 'key-123'")
        db.rollback()
        self.assertEqual(book._charge_state("intent-1", "key-123"), "resume")
        self.assertEqual(book._arm_charge("intent-1", "key-123", "apr-2"), "resume")

    def test_status_rewrite_cannot_rearm_a_charged_row(self) -> None:
        """A plain UPDATE to void must not let a retry arm the same charge again."""
        book = PaperBook(self.path)
        book._arm_charge("intent-1", "key-123", "apr-1")
        book._mark_charged("key-123", "apr-1", "d1", 1000)
        self.assertEqual(book._charge_state("intent-1", "key-123"), "resume")
        book.close()
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("UPDATE idempotency SET status = 'void' WHERE idempotency_key = 'key-123'")
        db.rollback()
        book = PaperBook(self.path)
        self.addCleanup(book.close)
        self.assertEqual(book._charge_state("intent-1", "key-123"), "resume")
        self.assertEqual(book._arm_charge("intent-1", "key-123", "apr-2"), "resume")

    def test_illegal_charge_updates_are_refused(self) -> None:
        book = PaperBook(self.path)
        self.addCleanup(book.close)
        book._arm_charge("intent-1", "key-123", "apr-1")
        book._mark_charged("key-123", "apr-1", "d1", 1000)
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        charged = (
            "UPDATE idempotency SET status = 'void' WHERE idempotency_key = 'key-123'",
            "UPDATE idempotency SET status = 'armed' WHERE idempotency_key = 'key-123'",
            "UPDATE idempotency SET intent_id = 'intent-X' WHERE idempotency_key = 'key-123'",
            "UPDATE idempotency SET idempotency_key = 'key-999' WHERE idempotency_key = 'key-123'",
            "UPDATE idempotency SET effect_sha256 = 'other' WHERE idempotency_key = 'key-123'",
        )
        for sql in charged:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute(sql)
            db.rollback()
        book._book(_intent(intent_id="intent-1"), gateway_module._GATEWAY_ONLY, charge_key="key-123")
        booked = (
            "UPDATE idempotency SET status = 'void' WHERE idempotency_key = 'key-123'",
            "UPDATE idempotency SET status = 'armed' WHERE idempotency_key = 'key-123'",
            "UPDATE idempotency SET status = 'charged' WHERE idempotency_key = 'key-123'",
            "UPDATE idempotency SET intent_id = 'intent-X' WHERE idempotency_key = 'key-123'",
            "UPDATE idempotency SET order_seq = 9 WHERE idempotency_key = 'key-123'",
        )
        for sql in booked:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute(sql)
            db.rollback()
        row = book._db.execute("SELECT intent_id, idempotency_key, status FROM idempotency"
                               " WHERE idempotency_key = 'key-123'").fetchone()
        self.assertEqual(row, ("intent-1", "key-123", "booked"))

    def test_legal_charge_transitions_still_commit(self) -> None:
        book = PaperBook(self.path)
        self.addCleanup(book.close)
        self.assertEqual(book._arm_charge("intent-1", "key-old", "apr-1"), "go")
        book._void_charge("key-old")  # armed -> void
        self.assertEqual(book._charge_state("intent-1", "key-old"), "void")
        self.assertEqual(book._arm_charge("intent-1", "key-new", "apr-2"), "go")  # void -> armed, new key
        row = book._db.execute("SELECT idempotency_key, effect_sha256, status FROM idempotency"
                               " WHERE intent_id = 'intent-1'").fetchone()
        self.assertEqual(row, ("key-new", "key-new", "armed"))
        book._mark_charged("key-new", "apr-2", "d1", 200)  # armed -> charged
        self.assertEqual(book._charge_state("intent-1", "key-new"), "resume")
        seq = book._book(_intent(intent_id="intent-1"), gateway_module._GATEWAY_ONLY, charge_key="key-new")
        row = book._db.execute("SELECT status, order_seq, digest, notional_minor FROM idempotency"
                               " WHERE intent_id = 'intent-1'").fetchone()
        self.assertEqual(row, ("booked", seq, "d1", 200))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].startswith("crash-"):
        _crash_main(sys.argv[1:])
    else:
        unittest.main()
