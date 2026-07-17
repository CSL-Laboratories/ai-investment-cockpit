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

    # ---- SEC-05/06 decision-agent starvation (W11) ------------------------------------
    # The separate LOW-PRIVILEGE Windows account decision-stage subprocesses run under. When set,
    # main.compose() FAILS CLOSED unless that account provably cannot read the bearer stores
    # (agent_runner/account_guard.py; docs/setup/low-priv-agent-account.md). None => dev, the
    # SEC-05 control is inactive (not satisfied). Non-secret. Env: COCKPIT_DECISION_AGENT_ACCOUNT.
    decision_agent_account: str | None = None
    # The pinned sandbox cwd for decision-agent subprocesses (SEC-06): the runner refuses any
    # decision job whose cwd != this. None => not pinned (dev). Env: COCKPIT_DECISION_AGENT_CWD.
    decision_agent_cwd: str | None = None

    # ---- SEC-11 external audit witness (W10) ------------------------------------------
    # The git-TRACKED head file the GitCommitWitness anchors (reviewed history — bridge/
    # witness.py). Env: COCKPIT_AUDIT_HEAD_FILE. Non-secret.
    audit_head_file: str = "data/audit-head.json"
    # The private #audit-log Discord channel id the DiscordAuditLogWitness posts to (server-
    # stamped, out-of-domain). None until provisioned; the bot TOKEN is keyring-only (Inv 4),
    # NEVER here. Env: COCKPIT_AUDIT_LOG_CHANNEL_ID.
    audit_log_channel_id: int | None = None

    # ---- W20 ingest feeds (docs/production-work-packages.md W20) ----------------------
    # NOTE for the later barrier merge: settings.py is also touched by W14(f)/W15/W16(c).
    # Everything W20 owns is inside THIS block; nothing above or below it is W20's.
    #
    # edgar-morning-brew's SQLite file — the cockpit reads it READ-ONLY (mode=ro URI,
    # ingest/edgar_brew.py:read_only_uri) and NEVER fetches sec.gov itself. Path is
    # operator-set/TBD: the brew's own default is a bare relative "edgar_morning_brew.db"
    # (its storage/engine.py:20 make_engine), which is not a location this process can assume,
    # so there is NO default here. None => the feed is NOT PROVISIONED, which
    # read_brew_run_outcome reports as BrewFeedStatus.NOT_PROVISIONED (stale=True,
    # quiet_day=False) — deliberately DISTINGUISHABLE from a legitimate quiet day, never a
    # silent no-op. Env: COCKPIT_BREW_DB_PATH. Non-secret.
    brew_db_path: str | None = None
    # The brew's runs/ artifact dir (its ArtifactWriter writes <YYYY-MM-DD>-<slug>.txt there
    # atomically; the FILENAME is the run-outcome signal). Required to tell "degraded"/"not-run"
    # from "nothing-material" — with it unset, brew_status() fails closed to NOT_RUN
    # (ingest/edgar_brew.py:make_brew_fetch). Operator-set/TBD, no default: it is derived from the
    # brew's output_dir. Env: COCKPIT_BREW_RUNS_DIR. Non-secret.
    brew_runs_dir: str | None = None
    #
    # FINRA short-interest / Reg SHO — ENRICHMENT ONLY (~2 weeks stale; never a hard event).
    # Dataset URL: base https://api.finra.org is confirmed but the dataset identifier + path
    # pattern are UNVERIFIABLE (console-gated), so this is operator-set/TBD with NO default;
    # make_finra_fetch raises rather than invent one. Env: COCKPIT_FINRA_ENDPOINT.
    finra_endpoint: str | None = None
    # Declared User-Agent, ALWAYS sent (a suppressed UA is empirically HTTP 403). FINRA publishes
    # no UA grammar, so the shape is operator-set/TBD. Env: COCKPIT_FINRA_USER_AGENT. Non-secret
    # (the OAuth2 client credentials are secrets and live in keyring, Inv 4 — never here).
    finra_user_agent: str | None = None
    # Self-imposed politeness floor in requests/sec, NOT a verified FINRA cap — FINRA documents no
    # rate limit and the SEC's 10 req/s figure governs sec.gov, not api.finra.org. The real
    # ceiling is operator-set/TBD. Env: COCKPIT_FINRA_RATE_PER_SEC.
    finra_rate_per_sec: float = Field(default=1.0, gt=0)
    # ---- end W20 block ----------------------------------------------------------------

    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
