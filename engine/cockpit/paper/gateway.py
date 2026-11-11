"""The single paper ``OrderGateway`` (build-order item 9, Cockpit part). Escalate-list code.

The only path that books an order in ``engine/``. Every order goes, in this order:

1. halt check (new risk is refused while halted; cancels stay allowed, Q26);
2. the deterministic risk pass (paper only, integer money, the instrument allowlist, size
   and quantity caps, rejecting and never clamping, Q25), then the sizing check: a buy
   must be the unchanged intent this gateway's ``size_buy`` made;
3. the Recursion core over its protocol: permission, the operator's one-use approval of
   this exact effect, then the installer-signed authority profile and its budgets
   (PRODUCT.md 4.2, O11, K3);
4. the host's run check, outside the book's lock, then booking, in one SQLite
   ``BEGIN IMMEDIATE`` transaction that re-reads the halt flag and re-verifies the run check
   within a bound, so a halt, a run fault or a connection trip committed before the booking wins.

Every refusal on ``size_buy``, ``request_approval`` and ``submit`` is written to the book's append-only
``audit`` table (stage, typed code, intent, run, connection, effect digest) before the
typed ``Refused`` is raised, and every booking writes a ``booked`` row in the booking's
own transaction (B15). An intent whose run ended in a fault, or whose connection the
engine breaker tripped, is refused here with ``core:run_faulted`` or
``core:connection_tripped`` (PRODUCT.md 4.2, K1).

Run check at the booking point. The gateway reaches the core only over its transport, so
the host hands in ``run_check``: the core ``ConnectionBreaker``'s
``check(run_id, connection, plugin_id)``. It returns a dict for a healthy run and raises
an error carrying the core's typed ``code`` otherwise (``run_unknown``, ``run_not_owned``,
``run_connection_mismatch``, ``run_faulted``, ``connection_tripped``). The core runs the
same check inside ``effect.request`` and ``effect.authorize``; the gateway asks it twice
more at booking, with the run and connection of the approved effect and its own plugin id
(Lucas, Nov 11: "Fix it now: move the breaker check outside the lock and re-verify"):

1. the full check, in the calling thread, before the book's lock and its SQLite write lock
   are taken, so a check that hangs never keeps a halt or a kill on this book waiting
   (that submit just does not book while it hangs);
2. a re-verify inside the booking transaction, next to the halt re-read, waited on for at
   most ``RUN_RECHECK_TIMEOUT_S`` (the check runs on one of the gateway's own worker
   threads; at most ``_RUN_CHECK_WORKERS_MAX`` run at once per gateway, and when all of them
   are still hung ``submit`` is refused ``run_check_busy`` before the charge). The frozen
   breaker API offers only ``check()``,
   so the re-verify is that same call, bounded by the wait rather than by what it reads. It
   must answer exactly what step 1 answered: a fault, a trip or a different binding
   committed in between is refused with the core's code (``core:run_faulted``, say) or
   ``run_check_changed``, and a re-verify that does not answer in time is
   ``run_check_timeout`` (``run_check_busy`` when too many are already in flight). The
   book's locks are therefore held for at most that bound however the check behaves.

A refusal at either step follows the charge, so it is ``leftover_charge`` with that code as
the cause, audited at stage ``book``. An error without a typed code, or a result that is
not a dict with ``str`` keys, is ``run_check_error``. A ``KeyboardInterrupt`` or
``SystemExit`` raised by the check is raised again, never turned into a refusal. A gateway built without a run check
books nothing: ``request_approval`` and ``submit`` refuse ``run_check_missing`` before any
core call. A fault or trip committed after the re-verify and before the booking's commit
is not seen.

Sizing and the order universe (PRODUCT.md at csl-research d84cdd26, lines 1183-1184 and
1474-1475): "Proposals carry conviction, not quantity, and the gateway sizes orders
(grill Q30; O47)." and "The order universe is a static, operator-approved allowlist; no
model or API changes execution eligibility at runtime." An order of either side reaches
approval or booking only as the exact ``Intent`` object this gateway's ``size_buy`` or
``size_sell`` returned, with every field unchanged; any other order, including a sell
built with a caller-chosen quantity and a sized buy whose side was changed to sell, is
refused ``quantity_not_sized`` (stage ``size``) before any core call. Neither sizer takes
a quantity. The gateway remembers the last ``SIZED_MAX`` intents it sized; an older one is
refused and must be sized again. A booked one stays remembered, so sending it again is
still refused ``intent_duplicate``.

Sells (Lucas, Nov 11: "Size sells from conviction, capped at the position held, no
caller-set quantity"). ``size_sell`` sizes a sell the way ``size_buy`` sizes a buy, from
the conviction's share of the notional cap and the quantity cap, and caps the share count
at the account's held shares available to sell (shares already reserved by a working
sell are not available). No held shares, or a size under one share, is refused
``size_zero``, like a buy. The allowlist is a ``frozenset`` of exact ``str``
instruments given at construction, with no setter. An instrument outside it is refused
``instrument_not_allowlisted`` in the risk pass and in ``size_buy``, before any core
call. Where the operator's approval of the list is recorded is not specified yet; like
the caps, the list is injected and not sourced.

Cash (B31, Lucas t1414u: "size to cash, refuse at the gateway"). The book tracks each
account's available cash and held shares. ``OrderGateway.size_buy`` turns a conviction
into a buy and caps the share count at available cash, so the approved order already
fits; ``size_sell`` caps a sell at held shares the same way. The gateway reads the same
tracked cash and shares again before the charge and refuses a buy over cash with
``cash_insufficient`` and a sell over held shares with ``holding_insufficient``. At send time it reads it once more, inside the
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

Locked book (B15). Before the charge, ``submit`` takes the book's write lock and lets it go
(``BEGIN IMMEDIATE`` then ``ROLLBACK``: no write, no fsync), waiting at most
``BOOK_LOCK_PROBE_S``. A book another connection or process holds write-locked past that is
refused ``book_locked`` at stage ``book`` before the core is called, so nothing is charged and
the approval stays unused; its audit row is tried with the same short wait. A lock taken
after the probe and before the booking (the probe cannot close that window) still waits the
full busy timeout at the booking and, if it outlasts it, is a ``leftover_charge``.

Halt state (Lucas, Nov 11: "Keep the durable halt flag as the source of truth, /dev/shm
only as a fast copy"). Whether the book is halted is only ever read from the book's own
committed ``state`` row (``halted``), on every check: the risk pass, the booking
transaction's re-read and ``halted()``. Nothing in ``/dev/shm`` is read as halt state, so a
copy there that is missing, deleted, stale or corrupt can never lift a halt. The durable
read fails closed: a ``halted`` row that is missing, not exactly the integer 0 or 1, or
that cannot be read at all (an I/O error, say) is refused ``halt_unreadable``. No order
is approved or booked on it (after the charge it is ``leftover_charge`` with that cause),
and a kill cannot confirm its halt on it. ``halt()`` always writes ``halted = 1`` unless
the row already reads exactly 1, so it also repairs a missing or corrupt row to halted.
There is no un-halt.

Halt priority. ``PaperBook.halt`` creates a flag file for the book (in ``/dev/shm`` where it
exists, else ``<book>-halt``), then takes the write
lock in a short retry loop instead of SQLite's backing-off busy handler, and removes the flag
after its commit. Every other writer waits while the flag exists (at most ``HALT_YIELD_S``;
a flag older than that, dated in the future, or that is not a regular file was left by a
halt that died, or not made by a halt, and is removed), so a stream of refusal or booking commits on other connections or processes
cannot starve a halt. The flag only orders writers; it is never read as halt state, and
its contents are never read. Reads never wait.

``PaperBook`` has no public booking method. Its private ``_book`` refuses unless handed a
one-shot ``_Grant`` that ``OrderGateway.submit`` mints after the risk pass and the core's
authorize, bound to that gateway, this book, that intent object and the approved effect.
At booking, ``_book`` runs that gateway's risk pass again (halt and caps included) and
refuses unless it returns exactly the granted effect. Every booked value (intent id,
account, instrument, side, quantity, limit price, reserved notional) is then taken from
that approved effect, never from the intent object, so a change to the intent after the
re-check cannot alter what is booked. The module attribute
``_GATEWAY_ONLY`` is only the grant's mint key: handing it to ``_book`` books nothing.

In-process Python cannot make a capability unforgeable: code in this process can still
reach ``_Grant`` and its mint key and forge a grant, and a forged grant does not prove
the core's approval. What this closes is the import-the-token path, and any grant,
forged or not, meets a gateway's risk pass and caps again at booking (a forger can
still name a gateway it built with its own caps). A static test refuses any reference
to ``_book`` or ``_GATEWAY_ONLY`` elsewhere in ``engine/``.

``PaperBook(path)`` refuses a missing path with ``book_missing`` and a file that is not a
paper book (zero bytes, not SQLite, or without exactly the tables, constraints, append-only
audit triggers, working-order index and state rows that ``create_book`` makes) with
``book_invalid``. After ``PaperBook.close`` every book method,
and so every gateway call that reaches the book, refuses with ``book_closed``; ``close``
takes the book's lock, so it never closes the connection under a statement in flight.

An ``Intent`` is paper only when its own instance holds ``venue`` and ``mode`` as the
exact ``str`` ``"paper"``. The dataclass defaults are not read: an instance with either
field missing or deleted is refused ``paper_only`` before any side effect.

Paper only. There is no broker, venue transport, credential or network code here, and
no Kalshi order code at all (not even demo). Risk logic uses no model, I/O beyond the
local book, or subprocess. The cap values and the allowlist are injected; they are not
sourced yet (F12).
"""

from __future__ import annotations

import hashlib
import os
import queue
import re
import sqlite3
import stat
import sys
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Callable, NamedTuple

from .core_client import MAX_INT, CoreClient, CoreRefused, canonical

__all__ = ["CONVICTION_FULL_BPS", "SIZED_MAX", "Caps", "Intent", "OrderGateway", "PaperBook", "Refused",
           "create_book"]

PAPER_VENUE = "paper"
# The rest of the five-field scope the core's ``check_effect`` requires and the installer's
# authority profile must match exactly. Fixed here: this gateway has one adapter (the local
# paper book), one environment, and does not classify instruments into contract families.
PAPER_CONTRACT_FAMILY = "unclassified"
PAPER_ADAPTER = "paper-book"
PAPER_ENVIRONMENT = "paper"
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_INSTRUMENT = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
_CODE = re.compile(r"[a-z0-9_]{1,64}")  # a typed refusal code from the host's run check
CONVICTION_FULL_BPS = 10_000  # conviction is an integer in basis points, 1..10_000
SIZED_MAX = 1024  # sized buys a gateway remembers; the oldest is dropped and must be sized again
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


_INTENT_FIELDS = ("intent_id", "account", "instrument", "side", "quantity", "limit_price_minor", "run_id",
                  "connection", "venue", "mode")


class _Order(NamedTuple):
    """The order inside an approved effect: what the core charged and the only values booked."""

    intent_id: str
    account: str
    instrument: str
    side: str
    quantity: int
    limit_price_minor: int
    notional_minor: int
    run_id: str
    connection: str


def _order_of(effect: object) -> _Order:
    """Read the order out of an effect ``risk_pass`` returned. Anything else is refused."""
    try:
        payload, scope = effect["payload"], effect["scope"]
        order = _Order(payload["intent_id"], scope["account"], payload["instrument"], payload["side"],
                       payload["quantity"], payload["limit_price_minor"], effect["amount_minor"],
                       effect["run_id"], effect["connection"])
    except (TypeError, KeyError, IndexError):
        raise Refused("book_direct_refused") from None
    texts = (order.intent_id, order.account, order.instrument, order.side, order.run_id, order.connection)
    numbers = (order.quantity, order.limit_price_minor, order.notional_minor)
    if (any(type(v) is not str for v in texts) or any(type(v) is not int or v <= 0 for v in numbers)
            or order.side not in ("buy", "sell") or order.notional_minor != order.quantity * order.limit_price_minor):
        raise Refused("book_direct_refused")
    return order


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
# The append-only triggers on ``audit`` and the partial index that keeps ``working()`` and
# ``cancel_all`` off cancelled history. Part of the schema the open check compares.
_TRIGGER_SCHEMAS = (
    "CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit"
    " BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END",
    "CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit"
    " BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END",
)
_INDEX_SCHEMAS = ("CREATE INDEX orders_working ON orders (intent_id) WHERE status = 'working'",)
_STATE_KEYS = ("halted", "seq", "halt_seq")  # every row of ``state``; each starts at integer 0
# How long ``submit`` waits for the book's write lock before the core charge (see the module doc).
BOOK_LOCK_PROBE_S = 1.0
_BUSY_TIMEOUT_MS = 30_000
# Halt priority: ``halt`` creates the book's halt flag before it takes the write lock; every other writer
# waits while that flag exists, at most ``HALT_YIELD_S``. A flag older than that is stale.
HALT_YIELD_S = 5.0
# The flag lives in shared memory where there is one (no filesystem journal, so creating and
# removing it adds nothing to the book's next fsync), else next to the book. Every process that
# opens the same book file derives the same flag path from the book's real path.
_HALT_FLAG_DIR = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None
_HALT_SPIN_S = 0.0002
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)  # POSIX only; Windows has no symlink-following open flag
# The longest the booking waits on the run check's re-verify while it holds the book's lock
# and write lock (see "Run check at the booking point" in the module doc).
RUN_RECHECK_TIMEOUT_S = 1.0
_RUN_CHECK_WORKERS_MAX = 8  # re-verifies in flight at once per gateway; past that refused, never queued
_RUN_CHECK_WORKER_IDLE_S = 30.0  # an idle re-verify worker exits after this long unused


class _BoundedCaller:
    """Runs a host callable on a worker thread and waits at most a given time for it.

    ``call`` returns ``(True, value)`` or ``(False, exception)`` for what the callable did; only
    the pool's own refusals are raised. A call that has not returned in time is
    ``run_check_timeout``; its worker is left to finish, its late result is discarded, and it
    rejoins the pool only if it ever returns. At most ``_RUN_CHECK_WORKERS_MAX`` calls run at
    once per pool (one pool per gateway), so hung checks cannot pile up threads without bound:
    past that a call is ``run_check_busy`` without waiting. A forked child starts with an empty
    pool (the parent's workers do not exist there).

    Latency only (no effect on what is decided): without the GIL, ``warm`` wakes an idle
    worker ahead of a call and the worker then polls for up to ``_SPIN_S`` before sleeping
    again, and the caller polls for the answer for up to ``_SPIN_S`` before it sleeps, so a
    hand-off does not pay two thread wake-ups. With the GIL, polling would only take the GIL
    from the other thread, so both sides just sleep until woken."""

    _SPIN_S = 0.002 if not getattr(sys, "_is_gil_enabled", lambda: True)() else 0.0

    def __init__(self) -> None:
        self._reset()
        _POOLS.add(self)

    def _reset(self) -> None:
        self._lock = threading.Lock()
        self._idle: list = []
        self._running = 0

    def saturated(self) -> bool:
        with self._lock:
            return not self._idle and self._running >= _RUN_CHECK_WORKERS_MAX

    def warm(self) -> None:
        if self._SPIN_S:
            with self._lock:
                jobs = self._idle[-1] if self._idle else None
            if jobs is not None:
                jobs.put(None)

    def call(self, fn, args: tuple, timeout_s: float):
        with self._lock:
            if self._idle:
                jobs = self._idle.pop()
            elif self._running >= _RUN_CHECK_WORKERS_MAX:
                raise Refused("run_check_busy")
            else:
                jobs = None
            self._running += 1
        done, box = threading.Lock(), []
        alive = jobs is not None  # an idle worker was taken; a new one is alive once started
        try:
            if jobs is None:
                jobs = queue.SimpleQueue()
                threading.Thread(target=self._work, args=(jobs,), daemon=True, name="cockpit-run-check").start()
                alive = True
            done.acquire()  # released by the worker once ``box`` holds the outcome
            start = time.monotonic()
            jobs.put((fn, args, done, box))
        except BaseException as exc:  # nothing was handed over: give the slot (and the worker) back
            with self._lock:
                self._running -= 1
                if alive:
                    self._idle.append(jobs)
            if isinstance(exc, Exception):
                raise Refused("run_check_error") from None
            raise
        if self._SPIN_S:
            spin_until = start + min(self._SPIN_S, timeout_s)
            while not box and time.monotonic() < spin_until:
                pass
        if not done.acquire(timeout=max(0.0, start + timeout_s - time.monotonic())):
            raise Refused("run_check_timeout")
        return box[0]

    def _work(self, jobs) -> None:
        while True:
            try:
                job = jobs.get(timeout=_RUN_CHECK_WORKER_IDLE_S)
            except queue.Empty:
                with self._lock:
                    if any(idle is jobs for idle in self._idle):  # not taken by a caller: retire
                        self._idle = [idle for idle in self._idle if idle is not jobs]
                        return
                continue  # a caller took this worker and is about to hand it a job
            if job is None:  # warmed: poll briefly for the call that is about to come
                spin_until = time.monotonic() + self._SPIN_S
                while jobs.empty() and time.monotonic() < spin_until:
                    pass
                continue
            fn, args, done, box = job
            try:
                outcome = (True, fn(*args))
            except BaseException as exc:  # noqa: BLE001 - handed back to the waiting caller, if any
                outcome = (False, exc)
            # Back in the pool before the caller wakes, so its next call finds this worker idle.
            with self._lock:
                self._running -= 1
                self._idle.append(jobs)
            box.append(outcome)
            done.release()
            del job, fn, args, done, box, outcome


_POOLS: "weakref.WeakSet[_BoundedCaller]" = weakref.WeakSet()
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=lambda: [pool._reset() for pool in list(_POOLS)])


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


def _schema_objects() -> dict:
    """Every table, trigger and index ``create_book`` makes: ``{(type, name): (tbl_name, sql)}``,
    read back from ``_TABLE_SCHEMAS``, ``_TRIGGER_SCHEMAS`` and ``_INDEX_SCHEMAS`` in a memory db."""
    db = sqlite3.connect(":memory:")
    try:
        for schema in _TABLE_SCHEMAS + _TRIGGER_SCHEMAS + _INDEX_SCHEMAS:
            db.execute(schema)
        return _read_objects(db)
    finally:
        db.close()


def _read_objects(db: sqlite3.Connection) -> dict:
    # SQLite's own autoindexes (primary keys) carry no SQL; the table SQL already pins them.
    return {(kind, name): (table, sql) for kind, name, table, sql in db.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL")}


_BOOK_OBJECTS = _schema_objects()


def _audit_column_names() -> list:
    db = sqlite3.connect(":memory:")
    try:
        db.execute(AUDIT_SCHEMA)
        return [row[1] for row in db.execute("PRAGMA table_info(audit)")]
    finally:
        db.close()


# ``audit()`` reads ``AUDIT_COLUMNS`` by name; checked once here, at import, against ``AUDIT_SCHEMA``.
if _audit_column_names() != list(AUDIT_COLUMNS):
    raise RuntimeError("AUDIT_COLUMNS does not match AUDIT_SCHEMA")
_STATE_ROWS = sorted((key, "integer") for key in _STATE_KEYS)


def _check_book_schema(db: sqlite3.Connection) -> None:
    """Refuse a file that does not hold exactly the tables, triggers, index and state rows
    ``create_book`` makes.

    The book's ``sqlite_master`` is read once and compared, statement for statement, with
    what ``create_book`` runs, so a missing ``CHECK`` constraint, a missing append-only
    trigger or a missing index is refused, not just a column difference. A file without
    the core book (``state`` and ``orders`` exactly as ``create_book`` makes them, with
    every state row) is ``book_invalid``. A core book from before the audit table (B15) is
    ``book_without_audit``; one from before the cash tables (B31) is ``book_without_cash``.
    Any other difference is ``book_invalid``."""
    have = _read_objects(db)
    for table in ("state", "orders"):
        if have.get(("table", table)) != _BOOK_OBJECTS[("table", table)]:
            raise Refused("book_invalid")
    rows = db.execute("SELECT k, typeof(v) FROM state WHERE k IN (%s) ORDER BY k"
                      % ", ".join("?" * len(_STATE_KEYS)), _STATE_KEYS).fetchall()
    if rows != _STATE_ROWS:
        raise Refused("book_invalid")
    if ("table", "audit") not in have:
        raise Refused("book_without_audit")
    if ("table", "cash") not in have or ("table", "holdings") not in have:
        raise Refused("book_without_cash")
    if have != _BOOK_OBJECTS:
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
        for schema in _TRIGGER_SCHEMAS + _INDEX_SCHEMAS:
            db.execute(schema)
        db.executemany("INSERT INTO cash VALUES (?, ?)", sorted(cash_minor.items()))
        db.executemany("INSERT INTO holdings VALUES (?, ?, ?)",
                       sorted((a, i, n) for (a, i), n in holdings.items()))
        db.execute("COMMIT")
    finally:
        db.close()


def _halt_flag_path(path: str) -> str:
    real = os.path.realpath(path)
    if _HALT_FLAG_DIR is None:
        return real + "-halt"
    return os.path.join(_HALT_FLAG_DIR, "csl-paper-halt-" + hashlib.sha256(real.encode()).hexdigest()[:32])


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
        self._closed = False
        self._halt_flag = _halt_flag_path(path)

    def _open_db(self) -> sqlite3.Connection:
        """The connection, or ``book_closed``. Call only with ``self._lock`` held."""
        if self._closed:
            raise Refused("book_closed")
        return self._db

    def _yield_to_halt(self) -> None:
        """Wait while a halt is taking the write lock (its flag exists), at most ``HALT_YIELD_S``.

        Called before ``self._lock`` is taken, so a halt on this same book is never blocked by
        a waiter. A flag older than ``HALT_YIELD_S`` was left by a halt that died; it is removed."""
        deadline = None
        while True:
            try:
                st = os.lstat(self._halt_flag)
            except OSError:
                return  # no halt in progress: the usual case, one stat
            age = time.time() - st.st_mtime
            # Stale, dated in the future, or not a regular file (``halt`` makes one, never following
            # a link): never a live halt. Removed, and the writer goes on.
            if not stat.S_ISREG(st.st_mode) or not 0 <= age <= HALT_YIELD_S:
                try:
                    os.unlink(self._halt_flag)
                except OSError:
                    pass
                return
            now = time.monotonic()
            if deadline is None:
                deadline = now + HALT_YIELD_S
            elif now >= deadline:
                return
            time.sleep(_HALT_SPIN_S * 5)

    def _tx(self, fn, busy_ms: int | None = None):
        self._yield_to_halt()
        with self._lock:
            db = self._open_db()
            if busy_ms is None:
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("PRAGMA busy_timeout=%d" % busy_ms)
                try:
                    db.execute("BEGIN IMMEDIATE")
                finally:
                    db.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
            try:
                out = fn(db)
                db.execute("COMMIT")
                return out
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def _next_seq(db) -> int:
        db.execute("UPDATE state SET v = v + 1 WHERE k = 'seq'")
        return PaperBook._state_int(db, "seq")

    @staticmethod
    def _state_int(db, key: str) -> int:
        """One ``state`` counter, exactly an ``int`` >= 0, or a typed ``book_invalid``."""
        row = db.execute("SELECT v FROM state WHERE k = ?", (key,)).fetchone()
        if row is None or type(row[0]) is not int or row[0] < 0:
            raise Refused("book_invalid")
        return row[0]

    def halt(self) -> int:
        """Set the halt flag in one committed step. Returns the halt's sequence number.

        Halt has priority over every other writer, across processes: it first creates the
        book's halt flag file, which every other writer waits on before it takes the write
        lock (``_yield_to_halt``), then takes the lock itself in a short retry loop instead of
        SQLite's backing-off busy handler, so a stream of refusal or booking commits on other
        connections cannot starve it. The flag is removed after the commit."""
        try:
            fd = os.open(self._halt_flag, os.O_WRONLY | os.O_CREAT | _NOFOLLOW, 0o600)
            try:
                os.utime(fd if os.utime in os.supports_fd else self._halt_flag)
            finally:
                os.close(fd)
        except Exception:  # noqa: BLE001 - no flag: the halt still takes the lock and commits, only without priority
            pass
        try:
            with self._lock:
                db = self._open_db()
                deadline = time.monotonic() + _BUSY_TIMEOUT_MS / 1000
                db.execute("PRAGMA busy_timeout=0")
                try:
                    while True:
                        try:
                            db.execute("BEGIN IMMEDIATE")
                            break
                        except sqlite3.OperationalError:
                            if time.monotonic() >= deadline:
                                raise
                            time.sleep(_HALT_SPIN_S)
                finally:
                    db.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
                try:
                    try:
                        already = self._halted_in(db)
                    except Refused:
                        already = False  # a missing or corrupt flag is written halted below
                    upsert = "INSERT INTO state VALUES (?, ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v"
                    try:
                        seq = self._state_int(db, "halt_seq") if already else 0
                    except Refused:
                        seq = 0
                    if seq == 0:
                        # The halted write comes first and depends on nothing else: a damaged counter
                        # row still leaves the book halted (``halt_seq`` then stays as it was, and 0
                        # is returned).
                        db.execute(upsert, ("halted", 1))
                        try:
                            seq = self._next_seq(db)
                        except Refused:
                            seq = 0
                        else:
                            db.execute(upsert, ("halt_seq", seq))
                    db.execute("COMMIT")
                    return seq
                except BaseException:
                    db.execute("ROLLBACK")
                    raise
        finally:
            try:
                os.unlink(self._halt_flag)
            except OSError:
                pass

    @staticmethod
    def _halted_in(db) -> bool:
        """The durable halt flag, read in ``db``. Fails closed: a row that is missing, not exactly
        the integer 0 or 1, or that cannot be read is ``halt_unreadable``, never "not halted"."""
        try:
            row = db.execute("SELECT v FROM state WHERE k = 'halted'").fetchone()
        except sqlite3.Error:
            raise Refused("halt_unreadable") from None
        if row is None or type(row[0]) is not int or row[0] not in (0, 1):
            raise Refused("halt_unreadable")
        return row[0] == 1

    def _probe_writable(self) -> None:
        """Refuse ``book_locked`` unless this connection can take the book's write lock within
        ``BOOK_LOCK_PROBE_S``. Takes it and lets it go (no write, no fsync)."""
        self._yield_to_halt()
        with self._lock:
            db = self._open_db()
            db.execute("PRAGMA busy_timeout=%d" % int(BOOK_LOCK_PROBE_S * 1000))
            try:
                db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                raise Refused("book_locked") from None
            finally:
                db.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
            db.execute("ROLLBACK")

    def halted(self) -> bool:
        """The durable halt flag. Raises ``Refused("halt_unreadable")`` when it cannot be read as
        exactly 0 or 1 (see the module doc); it never answers ``False`` on a read it could not make."""
        with self._lock:
            return self._halted_in(self._open_db())

    @staticmethod
    def _audit_row(db, row: dict) -> int:
        halt_seq = PaperBook._state_int(db, "halt_seq")
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
            return self._cash(self._open_db(), account)

    def held_shares(self, account: str, instrument: str) -> int:
        with self._lock:
            return self._held(self._open_db(), account, instrument)

    @classmethod
    def _check_funds(cls, db, intent: Intent | _Order) -> None:
        """Refuse an order the account cannot cover. Never clamps or resizes (Q25, B31)."""
        if intent.side == "buy":
            if intent.quantity * intent.limit_price_minor > cls._cash(db, intent.account):
                raise Refused("cash_insufficient")
        else:
            cls._cash(db, intent.account)  # the account must exist
            if intent.quantity > cls._held(db, intent.account, intent.instrument):
                raise Refused("holding_insufficient")

    def check_funds(self, intent: Intent | _Order) -> None:
        with self._lock:
            self._check_funds(self._open_db(), intent)

    def _has_intent(self, intent_id: str) -> bool:
        with self._lock:
            return self._open_db().execute("SELECT 1 FROM orders WHERE intent_id = ?",
                                           (intent_id,)).fetchone() is not None

    def _book(self, intent: Intent, token: object, audit: dict | None = None) -> int:
        """Write a working order. Not a public API: ``token`` must be the unused ``_Grant`` that
        ``OrderGateway.submit`` minted for this book and this intent object, after the risk pass,
        the funds check and the core's checks (PRODUCT.md 4.2 "One way in"). The grant is spent
        here, and the gateway's risk pass, caps and sizing check are run again before anything
        is written; a refusal from that re-check is raised as it is. What is written comes from
        the grant's approved effect alone, and the gateway's run check is asked inside the
        booking transaction. A grant can be forged in-process; see the module doc."""
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
        effect = token.effect
        order = _order_of(effect)  # read once; nothing below reads the intent's fields again
        if gateway.risk_pass(intent) != effect:
            raise Refused("book_direct_refused")
        gateway._require_sized(intent, order)  # buys and sells alike, on the granted effect
        # The full host run check, before the book's lock: however long it takes, a halt or a
        # kill on this book is never kept waiting by it.
        gateway._run_checks.warm()  # latency only: the re-verify below then finds its worker awake
        checked = gateway._check_run(order, None)

        def run(db):
            if self._halted_in(db):
                raise Refused("order_halted")
            # Re-verified under the lock, bounded: a fault, a trip or a new binding committed
            # since the check above wins, and so does a check that cannot answer in time.
            if gateway._check_run(order, RUN_RECHECK_TIMEOUT_S) != checked:
                raise Refused("run_check_changed")
            if db.execute("SELECT 1 FROM orders WHERE intent_id = ?", (order.intent_id,)).fetchone():
                raise Refused("intent_duplicate")
            self._check_funds(db, order)  # send-time cash check, same transaction as the booking
            if order.side == "buy":
                db.execute("UPDATE cash SET available_minor = available_minor - ? WHERE account = ?",
                           (order.notional_minor, order.account))
            else:
                db.execute("UPDATE holdings SET available = available - ? WHERE account = ? AND instrument = ?",
                           (order.quantity, order.account, order.instrument))
            seq = self._next_seq(db)
            db.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, 'working')",
                       (order.intent_id, seq, order.account, order.instrument, order.side,
                        order.quantity, order.limit_price_minor))
            if audit is not None:
                self._audit_row(db, dict(audit, event="booked", stage="book", code=None))
            return seq
        return self._tx(run)

    def record_refusal(self, row: dict, *, busy_s: float | None = None) -> int:
        """Append one refusal to the audit table in its own committed step. Returns its seq.

        ``busy_s`` bounds the wait for the write lock (``book_locked`` uses the probe's bound)."""
        write = lambda db: self._audit_row(db, dict(row, event="refused"))  # noqa: E731
        if busy_s is None:
            return self._tx(write)
        return self._tx(write, int(busy_s * 1000))

    def audit(self) -> list[dict]:
        with self._lock:
            rows = self._open_db().execute("SELECT " + ", ".join(AUDIT_COLUMNS)
                                           + " FROM audit ORDER BY seq").fetchall()
        return [dict(zip(AUDIT_COLUMNS, row)) for row in rows]

    def cancel_all(self) -> int:
        """Cancel every working paper order, releasing its reserved cash or shares.

        Allowed while halted (risk-reducing)."""
        def run(db):
            # One read of the working orders (through the ``orders_working`` partial index), then one
            # release per account (buys) and per held instrument (sells): the statements grow with
            # accounts and instruments, never with the number of working orders.
            cash: dict[str, int] = {}
            shares: dict[tuple, int] = {}
            for account, instrument, side, quantity, price in db.execute(
                    "SELECT account, instrument, side, quantity, limit_price_minor FROM orders"
                    " WHERE status = 'working'"):
                if side == "buy":
                    cash[account] = cash.get(account, 0) + quantity * price
                else:
                    shares[(account, instrument)] = shares.get((account, instrument), 0) + quantity
            for account, minor in sorted(cash.items()):
                db.execute("UPDATE cash SET available_minor = available_minor + ? WHERE account = ?",
                           (minor, account))
            for (account, instrument), quantity in sorted(shares.items()):
                db.execute("INSERT INTO holdings VALUES (?, ?, ?) ON CONFLICT (account, instrument)"
                           " DO UPDATE SET available = available + excluded.available",
                           (account, instrument, quantity))
            return db.execute("UPDATE orders SET status = 'cancelled' WHERE status = 'working'").rowcount
        return self._tx(run)

    def working(self) -> int:
        """How many paper orders are still working."""
        with self._lock:
            return self._open_db().execute("SELECT COUNT(*) FROM orders WHERE status = 'working'").fetchone()[0]

    def orders(self) -> list[tuple]:
        with self._lock:
            return self._open_db().execute("SELECT intent_id, seq, status FROM orders ORDER BY seq").fetchall()

    def halt_seq(self) -> int:
        with self._lock:
            return self._state_int(self._open_db(), "halt_seq")

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
    def __init__(self, *, plugin_id: str, book: PaperBook, core: CoreClient, caps: Caps, allowlist: frozenset,
                 run_check: Callable[[str, str, str], dict] | None = None, currency: str = "USD") -> None:
        if type(caps) is not Caps:
            raise Refused("invalid_cap")
        if type(allowlist) is not frozenset or any(type(item) is not str or not _INSTRUMENT.fullmatch(item)
                                                   for item in allowlist):
            raise Refused("invalid_allowlist")
        if run_check is not None and not callable(run_check):
            raise Refused("invalid_run_check")
        self._plugin_id = plugin_id
        self._book = book
        self._core = core
        self._caps = caps
        self._allowlist = allowlist
        self._run_check = run_check
        self._run_checks = _BoundedCaller()  # this gateway's re-verify workers; see _BoundedCaller
        self._currency = currency
        self._sized: dict[int, tuple] = {}  # id(intent) -> (the intent, its fields when sized), oldest first
        self._sized_lock = threading.Lock()
        # The kill switch composed on this gateway's book (``plugin.compose`` sets it), so a
        # host that holds only the gateway can still kill.
        self.kill_switch = None

    def __setattr__(self, name: str, value: object) -> None:
        # The order universe is fixed at construction: no setter, and no rebinding either.
        if name == "_allowlist" and "_allowlist" in self.__dict__:
            raise Refused("allowlist_immutable")
        object.__setattr__(self, name, value)

    @property
    def allowlist(self) -> frozenset:
        """The operator-approved instruments this gateway was built with. Read only."""
        return self._allowlist

    def halt(self) -> int:
        return self._book.halt()

    def cancel_all(self) -> int:
        return self._book.cancel_all()

    @staticmethod
    def _sized_order(order: _Order) -> tuple:
        # Every value the effect carries and the booking writes, with its exact type.
        return tuple((type(value), value) for value in (order.intent_id, order.account, order.instrument, order.side,
                                                        order.quantity, order.limit_price_minor, order.run_id,
                                                        order.connection))

    def _remember_sized(self, intent: Intent) -> None:
        fields = dict(zip(_INTENT_FIELDS, (_field(intent, name) for name in _INTENT_FIELDS)))
        quantity, price = fields["quantity"], fields["limit_price_minor"]
        notional = quantity * price if type(quantity) is int and type(price) is int else 0
        order = _Order(fields["intent_id"], fields["account"], fields["instrument"], fields["side"], quantity, price,
                       notional, fields["run_id"], fields["connection"])
        with self._sized_lock:
            self._sized[id(intent)] = (intent, self._sized_order(order))
            while len(self._sized) > SIZED_MAX:
                del self._sized[next(iter(self._sized))]

    def _require_sized(self, intent: Intent, order: _Order) -> None:
        """Refuse an order unless it is the intent object this gateway's ``size_buy`` or
        ``size_sell`` made and ``order`` (read from the effect the risk pass built, the one that
        is approved, charged and booked) carries exactly the values it was sized with. The
        intent is not read again here, so a change racing between the risk pass and this check
        cannot slip a caller's quantity or side past it. The side is compared too: a sized buy
        turned into a sell (or the reverse) is refused."""
        with self._sized_lock:
            held = self._sized.get(id(intent))
        if held is None or held[0] is not intent or self._sized_order(order) != held[1]:
            raise Refused("quantity_not_sized")

    def _check_run(self, order: _Order, timeout_s: float | None) -> tuple:
        """Ask the host's run check about the approved effect's run: in this thread when
        ``timeout_s`` is ``None``, else on a worker, waiting at most ``timeout_s``. Fails closed.
        Returns the check's answer as a comparable snapshot."""
        check = self._run_check
        if check is None:
            raise Refused("run_check_missing")
        args = (order.run_id, order.connection, self._plugin_id)
        if timeout_s is None:
            try:
                ok, out = True, check(*args)
            except Exception as exc:  # noqa: BLE001 - mapped below; an interrupt is not swallowed
                ok, out = False, exc
        else:
            ok, out = self._run_checks.call(check, args, timeout_s)  # raises only its own refusals
        if not ok:
            if not isinstance(out, Exception):
                raise out  # KeyboardInterrupt or SystemExit raised by the check: never a refusal
            # Whatever the check raised, nothing books; its typed code is the core's, prefixed.
            code = getattr(out, "code", None)
            if type(code) is str and _CODE.fullmatch(code):
                raise Refused("core:" + code) from None
            raise Refused("run_check_error") from None
        if type(out) is not dict or any(type(key) is not str for key in out):
            raise Refused("run_check_error")
        # Keys are distinct ``str``, so sorting never compares the values or their types.
        return tuple(sorted((key, type(value), value) for key, value in out.items()))

    def size_buy(self, *, intent_id: str, account: str, instrument: str, limit_price_minor: int,
                 conviction_bps: int, run_id: str, connection: str) -> Intent:
        """Turn a conviction into a paper buy, sized in code (Q30, O47) and capped at cash (B31).

        Shares are the largest whole count within the conviction's share of the notional cap,
        the quantity cap, and the account's tracked available cash. The returned intent
        already fits; the send-time check still refuses it if cash has since dropped. Only
        this returned object, unchanged, passes ``request_approval`` and ``submit`` as a buy.
        """
        return self._size("buy", intent_id=intent_id, account=account, instrument=instrument,
                          limit_price_minor=limit_price_minor, conviction_bps=conviction_bps, run_id=run_id,
                          connection=connection)

    def size_sell(self, *, intent_id: str, account: str, instrument: str, limit_price_minor: int,
                  conviction_bps: int, run_id: str, connection: str) -> Intent:
        """Turn a conviction into a paper sell, sized in code and capped at the position held.

        Lucas, Nov 11: "Size sells from conviction, capped at the position held, no caller-set
        quantity". Shares are the largest whole count within the conviction's share of the
        notional cap, the quantity cap, and the account's held shares of the instrument that
        are available to sell. There is no quantity argument. Nothing held is ``size_zero``.
        Only this returned object, unchanged, passes ``request_approval`` and ``submit`` as a sell.
        """
        return self._size("sell", intent_id=intent_id, account=account, instrument=instrument,
                          limit_price_minor=limit_price_minor, conviction_bps=conviction_bps, run_id=run_id,
                          connection=connection)

    def _size(self, side: str, *, intent_id: str, account: str, instrument: str, limit_price_minor: int,
              conviction_bps: int, run_id: str, connection: str) -> Intent:
        try:
            if type(conviction_bps) is not int or not 0 < conviction_bps <= CONVICTION_FULL_BPS:
                raise Refused("conviction_invalid")
            if not _positive_int(limit_price_minor, MAX_INT):
                raise Refused("money_invalid")
            for value in (intent_id, account, run_id, connection):
                if type(value) is not str or not _ID.fullmatch(value):
                    raise Refused("intent_invalid")
            if type(instrument) is not str or not _INSTRUMENT.fullmatch(instrument):
                raise Refused("instrument_invalid")
            if instrument not in self._allowlist:
                raise Refused("instrument_not_allowlisted")
            cash = self._book.available_cash_minor(account)  # also refuses an unknown account
            # Buys are capped at what the cash buys, sells at the shares held and not yet reserved.
            limit = cash // limit_price_minor if side == "buy" else self._book.held_shares(account, instrument)
            target_minor = self._caps.max_notional_minor * conviction_bps // CONVICTION_FULL_BPS
            shares = min(target_minor // limit_price_minor, self._caps.max_quantity, limit)
            if shares < 1:
                raise Refused("size_zero")
        except Refused as exc:
            proposal = Intent(intent_id=intent_id, account=account, instrument=instrument, side=side,
                              quantity=None, limit_price_minor=limit_price_minor,
                              run_id=run_id, connection=connection)
            raise self._refuse(exc, "size", proposal, None) from None
        intent = Intent(intent_id=intent_id, account=account, instrument=instrument, side=side, quantity=shares,
                        limit_price_minor=limit_price_minor, run_id=run_id, connection=connection)
        self._remember_sized(intent)
        return intent

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
        if instrument not in self._allowlist:
            raise Refused("instrument_not_allowlisted")
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
            if effect is not None:  # the checked effect, not a second read of the intent
                value = effect["payload"]["intent_id"] if name == "intent_id" else effect[name]
            else:
                value = getattr(intent, name, None) if type(intent) is Intent else None
            return value if type(value) is str and _ID.fullmatch(value) else None
        digest = hashlib.sha256(canonical(effect)).hexdigest() if effect is not None else None
        return {"plugin_id": self._plugin_id, "intent_id": field("intent_id"), "run_id": field("run_id"),
                "connection": field("connection"), "effect_sha256": digest}

    def _refuse(self, exc: Refused, stage: str, intent: object, effect: dict | None) -> Refused:
        """Audit a refusal before it is raised. The refusal stands even if the audit write fails."""
        try:
            # A locked book is refused without waiting the full busy timeout for its audit row too.
            row = dict(self._audit_base(intent, effect), stage=stage, code=str(exc))  # a leftover charge keeps its cause
            if exc.code == "book_locked":
                exc.audit_seq = self._book.record_refusal(row, busy_s=BOOK_LOCK_PROBE_S)
            else:
                exc.audit_seq = self._book.record_refusal(row)
        except Exception:  # noqa: BLE001 - an audit failure never turns a refusal into a pass
            exc.audit_seq = None
        return exc

    def request_approval(self, intent: Intent) -> dict:
        """Risk pass, then ask the core to draw the approval for exactly this effect."""
        effect = None
        stage = "risk"
        try:
            effect = self.risk_pass(intent)
            order = _order_of(effect)  # every later check reads the effect, not the intent again
            if self._run_check is None:
                raise Refused("run_check_missing")  # no booking-point run check: nothing may book
            stage = "size"
            self._require_sized(intent, order)  # a caller-sized order, buy or sell, never reaches approval
            stage = "request"
            # A booked intent id draws no new approval. Only the book is asked: no approval state here.
            if self._book._has_intent(order.intent_id):
                raise Refused("intent_duplicate")
            stage = "funds"
            self._book.check_funds(order)  # cash or held shares, so a doomed order is never approved
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
            order = _order_of(effect)  # every later check reads the effect, not the intent again
            if self._run_check is None:
                raise Refused("run_check_missing")  # no booking-point run check: nothing may book
            stage = "size"
            self._require_sized(intent, order)  # a caller-sized order never reaches the charge or the book
            # Every local refusal that does not need the core comes before the charge.
            stage = "book"
            if self._book._has_intent(order.intent_id):
                raise Refused("intent_duplicate")
            stage = "funds"
            self._book.check_funds(order)  # early read; the booking re-checks in its own transaction
            stage = "book"
            self._book._probe_writable()  # a write-locked book is refused before the core charges
            if self._run_checks.saturated():
                raise Refused("run_check_busy")  # re-verify workers all hung: refused before the charge
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
                # its code as the cause (a halt, a run fault or connection trip, the same intent or
                # a funds change during the core call, or the risk pass re-run at booking refused,
                # for example). There is no reverse: say so, never a clean refusal.
                raise Refused("leftover_charge", exc.code) from None
            except Exception as exc:
                raise Refused("leftover_charge", "booking_error:" + type(exc).__name__) from exc
        except Refused as exc:
            # ``from exc.__cause__`` keeps a booking error's cause; every other refusal has none.
            raise self._refuse(exc, stage, intent, effect) from exc.__cause__
        return {"status": "paper_working", "intent_id": order.intent_id, "seq": seq,
                "notional_minor": order.notional_minor, "digest": core.get("digest")}
