"""The kill switch's reach across venues (PRODUCT.md 4.2, Q26; F44, CSL-743). Paper and mock only.

The order halt is set first, in one committed step, before any venue is called. Then
each venue kill runs (Kalshi, Coinbase, Gemini, or any object with the same ``kill()``
contract); one venue failing or raising never skips the others. The switch is
``confirmed`` only when the halt and every venue are; otherwise it is ``pending`` and
calling ``kill`` again retries.
"""

from __future__ import annotations

import threading
from typing import Callable, Protocol, Sequence

__all__ = ["KillSwitch", "VenueKill"]


class VenueKill(Protocol):
    def kill(self) -> dict: ...  # {"venue": str, "status": "confirmed" | "pending", ...}


class KillSwitch:
    def __init__(self, halt: Callable[[], object], venues: Sequence[VenueKill]) -> None:
        self._halt = halt
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
            for venue in self._venues:
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
