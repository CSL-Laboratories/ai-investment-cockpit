"""WP0 — application settings (pydantic-settings).

Mode defaults to ``read_only`` (the safe floor of the autonomy ladder). Secrets are
NOT read from env here — they live in keyring (``core.secrets``, Invariant 4). Env
carries only non-secret operational config, prefixed ``COCKPIT_``.

``.env`` SUPPORT AND WHY IT NEEDS A GUARD
-----------------------------------------
Until RW-10 there was no ``env_file``, so every ``COCKPIT_*`` value lived in the environment
of whatever shell launched ``main.py`` — and nowhere else. A reboot, or simply a second
terminal, silently lost ``COCKPIT_BRIEF_CHANNEL_ID``, and the cockpit then computed, stored
and audited a brief before delivering it NOWHERE, logging a tidy NOT PROVISIONED line. The
sibling project lost a soak day to the same class of fault and wrote the lesson down: "an
interactive shell test is not proof that the scheduled-task identity can read its user
environment and keyring." Durable config is the fix.

But a config FILE is a tempting place to put a credential, and Invariant 4 says secrets live
in Windows Credential Manager and nowhere else — not in the repo, not in prompts, not in
logs. So ``.env`` support ships WITH ``assert_no_secrets_in_env_file``: startup FAILS CLOSED
if any keyring secret name appears there. The file is gitignored (``.env``, ``*.env``), but
gitignore only stops a commit — it does not stop a secret from sitting in plaintext on disk,
being read by any process running as this user, or being pasted into a support thread. The
guard is the control; the gitignore is a courtesy.
"""
from __future__ import annotations

import os
import re
from decimal import Decimal
from enum import Enum

from config.models import (
    DEFAULT_CONTEXT_TOKEN_CAP,
    DEFAULT_DECISION_MODEL,
    DECISION_MODEL_PINS,
    HAIKU_55_LONG_CONTEXT_TOKENS,
)
from pathlib import Path

from pydantic import AliasChoices, Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved relative to app/ (this file's parent's parent), so the cockpit finds the same file
# whether it is launched from the repo root or from app/. An absolute COCKPIT_ENV_FILE wins,
# which is what a Windows scheduled task should set — its cwd is not yours.
_APP_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = Path(os.environ.get("COCKPIT_ENV_FILE") or (_APP_DIR / ".env"))


class EnvFileSecretError(RuntimeError):
    """A keyring secret name was found in the .env file (Invariant 4)."""


# The keyring names main.py reads. Kept here as a literal rather than imported from main so a
# settings import never drags the composition root in; test_env_file_guard.py asserts the two
# lists agree, which is what stops this copy from silently going stale.
_KEYRING_SECRET_NAMES = (
    "audit-hmac-key", "discord_bot_token", "alpaca_key", "alpaca_secret",
    "finnhub_key", "cockpit_api_key", "witness_s3_access_key_id",
    "witness_s3_secret_access_key",
)
# Generic shapes that are a secret regardless of vendor. A name we have never seen before is
# still refused if it looks like a credential — the allowlist is what may PASS, not what fails.
_SECRETISH = re.compile(
    r"(?:^|_)(?:SECRET|TOKEN|PASSWORD|PASSWD|APIKEY|API_KEY|PRIVATE_KEY|CREDENTIAL|HMAC)"
    r"(?:_|$)|(?:^|_)KEY(?:_|$)",
    re.IGNORECASE,
)
# Explicitly NOT secrets, though they trip the shapes above. Each is a NAME or an ID, not a
# credential, and each is already non-secret elsewhere in this file.
_SECRETISH_ALLOWED = frozenset({
    "COCKPIT_SEC_USER_AGENT", "COCKPIT_FINRA_USER_AGENT",
    "COCKPIT_AUDIT_LOG_CHANNEL_ID", "COCKPIT_BRIEF_CHANNEL_ID",
    "COCKPIT_ALERTS_CHANNEL_ID", "COCKPIT_PROPOSALS_CHANNEL_ID",
    "COCKPIT_SYSTEM_CHANNEL_ID",
    # A numeric prompt-size limit (CSL-42), not a credential.
    "COCKPIT_CONTEXT_TOKEN_CAP",
})

# Static operator-configured paper universe.  This is intentionally conservative: a bounded
# list keeps one bad config value from widening the deterministic risk allowlist or exploding
# the intraday prompt.  Symbols are normalized before Settings is exposed to any workflow.
_WATCHLIST_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$")
_WATCHLIST_MAX_SYMBOLS = 25
_WATCHLIST_MAX_RAW_CHARS = 512
_WATCHLIST_MAX_SYMBOL_CHARS = 10
_OPERATOR_IDS_MAX_RAW_CHARS = 512
_OPERATOR_IDS_MAX_ENTRIES = 1
_OPERATOR_ID = re.compile(r"^\d{15,22}$")


def normalize_watchlist(raw: str | None) -> str:
    """Validate and canonicalize a comma/whitespace-separated public-equity watchlist.

    The result remains a non-secret Settings string for durable ``.env`` compatibility.  Callers
    consume :attr:`Settings.watchlist_symbols`, never the raw text.  No model/runtime mutation
    surface exists: changing this value requires an operator config edit plus restart.
    """
    if raw is None or not str(raw).strip():
        return ""
    text = str(raw)
    if len(text) > _WATCHLIST_MAX_RAW_CHARS:
        raise ValueError(f"watchlist text may contain at most {_WATCHLIST_MAX_RAW_CHARS} characters")
    tokens = [token.strip().upper() for token in re.split(r"[\s,]+", text) if token.strip()]
    if len(tokens) > _WATCHLIST_MAX_SYMBOLS:
        raise ValueError(f"watchlist may contain at most {_WATCHLIST_MAX_SYMBOLS} entries")
    ordered = list(dict.fromkeys(tokens))
    invalid = [
        symbol for symbol in ordered
        if len(symbol) > _WATCHLIST_MAX_SYMBOL_CHARS or not _WATCHLIST_SYMBOL.fullmatch(symbol)
    ]
    if invalid:
        raise ValueError(f"invalid watchlist symbol(s): {', '.join(invalid)}")
    return ",".join(ordered)


def normalize_operator_ids(raw: str | None) -> str:
    """Validate the one numeric Discord snowflake without exposing a mutation seam."""
    if raw is None or not str(raw).strip():
        return ""
    text = str(raw)
    if len(text) > _OPERATOR_IDS_MAX_RAW_CHARS:
        raise ValueError(
            f"operator IDs may contain at most {_OPERATOR_IDS_MAX_RAW_CHARS} characters"
        )
    tokens = [token.strip() for token in re.split(r"[\s,]+", text) if token.strip()]
    if len(tokens) > _OPERATOR_IDS_MAX_ENTRIES:
        raise ValueError("operator allowlist may contain exactly one Discord ID at most")
    ordered = list(dict.fromkeys(tokens))
    invalid = [token for token in ordered if not _OPERATOR_ID.fullmatch(token)]
    if invalid:
        raise ValueError(f"invalid operator ID(s): {', '.join(invalid)}")
    return ",".join(ordered)


def _iter_env_file(path: Path):
    """Yield ``(lineno, KEY)`` for each assignment in a dotenv file. Never yields a VALUE.

    Deliberately does not parse or retain the right-hand side: this function exists to police
    secrets, and a policing routine that carries the secret in memory (into a traceback, into
    a log record) defeats itself.
    """
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key.lower().startswith("export "):
            key = key[len("export "):].strip()
        yield lineno, key


def assert_no_secrets_in_env_file(path: Path | None = None) -> None:
    """FAIL CLOSED if the .env file carries anything that belongs in keyring (Inv 4).

    Raises :class:`EnvFileSecretError` naming the KEY and LINE — never the value, so the
    exception itself is safe to log, which is the whole point of raising one here rather
    than printing a warning nobody reads.
    """
    path = ENV_FILE if path is None else path
    if not path.is_file():
        return
    offenders: list[str] = []
    for lineno, key in _iter_env_file(path):
        bare = key[len("COCKPIT_"):] if key.startswith("COCKPIT_") else key
        is_keyring_name = any(
            n.replace("-", "_").upper() in (key.upper(), bare.upper())
            for n in _KEYRING_SECRET_NAMES
        )
        looks_secret = bool(_SECRETISH.search(key)) and key.upper() not in _SECRETISH_ALLOWED
        if is_keyring_name or looks_secret:
            offenders.append(f"{key} (line {lineno})")
    if offenders:
        raise EnvFileSecretError(
            f"{path} contains secret-shaped entries: {', '.join(offenders)}. Invariant 4: "
            "secrets live in Windows Credential Manager only. Remove the line(s) and store "
            "them with `python tools/verify_keys.py --set NAME`. If a name is genuinely not "
            "a secret, add it to _SECRETISH_ALLOWED with a reason rather than loosening the "
            "pattern."
        )


class Mode(str, Enum):
    """Autonomy ladder (docs/draft-discord-control-plane). Boots at the safe floor."""

    read_only = "read_only"
    paper = "paper"
    review_live = "review_live"
    auto_veto = "auto_veto"
    auto_full = "auto_full"


class DecisionRuntime(str, Enum):
    """Provider used for human-gated analysis and proposal jobs.

    This is deliberately narrower than the autonomy ladder: selecting a provider does not
    grant it broker tools or make it eligible for live/auto execution.  The latter remains a
    separate, hard-coded policy in ``orchestrator.mode_ladder``.
    """

    claude = "claude"
    codex = "codex"


class Settings(BaseSettings):
    # env_file is the DURABLE half of the config; a real environment variable still WINS over
    # the file (pydantic-settings precedence), so an operator can override one value for one
    # run without editing anything. Secrets are still refused — see
    # ``assert_no_secrets_in_env_file``, called by ``get_settings``.
    model_config = SettingsConfigDict(
        env_prefix="COCKPIT_", extra="ignore",
        env_file=str(ENV_FILE), env_file_encoding="utf-8",
    )
    _operator_ids_frozen: frozenset[str] = PrivateAttr(default_factory=frozenset)

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, values):
        """An empty value in .env (or env) means UNSET, not "parse '' as an int".

        ``COCKPIT_BRIEF_CHANNEL_ID=`` is what an operator writes to say "I have not filled
        this in yet" — and it is what every commented template produces the moment someone
        runs ``cp .env.example .env``. Without this, that copy raises five ValidationErrors
        and the cockpit REFUSES TO BOOT over blank optional fields, which is the opposite of
        the per-feed fail-closed posture: an unset channel id should mean that ONE feed is
        unprovisioned and logged, never that the process dies.

        Only whitespace-only STRINGS are dropped, so the field's own default applies. A
        present, non-blank value is untouched and still validated normally — this widens
        nothing and cannot turn a bad value into a good one.
        """
        if isinstance(values, dict):
            return {
                k: v for k, v in values.items()
                if not (isinstance(v, str) and v.strip() == "")
            }
        return values

    # Always boots OFF; the ladder only moves up through measured gates.
    mode: Mode = Mode.read_only

    # Local control plane — loopback only, no inbound ports (docs/security-architecture §6).
    api_host: str = "127.0.0.1"
    api_port: int = 8787

    # Deep-model daily escalation budget (docs/build-scaffold-ultracode WP8; default 5).
    deep_model_daily_budget: int = Field(default=5, ge=0)

    # Triage escalation sensitivity 0-100 (docs/draft-discord-control-plane /triage threshold).
    triage_threshold: int = Field(default=60, ge=0, le=100)

    # Static, operator-approved public-equity universe.  This is read-only at runtime and is
    # threaded into both triage relevance and the deterministic symbol allowlist.  Decision
    # models receive the resulting set but have no API/tool that can widen it.
    # Env: COCKPIT_WATCHLIST=MSFT,AAPL,...
    watchlist: str = ""

    @field_validator("watchlist", mode="before")
    @classmethod
    def _validate_watchlist(cls, value):
        return normalize_watchlist(value)

    @property
    def watchlist_symbols(self) -> frozenset[str]:
        return frozenset(self.watchlist.split(",")) if self.watchlist else frozenset()

    # W-23 strategy authority is deliberately narrower than the general awareness/order
    # watchlist.  A filing, model, or discovery feed cannot enroll a merger target: the
    # operator must place it in BOTH lists and restart the process.  Keeping a distinct env
    # name also makes an empty strategy universe the safe production default.
    # Env: COCKPIT_MERGER_WATCHLIST=TARGET,...
    merger_watchlist: str = ""

    @field_validator("merger_watchlist", mode="before")
    @classmethod
    def _validate_merger_watchlist(cls, value):
        return normalize_watchlist(value)

    @property
    def merger_watchlist_symbols(self) -> frozenset[str]:
        return (
            frozenset(self.merger_watchlist.split(","))
            if self.merger_watchlist
            else frozenset()
        )

    # Approval authority is loaded once at startup from the dedicated non-secret env name and
    # frozen into a private set.  ``operator_ids_config`` is excluded from dumps/repr; changing
    # that raw field later cannot change the authorization seam consumed by bridge.operators.
    operator_ids_config: str = Field(
        default="",
        validation_alias=AliasChoices("COCKPIT_OPERATOR_IDS", "operator_ids_config"),
        exclude=True,
        repr=False,
    )

    @field_validator("operator_ids_config", mode="before")
    @classmethod
    def _validate_operator_ids(cls, value):
        return normalize_operator_ids(value)

    def model_post_init(self, __context) -> None:
        # BUG-143: the .env secret guard ran ONLY in get_settings(), so any composition
        # path constructing Settings() directly loaded the durable .env file unpoliced.
        # The guard runs here too — on EVERY construction against the default env file —
        # so the "startup FAILS CLOSED" property holds by construction, not caller
        # discipline. (Per-call ``_env_file=`` test seams bypass the default path by
        # design; values already loaded are never logged — the guard names KEYS and
        # LINES only.)
        assert_no_secrets_in_env_file()
        del __context
        frozen = (
            frozenset(self.operator_ids_config.split(","))
            if self.operator_ids_config else frozenset()
        )
        object.__setattr__(self, "_operator_ids_frozen", frozen)

    @property
    def operator_ids(self) -> frozenset[str]:
        return self._operator_ids_frozen

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
    # Provider for triage, deep proposal, grounding, briefing, and fresh-interrogation jobs.
    # Both providers share schema validation -> repair once -> quarantine. Codex additionally
    # runs under the hardened tool-free/read-only/ephemeral CLI profile in agent_runner/runner.py.
    # This switch is valid only for read_only/paper human-gated work; it cannot relax the
    # Claude-only auto/live runtime pin. Env: COCKPIT_DECISION_RUNTIME.
    decision_runtime: DecisionRuntime = DecisionRuntime.claude
    # Trading-decision Claude model (CSL-42 / CSL-98). Allowlist only: Haiku 4.5 is the
    # fallback; set claude-haiku-5-5 when that id is available on the Claude CLI. This
    # does not select Opus, and it does not relax the Claude-only auto/live pin.
    # Env: COCKPIT_DECISION_MODEL.
    decision_model: str = DEFAULT_DECISION_MODEL
    # Hard prompt cap. Haiku 5.5 bills every token at 5x once the prompt exceeds 100k
    # input tokens, so the cap cannot be configured onto that tier. The estimator is
    # conservative (UTF-8 bytes / 3). Env: COCKPIT_CONTEXT_TOKEN_CAP.
    context_token_cap: int = Field(
        default=DEFAULT_CONTEXT_TOKEN_CAP,
        ge=1,
        le=HAIKU_55_LONG_CONTEXT_TOKENS - 1,
    )

    @field_validator("decision_model")
    @classmethod
    def _validate_decision_model(cls, value):
        key = str(value).strip()
        if key not in DECISION_MODEL_PINS:
            allowed = ", ".join(sorted(DECISION_MODEL_PINS))
            raise ValueError(f"decision_model must be one of: {allowed}")
        return key

    # The separate LOW-PRIVILEGE Windows account decision-stage subprocesses run under. When set,
    # main.compose() FAILS CLOSED unless that account provably cannot read the bearer stores
    # (agent_runner/account_guard.py; docs/setup/low-priv-agent-account.md). None => dev, the
    # SEC-05 control is inactive (not satisfied). Non-secret. Env: COCKPIT_DECISION_AGENT_ACCOUNT.
    decision_agent_account: str | None = None
    # The pinned sandbox cwd for decision-agent subprocesses (SEC-06): the runner refuses any
    # decision job whose cwd != this. None => not pinned (dev). Env: COCKPIT_DECISION_AGENT_CWD.
    decision_agent_cwd: str | None = None

    # ---- SEC-11 external audit witness (W10) ------------------------------------------
    # The ignored, machine-local mirror written by GitCommitWitness. Production disables that
    # class's optional git-commit callback. Env: COCKPIT_AUDIT_HEAD_FILE. Non-secret.
    audit_head_file: str = "data/audit-head.json"
    # The private #audit-log Discord channel id used for the operator-visible audit mirror and
    # W-24 exact-message chronology. Discord is NOT SEC-11 authority when the bot retains
    # Administrator. The bot TOKEN is keyring-only (Inv 4), never here.
    audit_log_channel_id: int | None = None
    # W-25: the only qualifying SEC-11 trust source. All values here are non-secret identifiers;
    # the access-key pair lives only in the OS credential store. Partial provisioning creates no
    # adapter. The bucket must have versioning + Object Lock with default Compliance retention.
    witness_s3_bucket: str | None = None
    witness_s3_region: str | None = None
    witness_s3_expected_bucket_owner: str | None = None
    witness_s3_prefix: str = "ai-investment-cockpit/v1"
    witness_s3_min_retention_days: int = Field(default=365, ge=30, le=36_500)

    @field_validator("witness_s3_expected_bucket_owner")
    @classmethod
    def _validate_witness_bucket_owner(cls, value):
        if value is not None and re.fullmatch(r"\d{12}", str(value)) is None:
            raise ValueError("witness S3 expected bucket owner must be a 12-digit AWS account ID")
        return value

    # ---- RW-01 earnings-calendar window ------------------------------------------------
    # How many days forward the earnings calendar is re-read each poll. NOT a cosmetic knob:
    # the calendar is FULL-MARKET, so the window length directly sets how much lands in the
    # morning brew's triage packet. Measured on the live free-tier feed: a 14-day window is
    # ~1500 entries ~= 86k tokens of prompt, most of it names the operator does not hold —
    # expensive, and it buries the signal it was meant to add.
    #
    # 3 days is the default because it covers "reporting imminently", which is the only part a
    # morning brief can act on. 0 DISABLES the feed entirely (no poller, no job) — the honest
    # setting until the dynamic universe (RW-05) can scope this to names that matter.
    # Env: COCKPIT_CALENDAR_LOOKAHEAD_DAYS.
    calendar_lookahead_days: int = Field(default=3, ge=0, le=90)

    # ---- RW-01 operator-surface channel map (docs/refactor-plan.md §5) ------------------
    # Optional Discord guild for immediate slash-command sync during development/deployment.
    # None deliberately keeps Discord's global sync behavior.  This is a public snowflake,
    # not a credential; the bot token remains keyring-only (Inv 4).
    # Env: COCKPIT_GUILD_ID.
    guild_id: int | None = Field(default=None, gt=0)

    # Discord channel ids the LOGICAL pipeline channels are delivered to (bridge/deliver.py).
    # Non-secret (the bot TOKEN is keyring-only, Inv 4). Each is INDEPENDENTLY optional: an
    # unset channel fails closed with a warning and is reported as undelivered — never
    # redirected into another channel. Phase-0.5 needs only brief_channel_id (the operator's
    # #morning-brew). The names are LOGICAL, so renaming the Discord channel changes nothing
    # above this line. Env: COCKPIT_BRIEF_CHANNEL_ID etc.
    brief_channel_id: int | None = None        # <- #morning-brew (the Phase-0.5 deliverable)
    alerts_channel_id: int | None = None
    proposals_channel_id: int | None = None
    system_channel_id: int | None = None

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

    # ---- RW-10 in-process EDGAR (app/filings/, vendored — see app/filings/PROVENANCE.md) --
    # SEC EDGAR needs NO API key: it is free and unauthenticated. It requires exactly two
    # things, a declared User-Agent and staying under 10 req/s, and BOTH are enforced inside
    # the vendored client rather than here (filings/edgar/fetch.py:452 raises on a blank UA
    # before building any request; :345 refuses a rate >= SEC_RATE_HARD_CEILING at
    # construction). These fields configure that client; they are not the control.
    #
    # WHY NO-DEFAULT-REQUIRED, UNLIKE UPSTREAM. edgar-morning-brew declares sec_user_agent
    # with NO default so ITS process refuses to start without one — correct there, because
    # the brew IS the EDGAR app. The cockpit is not: it fails closed PER FEED on a missing
    # secret rather than refusing to boot (main.compose docstring; the same shape as
    # alpaca/finnhub/finra above). Copying upstream's required field would mean one
    # unprovisioned feed takes down the order path, the risk engine, and the operator
    # surface with it. Unset => the EDGAR feed is NOT PROVISIONED and is logged as such;
    # sec.gov is still never called without a UA, because the client refuses.
    # Env: COCKPIT_SEC_USER_AGENT. Non-secret (SEC asks for "Name email@example.com").
    sec_user_agent: str | None = None
    # Token-bucket rate for filings/edgar/fetch.py. The SEC fair-access ceiling is 10 req/s
    # and the vendored client validates STRICTLY BELOW it, so this is a floor we choose, not
    # a limit we are trusted to respect. Upstream's default is 2.0; keep it.
    # Env: COCKPIT_SEC_RATE_LIMIT_PER_SEC.
    sec_rate_limit_per_sec: float = Field(default=2.0, gt=0, lt=10.0)
    # Byte budgets: one response, and one whole run. A filing is untrusted remote input of
    # unbounded size, so both caps are fail-closed halts in the client, not advisory.
    # Env: COCKPIT_SEC_MAX_RESPONSE_BYTES / COCKPIT_SEC_MAX_RUN_BYTES.
    sec_max_response_bytes: int = Field(default=32 * 1024 * 1024, ge=1, le=512 * 1024 * 1024)
    sec_max_run_bytes: int = Field(default=128 * 1024 * 1024, ge=1, le=2 * 1024 * 1024 * 1024)
    # Fail-closed cap on new in-scope filings discovered in ONE cik poll — bounds how much a
    # single surprise (an issuer dumping a backlog) can cost. Env: COCKPIT_SEC_MAX_NEW_FILINGS_PER_POLL.
    sec_max_new_filings_per_poll: int = Field(default=25, ge=1, le=250)
    # Free-disk reserve the write-once raw store keeps after publishing filing bytes, so a
    # full disk is refused BEFORE a half-written filing lands. Env: COCKPIT_RAW_MIN_FREE_BYTES.
    raw_min_free_bytes: int = Field(default=256 * 1024 * 1024, ge=0)
    # ---- end RW-10 block ----------------------------------------------------------------

    log_level: str = "INFO"


def get_settings() -> Settings:
    """Build Settings, refusing to proceed if the .env file holds a secret (Inv 4).

    The guard runs BEFORE construction on purpose. Reading the file first and complaining
    afterwards would already have loaded the secret into the process — and into any
    ``repr(settings)`` that later reaches a log line.
    """
    assert_no_secrets_in_env_file()
    return Settings()
