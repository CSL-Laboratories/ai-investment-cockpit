"""WP1b — in-memory per-vendor usage window + breaker state.

WP2/WP5 back this with the persisted ``UsageWindow`` model; the runner only needs the
counter + state + monotonic ``generation`` here. On a limit/health fault the runner
trips the vendor to a non-ok state and bumps ``generation`` (SEC-23) so any analysis
minted against an earlier window is invalidated (never trade on pre-exhaustion output).
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

    def is_open(self, vendor: str) -> bool:
        return self.window(vendor).state != "ok"
