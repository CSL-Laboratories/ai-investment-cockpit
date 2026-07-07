"""WP1a — AutomationRule stored model.

A compiled custom automation rule (SEC-19 / draft-2 /automation). The operator
states a rule in natural language; a cheap model compiles it to a structured
``predicate`` over TradeProposal fields; the human confirms exactly one
serialization whose ``confirmed_hash`` the risk engine then enforces
deterministically. Any drift between the stored predicate and the confirmed
hash disables the rule (needs_human_review). LLMs draft rules; they never *are*
the rules.

Design authority: draft-discord-control-plane.md /automation section,
security-hardening-backlog.md SEC-19, build-scaffold-ultracode.md WP1a. Do not
re-decide settled design here.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Numeric, DateTime, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class AutomationRule(Base):
    __tablename__ = "automation_rule"

    # id == rule_id: opaque string primary key.
    id: Mapped[str] = mapped_column(String, primary_key=True)

    # The operator's rule as originally stated (source of the compile step).
    natural_language: Mapped[str] = mapped_column(String)

    # Compiled structured predicate over TradeProposal fields (nested/dynamic).
    predicate: Mapped[dict] = mapped_column(JSON, default=dict)

    # Hex hash of the exact CompiledRule the human confirmed; risk/ enforces
    # ONLY this hash — any drift disables the rule (SEC-19).
    confirmed_hash: Mapped[str] = mapped_column(String)

    # Deterministically-computed worst-case exposure ($/interval) shown at
    # confirm time and hard-capped in risk/ (SEC-19).
    worst_case_notional: Mapped[float] = mapped_column(Numeric(18, 2))

    # Symbols the rule is scoped to (nested/dynamic -> JSON).
    symbols_scope: Mapped[list] = mapped_column(JSON, default=list)

    # Rule only authorizes trades in already-held positions when true.
    existing_positions_only: Mapped[bool] = mapped_column(Boolean, default=False)

    # Active flag; drift / breaker trip / expiry flips this off.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Required expiry + re-confirmation (SEC-19): a rule ALWAYS expires (non-null),
    # matching the CompiledRule agent-output contract it is persisted from.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
