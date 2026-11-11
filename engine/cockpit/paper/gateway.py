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

Every refusal on ``size_buy``, ``request_approval`` and ``submit`` is written to the book's append-only
``audit`` table (stage, typed code, intent, run, connection, effect digest) before the
typed ``Refused`` is raised, and every booking writes a ``booked`` row in the booking's
own transaction (B15). An intent whose run ended in a fault, or whose connection the
engine breaker tripped, is refused here with ``core:run_faulted`` or
``core:connection_tripped`` (PRODUCT.md 4.2, K1).

Cash (B31, Lucas t1414u: "size to cash, refuse at the gateway"). The book tracks each
account's available cash and held shares. ``OrderGateway.size_buy`` turns a conviction
into a buy and caps the share count at available cash, so the approved order already
fits. The gateway reads the same tracked cash again before the charge and refuses a buy
over it with ``cash_insufficient``. At send time it reads it once more, inside the
booking's ``BEGIN IMMEDIATE`` transaction; a buy over it there follows the charge, so it
is refused with ``leftover_charge`` and cause ``cash_insufficient`` (see below). It never
clamps or resizes. A booked buy reserves its notional; a booked sell reserves its
held shares. A sell of held shares is allowed at any book size, including below the
$2,000 equity floor that frozen ``app/`` enforces; there is no buy floor either
(PRODUCT.md 4.2, "Size cap in the new engine"; O47).

Step 3 is the charge. Every local refusal that does not need the core (steps 1 and 2,
a duplicate intent id, and the early funds read) is checked before it. An intent id
already in the book is refused at ``request_approval`` too, before an approval is drawn.
Any booking refusal after a successful charge, whatever its code, makes ``submit`` raise
``Refused`` with code ``leftover_charge`` and that refusal's code in ``.cause`` (for example
``order_halted``, ``intent_duplicate``, ``cash_insufficient``, ``holding_insufficient`` or
``account_unknown``, when a halt, the same intent or a funds change lands during the core
call). Any other booking exception after the charge (a locked book, say) is a
``leftover_charge`` as well, with ``.cause`` ``booking_error:<Class>``. Nothing is booked
and the charge is not reversed: the core exposes no reverse. The effect's SHA-256 is an
idempotency key in the book's ``idempotency`` table, written with ``INSERT`` / ``UPDATE``
inside an explicit ``BEGIN IMMEDIATE`` (never ``REPLACE``, and not on sqlite3's default
connection, whose implicit transaction rolls back if the process is killed). A retry of
that effect does not call the core again. If the charge row committed and the booking did
not, the retry finishes the booking. If the process died during the core call, the row
stays ``armed`` and the retry is ``charge_uncertain``. A charge row cannot be deleted, and
a replace that reuses its key or its intent is refused. An explicit seq of -1 is the same
sentinel an omitted integer key shows, so each append-only table also refuses a seq below
1 after the insert. A run fault the core has already
refused is stored in ``fault_state`` in the same transaction as its audit row, so a
restarted core does not forget it. The audit stage ``book`` alone does not say whether
the core charged; a ``leftover_charge`` code, or a committed idempotency row, does.

``PaperBook`` has no public booking method: its private ``_book`` refuses unless handed a
capability object that only ``OrderGateway.submit`` passes, and a static test refuses any
other reference to either name in ``engine/``.

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

__all__ = ["CONVICTION_FULL_BPS", "Caps", "Intent", "OrderGateway", "PaperBook", "Refused", "create_book"]

PAPER_VENUE = "paper"
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_INSTRUMENT = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
CONVICTION_FULL_BPS = 10_000  # conviction is an integer in basis points, 1..10_000
_GATEWAY_ONLY = object()  # booking capability; never exported or passed outside this module


class Refused(Exception):
    """``cause`` is set only on ``leftover_charge``: the code of whichever booking refusal followed
    the charge, or ``booking_error:<Class>`` for any other booking exception."""

    def __init__(self, code: str, cause: str | None = None) -> None:
        super().__init__(code if cause is None else code + ":" + cause)
        self.code = code
        self.cause = cause
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
# SQLite shows an unassigned INTEGER PRIMARY KEY as -1 inside BEFORE INSERT, so an explicit
# -1 takes the same path as an omitted seq. Do not write the guard as `IS NOT NULL`: NEW.seq
# is -1, not NULL, and that would refuse every ordinary insert. The AFTER INSERT backstop
# rejects any assigned seq below 1. recursive_triggers defaults off, so a BEFORE DELETE
# trigger does not fire for that REPLACE.
AUDIT_NO_EXPLICIT = ("CREATE TRIGGER audit_no_explicit_seq BEFORE INSERT ON audit"
                     " WHEN NEW.seq != -1 BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END")
AUDIT_NO_NONPOSITIVE = ("CREATE TRIGGER audit_no_nonpositive_seq AFTER INSERT ON audit"
                        " WHEN NEW.seq < 1 BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END")
FAULT_SCHEMA = ("CREATE TABLE fault_state (kind TEXT NOT NULL CHECK (kind = 'run'),"
                " subject TEXT NOT NULL, code TEXT NOT NULL, PRIMARY KEY (kind, subject))")
FAULT_SEEN_SCHEMA = ("CREATE TABLE fault_seen (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL,"
                     " subject TEXT NOT NULL)")
FAULT_NO_UPDATE = ("CREATE TRIGGER fault_state_no_update BEFORE UPDATE ON fault_state"
                   " BEGIN SELECT RAISE(ABORT, 'fault state is append-only'); END")
FAULT_NO_DELETE = ("CREATE TRIGGER fault_state_no_delete BEFORE DELETE ON fault_state"
                   " BEGIN SELECT RAISE(ABORT, 'fault state is append-only'); END")
FAULT_NO_REPLACE = ("CREATE TRIGGER fault_state_no_replace BEFORE INSERT ON fault_state"
                    " WHEN EXISTS (SELECT 1 FROM fault_seen WHERE kind = NEW.kind AND subject = NEW.subject)"
                    " BEGIN SELECT RAISE(ABORT, 'fault state is append-only'); END")
FAULT_REMEMBER = ("CREATE TRIGGER fault_state_remember AFTER INSERT ON fault_state"
                  " BEGIN INSERT INTO fault_seen (kind, subject) VALUES (NEW.kind, NEW.subject); END")
FAULT_SEEN_NO_EXPLICIT = ("CREATE TRIGGER fault_seen_no_explicit_seq BEFORE INSERT ON fault_seen"
                          " WHEN NEW.seq != -1"
                          " BEGIN SELECT RAISE(ABORT, 'fault state is append-only'); END")
FAULT_SEEN_NO_UPDATE = ("CREATE TRIGGER fault_seen_no_update BEFORE UPDATE ON fault_seen"
                        " BEGIN SELECT RAISE(ABORT, 'fault state is append-only'); END")
FAULT_SEEN_NO_DELETE = ("CREATE TRIGGER fault_seen_no_delete BEFORE DELETE ON fault_seen"
                        " BEGIN SELECT RAISE(ABORT, 'fault state is append-only'); END")
FAULT_SEEN_NO_NONPOSITIVE = ("CREATE TRIGGER fault_seen_no_nonpositive_seq AFTER INSERT ON fault_seen"
                             " WHEN NEW.seq < 1"
                             " BEGIN SELECT RAISE(ABORT, 'fault state is append-only'); END")
IDEMPOTENCY_SCHEMA = ("CREATE TABLE idempotency (idempotency_key TEXT PRIMARY KEY,"
                      " intent_id TEXT NOT NULL UNIQUE, effect_sha256 TEXT NOT NULL, approval_id TEXT,"
                      " status TEXT NOT NULL CHECK (status IN ('armed', 'charged', 'booked', 'void')),"
                      " order_seq INTEGER, notional_minor INTEGER, digest TEXT)")
IDEMPOTENCY_SEEN_SCHEMA = ("CREATE TABLE idempotency_seen (seq INTEGER PRIMARY KEY,"
                           " intent_id TEXT NOT NULL)")
IDEMPOTENCY_KEY_SEEN_SCHEMA = ("CREATE TABLE idempotency_key_seen (seq INTEGER PRIMARY KEY,"
                               " idempotency_key TEXT NOT NULL)")
IDEMPOTENCY_NO_REPLACE = ("CREATE TRIGGER idempotency_no_replace BEFORE INSERT ON idempotency"
                          " WHEN EXISTS (SELECT 1 FROM idempotency_seen WHERE intent_id = NEW.intent_id)"
                          " OR EXISTS (SELECT 1 FROM idempotency_key_seen"
                          " WHERE idempotency_key = NEW.idempotency_key)"
                          " BEGIN SELECT RAISE(ABORT, 'idempotency key is already recorded'); END")
IDEMPOTENCY_REMEMBER = ("CREATE TRIGGER idempotency_remember AFTER INSERT ON idempotency"
                        " BEGIN INSERT INTO idempotency_seen (intent_id) VALUES (NEW.intent_id); END")
IDEMPOTENCY_REMEMBER_KEY = ("CREATE TRIGGER idempotency_remember_key AFTER INSERT ON idempotency"
                            " BEGIN INSERT INTO idempotency_key_seen (idempotency_key)"
                            " VALUES (NEW.idempotency_key); END")
IDEMPOTENCY_REMEMBER_KEY_UPDATE = (
    "CREATE TRIGGER idempotency_remember_key_update AFTER UPDATE OF idempotency_key ON idempotency"
    " WHEN NOT EXISTS (SELECT 1 FROM idempotency_key_seen WHERE idempotency_key = NEW.idempotency_key)"
    " BEGIN INSERT INTO idempotency_key_seen (idempotency_key) VALUES (NEW.idempotency_key); END")
IDEMPOTENCY_NO_DELETE = ("CREATE TRIGGER idempotency_no_delete BEFORE DELETE ON idempotency"
                         " BEGIN SELECT RAISE(ABORT, 'idempotency key is already recorded'); END")
IDEMPOTENCY_SEEN_NO_EXPLICIT = ("CREATE TRIGGER idempotency_seen_no_explicit_seq BEFORE INSERT ON idempotency_seen"
                                " WHEN NEW.seq != -1"
                                " BEGIN SELECT RAISE(ABORT, 'idempotency key is already recorded'); END")
IDEMPOTENCY_SEEN_NO_NONPOSITIVE = (
    "CREATE TRIGGER idempotency_seen_no_nonpositive_seq AFTER INSERT ON idempotency_seen"
    " WHEN NEW.seq < 1 BEGIN SELECT RAISE(ABORT, 'idempotency key is already recorded'); END")
IDEMPOTENCY_SEEN_NO_UPDATE = ("CREATE TRIGGER idempotency_seen_no_update BEFORE UPDATE ON idempotency_seen"
                              " BEGIN SELECT RAISE(ABORT, 'idempotency key is already recorded'); END")
IDEMPOTENCY_SEEN_NO_DELETE = ("CREATE TRIGGER idempotency_seen_no_delete BEFORE DELETE ON idempotency_seen"
                              " BEGIN SELECT RAISE(ABORT, 'idempotency key is already recorded'); END")
IDEMPOTENCY_KEY_SEEN_NO_EXPLICIT = (
    "CREATE TRIGGER idempotency_key_seen_no_explicit_seq BEFORE INSERT ON idempotency_key_seen"
    " WHEN NEW.seq != -1 BEGIN SELECT RAISE(ABORT, 'idempotency key is already recorded'); END")
IDEMPOTENCY_KEY_SEEN_NO_NONPOSITIVE = (
    "CREATE TRIGGER idempotency_key_seen_no_nonpositive_seq AFTER INSERT ON idempotency_key_seen"
    " WHEN NEW.seq < 1 BEGIN SELECT RAISE(ABORT, 'idempotency key is already recorded'); END")
IDEMPOTENCY_KEY_SEEN_NO_UPDATE = (
    "CREATE TRIGGER idempotency_key_seen_no_update BEFORE UPDATE ON idempotency_key_seen"
    " BEGIN SELECT RAISE(ABORT, 'idempotency key is already recorded'); END")
IDEMPOTENCY_KEY_SEEN_NO_DELETE = (
    "CREATE TRIGGER idempotency_key_seen_no_delete BEFORE DELETE ON idempotency_key_seen"
    " BEGIN SELECT RAISE(ABORT, 'idempotency key is already recorded'); END")
_CRASH_TRIGGERS = (AUDIT_NO_EXPLICIT, AUDIT_NO_NONPOSITIVE, FAULT_NO_UPDATE, FAULT_NO_DELETE, FAULT_NO_REPLACE,
                   FAULT_REMEMBER, FAULT_SEEN_NO_EXPLICIT, FAULT_SEEN_NO_NONPOSITIVE, FAULT_SEEN_NO_UPDATE,
                   FAULT_SEEN_NO_DELETE, IDEMPOTENCY_NO_REPLACE, IDEMPOTENCY_NO_DELETE, IDEMPOTENCY_REMEMBER,
                   IDEMPOTENCY_REMEMBER_KEY, IDEMPOTENCY_REMEMBER_KEY_UPDATE, IDEMPOTENCY_SEEN_NO_EXPLICIT,
                   IDEMPOTENCY_SEEN_NO_NONPOSITIVE, IDEMPOTENCY_SEEN_NO_UPDATE, IDEMPOTENCY_SEEN_NO_DELETE,
                   IDEMPOTENCY_KEY_SEEN_NO_EXPLICIT, IDEMPOTENCY_KEY_SEEN_NO_NONPOSITIVE,
                   IDEMPOTENCY_KEY_SEEN_NO_UPDATE, IDEMPOTENCY_KEY_SEEN_NO_DELETE)


def create_book(path: str, *, cash_minor: dict, holdings: dict | None = None) -> None:
    """Create the paper book with each account's starting cash and held shares. Refuses if it exists.

    ``cash_minor`` maps account to integer minor units; ``holdings`` maps
    ``(account, instrument)`` to a share count. Both are validated before the file is made.
    """
    holdings = {} if holdings is None else holdings
    if type(cash_minor) is not dict or not cash_minor or type(holdings) is not dict:
        raise Refused("invalid_cash")
    for account, minor in cash_minor.items():
        if type(account) is not str or not _ID.fullmatch(account) or type(minor) is not int \
                or not 0 <= minor <= MAX_INT:
            raise Refused("invalid_cash")
    for key, shares in holdings.items():
        if type(key) is not tuple or len(key) != 2 or key[0] not in cash_minor \
                or type(key[1]) is not str or not _INSTRUMENT.fullmatch(key[1]) \
                or type(shares) is not int or not 0 <= shares <= MAX_INT:
            raise Refused("invalid_holding")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA recursive_triggers=ON")
        db.execute("BEGIN IMMEDIATE")  # one commit: a crash never leaves a book without its cash rows
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
        db.execute(AUDIT_NO_EXPLICIT)
        db.execute(FAULT_SCHEMA)
        db.execute(FAULT_SEEN_SCHEMA)
        db.execute(IDEMPOTENCY_SCHEMA)
        db.execute(IDEMPOTENCY_SEEN_SCHEMA)
        db.execute(IDEMPOTENCY_KEY_SEEN_SCHEMA)
        for statement in _CRASH_TRIGGERS:
            if statement != AUDIT_NO_EXPLICIT:
                db.execute(statement)
        db.execute("CREATE TABLE cash (account TEXT PRIMARY KEY,"
                   " available_minor INTEGER NOT NULL CHECK (available_minor >= 0))")
        db.execute("CREATE TABLE holdings (account TEXT NOT NULL, instrument TEXT NOT NULL,"
                   " available INTEGER NOT NULL CHECK (available >= 0), PRIMARY KEY (account, instrument))")
        db.executemany("INSERT INTO cash VALUES (?, ?)", sorted(cash_minor.items()))
        db.executemany("INSERT INTO holdings VALUES (?, ?, ?)",
                       sorted((a, i, n) for (a, i), n in holdings.items()))
        db.execute("COMMIT")
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
        self._db.execute("PRAGMA recursive_triggers=ON")
        self._lock = threading.Lock()
        if not self._db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit'").fetchone():
            self._db.close()
            raise Refused("book_without_audit")
        have = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"cash", "holdings"} <= have:
            self._db.close()
            raise Refused("book_without_cash")
        try:
            self._ensure_crash_tables()
        except BaseException:
            self._db.close()
            raise

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

    def _ensure_crash_tables(self) -> None:
        """Add the fault and idempotency guards to a book created before they existed."""
        def run(db):
            have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "fault_state" not in have:
                db.execute(FAULT_SCHEMA)
            if "fault_seen" not in have:
                db.execute(FAULT_SEEN_SCHEMA)
            if "idempotency" not in have:
                db.execute(IDEMPOTENCY_SCHEMA)
            if "idempotency_seen" not in have:
                db.execute(IDEMPOTENCY_SEEN_SCHEMA)
            if "idempotency_key_seen" not in have:
                db.execute(IDEMPOTENCY_KEY_SEEN_SCHEMA)
            # A book from the previous schema remembers intents only. Replace that guard so a
            # reuse of the charged key is refused too; the name stays, the body gains the key.
            stale = db.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger'"
                               " AND name = 'idempotency_no_replace'").fetchone()
            if stale is not None and "idempotency_key_seen" not in stale[0]:
                db.execute("DROP TRIGGER idempotency_no_replace")
            triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
            for statement in _CRASH_TRIGGERS:
                name = statement.split("CREATE TRIGGER ", 1)[1].split(" ", 1)[0]
                if name not in triggers:
                    db.execute(statement)
            db.execute("INSERT INTO idempotency_key_seen (idempotency_key) "
                       "SELECT idempotency_key FROM idempotency WHERE NOT EXISTS ("
                       "SELECT 1 FROM idempotency_key_seen"
                       " WHERE idempotency_key = idempotency.idempotency_key)")
        self._tx(run)

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
        # INSERT only. REPLACE deletes the prior row first, and a default sqlite3 connection
        # (isolation_level "") would roll the write back if the process were killed before commit.
        halt_seq = db.execute("SELECT v FROM state WHERE k = 'halt_seq'").fetchone()[0]
        cur = db.execute("INSERT INTO audit (event, stage, code, plugin_id, intent_id, run_id, connection,"
                         " effect_sha256, halt_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (row["event"], row["stage"], row.get("code"), row["plugin_id"],
                          row.get("intent_id"), row.get("run_id"), row.get("connection"),
                          row.get("effect_sha256"), halt_seq))
        return cur.lastrowid

    @staticmethod
    def _cash(db, account: str) -> int:
        row = db.execute("SELECT available_minor FROM cash WHERE account = ?", (account,)).fetchone()
        if row is None:
            raise Refused("account_unknown")
        return row[0]

    @staticmethod
    def _held(db, account: str, instrument: str) -> int:
        row = db.execute("SELECT available FROM holdings WHERE account = ? AND instrument = ?",
                         (account, instrument)).fetchone()
        return 0 if row is None else row[0]

    def available_cash_minor(self, account: str) -> int:
        """The tracked cash both the sizer and the send-time check read (B31)."""
        with self._lock:
            return self._cash(self._db, account)

    def held_shares(self, account: str, instrument: str) -> int:
        with self._lock:
            return self._held(self._db, account, instrument)

    @classmethod
    def _check_funds(cls, db, intent: Intent) -> None:
        """Refuse an order the account cannot cover. Never clamps or resizes (Q25, B31)."""
        if intent.side == "buy":
            if intent.quantity * intent.limit_price_minor > cls._cash(db, intent.account):
                raise Refused("cash_insufficient")
        else:
            cls._cash(db, intent.account)  # the account must exist
            if intent.quantity > cls._held(db, intent.account, intent.instrument):
                raise Refused("holding_insufficient")

    def check_funds(self, intent: Intent) -> None:
        with self._lock:
            self._check_funds(self._db, intent)

    def _has_intent(self, intent_id: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM orders WHERE intent_id = ?", (intent_id,)).fetchone() is not None

    def _book(self, intent: Intent, token: object, audit: dict | None = None, *,
              charge_key: str | None = None) -> int:
        """Write a working order. Not a public API: only ``OrderGateway.submit`` holds the token,
        after the risk pass, the funds check and the core's checks (PRODUCT.md 4.2 "One way in")."""
        if token is not _GATEWAY_ONLY:
            raise Refused("book_direct_refused")

        def run(db):
            if db.execute("SELECT v FROM state WHERE k = 'halted'").fetchone()[0]:
                raise Refused("order_halted")
            if db.execute("SELECT 1 FROM orders WHERE intent_id = ?", (intent.intent_id,)).fetchone():
                raise Refused("intent_duplicate")
            self._check_funds(db, intent)  # send-time cash check, same transaction as the booking
            if intent.side == "buy":
                db.execute("UPDATE cash SET available_minor = available_minor - ? WHERE account = ?",
                           (intent.quantity * intent.limit_price_minor, intent.account))
            else:
                db.execute("UPDATE holdings SET available = available - ? WHERE account = ? AND instrument = ?",
                           (intent.quantity, intent.account, intent.instrument))
            seq = self._next_seq(db)
            db.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, 'working')",
                       (intent.intent_id, seq, intent.account, intent.instrument, intent.side,
                        intent.quantity, intent.limit_price_minor))
            if audit is not None:
                self._audit_row(db, dict(audit, event="booked", stage="book", code=None))
            if charge_key is not None:
                # Same commit as the order. A crash before this COMMIT leaves status 'charged'.
                cur = db.execute("UPDATE idempotency SET status = 'booked', order_seq = ?"
                                 " WHERE idempotency_key = ? AND status = 'charged'",
                                 (seq, charge_key))
                if cur.rowcount != 1:
                    raise Refused("charge_state")
            return seq
        return self._tx(run)

    def _run_faulted(self, run_id: str) -> bool:
        def run(db):
            return db.execute("SELECT 1 FROM fault_state WHERE kind = 'run' AND subject = ?",
                              (run_id,)).fetchone() is not None
        return self._tx(run)

    def _lookup_charge(self, db, intent_id: str, key: str) -> str:
        row = db.execute("SELECT idempotency_key, effect_sha256, status FROM idempotency"
                         " WHERE intent_id = ?", (intent_id,)).fetchone()
        if row is None:
            other = db.execute("SELECT 1 FROM idempotency WHERE idempotency_key = ?", (key,)).fetchone()
            return "mismatch" if other is not None else "none"
        prev_key, prev_effect, status = row
        if prev_key != key or prev_effect != key:
            # A refused attempt never charged, so a different effect on this intent may proceed.
            # A charge that did arm or land stays bound to the effect that was sent.
            return "void" if status == "void" else "mismatch"
        if status == "armed":
            return "uncertain"
        if status == "void":
            return "void"
        if status in ("charged", "booked"):
            return "resume"
        return "uncertain"

    def _charge_state(self, intent_id: str, key: str) -> str:
        return self._tx(lambda db: self._lookup_charge(db, intent_id, key))

    def _arm_charge(self, intent_id: str, key: str, approval_id: str | None) -> str:
        """Commit ``armed`` before the core call. A concurrent insert loses and re-reads."""
        def run(db):
            state = self._lookup_charge(db, intent_id, key)
            if state == "none":
                db.execute("INSERT INTO idempotency (idempotency_key, intent_id, effect_sha256,"
                           " approval_id, status) VALUES (?, ?, ?, ?, 'armed')",
                           (key, intent_id, key, approval_id))
                return "go"
            if state == "void":
                cur = db.execute("UPDATE idempotency SET status = 'armed', approval_id = ?,"
                                 " idempotency_key = ?, effect_sha256 = ?"
                                 " WHERE intent_id = ? AND status = 'void'",
                                 (approval_id, key, key, intent_id))
                if cur.rowcount != 1:
                    return self._lookup_charge(db, intent_id, key)
                return "go"
            return state
        try:
            return self._tx(run)
        except sqlite3.IntegrityError:
            return self._charge_state(intent_id, key)

    def _void_charge(self, key: str) -> None:
        """The core refused before charging. A later approval of this effect may try again."""
        def run(db):
            db.execute("UPDATE idempotency SET status = 'void' WHERE idempotency_key = ?"
                       " AND status = 'armed'", (key,))
        self._tx(run)

    def _mark_charged(self, key: str, approval_id: str | None, digest: str | None, notional: int) -> None:
        def run(db):
            cur = db.execute("UPDATE idempotency SET status = 'charged', approval_id = ?, digest = ?,"
                             " notional_minor = ? WHERE idempotency_key = ? AND status = 'armed'",
                             (approval_id, digest, notional, key))
            if cur.rowcount != 1:
                raise Refused("charge_state")
        self._tx(run)

    def _charge_digest(self, key: str):
        def run(db):
            row = db.execute("SELECT digest FROM idempotency WHERE idempotency_key = ?", (key,)).fetchone()
            return None if row is None else row[0]
        return self._tx(run)

    def record_refusal(self, row: dict) -> int:
        """Append one refusal to the audit table in its own committed step. Returns its seq.

        A ``core:run_faulted`` row also records the run, in this same transaction, so a crash
        after the commit cannot drop the fault while keeping the audit line (or the reverse).
        """
        def run(db):
            seq = self._audit_row(db, dict(row, event="refused"))
            if row.get("code") == "core:run_faulted" and type(row.get("run_id")) is str:
                # A second refusal of the same run must not INSERT again: the append-only
                # trigger aborts a conflicting insert, and that would roll this audit row back.
                already = db.execute("SELECT 1 FROM fault_state WHERE kind = 'run' AND subject = ?",
                                     (row["run_id"],)).fetchone()
                if already is None:
                    db.execute("INSERT INTO fault_state (kind, subject, code)"
                               " VALUES ('run', ?, 'core:run_faulted')", (row["run_id"],))
            return seq
        return self._tx(run)

    def audit(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT " + ", ".join(AUDIT_COLUMNS) + " FROM audit ORDER BY seq").fetchall()
        return [dict(zip(AUDIT_COLUMNS, row)) for row in rows]

    def cancel_all(self) -> int:
        """Cancel every working paper order, releasing its reserved cash or shares.

        Allowed while halted (risk-reducing)."""
        def run(db):
            working = db.execute("SELECT account, instrument, side, quantity, limit_price_minor FROM orders"
                                 " WHERE status = 'working'").fetchall()
            for account, instrument, side, quantity, price in working:
                if side == "buy":
                    db.execute("UPDATE cash SET available_minor = available_minor + ? WHERE account = ?",
                               (quantity * price, account))
                else:
                    db.execute("INSERT INTO holdings VALUES (?, ?, ?) ON CONFLICT (account, instrument)"
                               " DO UPDATE SET available = available + excluded.available",
                               (account, instrument, quantity))
            return db.execute("UPDATE orders SET status = 'cancelled' WHERE status = 'working'").rowcount
        return self._tx(run)

    def working(self) -> int:
        """How many paper orders are still working."""
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM orders WHERE status = 'working'").fetchone()[0]

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

    def size_buy(self, *, intent_id: str, account: str, instrument: str, limit_price_minor: int,
                 conviction_bps: int, run_id: str, connection: str) -> Intent:
        """Turn a conviction into a paper buy, sized in code (Q30, O47) and capped at cash (B31).

        Shares are the largest whole count within the conviction's share of the notional cap,
        the quantity cap, and the account's tracked available cash. The returned intent
        already fits; the send-time check still refuses it if cash has since dropped.
        """
        try:
            if type(conviction_bps) is not int or not 0 < conviction_bps <= CONVICTION_FULL_BPS:
                raise Refused("conviction_invalid")
            if not _positive_int(limit_price_minor, MAX_INT):
                raise Refused("money_invalid")
            if type(account) is not str or not _ID.fullmatch(account):
                raise Refused("intent_invalid")
            cash = self._book.available_cash_minor(account)
            target_minor = self._caps.max_notional_minor * conviction_bps // CONVICTION_FULL_BPS
            shares = min(target_minor // limit_price_minor, self._caps.max_quantity, cash // limit_price_minor)
            if shares < 1:
                raise Refused("size_zero")
        except Refused as exc:
            proposal = Intent(intent_id=intent_id, account=account, instrument=instrument, side="buy",
                              quantity=None, limit_price_minor=limit_price_minor,
                              run_id=run_id, connection=connection)
            raise self._refuse(exc, "size", proposal, None) from None
        return Intent(intent_id=intent_id, account=account, instrument=instrument, side="buy", quantity=shares,
                      limit_price_minor=limit_price_minor, run_id=run_id, connection=connection)

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
                                                           code=str(exc)))  # a leftover charge keeps its cause
        except Exception:  # noqa: BLE001 - an audit failure never turns a refusal into a pass
            exc.audit_seq = None
        return exc

    def request_approval(self, intent: Intent) -> dict:
        """Risk pass, then ask the core to draw the approval for exactly this effect."""
        effect = None
        stage = "risk"
        try:
            effect = self.risk_pass(intent)
            stage = "request"
            # A booked intent id draws no new approval. Only the book is asked: no approval state here.
            if self._book._has_intent(intent.intent_id):
                raise Refused("intent_duplicate")
            if self._book._run_faulted(intent.run_id):
                raise Refused("core:run_faulted")
            stage = "funds"
            self._book.check_funds(intent)  # cash or held shares, so a doomed order is never approved
            stage = "request"
            try:
                return self._core.request_approval(effect)
            except CoreRefused as exc:
                raise Refused("core:" + exc.code) from None
        except Refused as exc:
            raise self._refuse(exc, stage, intent, effect) from None

    def submit(self, intent: Intent, approval_id: str, authority: dict | None) -> dict:
        effect = None
        stage = "risk"
        try:
            effect = self.risk_pass(intent)
            # Every local refusal that does not need the core comes before the charge.
            stage = "book"
            if self._book._has_intent(intent.intent_id):
                raise Refused("intent_duplicate")
            if self._book._run_faulted(intent.run_id):
                stage = "authorize"
                raise Refused("core:run_faulted")
            key = hashlib.sha256(canonical(effect)).hexdigest()
            approval = approval_id if type(approval_id) is str else None
            state = self._book._charge_state(intent.intent_id, key)
            if state == "mismatch":
                raise Refused("intent_duplicate")
            if state == "uncertain":
                stage = "authorize"
                raise Refused("charge_uncertain")
            core_digest = None
            charged_now = False
            if state in ("none", "void"):
                stage = "funds"
                self._book.check_funds(intent)  # early read; the booking re-checks in its own transaction
                stage = "authorize"
                state = self._book._arm_charge(intent.intent_id, key, approval)
                if state == "mismatch":
                    stage = "book"
                    raise Refused("intent_duplicate")
                if state == "uncertain":
                    raise Refused("charge_uncertain")
                if state == "go":
                    try:
                        core = self._core.authorize(effect, approval_id, authority)
                    except CoreRefused as exc:
                        self._book._void_charge(key)
                        raise Refused("core:" + exc.code) from None
                    digest = core.get("digest")
                    core_digest = digest if type(digest) is str else None
                    charged_now = True
            if state not in ("go", "resume"):
                stage = "authorize"
                raise Refused("charge_state")
            if core_digest is None:
                core_digest = self._book._charge_digest(key)
            stage = "book"
            audit = self._audit_base(intent, effect)
            try:
                if charged_now:
                    self._book._mark_charged(key, approval, core_digest, effect["amount_minor"])
                seq = self._book._book(intent, _GATEWAY_ONLY, audit, charge_key=key)
            except Refused as exc:
                # The core has charged and nothing was booked. Any booking refusal lands here, with
                # its code as the cause (a halt, the same intent or a funds change during the core
                # call, for example). There is no reverse: say so, never a clean refusal.
                raise Refused("leftover_charge", exc.code) from None
            except Exception as exc:
                raise Refused("leftover_charge", "booking_error:" + type(exc).__name__) from exc
        except Refused as exc:
            # ``from exc.__cause__`` keeps a booking error's cause; every other refusal has none.
            raise self._refuse(exc, stage, intent, effect) from exc.__cause__
        return {"status": "paper_working", "intent_id": intent.intent_id, "seq": seq,
                "notional_minor": effect["amount_minor"], "digest": core_digest}
