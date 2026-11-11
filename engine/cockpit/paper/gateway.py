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
and the charge is not reversed: the core exposes no reverse. A retry needs a new approval
and will charge again. The audit stage ``book`` alone does not say whether the core
charged; only a ``leftover_charge`` code does.

``PaperBook`` has no public booking method. Its private ``_book`` refuses unless handed a
one-shot ``_Grant`` that ``OrderGateway.submit`` mints after the risk pass and the core's
authorize, bound to that gateway, this book, that intent object and the approved effect.
At booking, ``_book`` runs that gateway's risk pass again (halt and caps included) and
refuses unless it returns exactly the granted effect. The module attribute
``_GATEWAY_ONLY`` is only the grant's mint key: handing it to ``_book`` books nothing.

In-process Python cannot make a capability unforgeable: code in this process can still
reach ``_Grant`` and its mint key and forge a grant, and a forged grant does not prove
the core's approval. What this closes is the import-the-token path, and any grant,
forged or not, meets a gateway's risk pass and caps again at booking (a forger can
still name a gateway it built with its own caps). A static test refuses any reference
to ``_book`` or ``_GATEWAY_ONLY`` elsewhere in ``engine/``.

``PaperBook(path)`` refuses a missing path with ``book_missing`` and a file that is not a
paper book (zero bytes, not SQLite, or without the tables, columns and state rows that
``create_book`` makes) with ``book_invalid``.

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
# The rest of the five-field scope the core's ``check_effect`` requires and the installer's
# authority profile must match exactly. Fixed here: this gateway has one adapter (the local
# paper book), one environment, and does not classify instruments into contract families.
PAPER_CONTRACT_FAMILY = "unclassified"
PAPER_ADAPTER = "paper-book"
PAPER_ENVIRONMENT = "paper"
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_INSTRUMENT = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
CONVICTION_FULL_BPS = 10_000  # conviction is an integer in basis points, 1..10_000
_GATEWAY_ONLY = object()  # private mint key of ``_Grant``; on its own it books nothing


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
# Every table of a paper book. ``create_book`` runs exactly these and the schema check on
# open derives its expected columns from them, so the two cannot drift.
_TABLE_SCHEMAS = (
    "CREATE TABLE state (k TEXT PRIMARY KEY, v INTEGER NOT NULL)",
    "CREATE TABLE orders (intent_id TEXT PRIMARY KEY, seq INTEGER NOT NULL,"
    " account TEXT NOT NULL, instrument TEXT NOT NULL, side TEXT NOT NULL,"
    " quantity INTEGER NOT NULL, limit_price_minor INTEGER NOT NULL,"
    " status TEXT NOT NULL)",
    AUDIT_SCHEMA,
    "CREATE TABLE cash (account TEXT PRIMARY KEY,"
    " available_minor INTEGER NOT NULL CHECK (available_minor >= 0))",
    "CREATE TABLE holdings (account TEXT NOT NULL, instrument TEXT NOT NULL,"
    " available INTEGER NOT NULL CHECK (available >= 0), PRIMARY KEY (account, instrument))",
)
_STATE_KEYS = ("halted", "seq", "halt_seq")  # every row of ``state``; each starts at integer 0


class _Grant:
    """One-shot booking grant, minted only by ``OrderGateway.submit``. Not exported.

    Not unforgeable (nothing in-process is): ``PaperBook._book`` re-runs the named
    gateway's risk pass and caps before it books on any grant.
    """

    __slots__ = ("gateway", "book", "intent", "effect", "used")

    def __init__(self, key: object, *, gateway: object, book: object, intent: object, effect: object) -> None:
        if key is not _GATEWAY_ONLY:
            raise Refused("book_direct_refused")
        self.gateway = gateway
        self.book = book
        self.intent = intent
        self.effect = effect
        self.used = False


def _table_columns(db: sqlite3.Connection, table: str) -> list[tuple]:
    return [(row[1], row[2]) for row in db.execute("PRAGMA table_info(%s)" % table)]


def _schema_columns() -> dict:
    """Each book table's ``(name, type)`` columns, in order, read back from ``_TABLE_SCHEMAS``."""
    db = sqlite3.connect(":memory:")
    try:
        for schema in _TABLE_SCHEMAS:
            db.execute(schema)
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {table: _table_columns(db, table) for table in tables}
    finally:
        db.close()


_BOOK_COLUMNS = _schema_columns()
_STATE_ROWS = sorted((key, "integer") for key in _STATE_KEYS)


def _check_book_schema(db: sqlite3.Connection) -> None:
    """Refuse a file that does not hold the tables, columns and state rows ``create_book`` makes.

    A file without the core book (``state`` and ``orders`` exactly as ``create_book`` makes
    them, with every state row) is ``book_invalid``. A core book from before the audit
    table (B15) is ``book_without_audit``; one from before the cash tables (B31) is
    ``book_without_cash``. Any other column difference is ``book_invalid``."""
    for table in ("state", "orders"):
        if _table_columns(db, table) != _BOOK_COLUMNS[table]:
            raise Refused("book_invalid")
    rows = db.execute("SELECT k, typeof(v) FROM state WHERE k IN (%s) ORDER BY k"
                      % ", ".join("?" * len(_STATE_KEYS)), _STATE_KEYS).fetchall()
    if rows != _STATE_ROWS:
        raise Refused("book_invalid")
    have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "audit" not in have:
        raise Refused("book_without_audit")
    if not {"cash", "holdings"} <= have:
        raise Refused("book_without_cash")
    for table, expected in _BOOK_COLUMNS.items():
        if _table_columns(db, table) != expected:
            raise Refused("book_invalid")
    if [name for name, _ in _table_columns(db, "audit")] != list(AUDIT_COLUMNS):
        raise Refused("book_invalid")
    rows = db.execute("SELECT k, typeof(v) FROM state WHERE k IN (%s) ORDER BY k"
                      % ", ".join("?" * len(_STATE_KEYS)), _STATE_KEYS).fetchall()
    if rows != _STATE_ROWS:
        raise Refused("book_invalid")


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
        for schema in _TABLE_SCHEMAS:
            db.execute(schema)
        db.executemany("INSERT INTO state VALUES (?, 0)", [(key,) for key in _STATE_KEYS])
        db.execute("CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit"
                   " BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END")
        db.execute("CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit"
                   " BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END")
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
        try:
            db = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
        except sqlite3.DatabaseError:
            raise Refused("book_invalid") from None
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=30000")
            _check_book_schema(db)
        except Refused:
            db.close()
            raise
        except Exception:  # noqa: BLE001 - not SQLite, or not a paper book: never a raw error later
            db.close()
            raise Refused("book_invalid") from None
        self._db = db
        self._lock = threading.Lock()

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

    def _has_intent(self, intent_id: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM orders WHERE intent_id = ?", (intent_id,)).fetchone() is not None

    def _book(self, intent: Intent, token: object, audit: dict | None = None) -> int:
        """Write a working order. Not a public API: ``token`` must be the unused ``_Grant`` that
        ``OrderGateway.submit`` minted for this book and this intent object, after the risk pass,
        the funds check and the core's checks (PRODUCT.md 4.2 "One way in"). The grant is spent
        here, and the gateway's risk pass and caps are run again before anything is written; a
        refusal from that re-check is raised as it is. A grant can be forged in-process; see the
        module doc."""
        if type(token) is not _Grant:
            raise Refused("book_direct_refused")
        with self._lock:
            if token.used:
                raise Refused("book_direct_refused")
            token.used = True  # one-shot: spent before the booking, whatever follows
        gateway = token.gateway
        if (token.book is not self or token.intent is not intent or type(gateway) is not OrderGateway
                or gateway._book is not self):
            raise Refused("book_direct_refused")
        if gateway.risk_pass(intent) != token.effect:
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


def _field(intent: Intent, name: str) -> object:
    """Read one intent field. An ``Intent`` made without ``__init__``, or with a field deleted,
    is refused ``intent_invalid``, never an ``AttributeError``."""
    try:
        return object.__getattribute__(intent, name)
    except AttributeError:
        raise Refused("intent_invalid") from None


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
        # Exact ``str`` only: a subclass or an object with rigged ``__eq__``/``__ne__`` is not paper.
        mode, venue = _field(intent, "mode"), _field(intent, "venue")
        if type(mode) is not str or mode != "paper" or type(venue) is not str or venue != PAPER_VENUE:
            raise Refused("paper_only")
        intent_id, account = _field(intent, "intent_id"), _field(intent, "account")
        run_id, connection = _field(intent, "run_id"), _field(intent, "connection")
        for value in (intent_id, account, run_id, connection):
            if type(value) is not str or not _ID.fullmatch(value):
                raise Refused("intent_invalid")
        instrument = _field(intent, "instrument")
        if type(instrument) is not str or not _INSTRUMENT.fullmatch(instrument):
            raise Refused("instrument_invalid")
        side = _field(intent, "side")
        if type(side) is not str or side not in ("buy", "sell"):
            raise Refused("side_invalid")
        quantity, limit_price_minor = _field(intent, "quantity"), _field(intent, "limit_price_minor")
        if not _positive_int(quantity, MAX_INT) or not _positive_int(limit_price_minor, MAX_INT):
            raise Refused("money_invalid")
        if quantity > self._caps.max_quantity:
            raise Refused("size_cap_quantity")
        notional = quantity * limit_price_minor
        if notional > self._caps.max_notional_minor:
            raise Refused("size_cap_notional")
        return {
            "schema": "csl-effect/vNext", "kind": "order.submit", "plugin_id": self._plugin_id,
            "run_id": run_id, "connection": connection,
            "scope": {"account": account, "venue": PAPER_VENUE, "contract_family": PAPER_CONTRACT_FAMILY,
                      "adapter": PAPER_ADAPTER, "environment": PAPER_ENVIRONMENT},
            "currency": self._currency, "amount_minor": notional,
            "payload": {"intent_id": intent_id, "instrument": instrument, "side": side,
                        "quantity": quantity, "limit_price_minor": limit_price_minor, "mode": "paper"},
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
            stage = "funds"
            self._book.check_funds(intent)  # early read; the booking re-checks in its own transaction
            stage = "authorize"
            try:
                core = self._core.authorize(effect, approval_id, authority)
            except CoreRefused as exc:
                raise Refused("core:" + exc.code) from None
            stage = "book"
            audit = self._audit_base(intent, effect)
            try:
                # The grant carries exactly the effect the risk pass returned and the core charged.
                grant = _Grant(_GATEWAY_ONLY, gateway=self, book=self._book, intent=intent, effect=effect)
                seq = self._book._book(intent, grant, audit)
            except Refused as exc:
                # The core has charged and nothing was booked. Any booking refusal lands here, with
                # its code as the cause (a halt, the same intent or a funds change during the core
                # call, or the risk pass re-run at booking refused, for example). There is no
                # reverse: say so, never a clean refusal.
                raise Refused("leftover_charge", exc.code) from None
            except Exception as exc:
                raise Refused("leftover_charge", "booking_error:" + type(exc).__name__) from exc
        except Refused as exc:
            # ``from exc.__cause__`` keeps a booking error's cause; every other refusal has none.
            raise self._refuse(exc, stage, intent, effect) from exc.__cause__
        return {"status": "paper_working", "intent_id": intent.intent_id, "seq": seq,
                "notional_minor": effect["amount_minor"], "digest": core.get("digest")}
