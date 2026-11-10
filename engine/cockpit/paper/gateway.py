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

``PaperBook`` has no public booking method: its private ``_book`` refuses unless handed a
capability object that only ``OrderGateway.submit`` passes, and a static test refuses any
other reference to either name in ``engine/``.

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
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_INSTRUMENT = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
_GATEWAY_ONLY = object()  # booking capability; never exported or passed outside this module


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
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA busy_timeout=30000")
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

    def _has_intent(self, intent_id: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM orders WHERE intent_id = ?", (intent_id,)).fetchone() is not None

    def _book(self, intent: Intent, token: object) -> int:
        """Write a working order. Not a public API: only ``OrderGateway.submit`` holds the token,
        after the risk pass and the core's checks (PRODUCT.md 4.2 "One way in")."""
        if token is not _GATEWAY_ONLY:
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
            seq = self._book._book(intent, _GATEWAY_ONLY)
        except Refused as exc:
            # The core has charged and nothing was booked (a halt or the same intent committed
            # during the core call). There is no reverse: say so, never a clean refusal.
            raise Refused("leftover_charge", exc.code) from None
        except Exception as exc:
            raise Refused("leftover_charge", "booking_error:" + type(exc).__name__) from exc
        return {"status": "paper_working", "intent_id": intent.intent_id, "seq": seq,
                "notional_minor": effect["amount_minor"], "digest": core.get("digest")}
