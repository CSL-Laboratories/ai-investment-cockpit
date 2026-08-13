"""WP5 — deterministic circuit breakers + the L8 usage-window degradation (sec-arch §4
L6/L8; Inv 5/7; SEC-01/SEC-23).

Two distinct degradation families, deliberately kept apart:

**Circuit breakers (L6)** — pure predicates over typed signals: daily realized-loss limit,
N consecutive losing trades, schema-validation failure-rate spike, and a broker-error spike.
Any trip writes a ``BreakerState`` row + ``AuditEntry`` (via
record_state_change, Inv 6) and drops the ladder to ``review_live`` — never increasing
autonomy. ``trip`` returns a ``SystemEvent`` (the bridge renders it; the core never imports
discord). SEC-01 adds a **synchronous** injection/anomaly breaker (``check_order_path``)
that fires INSIDE the order path — before any veto window elapses — and force-disarms.

**Watch-only conditions (L8)** — usage-window exhaustion and stale market data degrade to
WATCH-ONLY (order path disabled, events queued+expired, never dropped) and do not merely drop
to ``review_live``; they bump the monotonic ``UsageTracker`` generation so any analysis is
refused (``assert_fresh_analysis``, SEC-23). Recovery from watch-only is fail-closed to
``review_live`` via ``ModeLadder.recover`` (never a silent re-arm).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from config.settings import Mode
from core.ids import new_id
from schemas.models.breaker_state import BreakerState

from .mode_ladder import ModeLadder
from .state import (
    EVENT_BREAKER_CLEAR,
    EVENT_BREAKER_TRIP,
    EVENT_WATCH_ONLY,
    OrchestratorState,
    SystemEvent,
)

# breaker names (stable identifiers used in BreakerState.breaker_name + audit payloads)
BRK_DAILY_LOSS = "daily_realized_loss"
BRK_CONSECUTIVE_LOSSES = "consecutive_losses"
BRK_SCHEMA_FAILURES = "schema_failure_rate"
BRK_STALE_DATA = "stale_market_data"
BRK_BROKER_ERRORS = "broker_error_spike"
BRK_INJECTION = "injection_anomaly"


class StaleAnalysisError(Exception):
    """SEC-23 — analysis minted before a usage-window generation bump is refused."""


def _dec(x) -> Decimal:
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


@dataclass(frozen=True)
class BreakerThresholds:
    daily_loss_limit: Decimal = Decimal("500")     # trip if realized LOSS magnitude exceeds
    max_consecutive_losses: int = 3
    schema_failure_rate: float = 0.25
    schema_min_sample: int = 8
    data_staleness_s: float = 120.0
    broker_error_limit: int = 5


@dataclass
class BreakerSignals:
    """Runtime signals sampled by the intraday loop.

    BUG-058: ``None`` means NOT SAMPLED this run — that breaker cannot fire and is listed
    by :func:`unsampled` so the absence is visible on the surface. The old defaults were
    benign VALUES (0 / 0.0), which made an unmeasured signal indistinguishable from a
    measured-and-healthy one: four of the five breakers could never fire in production
    because their only caller sampled schema counts alone, while the stale-data breaker —
    Invariant 5's fail-closed control — read "fresh" forever. A sampled-but-benign signal
    must now be passed explicitly.

    Sampling status: ``schema_*`` and ``broker_errors`` are wired (triage tick / gateway).
    ``realized_loss_today``/``consecutive_losses`` arrive with the paper P&L loop (W-22)
    and ``max_data_age_s`` with the RW-14 quote poller — until then they are honestly
    unsampled, never silently fine."""

    realized_loss_today: Decimal | None = None     # positive == a loss
    consecutive_losses: int | None = None
    schema_failures: int | None = None
    schema_validations: int | None = None
    max_data_age_s: float | None = None
    broker_errors: int | None = None


# ---- pure predicates (deterministic; no I/O) ------------------------------------------
def breach_daily_loss(realized_loss, limit) -> bool:
    return _dec(realized_loss) > _dec(limit)


def breach_consecutive_losses(n: int, limit: int) -> bool:
    return n >= limit


def breach_schema_failures(failures: int, total: int, rate: float, min_sample: int) -> bool:
    return total >= min_sample and total > 0 and (failures / total) > rate


def breach_stale_data(age_s: float, limit_s: float) -> bool:
    return age_s > limit_s


def breach_broker_errors(errors: int, limit: int) -> bool:
    return errors >= limit


def evaluate(signals: BreakerSignals, th: BreakerThresholds | None = None) -> list[str]:
    """Return the names of every breaker whose predicate trips for these signals (pure).
    An unsampled (None) signal cannot trip — and cannot read as healthy either; it is
    reported by :func:`unsampled`."""
    th = th or BreakerThresholds()
    tripped: list[str] = []
    if signals.realized_loss_today is not None and breach_daily_loss(
            signals.realized_loss_today, th.daily_loss_limit):
        tripped.append(BRK_DAILY_LOSS)
    if signals.consecutive_losses is not None and breach_consecutive_losses(
            signals.consecutive_losses, th.max_consecutive_losses):
        tripped.append(BRK_CONSECUTIVE_LOSSES)
    if (signals.schema_failures is not None and signals.schema_validations is not None
            and breach_schema_failures(signals.schema_failures, signals.schema_validations,
                                       th.schema_failure_rate, th.schema_min_sample)):
        tripped.append(BRK_SCHEMA_FAILURES)
    if signals.max_data_age_s is not None and breach_stale_data(
            signals.max_data_age_s, th.data_staleness_s):
        tripped.append(BRK_STALE_DATA)
    if signals.broker_errors is not None and breach_broker_errors(
            signals.broker_errors, th.broker_error_limit):
        tripped.append(BRK_BROKER_ERRORS)
    return tripped


def unsampled(signals: BreakerSignals) -> list[str]:
    """The breakers that CANNOT fire this run because their signal was never sampled.
    Surfaced (audited batch payload / status) so a dead wire reads as ABSENT, not healthy —
    the '/quant/status advertising a check that could never fire' rule."""
    out: list[str] = []
    if signals.realized_loss_today is None:
        out.append(BRK_DAILY_LOSS)
    if signals.consecutive_losses is None:
        out.append(BRK_CONSECUTIVE_LOSSES)
    if signals.schema_failures is None or signals.schema_validations is None:
        out.append(BRK_SCHEMA_FAILURES)
    if signals.max_data_age_s is None:
        out.append(BRK_STALE_DATA)
    if signals.broker_errors is None:
        out.append(BRK_BROKER_ERRORS)
    return out


def default_injection_predicate(context=None) -> bool:
    """SEC-01 fallback used when a caller (e.g. ``AutoVetoCoordinator.resolve``) does not pass
    an explicit predicate. Callers pass a DICT context, so it FAILS CLOSED off dict keys —
    mirroring ``workflows.grounding.injection_predicate`` (dependency-light: no import of
    workflows here). Trips on a grounding-flagged proposal, a same-ticker escalation burst
    (count >= 4, or malformed burst), or an explicit ``injection_detected`` flag. A non-dict
    context still honours an ``injection_detected`` attribute."""
    if not context:
        return False
    if isinstance(context, dict):
        if context.get("injection_detected") or context.get("grounding_flagged"):
            return True
        burst = context.get("same_ticker_burst")
        if isinstance(burst, dict):
            try:
                if int(burst.get("count", 0)) >= 4:
                    return True
            except (TypeError, ValueError):
                return True   # malformed burst context fails closed
        return False
    return bool(getattr(context, "injection_detected", False))


class BreakerEngine:
    def __init__(self, st: OrchestratorState) -> None:
        self.st = st
        self._ladder = ModeLadder(st)

    def _mode_str(self) -> str:
        return self._ladder.current().value

    # ---- trip / clear ------------------------------------------------------------------
    def trip(self, name: str, reason: str, *, clock=None, actor="system") -> SystemEvent:
        """Record a breaker trip (BreakerState + AuditEntry, atomic) and drop to
        ``review_live``. Returns a ``#system`` event."""
        clock = clock or self.st.clock
        now = clock.now()
        row = BreakerState(
            id=new_id("brk"), breaker_name=name, tripped=True, tripped_at=now,
            reason=reason, created_at=now,
        )
        self.st.audit.record_state_change(
            row, EVENT_BREAKER_TRIP, actor=actor, payload={"breaker": name, "reason": reason}
        )
        mode_after = self._ladder.drop_to_review_live(
            f"breaker {name}: {reason}", clock=clock, actor=actor, kind="breaker_drop"
        )
        return SystemEvent("#system", "breaker_trip", name, reason, mode_after.value, now)

    def clear(self, name: str, reason: str, *, clock=None, actor="operator") -> BreakerState:
        """Record a breaker clear. Does NOT re-arm any mode — recovery to an auto mode is an
        explicit daily re-arm (SEC-23)."""
        clock = clock or self.st.clock
        now = clock.now()
        row = BreakerState(
            id=new_id("brk"), breaker_name=name, tripped=False, cleared_at=now,
            reason=reason, created_at=now,
        )
        self.st.audit.record_state_change(
            row, EVENT_BREAKER_CLEAR, actor=actor, payload={"breaker": name, "reason": reason}
        )
        return row

    def evaluate_and_trip(self, signals: BreakerSignals, th: BreakerThresholds | None = None,
                          *, clock=None, actor="system") -> list[SystemEvent]:
        """Route stale data to L8 watch-only; trip every other threshold through L6."""
        events = []
        for name in evaluate(signals, th):
            if name == BRK_STALE_DATA:
                if not self.is_watch_only("market_data"):
                    events.append(self.enter_watch_only(
                        "market_data", "stale market-data threshold breached",
                        clock=clock, actor=actor,
                    ))
            else:
                events.append(self.trip(name, "threshold breached", clock=clock, actor=actor))
        return events

    # ---- SEC-01 synchronous injection/anomaly breaker in the order path ----------------
    def check_order_path(self, *, context=None, predicate=None, reason="injection/anomaly detected",
                         clock=None, actor="system") -> SystemEvent | None:
        """Fire SYNCHRONOUSLY before any auto-execution (before a veto window elapses / an
        ``auto_full`` submit). If the injection/anomaly predicate trips, force-disarm to
        ``review_live`` and audit. Returns the event, or ``None`` if clean."""
        pred = predicate or default_injection_predicate
        if not pred(context):
            return None
        return self.trip(BRK_INJECTION, reason, clock=clock, actor=actor)

    # ---- L8 usage-window exhaustion -> WATCH-ONLY (separate from L6) --------------------
    def enter_watch_only(self, vendor: str, reason: str, *, clock=None, actor="system") -> SystemEvent:
        """Degrade to watch-only on usage-window exhaustion: order path disabled, events
        queued+expired (never dropped), monotonic generation bumped so pre-exhaustion
        analysis is refused (SEC-23). Does NOT drop the mode to review_live (L8, not L6)."""
        clock = clock or self.st.clock
        w = self.st.usage.trip(vendor, "exhausted")   # bumps generation
        self.st.audit.append(
            EVENT_WATCH_ONLY, actor=actor, refs={"vendor": vendor},
            payload={"vendor": vendor, "state": w.state, "generation": w.generation,
                     "reason": reason, "watch_only": True},
        )
        return SystemEvent("#system", "watch_only", None, reason, self._mode_str(), clock.now())

    def is_watch_only(self, vendor: str | None = None) -> bool:
        """True iff a (or the given) vendor window is exhausted — the order path is disabled
        while any vendor is watch-only."""
        if vendor is not None:
            return self.st.usage.window(vendor).state == "exhausted"
        return any(w.state == "exhausted" for w in self.st.usage._w.values())

    def current_generation(self, vendor: str) -> int:
        return self.st.usage.window(vendor).generation

    def assert_fresh_analysis(self, vendor: str, analysis_generation: int) -> None:
        """SEC-23 — refuse analysis minted before the current usage-window generation (a
        blackout must not become cover for auto-execution after a reset)."""
        cur = self.current_generation(vendor)
        if analysis_generation < cur:
            raise StaleAnalysisError(
                f"analysis generation {analysis_generation} < current {cur} for {vendor} (SEC-23)"
            )

    def recover_from_watch_only(self, vendor: str, prev_state, *, clock=None,
                                actor="system") -> tuple[SystemEvent, Mode]:
        """Exit watch-only: replenish the window (new generation, so anything minted during
        the blackout is also invalidated) and recover FAIL-CLOSED to ``review_live`` — never
        the prior auto mode (SEC-23)."""
        clock = clock or self.st.clock
        w = self.st.usage.window(vendor)
        w.state = "ok"
        w.generation += 1
        mode_after = self._ladder.recover(prev_state, clock=clock, actor=actor)
        self.st.audit.append(
            EVENT_WATCH_ONLY, actor=actor, refs={"vendor": vendor},
            payload={"vendor": vendor, "state": "ok", "generation": w.generation,
                     "recovered": True, "mode_after": mode_after.value},
        )
        event = SystemEvent("#system", "watch_only", None, "recovered", mode_after.value, clock.now())
        return event, mode_after
