"""WP1b — the agent-call spec handed to the runner."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentSpec:
    job_id: str
    runtime: str                       # "claude" | "codex"
    model_alias: str                   # "triage" | "deep" | "codex"
    prompt: str
    schema: dict | None = None         # jsonschema the output must satisfy (Inv 3)
    max_turns: int = 6
    allowed_tools: list[str] = field(default_factory=list)
    timeout_s: float = 120.0
    # Decision-stage jobs (triage/deep/verifier) MUST run tool-starved (SEC-06).
    decision_stage: bool = True
    session_id: str | None = None      # set to resume an existing session
    cwd: str | None = None
