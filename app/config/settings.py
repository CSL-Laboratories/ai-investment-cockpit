"""WP0 — application settings (pydantic-settings).

Mode defaults to ``read_only`` (the safe floor of the autonomy ladder). Secrets are
NOT read from env here — they live in keyring (``core.secrets``, Invariant 4). Env
carries only non-secret operational config, prefixed ``COCKPIT_``.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(str, Enum):
    """Autonomy ladder (docs/draft-discord-control-plane). Boots at the safe floor."""

    read_only = "read_only"
    paper = "paper"
    review_live = "review_live"
    auto_veto = "auto_veto"
    auto_full = "auto_full"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COCKPIT_", extra="ignore")

    # Always boots OFF; the ladder only moves up through measured gates.
    mode: Mode = Mode.read_only

    # Local control plane — loopback only, no inbound ports (docs/security-architecture §6).
    api_host: str = "127.0.0.1"
    api_port: int = 8787

    # Deep-model daily escalation budget (docs/build-scaffold-ultracode WP8; default 5).
    deep_model_daily_budget: int = Field(default=5, ge=0)

    # Triage escalation sensitivity 0-100 (docs/draft-discord-control-plane /triage threshold).
    triage_threshold: int = Field(default=60, ge=0, le=100)

    # ---- order-path knobs (WP6/WP7) ---------------------------------------------------
    # A live order at/above this notional needs the operator's modal double-confirm (SEC-15;
    # bridge/approval.py). Env: COCKPIT_MODAL_CONFIRM_NOTIONAL.
    modal_confirm_notional: Decimal = Field(default=Decimal("1000"), ge=0)
    # SEC-07 pending-exposure cap: sum(approved-but-unfilled notional) + open must stay <= this
    # inside the locked submit (brokers/gateway.py). Env: COCKPIT_PENDING_EXPOSURE_CAP.
    pending_exposure_cap: Decimal = Field(default=Decimal("10000"), ge=0)
    # Approval time-to-live: an approval older than this (measured from RiskDecision eval) voids
    # before submit (brokers/gateway.py). Env: COCKPIT_APPROVAL_TTL_SECONDS.
    approval_ttl_seconds: int = Field(default=900, ge=0)

    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
