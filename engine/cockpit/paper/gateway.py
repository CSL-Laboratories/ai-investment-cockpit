"""The single paper ``OrderGateway`` (build-order item 9, Cockpit part). Escalate-list code.

The only path that books an order in ``engine/``. Every order goes, in this order:

1. halt check (new risk is refused while halted; cancels stay allowed, Q26);
2. the deterministic risk pass (paper only, integer money, size and quantity caps,
   rejecting and never clamping, Q25);
3. the Recursion core over its protocol: permission, the operator's one-use approval of
   this exact effect, then the installer-signed authority profile and its budgets
   (PRODUCT.md 4.2, O11, K3);
4. booking, in one SQLite ``BEGIN IMMEDIATE`` transaction that re-reads the halt flag,
   so a halt committed by any process before the booking wins.

Every refusal on ``request_approval`` and ``submit`` is written to the book's append-only
``audit`` table (stage, typed code, intent, run, connection, effect digest) before the
typed ``Refused`` is raised, and every booking writes a ``booked`` row in the booking's
own transaction (B15). An intent whose run ended in a fault, or whose connection the
engine breaker tripped, is refused here with ``core:run_faulted`` or
``core:connection_tripped`` (PRODUCT.md 4.2, K1).

Paper only. There is no broker, venue transport, credential or network code here, and
no Kalshi order code at all (not even demo). Risk logic uses no model, I/O beyond the
local book, or subprocess. The cap values are injected; they are not sourced yet (F12).
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
from dataclasses import dataclass

from .core_client import MAX_INT, CoreClient, CoreRefused, canonical

__all__ = ["Caps", "Intent", "OrderGateway", "PaperBook", "Refused", "create_book"]

PAPER_VENUE = "paper"
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_INSTRUMENT = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")


class Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
        self.audit_seq: int | None = None


@dataclass(frozen=True)
class Caps:
    max_notional_minor: int
    max_quantity: int

    def __post_init__(self) -> None:
        for value in (self.max_notional_minor, self.max_quantity):
            if type(value) is not int or not 0 < value <= MAX_INT:
                raise Refused("invalid_cap")


@dataclass(frozen=True)
class Intent:
    intent_id: str
    account: str
    instrument: str
    side: str
    quantity: object
    limit_price_minor: object
    run_id: str
    connection: str
    venue: str = PAPER_VENUE
    mode: str = "paper"


AUDIT_SCHEMA = ("CREATE TABLE audit (seq INTEGER PRIMARY KEY, event TEXT NOT NULL,"
                " stage TEXT NOT NULL, code TEXT, plugin_id TEXT NOT NULL, intent_id TEXT,"
                " run_id TEXT, connection TEXT, effect_sha256 TEXT, halt_seq INTEGER NOT NULL)")
AUDIT_COLUMNS = ("seq", "event", "stage", "code", "plugin_id", "intent_id", "run_id", "connection",
                 "effect_sha256", "halt_seq")


def create_book(path: str) -> None:
    """Create the paper book. Refuses if it exists."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE state (k TEXT PRIMARY KEY, v INTEGER NOT NULL)")
        db.execute("INSERT INTO state VALUES ('halted', 0), ('seq', 0), ('halt_seq', 0)")
        db.execute("CREATE TABLE orders (intent_id TEXT PRIMARY KEY, seq INTEGER NOT NULL,"
                   " account TEXT NOT NULL, instrument TEXT NOT NULL, side TEXT NOT NULL,"
                   " quantity INTEGER NOT NULL, limit_price_minor INTEGER NOT NULL,"
                   " status TEXT NOT NULL)")
        db.execute(AUDIT_SCHEMA)
        db.execute("CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit"
                   " BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END")
        db.execute("CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit"
                   " BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END")
    finally:
        db.close()


class PaperBook:
    """Paper orders and the halt flag, shared by every process that opens the same file."""

    def __init__(self, path: str) -> None:
        if not os.path.isfile(path):
            raise Refused("book_missing")
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA busy_timeout=30000")
        self._lock = threading.Lock()
        if not self._db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit'").fetchone():
            self._db.close()
            raise Refused("book_without_audit")

    def _tx(self, fn):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                out = fn(self._db)
                self._db.execute("COMMIT")
                return out
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    @staticmethod
    def _next_seq(db) -> int:
        db.execute("UPDATE state SET v = v + 1 WHERE k = 'seq'")
        return db.execute("SELECT v FROM state WHERE k = 'seq'").fetchone()[0]

    def halt(self) -> int:
        """Set the halt flag in one committed step. Returns the halt's sequence number."""
        def run(db):
            if db.execute("SELECT v FROM state WHERE k = 'halted'").fetchone()[0]:
                return db.execute("SELECT v FROM state WHERE k = 'halt_seq'").fetchone()[0]
            seq = self._next_seq(db)
            db.execute("UPDATE state SET v = 1 WHERE k = 'halted'")
            db.execute("UPDATE state SET v = ? WHERE k = 'halt_seq'", (seq,))
            return seq
        return self._tx(run)

    def halted(self) -> bool:
        with self._lock:
            return bool(self._db.execute("SELECT v FROM state WHERE k = 'halted'").fetchone()[0])

    @staticmethod
    def _audit_row(db, row: dict) -> int:
        halt_seq = db.execute("SELECT v FROM state WHERE k = 'halt_seq'").fetchone()[0]
        cur = db.execute("INSERT INTO audit (event, stage, code, plugin_id, intent_id, run_id, connection,"
                         " effect_sha256, halt_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (row["event"], row["stage"], row.get("code"), row["plugin_id"],
                          row.get("intent_id"), row.get("run_id"), row.get("connection"),
                          row.get("effect_sha256"), halt_seq))
        return cur.lastrowid

    def book(self, intent: Intent, audit: dict | None = None) -> int:
        def run(db):
            if db.execute("SELECT v FROM state WHERE k = 'halted'").fetchone()[0]:
                raise Refused("order_halted")
            if db.execute("SELECT 1 FROM orders WHERE intent_id = ?", (intent.intent_id,)).fetchone():
                raise Refused("intent_duplicate")
            seq = self._next_seq(db)
            db.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, 'working')",
                       (intent.intent_id, seq, intent.account, intent.instrument, intent.side,
                        intent.quantity, intent.limit_price_minor))
            if audit is not None:
                self._audit_row(db, dict(audit, event="booked", stage="book", code=None))
            return seq
        return self._tx(run)

    def record_refusal(self, row: dict) -> int:
        """Append one refusal to the audit table in its own committed step. Returns its seq."""
        return self._tx(lambda db: self._audit_row(db, dict(row, event="refused")))

    def audit(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT " + ", ".join(AUDIT_COLUMNS) + " FROM audit ORDER BY seq").fetchall()
        return [dict(zip(AUDIT_COLUMNS, row)) for row in rows]

    def cancel_all(self) -> int:
        """Cancel every working paper order. Allowed while halted (risk-reducing)."""
        return self._tx(lambda db: db.execute(
            "UPDATE orders SET status = 'cancelled' WHERE status = 'working'").rowcount)

    def orders(self) -> list[tuple]:
        with self._lock:
            return self._db.execute("SELECT intent_id, seq, status FROM orders ORDER BY seq").fetchall()

    def halt_seq(self) -> int:
        with self._lock:
            return self._db.execute("SELECT v FROM state WHERE k = 'halt_seq'").fetchone()[0]

    def close(self) -> None:
        self._db.close()


def _positive_int(value: object, limit: int) -> bool:
    return type(value) is int and 0 < value <= limit


class OrderGateway:
    def __init__(self, *, plugin_id: str, book: PaperBook, core: CoreClient, caps: Caps,
                 currency: str = "USD") -> None:
        if type(caps) is not Caps:
            raise Refused("invalid_cap")
        self._plugin_id = plugin_id
        self._book = book
        self._core = core
        self._caps = caps
        self._currency = currency

    def halt(self) -> int:
        return self._book.halt()

    def cancel_all(self) -> int:
        return self._book.cancel_all()

    def risk_pass(self, intent: object) -> dict:
        """Deterministic checks. Returns the exact effect the operator must approve."""
        if type(intent) is not Intent:
            raise Refused("intent_invalid")
        if self._book.halted():
            raise Refused("order_halted")
        if intent.mode != "paper" or intent.venue != PAPER_VENUE:
            raise Refused("paper_only")
        for value in (intent.intent_id, intent.account, intent.run_id, intent.connection):
            if type(value) is not str or not _ID.fullmatch(value):
                raise Refused("intent_invalid")
        if type(intent.instrument) is not str or not _INSTRUMENT.fullmatch(intent.instrument):
            raise Refused("instrument_invalid")
        if intent.side not in ("buy", "sell"):
            raise Refused("side_invalid")
        if not _positive_int(intent.quantity, MAX_INT) or not _positive_int(intent.limit_price_minor, MAX_INT):
            raise Refused("money_invalid")
        if intent.quantity > self._caps.max_quantity:
            raise Refused("size_cap_quantity")
        notional = intent.quantity * intent.limit_price_minor
        if notional > self._caps.max_notional_minor:
            raise Refused("size_cap_notional")
        return {
            "schema": "csl-effect/vNext", "kind": "order.submit", "plugin_id": self._plugin_id,
            "run_id": intent.run_id, "connection": intent.connection,
            "scope": {"account": intent.account, "venue": PAPER_VENUE},
            "currency": self._currency, "amount_minor": notional,
            "payload": {"intent_id": intent.intent_id, "instrument": intent.instrument,
                        "side": intent.side, "quantity": intent.quantity,
                        "limit_price_minor": intent.limit_price_minor, "mode": "paper"},
        }

    def _audit_base(self, intent: object, effect: dict | None) -> dict:
        def field(name: str) -> str | None:
            value = getattr(intent, name, None) if type(intent) is Intent else None
            return value if type(value) is str and _ID.fullmatch(value) else None
        digest = hashlib.sha256(canonical(effect)).hexdigest() if effect is not None else None
        return {"plugin_id": self._plugin_id, "intent_id": field("intent_id"), "run_id": field("run_id"),
                "connection": field("connection"), "effect_sha256": digest}

    def _refuse(self, exc: Refused, stage: str, intent: object, effect: dict | None) -> Refused:
        """Audit a refusal before it is raised. The refusal stands even if the audit write fails."""
        try:
            exc.audit_seq = self._book.record_refusal(dict(self._audit_base(intent, effect), stage=stage,
                                                           code=exc.code))
        except Exception:  # noqa: BLE001 - an audit failure never turns a refusal into a pass
            exc.audit_seq = None
        return exc

    def request_approval(self, intent: Intent) -> dict:
        """Risk pass, then ask the core to draw the approval for exactly this effect."""
        effect = None
        try:
            effect = self.risk_pass(intent)
            try:
                return self._core.request_approval(effect)
            except CoreRefused as exc:
                raise Refused("core:" + exc.code) from None
        except Refused as exc:
            raise self._refuse(exc, "request" if effect is not None else "risk", intent, effect) from None

    def submit(self, intent: Intent, approval_id: str, authority: dict | None) -> dict:
        effect = None
        stage = "risk"
        try:
            effect = self.risk_pass(intent)
            stage = "authorize"
            try:
                core = self._core.authorize(effect, approval_id, authority)
            except CoreRefused as exc:
                raise Refused("core:" + exc.code) from None
            stage = "book"
            seq = self._book.book(intent, self._audit_base(intent, effect))
        except Refused as exc:
            raise self._refuse(exc, stage, intent, effect) from None
        return {"status": "paper_working", "intent_id": intent.intent_id, "seq": seq,
                "notional_minor": effect["amount_minor"], "digest": core.get("digest")}
