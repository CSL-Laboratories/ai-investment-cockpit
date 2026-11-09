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
fits. At send time the gateway reads the same tracked cash again, inside the booking's
``BEGIN IMMEDIATE`` transaction, and refuses a buy over it with ``cash_insufficient``. It
never clamps or resizes. A booked buy reserves its notional; a booked sell reserves its
held shares. A sell of held shares is allowed at any book size, including below the
$2,000 equity floor that frozen ``app/`` enforces; there is no buy floor either
(PRODUCT.md 4.2, "Size cap in the new engine"; O47).

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
        self._lock = threading.Lock()
        if not self._db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit'").fetchone():
            self._db.close()
            raise Refused("book_without_audit")
        have = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"cash", "holdings"} <= have:
            self._db.close()
            raise Refused("book_without_cash")

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

    def _book(self, intent: Intent, token: object, audit: dict | None = None) -> int:
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
                                                           code=exc.code))
        except Exception:  # noqa: BLE001 - an audit failure never turns a refusal into a pass
            exc.audit_seq = None
        return exc

    def request_approval(self, intent: Intent) -> dict:
        """Risk pass, then ask the core to draw the approval for exactly this effect."""
        effect = None
        stage = "risk"
        try:
            effect = self.risk_pass(intent)
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
            stage = "funds"
            self._book.check_funds(intent)  # early read; the booking re-checks in its own transaction
            stage = "authorize"
            try:
                core = self._core.authorize(effect, approval_id, authority)
            except CoreRefused as exc:
                raise Refused("core:" + exc.code) from None
            stage = "book"
            seq = self._book._book(intent, _GATEWAY_ONLY, self._audit_base(intent, effect))
        except Refused as exc:
            raise self._refuse(exc, stage, intent, effect) from None
        return {"status": "paper_working", "intent_id": intent.intent_id, "seq": seq,
                "notional_minor": effect["amount_minor"], "digest": core.get("digest")}
