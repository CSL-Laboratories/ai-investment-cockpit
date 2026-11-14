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
2. a re-verify inside the booking transaction, started next to the halt re-read and waited
   on before the commit, for at most ``RUN_RECHECK_TIMEOUT_S`` from its start (the check runs
   on a worker thread of a shared pool while the booking's own statements run; at
   most ``_RUN_CHECK_WORKERS_MAX`` run at once per gateway and ``_RUN_CHECK_THREADS_MAX`` in
   the process; when either is used up by hung checks, ``submit`` is refused
   ``run_check_busy`` before the charge, and a cap reached between that test and the
   booking is ``leftover_charge`` with that cause, never a booking). The frozen
   breaker API offers only ``check()``, so the re-verify is that same call, bounded by the wait rather than by what it reads. It
   must answer exactly what step 1 answered: a fault, a trip or a different binding
   committed in between is refused with the core's code (``core:run_faulted``, say) or
   ``run_check_changed``, and a re-verify that does not answer in time is
   ``run_check_timeout`` (``run_check_busy`` when too many are already in flight). The
   book's locks are therefore held for at most that bound, or the booking's own statements
   if they take longer (they run meanwhile), however the check behaves. Nothing commits before the
   re-verify has answered the same as step 1: a different answer, a refusal or no answer in
   time rolls the transaction back. When one of the booking's statements fails (a duplicate,
   short funds, a database error), the re-verify is still waited for and its refusal wins,
   as when it ran before those statements (Lucas, Nov 11 12:00pm: "Yes, run the re-check
   during the database writes on #320"). An interrupt raised by one of those statements
   (``KeyboardInterrupt``, ``SystemExit``) also waits for it, at most to the same bound,
   and is then raised as it is; an interrupt landing anywhere else just rolls back, and
   the worker gives its slot back when the check returns.

A refusal at either step follows the charge, so it is ``leftover_charge`` with that code as
the cause, audited at stage ``book``. An error without a typed code, or a result that is
not a dict with ``str`` keys, is ``run_check_error``. A ``KeyboardInterrupt`` or
``SystemExit`` raised by the check is raised again, never turned into a refusal. A gateway built without a run check
books nothing: ``request_approval`` and ``submit`` refuse ``run_check_missing`` before any
core call. A fault or trip committed after the re-verify read the store and before the
booking's commit is not seen.

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
transaction's re-read and ``halted()``. The fast copy is the halt flag file of "Halt
priority" below (kept in a folder only the book's user can write, or else beside the book,
and no longer in ``/dev/shm`` unless one of those is there).
Neither it nor the count of halts in progress is ever read as halt state, so a copy that is
missing, deleted, stale or corrupt can never lift a halt. The durable
read fails closed: a ``halted`` row that is missing, not exactly the integer 0 or 1, or
that cannot be read at all (an I/O error, say) is refused ``halt_unreadable``. No order
is approved or booked on it (after the charge it is ``leftover_charge`` with that cause),
and a kill cannot confirm its halt on it. ``halt()`` always writes ``halted = 1`` unless
the row already reads exactly 1, so it also repairs a missing or corrupt row to halted.
There is no un-halt.

Halt priority (Lucas, Nov 12, to the decision sheet's recommendations B1, "From the call,
inside one process", and B2, the fast flag moved out of ``/dev/shm`` into an owner-only
folder and opened "so that it cannot block": "Yes, as recommended"). A halt has priority over
the book's other writers from the moment it is called, inside one process. Across processes
it has priority as far as a flag file reaches, which is best effort. A writer here is a
transaction (``_tx``) or ``submit``'s probe (``_probe_writable``).

Inside one process: a count. The process counts the halts in progress on each book file
(``_HALTS``). The count is shared by every ``PaperBook`` handle the process has open on that
file (its key is the book's real path) and has its own small lock, never a book's lock.
``PaperBook.halt`` on an open handle counts itself in as its first act and counts itself out
when it ends, however it ends (for an interrupt that lands while it is counting itself out,
see the limits). On a closed handle it is refused ``book_closed`` before that and touches
neither the count nor the flag. While the count is above zero, every writer on
that book in this process waits before it takes its handle's lock and looks again once it
holds it: one that was already waiting on the lock when the halt was called lets go of it and
waits again. That holds whatever the flag file's age and whether or not the file exists. A
halt therefore waits for the one transaction or probe in progress on its own connection, not
once for each one queued behind it on that connection's lock; once the halt has committed, a
queued booking finds the book halted and is ``leftover_charge`` with cause ``order_halted``.
The one in progress is not interrupted. It holds the lock while it waits for the write lock,
if another connection has it (up to ``_BUSY_TIMEOUT_MS`` for a transaction,
``BOOK_LOCK_PROBE_S`` for a probe or a ``book_locked`` audit row), and then for its statements
(at most ``RUN_RECHECK_TIMEOUT_S`` for a booking whose re-verify hangs).

Across processes: a flag file. Having counted itself in, ``halt`` makes the book's flag file,
then takes the write lock in a short retry loop instead of SQLite's backing-off busy handler.
While it retries it dates the flag again about once a second (``_HALT_FLAG_REFRESH_S``) and
makes it again at once whenever it finds it gone. A writer whose process counts no halt on
the book looks at the flag instead, before it takes its handle's lock and again once it holds
it, and waits while the flag is live: a regular file dated within the last ``HALT_YIELD_S``
(or less than the clock's grain, ``_HALT_FLAG_GRAIN_S``, ahead). Any other entry there was
left by a halt that died, or was not made by a halt. The writer looks at it once more and
removes it only if it is still the entry it judged (same device and inode) and still not
live; the time the writer has already waited still counts towards its cap. When a halt ends,
the flag is removed unless another halt is still in progress on the book in the same process.
A halt that begins while another is ending makes its flag only after that removal (one lock
per book orders the two), so it keeps its new flag. Within the limits below, a stream of
refusal or booking commits on other connections or processes therefore does not starve a halt.

The cap. A writer waits on halts for at most ``HALT_YIELD_TOTAL_S`` in one transaction or
probe, counted from its first look at a halt in progress (the count above zero, or a live
flag).

Where the flag is kept. In one of two places: under the user's runtime folder, or beside the
book. On POSIX, when ``XDG_RUNTIME_DIR`` is set to
an absolute path whose ``lstat`` shows a real directory that this process's user owns and
that neither its group nor others can write, the flag is
``<XDG_RUNTIME_DIR>/csl-cockpit/csl-paper-halt-<first 32 hex digits of the SHA-256 of the
book's real path>``. ``csl-cockpit`` is made with mode 0700 if it is missing and must pass
the same check. The check is about who can write the folder, not who can read it: one that
others can list (mode 0755) passes. Otherwise, and always on Windows, the flag is ``<the
book's real path>-halt``, beside the book. A handle works its flag's path out once, when it is opened
(that is also when a missing ``csl-cockpit`` is made). No folder is picked for being shared:
``/dev/shm``, where the flag was once kept at a path anyone could put something at, is never
picked, and as a runtime folder it fails the check wherever anyone can write it. The check
does not look at where a folder is, though. The flag is under ``/dev/shm`` wherever a runtime
folder that passes the check is (a folder of the user's own in there is used like any other)
and wherever the book itself is kept.

Making or dating the flag (``_raise_halt_flag``) never waits on what is at its path, and on
POSIX never makes or opens anything through a symbolic link there (for Windows, and for a
hard link, see the limits). The path is looked at first (``lstat``). A live flag is dated in
place: on POSIX through an open that creates nothing, with ``O_NONBLOCK`` and ``O_NOFOLLOW``,
and only once ``fstat`` shows that what was opened is a regular file; on Windows, which has
neither flag, by path. Anything else at the path (a stale flag, a symbolic link, a FIFO) is
never opened and never dated: it is removed (a link itself, not its target) and a new flag is
made with ``O_CREAT | O_EXCL``. A new flag is therefore a new file, never one that a writer has
already judged stale. It is made through a file object that owns its descriptor. That object
is held only by the evaluation stack of the call that makes the flag and is closed from C in
the same statement, so that an interrupt between the making and the closing leaves no
descriptor open unless something else holds that object (see the limits; on Windows an open
descriptor keeps the flag from being removed). A directory at the path is the exception: it
cannot be removed that way, by a halt or by a writer, so it stays and no flag is made while it is
there. The first making of the flag is inside the ``try`` whose ``finally`` counts the halt
out and removes the flag, so an interrupt at that point still reaches both. When the flag
cannot be made or dated, the halt goes on without it.

Limits that remain:

* A writer that already holds its connection's lock and is waiting inside ``BEGIN IMMEDIATE``
  for another connection's transaction is past both looks and still runs first, so a halt
  called on that connection can wait one transaction for each connection writing to the book.
  The halt's own ``_BUSY_TIMEOUT_MS`` bounds only its retries for the write lock: kept off
  its connection's lock for longer than that, it still goes on and commits, late.
* The cap runs from a writer's first look at a halt in progress, whether the writer then
  spends the time waiting on halts or queued on its handle's lock. Once it has passed, the
  writer gives way to nothing more in that transaction or probe, not even to a halt that
  begins later, and a halt that begins behind several such writers on its connection's lock
  waits for each of them. A halt still in progress after ``HALT_YIELD_TOTAL_S`` (kept off its
  connection's lock or the write lock for that long) no longer holds back the writers that
  have waited the cap out, and every transaction or probe that starts behind it waits the
  whole cap first.
* A halt dates its flag again only in its retry loop, not while it waits for its own
  connection's lock. If that wait outlasts ``HALT_YIELD_S``, writers in other processes take
  the flag for a dead halt's, remove it and stop waiting (writers in the halt's own process
  still wait: the count). The halt makes it again at its first failed try for the write
  lock; when its first try succeeds it commits without one.
* Each process counts only its own halts, and they all share the one flag file. A halt that
  ends in one process removes the file even while a halt is still in progress on the book in
  another process. Writers outside that other process then stop waiting until its halt makes
  the flag again: at once if it is retrying for the write lock, not while it waits for its
  own connection's lock.
* A writer's last look at a flag it judged stale and its removal of it are two system calls.
  A halt that makes its flag between the two loses it. Writers in that halt's own process
  still wait (the count); writers in other processes do not, until the halt makes the flag
  again (as above).
* Processes that work out different flag paths do not see each other's flags, so between them
  a halt has no priority: one started without ``XDG_RUNTIME_DIR`` (or with one that fails the
  check) and one started with a usable one; two with different ``XDG_RUNTIME_DIR`` values (two
  users, say); two that reach the book file under different real paths (a hard link), which
  inside one process also means two counts.
* Where the flag cannot be made, the halt goes on without it: it keeps its priority over the
  writers in its own process (the count) and has none over writers in other processes. A
  directory at the flag's path is one such case, and it lasts until someone removes the
  directory: neither a halt nor a writer does.
* Beside the book, the flag is only as private as the book's folder. Whoever can write that
  folder can remove the flag or put something else at its path, which costs a halt its
  priority over writers in other processes. Nothing put there is waited on by the halt, and
  none of it touches the halt's priority inside its own process. A hard link put there to a
  regular file is a regular file like any other: if its date makes it look live, a halt dates
  it in place, which sets the times of the file it names.
* An interrupt (``KeyboardInterrupt``, say) can end a halt at any point, and can leave the
  halt's flag behind: when it lands while the halt is counting itself out, and when it keeps
  the counting out from running at all. (On CPython 3.11 to 3.14 an interrupt raised by a
  trace function at the line of a ``try:`` does the second: that line has an instruction that
  no handler of the function covers, so no ``finally`` and no ``with`` of the halt sees the
  interrupt.) Writers in every process then wait on that flag until it is ``HALT_YIELD_S``
  old, and the first of them to find it stale removes it; a halt that follows on the book
  removes it when it ends.
* A halt is out of the count once its call is over, however the call ended and whatever is
  kept afterwards: the interrupt, its traceback, the frames of the call or a copy of their
  local variables (an interactive session, a logging record, a debugger; ``KillSwitch.kill``
  keeps the interrupt it caught from its halt, traceback and frames with it, until it has
  tried every venue). No handler has to run for that. The object the call is counted by
  (``_Halt``) is held by the call's evaluation stack and by no variable, and the interpreter
  releases that stack when the call returns and when it unwinds. This rests on the
  interpreter, not on the language. It was measured on CPython 3.11 to 3.14 (3.13 and 3.14
  also without the GIL), with one interrupt raised by a trace function before each
  instruction in turn of every function of this module that the call runs, the call made
  directly and by ``KillSwitch.kill``. The halt stays counted for as long as something else
  holds that object. Two things were found that can: code that called the halt's token
  while the call ran and kept the result, and a ``sys.monitoring`` tool that keeps the first
  argument of the calls it watches in ``halt``. And on an interpreter that does not free an
  object with its last reference, the halt stays counted until its collector has run. For
  that long every transaction and probe on that book in that process waits its whole cap.
  None of this touches halt state: the committed row decides.
* A new flag's descriptor closes when the file object that owns it is closed or goes. That
  object is held by the evaluation stack of the call that makes the flag and by no variable,
  and it is closed from C in the statement that makes it. So the interpreter reports no call
  on it and hands it to nothing. (A function set with ``sys.setprofile`` and a
  ``sys.monitoring`` callback are handed the callable of every call they watch, the second
  also the call's first argument. What such a tool was handed stays in its frame, and so in
  the traceback of an interrupt it raises, for as long as that interrupt is kept.) An
  interrupt before the close unwinds the frame, the stack is released and the object goes.
  Like the count, this rests on the interpreter. It was measured on CPython 3.11 to 3.14
  (3.13 and 3.14 also without the GIL), on Windows and on Linux, the halt called directly
  and by ``KillSwitch.kill`` and the interrupt kept each time: with one interrupt raised by
  a trace function before each instruction in turn, and with one raised by a
  ``sys.setprofile`` function or (from 3.12) a ``sys.monitoring`` callback at each call and
  return in turn that it is told of, no halt that followed left its flag behind. The
  descriptor stays open for as long as something else holds that object. Nothing is handed
  it; code that goes looking for it and keeps it could hold it (a scan of the objects the
  collector tracks, say; none was run). And on an interpreter that does not free an object
  with its last reference, it stays open until the collector has run. On POSIX an open
  descriptor does not keep the flag from being removed. On Windows it does: for that long,
  every halt on the book leaves its flag behind when it ends, and writers in every process
  wait on each until it is ``HALT_YIELD_S`` old.
* Dating a live flag on POSIX opens it (``_date_halt_flag``), and that descriptor is a
  number that no object owns. An interrupt that lands after the open and skips the close
  (between the open and the ``try``, at an instruction that the ``try`` does not cover, or in
  the ``finally`` before the close has run) leaves it open for the life of the process, one
  descriptor each time, whether or not the interrupt is kept. On Linux one interrupt raised
  by a trace function did that, one raised by a profile function did, and so did interrupts
  raised by the interpreter's own signal handler while a halt was called in a loop. It
  leaves no flag behind (POSIX removes a file that is open) and touches neither the count
  nor halt state. Windows dates by path and opens nothing. A halt dates a flag only when it
  finds a live one at its path: its own while it retries for the write lock, another halt's,
  or one that an interrupted halt left.
* An interrupt can also leave a lock held or a transaction open, where it skips what would
  have released it. Raised by a trace function at an instruction of the halt that no handler
  covers, or between the body of a ``with`` block and the call that leaves it, one interrupt
  left the book's own lock held (every later call on that handle then waits, and a kill that
  had called the halt did not return); or left the book's flag lock or the lock of the count
  held (the halt itself then waited on it in its own counting out, as every later halt on
  that book does on the first, and every later halt, transaction and probe on every book in
  the process on the second); or left the book's connection inside the transaction it had
  begun. Where that was after the halt's write, the write was in the transaction, not
  committed, and the book's own lock was left held as well: no other connection read the
  book as halted, and the call had ended with the interrupt. Nothing releases a lock left
  held. And an interrupt that lands after the commit, before the halt has left the ``try``
  around it, runs the rollback with nothing open: SQLite's error for that comes out of
  ``halt`` in the interrupt's place, with the interrupt as its context. The halt is
  committed, and a kill that had called it reads the book as halted and does not raise the
  interrupt again. The book's own lock, the transaction and that error are the same without
  the count and the flag. Under real signals (SIGINT, on Linux) the interrupts raised in
  the halt's own code were raised on entering a function, after a call and inside one
  arithmetic operation, never at the line of a ``try:``, and none left a lock held on 3.11,
  3.12 and 3.13; on every version one can leave the transaction open, and one can be
  replaced by that error. For 3.14 see the next limit. Nothing here guards against any of
  this.
* On CPython 3.14, with or without the GIL, an interrupt raised by a signal handler
  (``KeyboardInterrupt`` from Ctrl-C, say) can land just after a ``with`` statement has taken a
  lock and before the statement is bound to give it back, and the lock then stays held. This
  is CPython issue gh-148874 (https://github.com/python/cpython/issues/148874), fixed in 3.15
  and, as of 3.14.10, in no 3.14 release. It was seen under real signals on 3.14.8 for each
  of the three locks a halt takes, and on none of 3.11, 3.12 and 3.13. Left held, the book's
  own lock stops every later call on that handle, a halt included; a book's flag lock stops
  every later halt on that book in the process; and the lock of the count stops every later
  halt, transaction and probe on every book in the process. Each waits for good. Nothing
  here guards against it: the engine needs a host that never lets a signal handler raise an
  exception into a thread that is running it.
* Windows offers no way to refuse a link at open. There the flag's path is looked at first,
  and what is there is dated only when the look showed a regular file and a flag is made only
  when it showed nothing (or what it showed was removed). A link put at the path between
  that look and the call that follows it can be followed: dating it sets its target's access
  and modification times to now, and making a flag through a dangling link can make its
  target.
* A forked child starts with an empty count: it sees a halt that was in progress in its
  parent only through the flag.
* One ``submit`` is up to three waits on halts (its probe, its booking and the audit row of a
  refusal), each with its own ``HALT_YIELD_TOTAL_S``.

The count and the flag only order writers. Neither is ever read as halt state, and the flag's
contents are never read. Reads never wait on either; like every call on a connection they
wait for its lock.

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

import functools
import hashlib
import io
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
# Halt priority: ``halt`` counts itself in (``_HALTS``) and makes the book's halt flag before it takes the
# write lock. Every other writer waits while a halt is in progress on the book in its own process (the
# count) or while that flag is live. A flag is live for ``HALT_YIELD_S`` after its halt last dated it; an
# older one is stale. A writer that was already waiting on the book's lock when the halt began lets go of
# the lock once it gets it and waits again, so a halt does not wait for writers queued ahead of it on
# that lock.
HALT_YIELD_S = 5.0
# How often a halt dates its flag again while it retries for the write lock, so that the flag's age is
# the time since its halt last touched it and a halt that is still trying is not taken for a dead one.
_HALT_FLAG_REFRESH_S = 1.0
# The longest one writer waits on halt flags in a single transaction or probe, however long one halt
# takes and however many follow one another. A flag left by a halt that died must not use up the wait
# a retried halt is owed, and flags that keep coming must not hold a writer for ever.
HALT_YIELD_TOTAL_S = 10.0
# The flag's date and ``time.time()`` are not one clock reading. On Windows a flag made in the
# current clock tick reads one float step (about 0.2 us) in the future roughly one look in seven,
# and a file system may stamp with a finer clock than ``time.time()`` (a tick is up to 15.6 ms
# there). A flag dated less than this far ahead is therefore a halt in progress, not a planted date.
_HALT_FLAG_GRAIN_S = 0.1
# ``/dev/shm``, which anyone can write, is never picked for the flag. It lives in a folder only the
# book's user can write, ``_HALT_FLAG_SUBDIR`` under ``$XDG_RUNTIME_DIR``, where that is usable
# (``_halt_flag_dir``), else next to the book; it is under ``/dev/shm`` only where one of those is.
# Processes that open the same book file with the same usable ``XDG_RUNTIME_DIR``, or all without one,
# derive the same flag path from the book's real path.
_HALT_FLAG_SUBDIR = "csl-cockpit"
_HALT_SPIN_S = 0.0002
# POSIX only. Windows has neither flag: there the flag's path is looked at (``lstat``) before it is dated
# by path, and a new flag is only ever made with ``O_EXCL`` (``_raise_halt_flag``).
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)  # no open of the flag's path may wait (on a FIFO, say)
# The longest the booking waits on the run check's re-verify while it holds the book's lock
# and write lock (see "Run check at the booking point" in the module doc).
RUN_RECHECK_TIMEOUT_S = 1.0
_RUN_CHECK_WORKERS_MAX = 8  # re-verifies in flight at once per gateway; past that refused, never queued
_RUN_CHECK_THREADS_MAX = 64  # re-verify worker threads in this process, all gateways together
_RUN_CHECK_WORKER_IDLE_S = 30.0  # an idle re-verify worker exits after this long unused


class _Slots:
    """One gateway's count of re-verifies in flight, capped at ``_RUN_CHECK_WORKERS_MAX``, so
    checks hung behind one gateway never refuse another gateway's."""

    def __init__(self) -> None:
        self._reset()
        _FORK_RESET.add(self)

    def _reset(self) -> None:
        self.lock = threading.Lock()
        self.used = 0

    def full(self) -> bool:
        with self.lock:
            return self.used >= _RUN_CHECK_WORKERS_MAX

    def give(self) -> None:
        with self.lock:
            self.used -= 1


_SLOTS, _SLOT, _TAKEN, _JOBS, _ALIVE, _GIVEN = range(6)  # a call's ticket, see ``_BoundedCaller.start``


class _BoundedCaller:
    """Runs a host callable on a worker thread and waits at most a given time for it.

    ``call`` (``start`` then ``join``) returns ``(True, value)`` or ``(False, exception)`` for
    what the callable did; only the pool's own refusals are raised. A call that has not returned in time is
    ``run_check_timeout``; its worker is left to finish, its late result is discarded, and it
    rejoins the pool only if it ever returns. A call takes one of its gateway's ``_Slots``
    (at most ``_RUN_CHECK_WORKERS_MAX`` in flight per gateway) and the pool never runs more
    than ``_RUN_CHECK_THREADS_MAX`` workers, so hung checks cannot pile up threads without
    bound: past either cap a call is ``run_check_busy`` without waiting. Idle workers exit
    after ``_RUN_CHECK_WORKER_IDLE_S``. A forked child starts with an empty pool and empty
    slots (the parent's workers do not exist there).

    Latency only (no effect on what is decided): without the GIL, ``warm`` wakes an idle
    worker ahead of a call and the worker then polls for up to ``_SPIN_S`` before sleeping
    again, and the caller polls for the answer for up to ``_SPIN_S`` before it sleeps, so a
    hand-off does not pay two thread wake-ups. With the GIL, polling would only take the GIL
    from the other thread, so both sides just sleep until woken."""

    _SPIN_S = 0.002 if not getattr(sys, "_is_gil_enabled", lambda: True)() else 0.0

    def __init__(self) -> None:
        self._reset()
        _FORK_RESET.add(self)

    def _reset(self) -> None:
        self._lock = threading.Lock()
        self._idle: list = []
        self._running = 0

    def warm(self) -> None:
        if self._SPIN_S:
            with self._lock:
                jobs = self._idle[-1] if self._idle else None
            if jobs is not None:
                jobs.put(None)

    def full(self) -> bool:
        with self._lock:
            return not self._idle and self._running >= _RUN_CHECK_THREADS_MAX

    def _give_back(self, ticket: list) -> None:
        """Return what one call took (its gateway slot, its worker), exactly once: the caller
        (when interrupted) and the worker (when done) may both get here."""
        with self._lock:
            if ticket[_GIVEN]:
                return
            ticket[_GIVEN] = True
            if ticket[_TAKEN]:
                self._running -= 1
            if ticket[_ALIVE]:
                self._idle.append(ticket[_JOBS])
        if ticket[_SLOT]:
            ticket[_SLOTS].give()

    def start(self, fn, args: tuple, timeout_s: float, slots: _Slots) -> tuple:
        """Hand ``fn(*args)`` to a worker and return at once; ``join`` waits for its outcome.
        The deadline is fixed here: ``join`` waits at most until ``timeout_s`` after this."""
        # What this call has taken so far, so that whoever gives it back (an interrupt here, or
        # the worker) returns exactly that, once.
        ticket = [slots, False, False, None, False, False]  # slots, slot, taken, jobs, alive, given
        done, box = threading.Lock(), []
        try:
            with slots.lock:
                if slots.used >= _RUN_CHECK_WORKERS_MAX:
                    raise Refused("run_check_busy")
                slots.used += 1
                ticket[_SLOT] = True
            with self._lock:
                if self._idle:
                    ticket[_JOBS] = self._idle.pop()
                    ticket[_ALIVE] = True
                elif self._running >= _RUN_CHECK_THREADS_MAX:
                    raise Refused("run_check_busy")
                self._running += 1
                ticket[_TAKEN] = True
            jobs = ticket[_JOBS]
            if jobs is None:
                jobs = ticket[_JOBS] = queue.SimpleQueue()
                threading.Thread(target=self._work, args=(jobs,), daemon=True, name="cockpit-run-check").start()
                ticket[_ALIVE] = True
            done.acquire()  # released by the worker once ``box`` holds the outcome
            deadline = time.monotonic() + timeout_s
            jobs.put((fn, args, done, box, ticket))
        except BaseException as exc:
            self._give_back(ticket)  # a no-op if the worker already gave it back
            if isinstance(exc, Refused) or not isinstance(exc, Exception):
                raise
            raise Refused("run_check_error") from None
        return done, box, deadline

    def join(self, pending: tuple):
        """The outcome of a ``start``ed call, or ``run_check_timeout`` once its deadline passes."""
        done, box, deadline = pending
        if self._SPIN_S:
            spin_until = min(time.monotonic() + self._SPIN_S, deadline)
            while not box and time.monotonic() < spin_until:
                pass
        if not done.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise Refused("run_check_timeout")
        return box[0]

    def call(self, fn, args: tuple, timeout_s: float, slots: _Slots):
        return self.join(self.start(fn, args, timeout_s, slots))

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
            fn, args, done, box, ticket = job
            try:
                outcome = (True, fn(*args))
            except BaseException as exc:  # noqa: BLE001 - handed back to the waiting caller, if any
                outcome = (False, exc)
            # Back in the pool before the caller wakes, so its next call finds this worker idle.
            self._give_back(ticket)
            box.append(outcome)
            done.release()
            del job, fn, args, done, box, ticket, outcome


class _Halt:
    """One call of ``PaperBook.halt``, as an object. The call makes it before it counts itself
    in and holds it for as long as it runs on its evaluation stack, as the argument of one call
    expression, and in no variable of any frame. The count is never handed it and never keeps
    it: it knows the call by a weak reference to it, the halt's token (``_Halts``)."""

    __slots__ = ("__weakref__",)


class _Halts:
    """The halts in progress in this process, counted per book file (see "Halt priority" in the
    module doc). Shared by every ``PaperBook`` handle open on the same file: the key is the book's
    real path, the one its flag is named from. Guarded by its own small lock, never a book's lock,
    so a halt takes its place in the count however long its handle's lock is held. That lock is
    never held across a system call: a writer reading the count waits on no file system.

    Each halt is one entry, under its token: a weak reference, made by the running ``halt``
    call, to an object that only that call holds (``_Halt``). ``begin``, ``end`` and ``forget``
    are handed the token, never the object. The entry is kept under the token itself, not under
    a number taken from it (an address, say, which a later object can be given). A weak
    reference whose object is gone is equal to nothing but itself, and one whose object is alive
    only to a reference to that same object. So an entry is found by the token it was put in
    under and, while its object is alive, by another weak reference to that same object
    (``halt`` makes none), and never through the object of another halt, not even a later one
    that was made where one that is gone used to be; and an ``end`` or ``forget`` whose
    ``begin`` never happened takes no entry out and removes no flag, whatever else is in the
    count. (``halt`` hands its token in while its object is alive, and ``begin`` hashes it
    then. A token that was never hashed while its object was alive cannot be looked up once
    the object has gone: with anything in the count, ``end`` and ``forget`` raise ``TypeError``
    for it, and still take no entry out.)

    ``begin`` puts the entry in, in one step: an interrupt lands before it or after it. Taking
    it out is not one step, because ``end`` takes the book's flag lock first, so an interrupt can
    land inside ``end`` before the halt is out of the count; and an interrupt can keep ``end``
    from being called at all. Two things keep the count right all the same. ``halt`` calls
    ``forget`` in a ``finally`` around ``end``, which takes the entry out whatever happened in
    ``end``. And an entry whose token's object is gone belongs to no running ``halt``: it is
    not counted, and it is dropped at the next look. The object is gone once the call is over,
    however the call ended and with no handler run for it: ``halt`` holds it on its evaluation
    stack, as the argument of the call expression that runs its body, and in no variable, and
    the interpreter releases that stack when the call returns and when it unwinds. Nothing
    else is ever handed the object (a look at the count holds it only for the moment it takes
    to see that it is alive). So an entry that neither ``end`` nor ``forget`` took out stops
    being counted as the call ends, whether or not the interrupt, its traceback, the call's
    frames or a copy of their local variables is kept afterwards. That rests on the
    interpreter and was measured, not proved: see the limits in the module doc, which also say
    what can still hold the object. A forked child starts with an empty count (the parent's
    halts are not running there).

    The entries are read and changed only with the count's lock held, on a build with a GIL and
    on one without. The tokens have no callback, so nothing of the count's runs when an object
    is freed, at whatever moment and on whatever thread that happens: an entry whose object is
    gone just waits for the next look.

    Each book also has a flag lock (``flag_lock``). A halt that ends holds it from finding
    itself the last one in progress until it has removed the flag, and a halt that begins makes
    its flag under it. So the one that begins, already counted in, makes its flag only after the
    one that is ending has removed the old one, and never loses the new one to it."""

    def __init__(self) -> None:
        self._reset()
        _FORK_RESET.add(self)

    def _reset(self) -> None:
        self._lock = threading.Lock()
        # a halt's token (a weak reference to its ``_Halt``) -> its book's real path
        self._halts: dict[weakref.ref[_Halt], str] = {}
        self._flag_locks: dict[str, threading.Lock] = {}  # a book's real path -> its flag lock

    def begin(self, book: str, halt: weakref.ref[_Halt]) -> None:
        with self._lock:
            self._halts[halt] = book

    def _in_progress(self, book: str) -> int:
        """How many halts are in progress on ``book``. Call only with ``self._lock`` held. An
        entry whose token's object is gone belongs to no running ``halt``: it is dropped, not
        counted."""
        if not self._halts:
            return 0  # the usual case
        halts = 0
        for token, owner in list(self._halts.items()):
            if token() is None:
                del self._halts[token]
            elif owner == book:
                halts += 1
        return halts

    def count(self, book: str) -> int:
        with self._lock:
            return self._in_progress(book)

    def flag_lock(self, book: str) -> threading.Lock:
        with self._lock:
            return self._flag_locks.setdefault(book, threading.Lock())

    def end(self, book: str, halt: weakref.ref[_Halt], flag: str) -> None:
        """Take the halt with the token ``halt`` out of the count. If no other halt is then in
        progress on that book, remove its flag, under the book's flag lock (see the class doc)."""
        with self.flag_lock(book):
            with self._lock:
                if self._halts.pop(halt, None) is None:
                    return  # it never took its place, so it made no flag either
                if self._in_progress(book):
                    return  # another halt on this book is still in progress: the flag stays
            try:
                os.unlink(flag)
            except OSError:
                pass

    def forget(self, halt: weakref.ref[_Halt]) -> None:
        """Take the halt with the token ``halt`` out of the count and do nothing else: one step,
        no flag lock, no file system. ``halt`` calls it after ``end``, in a ``finally``, for the
        case where an interrupt landed inside ``end`` before the entry was out. The flag is then
        left as it is, and writers treat it like any flag whose halt has gone
        (``_yield_to_halt``)."""
        with self._lock:
            self._halts.pop(halt, None)


_FORK_RESET: "weakref.WeakSet" = weakref.WeakSet()
_RUN_CHECKS = _BoundedCaller()
_HALTS = _Halts()
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=lambda: [item._reset() for item in list(_FORK_RESET)])


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


def _private_dir(path: str) -> bool:
    """Whether ``path`` is a real directory (by ``lstat``: a link to one is not) that this
    process's user owns and that neither its group nor anyone else can write. POSIX only."""
    try:
        st = os.lstat(path)
    except (OSError, ValueError):
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & 0o022


def _halt_flag_dir() -> str | None:
    """The folder halt flags are kept in, which only the book's user can write, or ``None`` for
    "beside the book".

    On POSIX, when ``XDG_RUNTIME_DIR`` is set to an absolute path that is a private directory
    (``_private_dir``), the folder is ``_HALT_FLAG_SUBDIR`` inside it: made with mode 0700 if it
    is missing, and used only if it passes the same check. In every other case, and always on
    Windows, there is no such folder. A folder that comes back has passed the check, so nobody
    else can write it: ``/dev/shm`` itself does not pass where anyone can write it. Where the
    folder is, is not looked at: one of the user's own under ``/dev/shm`` is used like any
    other."""
    if os.name != "posix":
        return None
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base or not os.path.isabs(base) or not _private_dir(base):
        return None
    folder = os.path.join(base, _HALT_FLAG_SUBDIR)
    try:
        os.mkdir(folder, 0o700)
    except FileExistsError:
        pass
    except OSError:
        return None
    return folder if _private_dir(folder) else None


def _halt_flag_path(path: str) -> str:
    """Where the halt flag of the book at ``path`` is kept: named from the book's real path, in
    the folder only the book's user can write (``_halt_flag_dir``) or else beside the book."""
    real = os.path.realpath(path)
    folder = _halt_flag_dir()
    if folder is None:
        return real + "-halt"
    return os.path.join(folder, "csl-paper-halt-" + hashlib.sha256(real.encode()).hexdigest()[:32])


def _halt_flag_live(st: os.stat_result) -> bool:
    """Whether a halt flag's ``lstat`` shows a halt in progress: a regular file (``halt`` makes
    one, never through a link) dated from ``_HALT_FLAG_GRAIN_S`` ahead of now back to
    ``HALT_YIELD_S`` ago. A halt dates its flag again while it retries for the write lock, so
    an older flag has not been touched by its halt for that long. Older, dated further ahead,
    or anything else was left by a halt that died or was not made by a halt (or by a halt still
    waiting for its own connection's lock: see "Halt priority" in the module doc)."""
    return stat.S_ISREG(st.st_mode) and -_HALT_FLAG_GRAIN_S <= time.time() - st.st_mtime <= HALT_YIELD_S


def _date_halt_flag(flag: str) -> bool:
    """Date a live halt flag now, in place. Call only right after an ``lstat`` of ``flag`` showed
    a regular file. Returns whether it was dated. Never waits and never raises ``OSError``.

    Where ``os.utime`` takes a file descriptor (POSIX): the path is opened without creating
    anything, with ``O_NONBLOCK`` (a FIFO put there since the look cannot hold the open) and
    ``O_NOFOLLOW`` (nor can a link be followed), and its time is touched only once ``fstat``
    shows that what was opened is a regular file. Elsewhere (Windows, which has none of the
    three) it is dated by path: a link put there since the look would be followed (see "Halt
    priority" in the module doc)."""
    try:
        if os.utime not in os.supports_fd:
            os.utime(flag)
            return True
        fd = os.open(flag, os.O_WRONLY | _NOFOLLOW | _NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
            os.utime(fd)
            return True
        finally:
            os.close(fd)
    except OSError:
        return False


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
        # The book file's real path: what this handle's halts are counted under (``_HALTS``), with
        # every other handle this process has on the same file, and what the flag is named from.
        self._halt_key = os.path.realpath(path)
        self._halt_flag = _halt_flag_path(self._halt_key)

    def _open_db(self) -> sqlite3.Connection:
        """The connection, or ``book_closed``. Call only with ``self._lock`` held."""
        if self._closed:
            raise Refused("book_closed")
        return self._db

    def _yield_to_halt(self, cap: float | None = None) -> float | None:
        """Wait while a halt is in progress on this book, at most until ``cap``.

        A halt is in progress while this process counts one on the book file (``_HALTS``: from
        the moment ``halt`` is called on any handle open on it, whatever the flag file's age and
        whether or not it exists) or, failing that, while the book's flag file is live (a halt
        in another process). Called before ``self._lock`` is taken, so a halt on this same book
        is never blocked by a waiter. A flag that is not live (``_halt_flag_live``) was left by
        a halt that died, or was not made by a halt; it is removed (``_drop_stale_flag``),
        except a directory, which cannot be removed that way and is left.

        ``cap`` is the monotonic time at which this writer stops waiting on halts in the
        transaction or probe it is in: ``None`` until it first meets a halt in progress, and
        from then on ``HALT_YIELD_TOTAL_S`` after that look. What comes back is ``cap`` brought
        up to date (still ``None`` for a writer that has met no halt in progress). The caller
        hands it to the look under the lock (``_give_way``) and to any later wait, so however
        long one halt takes and however many follow one another, all of the writer's waits end
        at the one cap."""
        while True:
            if not _HALTS.count(self._halt_key):
                try:
                    st = os.lstat(self._halt_flag)
                except OSError:
                    return cap  # no halt in progress: the usual case, one stat
                # Stale, dated in the future (past the clock's grain), or not a regular file:
                # never a live halt (``_halt_flag_live``). Removed, and the writer goes on with
                # the wait it has already spent still counted.
                if not _halt_flag_live(st):
                    self._drop_stale_flag(st)
                    return cap
            now = time.monotonic()
            if cap is None:
                cap = now + HALT_YIELD_TOTAL_S
            if now >= cap:
                return cap
            time.sleep(_HALT_SPIN_S * 5)

    def _drop_stale_flag(self, judged: os.stat_result) -> None:
        """Remove the flag a writer has just judged not live, after one more look at it.

        A halt may have made or dated the flag since ``judged`` was read. So the path is looked
        at again immediately before the removal, and the entry is removed only if it is still
        the one that was judged (same device and inode) and still not live. The look and the
        removal are two system calls: a flag made between them is still removed (see "Halt
        priority" in the module doc)."""
        try:
            st = os.lstat(self._halt_flag)
            if (st.st_dev, st.st_ino) == (judged.st_dev, judged.st_ino) and not _halt_flag_live(st):
                os.unlink(self._halt_flag)
        except OSError:
            pass

    def _give_way(self, cap: float | None) -> float | None:
        """With ``self._lock`` just taken and nothing done yet: must this writer let a halt go first?

        A writer that was already waiting on ``self._lock`` when a halt began is past
        ``_yield_to_halt``. Without this it would take the lock ahead of the halt, and every
        writer queued that way would hold it for its whole transaction or probe (a booking whose
        re-verify hangs for ``RUN_RECHECK_TIMEOUT_S``, a probe waiting on another connection's
        transaction for ``BOOK_LOCK_PROBE_S``), so the halt would wait once per queued writer.
        Returns the cap to wait under (as ``_yield_to_halt``) when a halt is in progress (this
        process counts one on the book file, or the flag is live) and the writer's cap has not
        passed; the caller lets go of the lock and waits again. Returns ``None`` when there is
        no halt in progress (the usual case, one stat) or the cap has passed. The flag is judged
        as in ``_yield_to_halt`` and never removed here."""
        if not _HALTS.count(self._halt_key):
            try:
                st = os.lstat(self._halt_flag)
            except OSError:
                return None
            if not _halt_flag_live(st):
                return None  # not a live halt: nobody waits on it (``_yield_to_halt`` removes it)
        now = time.monotonic()
        if cap is None:
            cap = now + HALT_YIELD_TOTAL_S
        return cap if now < cap else None

    def _tx(self, fn, busy_ms: int | None = None):
        cap = None
        while True:
            cap = self._yield_to_halt(cap)
            with self._lock:
                again = self._give_way(cap)
                if again is not None:
                    cap = again
                    continue  # lets go of the lock: the halt that began meanwhile goes first
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

    def _raise_halt_flag(self) -> None:
        """Make the book's halt flag file, or date it now if a live one is there. Never waits on
        what is at the flag's path, on POSIX never makes or opens anything through a symbolic
        link there, and never raises: with no flag the halt still takes the lock and commits,
        with priority over the writers in its own process (the count) and none over those in
        other processes.

        The path is looked at first (``lstat``). A live flag (this halt's own, or another halt's)
        is dated in place (``_date_halt_flag``). Anything else there (a stale flag, a symbolic
        link, a FIFO), or a flag that could not be dated after all (it went, or changed, since
        the look), is never dated: it is removed, as a writer removes it (a link itself goes,
        not its target), and a new flag is made with ``O_CREAT | O_EXCL``, which on POSIX opens
        no existing entry and makes nothing through a link. A new flag is therefore a new file,
        never one that a writer has already judged stale. It is made through a file object,
        which closes its descriptor when it goes and which nothing but this call's evaluation
        stack holds: an interrupt between the making and the closing leaves no descriptor
        open unless something else has got hold of that object (see the limits in the module
        doc; on Windows an open descriptor keeps the flag from being removed, by this halt and
        by every later one). A directory there cannot be removed
        that way: it stays, and no flag is made. Windows cannot refuse a link at open: there a
        link put at the path between the look and the dating or the making can be followed (see
        "Halt priority" in the module doc)."""
        flag = self._halt_flag
        try:
            try:
                st = os.lstat(flag)
            except FileNotFoundError:
                st = None
            if st is not None:
                if _halt_flag_live(st) and _date_halt_flag(flag):
                    return
                try:
                    os.unlink(flag)
                except FileNotFoundError:
                    pass
            # Made through a file object, which closes its descriptor when it goes. (An open one
            # would, on Windows, keep every later halt from removing the flag.) ``"x"`` is
            # ``O_CREAT | O_EXCL``; the opener gives the file its mode of 0600, and ``open`` calls
            # it itself, so that no Python code runs between the system call and the object that
            # owns the descriptor. That object is held by a tuple on this frame's evaluation
            # stack and by no variable, and ``map`` closes it from C. The interpreter therefore
            # reports no call on the object: a tool that watches calls (``sys.setprofile``,
            # ``sys.monitoring``) is handed the callable of each call, and here those are
            # ``functools.partial``, ``open`` and ``map``, never a method of the file. Written
            # ``open(...).close()``, the tool
            # was handed the bound ``close``, and one that raised an interrupt there kept the
            # file open for as long as the interrupt was kept. An interrupt before the close
            # unwinds this frame: the stack is released and the object goes, its descriptor
            # with it.
            for _ in map(io.FileIO.close, (
                    open(flag, "xb", buffering=0, opener=functools.partial(os.open, mode=0o600)),)):
                pass
        except Exception:  # noqa: BLE001 - no flag: the halt still takes the lock and commits (see the docstring)
            pass

    def halt(self) -> int:
        """Set the halt flag in one committed step. Returns the halt's sequence number.

        On a closed book it is refused ``book_closed`` before anything is touched, the count and
        the flag included.

        Halt has priority over every other writer on the book from the moment it is called, in
        its own process: on an open book its first act is to count itself in (``_HALTS``).
        While the count is above zero every writer in this process, on any handle open on the
        same book file, waits before it takes its handle's lock (``_yield_to_halt``), and a
        transaction (``_tx``) or probe (``_probe_writable``) that was already waiting on the
        book's lock gives the lock back when it gets it (``_give_way``), each for at most
        ``HALT_YIELD_TOTAL_S`` in all.

        Across processes it has priority as far as the book's halt flag file reaches (best
        effort): it makes that file next, and writers in other processes wait while it is live.
        The halt then takes the lock itself in a short retry loop instead of SQLite's
        backing-off busy handler, so a stream of refusal or booking commits on other
        connections cannot starve it. While it retries it dates the flag again every
        ``_HALT_FLAG_REFRESH_S``, and makes it again at once whenever it finds it gone, so that
        writers in other processes do not take a halt that is still trying for one that died.

        When the halt ends (committed, refused or timed out), it counts itself out, and the
        flag is removed only if no other halt is then in progress on the book in this process.
        Both happen after the commit, never before it: other writers stop waiting the moment
        the count and the flag let them. An interrupt can cut the counting out short, or keep
        it from running. This halt is out of the count all the same once the call is over,
        because the count goes by an object that only this call's evaluation stack holds
        (``_Halts``); its flag can then be left behind, to go stale like a dead halt's. See the
        limits in the module doc.

        It gives up (the SQLite error is raised) when it has retried for the write lock for
        ``_BUSY_TIMEOUT_MS``, counted from when it takes the book's lock. That is not a bound
        on the whole call: the wait for the book's lock comes first and is not counted."""
        if self._closed:
            raise Refused("book_closed")  # nothing touched: this handle's halt can never commit
        # This call, as an object (``_Halt``), is held by the argument of the one expression below
        # (a tuple inside a ``map`` object on this frame's evaluation stack) and by no variable of
        # any frame. ``_halt_counted`` is handed its token only. When that call returns or unwinds,
        # the interpreter releases the stack and the object goes, with no handler to run (``_Halts``).
        return next(map(self._halt_counted, map(weakref.ref, (_Halt(),))))

    def _halt_counted(self, mine: weakref.ref[_Halt]) -> int:
        """The body of ``halt``, counted in under ``mine``, this halt's token. Only ``halt`` calls
        it, and only in the way it does: the object the token refers to has to stay alive for as
        long as this runs, held by nothing that outlasts the call."""
        try:
            _HALTS.begin(self._halt_key, mine)
            with _HALTS.flag_lock(self._halt_key):  # after a halt that is ending has removed the old flag
                self._raise_halt_flag()
            raised = time.monotonic()
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
                            now = time.monotonic()
                            if now >= deadline:
                                raise
                            if now - raised >= _HALT_FLAG_REFRESH_S or not os.path.lexists(self._halt_flag):
                                self._raise_halt_flag()
                                raised = now
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
            # Counted out, and the flag removed unless another halt is in progress on this book.
            try:
                _HALTS.end(self._halt_key, mine, self._halt_flag)
            finally:
                _HALTS.forget(mine)  # still counted in only if an interrupt landed inside ``end``

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
        ``BOOK_LOCK_PROBE_S``. Takes it and lets it go (no write, no fsync).

        Gives way to a halt as a transaction does (``_tx``): a probe that was already waiting on
        ``self._lock`` when a halt began lets go of it. Otherwise, while another connection holds
        the write lock, a halt would wait ``BOOK_LOCK_PROBE_S`` for each submit queued ahead of
        it."""
        cap = None
        while True:
            cap = self._yield_to_halt(cap)
            with self._lock:
                again = self._give_way(cap)
                if again is not None:
                    cap = again
                    continue  # lets go of the lock: the halt that began meanwhile goes first
                db = self._open_db()
                db.execute("PRAGMA busy_timeout=%d" % int(BOOK_LOCK_PROBE_S * 1000))
                try:
                    db.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError:
                    raise Refused("book_locked") from None
                finally:
                    db.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
                db.execute("ROLLBACK")
                return

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
        checked = gateway._check_run(order, None)

        def run(db):
            if self._halted_in(db):
                raise Refused("order_halted")
            # Re-verified under the lock, bounded: a fault, a trip or a new binding committed
            # since the check above wins, and so does a check that cannot answer in time. It
            # runs while the statements below do and is waited for before the commit (``_tx``
            # rolls back on any refusal); the deadline is fixed now.
            pending = gateway._start_run_check(order, RUN_RECHECK_TIMEOUT_S)
            try:
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
            except Exception:
                # The re-verify's refusal or changed answer wins over the statement's error, as
                # when it ran before these statements.
                if gateway._join_run_check(pending) != checked:
                    raise Refused("run_check_changed") from None
                raise
            except BaseException:
                # An interrupt in this thread waits for the re-verify too (at most to its
                # deadline) and is then raised as it is, never replaced by a refusal.
                try:
                    gateway._join_run_check(pending)
                except BaseException:  # noqa: BLE001 - the interrupt below is what is raised
                    pass
                raise
            if gateway._join_run_check(pending) != checked:
                raise Refused("run_check_changed")
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
        self._run_check_slots = _Slots()  # this gateway's re-verifies in flight; see _BoundedCaller
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
        if timeout_s is not None:
            return self._join_run_check(self._start_run_check(order, timeout_s))
        check = self._run_check
        if check is None:
            raise Refused("run_check_missing")
        try:
            ok, out = True, check(order.run_id, order.connection, self._plugin_id)
        except Exception as exc:  # noqa: BLE001 - mapped below; an interrupt is not swallowed
            ok, out = False, exc
        return self._run_check_snapshot(ok, out)

    def _start_run_check(self, order: _Order, timeout_s: float) -> tuple:
        """Start the run check on a worker; ``_join_run_check`` waits at most ``timeout_s`` from
        now for its answer. Raises only the pool's own refusals (``run_check_busy``, ...)."""
        check = self._run_check
        if check is None:
            raise Refused("run_check_missing")
        args = (order.run_id, order.connection, self._plugin_id)
        return _RUN_CHECKS.start(check, args, timeout_s, self._run_check_slots)

    def _join_run_check(self, pending: tuple) -> tuple:
        """The started check's answer as a comparable snapshot, or its refusal. Fails closed."""
        ok, out = _RUN_CHECKS.join(pending)  # raises only its own refusals
        return self._run_check_snapshot(ok, out)

    @staticmethod
    def _run_check_snapshot(ok: bool, out: object) -> tuple:
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
            if self._run_check_slots.full() or _RUN_CHECKS.full():
                raise Refused("run_check_busy")  # re-verify workers all hung: refused before the charge
            stage = "authorize"
            _RUN_CHECKS.warm()  # latency only: the booking's re-verify then finds its worker awake
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
