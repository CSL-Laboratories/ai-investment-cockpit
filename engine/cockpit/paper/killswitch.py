"""The kill switch's reach across venues (PRODUCT.md 4.2, Q26, O42; F44, CSL-743). Paper and mock only.

The order halt is set first, in one committed step, before any venue is called. Then the
paper book's working orders are cancelled (the paper book is a venue too: O42, K1). Both
run in the calling thread. The paper cancel is ``confirmed`` only when the book re-read
after it lists no working order. The halt is ``confirmed`` only when
``paper_book.halted()`` reads ``True`` after the halt call; a callable that returns
without this book reading halted stays pending. A refusal from the book (a closed book,
say) is reported as ``refused:<code>``, any other failure as ``error:<Class>``.

Then every venue kill runs (Kalshi, Coinbase, Gemini, or any object with the same
``kill()`` contract), each in its own daemon thread, all started before any is waited
for, so one venue failing, raising or hanging never skips or stalls another:

* a venue that raises anything, ``BaseException`` included (``asyncio.CancelledError``,
  say), is ``pending`` with detail ``kill: error:<Class>``. A ``KeyboardInterrupt`` or
  ``SystemExit`` caught from the halt, the paper cancel or a venue is raised again from
  ``kill`` only after every venue has been attempted;
* a venue that has not returned within ``venue_timeout_s`` of the venues being started
  (default 10 seconds, one bound for all of them) is ``pending`` with detail
  ``kill: timeout``. Its thread is left to finish and its late result is discarded;
* a later ``kill`` never starts a second call on a venue whose earlier call is still
  running: that venue is ``pending`` with detail ``kill: in_flight``, and the other
  venues, the halt and the paper cancel run as usual. ``kill`` itself returns within the
  bound however long a venue hangs.

Nothing is kept between calls: every ``kill`` calls the halt, the paper cancel and every
venue again, and reports only what this call saw.

A venue counts as ``confirmed`` only on revoke evidence from that ``kill()`` call. Its
result must be exactly a ``dict`` in which, all as exact ``str`` (not a subclass, so the
comparison is ``str.__eq__``): ``status`` is ``"confirmed"``, ``halt`` is ``"confirmed"``,
its one cancel leg (``cancel_all`` or ``cancel_orders``) is ``"confirmed"``, and either

* ``revoke_grant`` is ``"confirmed"`` and ``revoke_evidence`` is exactly a ``dict`` whose
  ``http_status`` is exactly the ``int`` 200 and whose ``probe_api`` and
  ``probe_refresh`` are each ``"unauthorized"`` (an OAuth grant: the revoke answered 200
  and both the API call and the refresh-token exchange were then refused); or
* the venue is ``"kalshi"``, ``revoke_key`` is ``"confirmed"`` and ``revoke_evidence``
  holds ``accepted`` exactly ``True`` and ``probe`` ``"unauthorized"`` (an API key has no
  refresh-token exchange: the accepted revoke and its one authenticated probe).

A bare ``{"status": "confirmed"}`` is not evidence. Anything else (another type, a
``str`` subclass, an object whose ``__eq__`` or ``__ne__`` claims equality, a missing
leg or probe) is reported with the exact ``str`` ``"pending"``; a result that claimed
``confirmed`` without the evidence also carries ``evidence: missing``.

The switch's own ``status``:

* ``confirmed`` only with at least one venue composed, when the halt, the paper cancel
  and every venue are confirmed as above;
* ``paper_only`` when no venue is composed and the halt and the paper cancel are both
  confirmed. It is terminal and truthful: trading is halted and every paper order is
  cancelled, and no credential exists to revoke, so nothing was revoked or probed;
* ``pending`` otherwise; calling ``kill`` again retries.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Protocol, Sequence

from .gateway import Refused

__all__ = ["DEFAULT_VENUE_TIMEOUT_S", "KillSwitch", "PaperBookKill", "VenueKill"]

DEFAULT_VENUE_TIMEOUT_S = 10.0
_CANCEL_LEGS = ("cancel_all", "cancel_orders")


class VenueKill(Protocol):
    # {"venue": str, "status": "confirmed" | "pending", "halt": ..., a cancel leg, a revoke
    #  leg, "revoke_evidence": {...}, ...}; see the module doc for what confirms.
    def kill(self) -> dict: ...


class _PaperBook(Protocol):
    def cancel_all(self) -> int: ...
    def working(self) -> int: ...
    def halted(self) -> bool: ...


def _why(exc: BaseException) -> str:
    """``refused:<code>`` for the book's own typed refusal, ``error:<Class>`` for anything else."""
    if type(exc) is Refused and type(exc.code) is str:
        return "refused:" + exc.code
    return "error:" + type(exc).__name__


def _is(value: object, want: str) -> bool:
    """Exact ``str`` only: compared by ``str.__eq__``, never by the value's own."""
    return type(value) is str and value == want


def _venue_confirmed(out: object) -> bool:
    """True only when the venue result carries this kill's revoke evidence (module doc)."""
    if type(out) is not dict:
        return False
    if not _is(out.get("status"), "confirmed") or not _is(out.get("halt"), "confirmed"):
        return False
    cancel_legs = [leg for leg in _CANCEL_LEGS if leg in out]
    if len(cancel_legs) != 1 or not _is(out[cancel_legs[0]], "confirmed"):
        return False
    evidence = out.get("revoke_evidence")
    if type(evidence) is not dict:
        return False
    if "revoke_grant" in out and "revoke_key" not in out:
        http_status = evidence.get("http_status")
        return (_is(out["revoke_grant"], "confirmed") and type(http_status) is int and http_status == 200
                and _is(evidence.get("probe_api"), "unauthorized")
                and _is(evidence.get("probe_refresh"), "unauthorized"))
    if "revoke_key" in out and "revoke_grant" not in out:
        return (_is(out.get("venue"), "kalshi") and _is(out["revoke_key"], "confirmed")
                and evidence.get("accepted") is True and _is(evidence.get("probe"), "unauthorized"))
    return False


def _run_venue(venue: VenueKill, box: list) -> None:
    try:
        box.append((True, venue.kill()))
    except BaseException as exc:  # noqa: BLE001 - reported by the switch; one venue never skips another
        box.append((False, exc))


class PaperBookKill:
    """Cancel every working paper order; confirmed only when the book then lists none working."""

    def __init__(self, book: _PaperBook) -> None:
        self._book = book

    def kill(self) -> dict:
        out = {"venue": "paper", "status": "pending", "cancelled": 0, "detail": {}}
        try:
            out["cancelled"] = self._book.cancel_all()
        except Exception as exc:  # noqa: BLE001 - "unknown" is never "done"
            out["detail"]["cancel_orders"] = _why(exc)
        try:
            remaining = self._book.working()
        except Exception as exc:  # noqa: BLE001
            out["detail"].setdefault("cancel_orders", "relist_error:" + type(exc).__name__)
            return out
        if type(remaining) is not int or remaining != 0:
            out["detail"].setdefault("cancel_orders", f"remaining:{remaining}")
            return out
        if not out["detail"]:
            out["status"] = "confirmed"
        return out


class KillSwitch:
    def __init__(self, halt: Callable[[], object], venues: Sequence[VenueKill], *, paper_book: _PaperBook,
                 venue_timeout_s: float = DEFAULT_VENUE_TIMEOUT_S) -> None:
        if type(venue_timeout_s) not in (int, float) or not 0 < venue_timeout_s < float("inf"):
            raise ValueError("venue_timeout_s must be a positive, finite number of seconds")
        self._halt = halt
        self._paper_book = paper_book
        self._paper = PaperBookKill(paper_book)
        self._venues = tuple(venues)
        self._venue_timeout_s = venue_timeout_s
        self._lock = threading.Lock()
        self._in_flight: dict[int, threading.Thread] = {}  # venue index -> its unfinished kill thread

    @staticmethod
    def _pending(venue: object, why: str) -> dict:
        return {"venue": type(venue).__name__, "status": "pending", "detail": {"kill": why}}

    def _kill_venues(self, interrupts: list) -> list:
        """Start every venue kill, then wait for all of them under one bound."""
        runs = []
        for index, venue in enumerate(self._venues):
            earlier = self._in_flight.get(index)
            if earlier is not None and earlier.is_alive():
                runs.append((venue, None, None))  # never a second concurrent kill on one venue
                continue
            box: list = []
            thread = threading.Thread(target=_run_venue, args=(venue, box), daemon=True,
                                      name="venue-kill-%d" % index)
            try:
                thread.start()
            except Exception as exc:  # noqa: BLE001 - no thread to run it on: unknown, never done
                self._in_flight.pop(index, None)
                runs.append((venue, None, [(False, exc)]))
                continue
            self._in_flight[index] = thread
            runs.append((venue, thread, box))
        deadline = time.monotonic() + self._venue_timeout_s
        results = []
        for venue, thread, box in runs:
            if box is None:
                results.append(self._pending(venue, "in_flight"))
                continue
            if thread is not None:
                thread.join(max(0.0, deadline - time.monotonic()))
            finished = list(box)  # one read: a result landing after it belongs to no kill
            if not finished:
                results.append(self._pending(venue, "timeout"))
                continue
            returned, value = finished[0]
            if returned:
                results.append(value)
                continue
            if isinstance(value, (KeyboardInterrupt, SystemExit)):
                interrupts.append(value)
            results.append(self._pending(venue, "error:" + type(value).__name__))
        return results

    def kill(self) -> dict:
        with self._lock:
            interrupts: list = []  # raised again only after every venue was attempted
            halt_error = None
            try:
                self._halt()
            except BaseException as exc:  # noqa: BLE001 - still reach every venue
                halt_error = _why(exc)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    interrupts.append(exc)
            try:
                read = self._paper_book.halted()
            except BaseException as exc:  # noqa: BLE001
                halt = halt_error or _why(exc)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    interrupts.append(exc)
            else:
                # Confirm on the book read, not on the callable returning.
                halt = "confirmed" if read is True else (halt_error or "not_halted")
            try:
                paper = self._paper.kill()
            except BaseException as exc:  # noqa: BLE001
                paper = {"venue": "paper", "status": "pending", "cancelled": 0,
                         "detail": {"cancel_orders": "error:" + type(exc).__name__}}
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    interrupts.append(exc)
            paper_confirmed = _is(paper.get("status"), "confirmed")
            venues = [paper]
            venues_confirmed = True
            for out in self._kill_venues(interrupts):
                if not _venue_confirmed(out):
                    claimed = type(out) is dict and _is(out.get("status"), "confirmed")
                    out = dict(out) if type(out) is dict else {"venue": "unreadable"}
                    out["status"] = "pending"
                    if claimed:
                        out["evidence"] = "missing"  # said confirmed without this kill's revoke evidence
                    venues_confirmed = False
                venues.append(out)
            if interrupts:
                raise interrupts[0]
            if halt != "confirmed" or not paper_confirmed or not venues_confirmed:
                status = "pending"
            else:
                # With no venue there is no credential to revoke: halted and paper cancelled, not "confirmed".
                status = "confirmed" if self._venues else "paper_only"
            return {"status": status, "halt": halt, "venues": venues}
