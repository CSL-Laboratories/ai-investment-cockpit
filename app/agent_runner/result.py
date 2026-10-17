"""WP1b — agent-runner result + status constants."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

OK = "ok"
NEEDS_HUMAN = "needs_human_review"   # quarantine (Inv 3)
LIMIT_HIT = "limit_hit"              # usage/health fault -> breaker, fail-closed (Inv 7 / SEC-23)
TIMEOUT = "timeout"
ERROR = "error"


@dataclass
class AgentResult:
    status: str
    validated: dict | None = None       # schema-valid output, only when status == OK
    raw: str = ""
    session_id: str | None = None
    repaired: bool = False
    error: str | None = None
    prior_raw: str = ""                 # first attempt's raw output when a repair call replaced it
    dispatched: bool = True             # False when refused before any subprocess was spawned
    usage_state: str = "ok"           # ok | degraded | exhausted (vendor window after this call)


# Saga pivot for an unused deep-escalation reservation. Set once a child
# process exists or a Jev request is sent. Entering call_agent, run_agent
# preflight, or transport.run is not enough: a refusal before spawn still
# releases the reservation.
_AGENT_DISPATCHED: ContextVar[bool] = ContextVar(
    "cockpit_agent_dispatched", default=False)


def watch_agent_dispatch() -> Token[bool]:
    """Mark the following span as not yet dispatched."""
    return _AGENT_DISPATCHED.set(False)


def agent_dispatched() -> bool:
    return _AGENT_DISPATCHED.get()


def restore_agent_dispatch_watch(token: Token[bool]) -> None:
    _AGENT_DISPATCHED.reset(token)


def mark_agent_dispatched() -> None:
    """Record that a model call has reached the transport or Jev HTTP boundary."""
    _AGENT_DISPATCHED.set(True)
