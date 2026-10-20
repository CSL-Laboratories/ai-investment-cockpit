"""WP1a — AgentJob stored model.

An `AgentJob` is one headless agent run (``claude -p`` / ``codex exec``): its
runtime + resolved model pin, the prompt template and its hash, the input refs
it saw, the output schema it was validated against, lifecycle status, usage
estimate, and the captured ``session_id`` for resume/interrogation.

Authority:
- docs/cockpit-plan-original.md §4 (AgentJob field list)
- docs/draft-discord-control-plane.md (session_id capture + resume pattern,
  POST /jobs payload: runtime/model/prompt_template/input_refs/schema_id)
- docs/enhancements-and-additions.md E-06 (provenance: model pinned-not-alias,
  prompt_hash — the resolved pin makes a result reproducible/attributable)
- docs/build-scaffold-ultracode.md WP1a
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class AgentJob(Base):
    __tablename__ = "agent_job"

    # Opaque string primary key (id -> String per field conventions).
    id: Mapped[str] = mapped_column(String, primary_key=True)

    # Which subprocess runtime executed the job (e.g. "claude", "codex").
    runtime: Mapped[str] = mapped_column(String)

    # Alias/logical model requested (e.g. "triage", "deep").
    model: Mapped[str] = mapped_column(String)

    # Resolved pinned model id (E-06: record the pin, not the alias, so the
    # result is reproducible and a model swap is attributable).
    model_pinned: Mapped[str] = mapped_column(String)

    # Prompt template identifier/name; nullable for ad-hoc runs.
    prompt_template: Mapped[str | None] = mapped_column(String, nullable=True)

    # Hex hash of the rendered prompt (E-06 provenance).
    prompt_hash: Mapped[str] = mapped_column(String)

    # Refs to the raw input packet(s) the agent saw (nested -> JSON).
    input_refs: Mapped[dict] = mapped_column(JSON, default=dict)

    # Id of the output JSON Schema the result is validated against; nullable
    # for jobs with no structured-output contract.
    schema_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Lifecycle status (e.g. queued / running / done / quarantined / failed).
    status: Mapped[str] = mapped_column(String)

    # Estimated usage/cost for the run in USD. Scale 8 holds a sub-cent Haiku
    # decision (about $0.001). Nullable until the workflow cost log fills it.
    usage_estimate: Mapped[float | None] = mapped_column(Numeric(18, 8), nullable=True)

    # Captured headless session id for resume/interrogation; nullable until
    # the subprocess returns it.
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Run timing (timestamps -> DateTime(timezone=True)); nullable until set.
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Row creation time (non-nullable per shared model convention).
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
