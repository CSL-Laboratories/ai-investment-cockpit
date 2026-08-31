"""WP1b — per-process vendor usage facade with durable safety-state restoration.

The request counter is session-local.  The load-bearing ``state`` and monotonic
``generation`` are restored by :class:`orchestrator.state.OrchestratorState` from the
HMAC-signed ``EVENT_WATCH_ONLY`` ledger at composition time.  The runner only needs this
small mutable facade: on a limit/health fault it trips the vendor and bumps ``generation``
(SEC-23), invalidating analysis minted against an earlier window.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VendorWindow:
    vendor: str
    used: int = 0
    generation: int = 0
    state: str = "ok"          # ok | degraded | exhausted


class UsageTracker:
    def __init__(self) -> None:
        self._w: dict[str, VendorWindow] = {}
        # Set only when startup could not prove a trustworthy watch-only snapshot.  A
        # synthetic exhausted window then disables the global order path while awareness
        # can still boot and expose the failure.
        self.integrity_error: str | None = None

    def window(self, vendor: str) -> VendorWindow:
        return self._w.setdefault(vendor, VendorWindow(vendor))

    def record(self, vendor: str, n: int = 1) -> VendorWindow:
        w = self.window(vendor)
        w.used += n
        return w

    def trip(self, vendor: str, state: str = "exhausted") -> VendorWindow:
        w = self.window(vendor)
        w.state = state
        w.generation += 1          # SEC-23: invalidate pre-exhaustion analysis
        return w

    def restore(self, vendor: str, *, state: str, generation: int) -> VendorWindow:
        """Restore one signed safety snapshot without manufacturing a new generation.

        The ledger is scanned in sequence order, so generations may only advance.  Equal
        generations must describe the same state; anything else is malformed authority and
        the composition layer converts it to a global fail-closed sentinel.
        """
        if not isinstance(vendor, str) or not vendor.strip():
            raise ValueError("usage vendor must be a non-empty string")
        if state not in {"ok", "degraded", "exhausted"}:
            raise ValueError(f"invalid usage state {state!r}")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError(f"invalid usage generation {generation!r}")
        vendor = vendor.strip()
        current = self._w.get(vendor)
        if current is not None:
            if generation < current.generation:
                raise ValueError(
                    f"usage generation regressed for {vendor}: "
                    f"{generation} < {current.generation}"
                )
            if generation == current.generation and state != current.state:
                raise ValueError(
                    f"usage state conflicts at generation {generation} for {vendor}"
                )
        restored = VendorWindow(vendor=vendor, generation=generation, state=state)
        self._w[vendor] = restored
        return restored

    def fail_closed(self, reason: str) -> VendorWindow:
        """Disable every order path when durable usage authority cannot be trusted."""
        self.integrity_error = str(reason)
        return self.restore(
            "__watch_only_integrity__",
            state="exhausted",
            generation=(2**63 - 1),
        )

    def is_open(self, vendor: str) -> bool:
        return self.window(vendor).state != "ok"
