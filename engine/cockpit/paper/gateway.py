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

Step 3 is the charge. Every local refusal that does not need the core (steps 1 and 2,
and a duplicate intent id) is checked before it. An intent id already in the book is
refused at ``request_approval`` too, before an approval is drawn. If the booking still
refuses after a successful charge (a halt or the same intent committed during the core
call), ``submit`` raises ``Refused`` with code ``leftover_charge`` and the booking refusal
in ``.cause``. Any other booking exception after the charge (a locked book, say) is a
``leftover_charge`` as well, with ``.cause`` ``booking_error:<Class>``. Nothing is booked
and the charge is not reversed: the core exposes no reverse.

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
``create_book`` makes) with ``book_invalid``. After ``PaperBook.close`` every book method,
and so every gateway call that reaches the book, refuses with ``book_closed``; ``close``
takes the book's lock, so it never closes the connection under a statement in flight.

An ``Intent`` is paper only when its own instance holds ``venue`` and ``mode`` as the
exact ``str`` ``"paper"``. The dataclass defaults are not read: an instance with either
field missing or deleted is refused ``paper_only`` before any side effect.

Paper only. There is no broker, venue transport, credential or network code here, and
no Kalshi order code at all (not even demo). Risk logic uses no model, I/O beyond the
local book, or subprocess. The cap values are injected; they are not sourced yet (F12).
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from dataclasses import dataclass

from .core_client import MAX_INT, CoreClient, CoreRefused

__all__ = ["Caps", "Intent", "OrderGateway", "PaperBook", "Refused", "create_book"]

PAPER_VENUE = "paper"
# The rest of the five-field scope the core's ``check_effect`` requires and the installer's
# authority profile must match exactly. Fixed here: this gateway has one adapter (the local
# paper book), one environment, and does not classify instruments into contract families.
PAPER_CONTRACT_FAMILY = "unclassified"
PAPER_ADAPTER = "paper-book"
PAPER_ENVIRONMENT = "paper"
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_INSTRUMENT = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
_GATEWAY_ONLY = object()  # private mint key of ``_Grant``; on its own it books nothing
_STATE_COLUMNS = [("k", "TEXT"), ("v", "INTEGER")]
_STATE_ROWS = [("halt_seq", "integer"), ("halted", "integer"), ("seq", "integer")]
_ORDER_COLUMNS = [("intent_id", "TEXT"), ("seq", "INTEGER"), ("account", "TEXT"), ("instrument", "TEXT"),
                  ("side", "TEXT"), ("quantity", "INTEGER"), ("limit_price_minor", "INTEGER"),
                  ("status", "TEXT")]


class Refused(Exception):
    """``cause`` is set only on ``leftover_charge``: the booking refusal that followed the charge,
    or ``booking_error:<Class>`` for any other booking exception."""

    def __init__(self, code: str, cause: str | None = None) -> None:
        super().__init__(code if cause is None else code + ":" + cause)
        self.code = code
        self.cause = cause


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


def _check_book_schema(db: sqlite3.Connection) -> None:
    """Refuse a file that does not hold the tables, columns and state rows ``create_book`` makes."""
    def columns(table: str) -> list[tuple]:
        return [(row[1], row[2]) for row in db.execute("PRAGMA table_info(%s)" % table)]
    if columns("state") != _STATE_COLUMNS or columns("orders") != _ORDER_COLUMNS:
        raise Refused("book_invalid")
    rows = db.execute("SELECT k, typeof(v) FROM state WHERE k IN ('halted', 'seq', 'halt_seq')"
                      " ORDER BY k").fetchall()
    if rows != _STATE_ROWS:
        raise Refused("book_invalid")


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
        except Exception:  # noqa: BLE001 - not SQLite, or not a paper book: never a raw error later
            db.close()
            raise Refused("book_invalid") from None
        self._db = db
        self._lock = threading.Lock()
        self._closed = False

    def _open_db(self) -> sqlite3.Connection:
        """The connection, or ``book_closed``. Call only with ``self._lock`` held."""
        if self._closed:
            raise Refused("book_closed")
        return self._db

    def _tx(self, fn):
        with self._lock:
            self._open_db().execute("BEGIN IMMEDIATE")
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
            return bool(self._open_db().execute("SELECT v FROM state WHERE k = 'halted'").fetchone()[0])

    def _has_intent(self, intent_id: str) -> bool:
        with self._lock:
            return self._open_db().execute("SELECT 1 FROM orders WHERE intent_id = ?",
                                           (intent_id,)).fetchone() is not None

    def _book(self, intent: Intent, token: object) -> int:
        """Write a working order. Not a public API: ``token`` must be the unused ``_Grant`` that
        ``OrderGateway.submit`` minted for this book and this intent object, after the risk pass
        and the core's checks (PRODUCT.md 4.2 "One way in"). The grant is spent here, and the
        gateway's risk pass and caps are run again before anything is written; a refusal from
        that re-check is raised as it is. A grant can be forged in-process; see the module doc."""
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
            seq = self._next_seq(db)
            db.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, 'working')",
                       (intent.intent_id, seq, intent.account, intent.instrument, intent.side,
                        intent.quantity, intent.limit_price_minor))
            return seq
        return self._tx(run)

    def cancel_all(self) -> int:
        """Cancel every working paper order. Allowed while halted (risk-reducing)."""
        return self._tx(lambda db: db.execute(
            "UPDATE orders SET status = 'cancelled' WHERE status = 'working'").rowcount)

    def working(self) -> int:
        """How many paper orders are still working."""
        with self._lock:
            return self._open_db().execute("SELECT COUNT(*) FROM orders WHERE status = 'working'").fetchone()[0]

    def orders(self) -> list[tuple]:
        with self._lock:
            return self._open_db().execute("SELECT intent_id, seq, status FROM orders ORDER BY seq").fetchall()

    def halt_seq(self) -> int:
        with self._lock:
            return self._open_db().execute("SELECT v FROM state WHERE k = 'halt_seq'").fetchone()[0]

    def close(self) -> None:
        """Close the book. Waits for a statement in flight; every later call refuses ``book_closed``."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._db.close()


def _positive_int(value: object, limit: int) -> bool:
    return type(value) is int and 0 < value <= limit


def _own_field(intent: Intent, name: str) -> object:
    """Read ``venue`` or ``mode`` from the intent's own instance dict, never the dataclass
    default: an instance without the field has not said paper, so it is refused ``paper_only``."""
    try:
        own = object.__getattribute__(intent, "__dict__")
    except AttributeError:
        raise Refused("paper_only") from None
    if type(own) is not dict or name not in own:
        raise Refused("paper_only")
    return dict.__getitem__(own, name)


def _field(intent: Intent, name: str) -> object:
    """Read one intent field other than ``venue`` and ``mode`` (those go through ``_own_field``).
    An ``Intent`` with the field deleted is refused ``intent_invalid``, never an ``AttributeError``."""
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
        # The kill switch composed on this gateway's book (``plugin.compose`` sets it), so a
        # host that holds only the gateway can still kill.
        self.kill_switch = None

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
        # Exact ``str`` only: a subclass or an object with rigged ``__eq__``/``__ne__`` is not paper.
        mode, venue = _own_field(intent, "mode"), _own_field(intent, "venue")
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

    def request_approval(self, intent: Intent) -> dict:
        """Risk pass, then ask the core to draw the approval for exactly this effect."""
        effect = self.risk_pass(intent)
        # A booked intent id draws no new approval. Only the book is asked: no approval state here.
        if self._book._has_intent(intent.intent_id):
            raise Refused("intent_duplicate")
        try:
            return self._core.request_approval(effect)
        except CoreRefused as exc:
            raise Refused("core:" + exc.code) from None

    def submit(self, intent: Intent, approval_id: str, authority: dict | None) -> dict:
        effect = self.risk_pass(intent)
        # Every local refusal that does not need the core comes before the charge.
        if self._book._has_intent(intent.intent_id):
            raise Refused("intent_duplicate")
        try:
            core = self._core.authorize(effect, approval_id, authority)
        except CoreRefused as exc:
            raise Refused("core:" + exc.code) from None
        try:
            grant = _Grant(_GATEWAY_ONLY, gateway=self, book=self._book, intent=intent, effect=effect)
            seq = self._book._book(intent, grant)
        except Refused as exc:
            # The core has charged and nothing was booked (a halt or the same intent committed
            # during the core call, or the risk pass re-run at booking refused). There is no
            # reverse: say so, never a clean refusal.
            raise Refused("leftover_charge", exc.code) from None
        except Exception as exc:
            raise Refused("leftover_charge", "booking_error:" + type(exc).__name__) from exc
        return {"status": "paper_working", "intent_id": intent.intent_id, "seq": seq,
                "notional_minor": effect["amount_minor"], "digest": core.get("digest")}
