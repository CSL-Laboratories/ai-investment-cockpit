"""The kill switch's reach across venues (PRODUCT.md 4.2, Q26, O42; F44, CSL-743). Paper and mock only.

The order halt is set first, in one committed step, before any venue is called. Then the
paper book's working orders are cancelled (the paper book is a venue too: O42, K1), and
each venue kill runs (Kalshi, Coinbase, Gemini, or any object with the same ``kill()``
contract); one venue failing or raising never skips the others. The paper cancel is
``confirmed`` only when the book re-read after it lists no working order. The switch is
``confirmed`` only when the halt, the paper cancel and every venue are; otherwise it is
``pending`` and calling ``kill`` again retries.
"""

from __future__ import annotations

import threading
from typing import Callable, Protocol, Sequence

__all__ = ["KillSwitch", "PaperBookKill", "VenueKill"]


class VenueKill(Protocol):
    def kill(self) -> dict: ...  # {"venue": str, "status": "confirmed" | "pending", ...}


class _PaperBook(Protocol):
    def cancel_all(self) -> int: ...
    def working(self) -> int: ...


class PaperBookKill:
    """Cancel every working paper order; confirmed only when the book then lists none working."""

    def __init__(self, book: _PaperBook) -> None:
        self._book = book

    def kill(self) -> dict:
        out = {"venue": "paper", "status": "pending", "cancelled": 0, "detail": {}}
        try:
            out["cancelled"] = self._book.cancel_all()
        except Exception as exc:  # noqa: BLE001 - "unknown" is never "done"
            out["detail"]["cancel_orders"] = "error:" + type(exc).__name__
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
    def __init__(self, halt: Callable[[], object], venues: Sequence[VenueKill], *, paper_book: _PaperBook) -> None:
        self._halt = halt
        self._paper = PaperBookKill(paper_book)
        self._venues = tuple(venues)
        self._lock = threading.Lock()

    def kill(self) -> dict:
        with self._lock:
            halt = "confirmed"
            try:
                self._halt()
            except Exception as exc:  # noqa: BLE001 - still reach every venue
                halt = "error:" + type(exc).__name__
            venues = []
            for venue in (self._paper, *self._venues):
                try:
                    out = venue.kill()
                except Exception as exc:  # noqa: BLE001 - one venue never skips another
                    out = {"venue": type(venue).__name__, "status": "pending",
                           "detail": {"kill": "error:" + type(exc).__name__}}
                if type(out) is not dict or out.get("status") != "confirmed":
                    out = dict(out) if type(out) is dict else {"venue": type(venue).__name__}
                    out["status"] = "pending"
                venues.append(out)
            done = halt == "confirmed" and all(v["status"] == "confirmed" for v in venues)
            return {"status": "confirmed" if done else "pending", "halt": halt, "venues": venues}
