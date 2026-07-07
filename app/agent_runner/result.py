"""WP1b — agent-runner result + status constants."""
from __future__ import annotations

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
    usage_state: str = "ok"             # ok | degraded | exhausted (vendor window after this call)
