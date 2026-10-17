"""Session-local vendor accounting plus fail-closed watch-only authority views.

``used`` is deliberately process-local. Safety state is different: a committed
``state``/``generation`` comes only from the signed ledger, while an admitted transition
immediately denies orders until that exact transition commits or is reconciled. The
orchestrator owns persistence and the short admission lock; this object only publishes
coherent in-memory views under its own lock.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass


_STATES = frozenset({"ok", "degraded", "exhausted"})


@dataclass
class VendorWindow:
    vendor: str
    used: int = 0
    generation: int = 0
    state: str = "ok"


@dataclass
class SafetyTransition:
    transition_id: str
    vendor: str
    kind: str                    # trip | recovery
    target_state: str
    generation: int
    reason: str
    actor: str
    phase: str = "pending"       # pending | persisting | failed
    error: str | None = None


class UsageTracker:
    def __init__(self) -> None:
        self._w: dict[str, VendorWindow] = {}
        self._pending: dict[str, SafetyTransition] = {}
        self._lock = threading.RLock()
        self.integrity_error: str | None = None
        self.admission_version = 0

    def window(self, vendor: str) -> VendorWindow:
        with self._lock:
            return self._w.setdefault(vendor, VendorWindow(vendor))

    def record(self, vendor: str, n: int = 1) -> VendorWindow:
        with self._lock:
            w = self._w.setdefault(vendor, VendorWindow(vendor))
            w.used += n
            return w

    def trip(self, vendor: str, state: str = "exhausted") -> VendorWindow:
        """Compatibility mutation for isolated runner tests, never production authority.

        Production health faults receive an injected BreakerEngine callback and therefore
        use ``admit_trip`` plus signed persistence. Keeping this small method avoids making
        the dependency-free runner construct orchestrator/storage objects.
        """
        if state not in _STATES - {"ok"}:
            raise ValueError(f"invalid trip state {state!r}")
        with self._lock:
            w = self._w.setdefault(vendor, VendorWindow(vendor))
            w.state = state
            w.generation += 1
            self.admission_version += 1
            return w

    def restore(self, vendor: str, *, state: str, generation: int) -> VendorWindow:
        """Restore one signed committed snapshot without manufacturing a generation."""
        if not isinstance(vendor, str) or not vendor.strip():
            raise ValueError("usage vendor must be a non-empty string")
        if state not in _STATES:
            raise ValueError(f"invalid usage state {state!r}")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError(f"invalid usage generation {generation!r}")
        vendor = vendor.strip()
        with self._lock:
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
            used = current.used if current is not None else 0
            restored = VendorWindow(
                vendor=vendor, used=used, generation=generation, state=state
            )
            self._w[vendor] = restored
            return restored

    def admit_trip(
        self, vendor: str, *, state: str, transition_id: str, reason: str, actor: str
    ) -> tuple[SafetyTransition | None, bool]:
        """Synchronously deny, returning ``(transition, caller_must_persist)``.

        A retry of a failed open episode reuses its identity/generation. A trip admitted
        over a pending recovery supersedes it and allocates above that candidate so a late
        recovery commit cannot create contradictory equal-generation authority.
        """
        if state not in _STATES - {"ok"}:
            raise ValueError(f"invalid trip state {state!r}")
        if not isinstance(vendor, str) or not vendor.strip():
            raise ValueError("usage vendor must be a non-empty string")
        vendor = vendor.strip()
        with self._lock:
            current = self._w.setdefault(vendor, VendorWindow(vendor))
            pending = self._pending.get(vendor)
            if pending is not None and pending.kind == "trip":
                if pending.phase == "persisting":
                    return pending, False
                pending.phase, pending.error = "pending", None
                self.admission_version += 1
                return pending, True
            if pending is None and current.state != "ok":
                return None, False
            generation = max(
                current.generation,
                pending.generation if pending is not None else current.generation,
            ) + 1
            transition = SafetyTransition(
                transition_id=transition_id,
                vendor=vendor,
                kind="trip",
                target_state=state,
                generation=generation,
                reason=reason,
                actor=actor,
            )
            self._pending[vendor] = transition
            self.admission_version += 1
            return transition, True

    def admit_recovery(
        self, vendor: str, *, transition_id: str, actor: str
    ) -> tuple[SafetyTransition | None, bool]:
        """Reserve recovery without publishing permission; committed denial remains effective."""
        if not isinstance(vendor, str) or not vendor.strip():
            raise ValueError("usage vendor must be a non-empty string")
        vendor = vendor.strip()
        with self._lock:
            current = self._w.setdefault(vendor, VendorWindow(vendor))
            pending = self._pending.get(vendor)
            if pending is not None:
                if pending.kind != "recovery":
                    return None, False
                if pending.phase == "persisting":
                    return pending, False
                pending.phase, pending.error = "pending", None
                self.admission_version += 1
                return pending, True
            if current.state == "ok" or self.integrity_error:
                return None, False
            transition = SafetyTransition(
                transition_id=transition_id,
                vendor=vendor,
                kind="recovery",
                target_state="ok",
                generation=current.generation + 1,
                reason="recovered",
                actor=actor,
            )
            self._pending[vendor] = transition
            self.admission_version += 1
            return transition, True

    def mark_persisting(self, transition: SafetyTransition) -> bool:
        with self._lock:
            current = self._pending.get(transition.vendor)
            if current is not transition:
                return False
            transition.phase, transition.error = "persisting", None
            return True

    def mark_committed(self, transition: SafetyTransition) -> VendorWindow:
        """Publish a proven committed event, even if a newer denial superseded it."""
        with self._lock:
            window = self._w.setdefault(transition.vendor, VendorWindow(transition.vendor))
            if transition.generation < window.generation:
                if self._pending.get(transition.vendor) is transition:
                    raise ValueError("current committed watch-only generation regressed")
                return window  # delayed publication already superseded by a newer commit
            if (transition.generation == window.generation
                    and transition.target_state != window.state):
                raise ValueError("committed watch-only generation conflicts")
            window.state = transition.target_state
            window.generation = transition.generation
            if self._pending.get(transition.vendor) is transition:
                self._pending.pop(transition.vendor, None)
            self.admission_version += 1
            return window

    def mark_failed(self, transition: SafetyTransition, error: str) -> None:
        with self._lock:
            if self._pending.get(transition.vendor) is transition:
                transition.phase = "failed"
                transition.error = error
                self.admission_version += 1

    def transition_is_current(self, transition: SafetyTransition) -> bool:
        with self._lock:
            return self._pending.get(transition.vendor) is transition

    def pending_transition(self, vendor: str) -> SafetyTransition | None:
        with self._lock:
            return self._pending.get(vendor)

    def note_admission_change(self) -> None:
        with self._lock:
            self.admission_version += 1

    def fail_closed(self, reason: str) -> VendorWindow:
        """Install a sticky global integrity denial; a later green read cannot clear it."""
        with self._lock:
            self.integrity_error = str(reason)
            window = self._w.get("__watch_only_integrity__")
            if window is None:
                window = VendorWindow(
                    "__watch_only_integrity__",
                    generation=2**63 - 1,
                    state="exhausted",
                )
                self._w[window.vendor] = window
            self.admission_version += 1
            return window

    def is_open(self, vendor: str) -> bool:
        with self._lock:
            pending = self._pending.get(vendor)
            return bool(
                self.integrity_error
                or pending is not None
                or self._w.setdefault(vendor, VendorWindow(vendor)).state != "ok"
            )

    def denial_reason(self, vendor: str | None = None) -> str | None:
        """Return a stable internal refusal token for effective in-memory authority."""
        with self._lock:
            if self.integrity_error:
                return (
                    "final_audit_unverified"
                    if self.integrity_error.startswith("final audit")
                    else "watch_only_persistence_failed"
                )
            if vendor is not None:
                pending = [self._pending.get(vendor)]
            else:
                pending = [
                    transition for name, transition in self._pending.items()
                    if name != "jev"
                ]
            pending = [transition for transition in pending if transition is not None]
            if pending:
                return (
                    "watch_only_persistence_failed"
                    if any(transition.phase == "failed" for transition in pending)
                    else "watch_only_pending"
                )
            if vendor is not None:
                return (
                    "watch_only_active"
                    if self._w.setdefault(vendor, VendorWindow(vendor)).state != "ok"
                    else None
                )
            # ``jev`` is a provider cooldown. Claude still writes the analysis, so
            # that window must not freeze triage or the unscoped order view.
            return (
                "watch_only_active"
                if any(
                    window.state != "ok" and window.vendor != "jev"
                    for window in self._w.values()
                )
                else None
            )

    def committed_snapshot(self) -> dict[str, dict[str, int | str]]:
        with self._lock:
            return {
                vendor: {"state": window.state, "generation": window.generation}
                for vendor, window in sorted(self._w.items())
                if vendor != "__watch_only_integrity__"
                and not (window.state == "ok" and window.generation == 0)
            }

    def status_snapshot(self) -> dict:
        with self._lock:
            return {
                "committed": {
                    vendor: {
                        "state": window.state,
                        "used": window.used,
                        "generation": window.generation,
                    }
                    for vendor, window in sorted(self._w.items())
                },
                "pending": {
                    vendor: {
                        "transition_id": transition.transition_id,
                        "kind": transition.kind,
                        "target_state": transition.target_state,
                        "generation": transition.generation,
                        "phase": transition.phase,
                        "persistence_error": transition.error,
                    }
                    for vendor, transition in sorted(self._pending.items())
                },
                "integrity_error": self.integrity_error,
                "admission_version": self.admission_version,
            }
