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
from pathlib import Path

from pydantic import AliasChoices, Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The ONE upper bound on the options haircut, imported rather than restated. ``copytrade.policy``
# is a leaf (stdlib only, no config import), so this cannot cycle — and the alternative, a second
# literal here, is exactly the disagreement that let ``le=1`` accept the value ``CopyPolicy``
# refuses and turn a boot-time config error into a silently dead feature at runtime.
from copytrade.policy import MAX_OPTIONS_HAIRCUT

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
    "finnhub_key", "cockpit_api_key",
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

# RW-16 congressional follow list. The ENTRY count is bounded by copytrade.policy
# (MAX_FOLLOW_ENTRIES); this is the raw-text bound, the same defence-in-depth the watchlist
# gets — a config value that arrives megabyte-sized is a bug or an attack, never a follow list.
_CONGRESS_FOLLOW_MAX_RAW_CHARS = 2048
# The default options handling, spelled here so this module never imports copytrade just to
# state a default. The VALIDATION set is still read from copytrade.policy.OPTIONS_POLICIES
# (normalize_congress_options_policy), so the two files cannot disagree about what is legal.
_CONGRESS_OPTIONS_DEFAULT = "underlying"


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


def normalize_congress_follow(raw: str | None) -> str:
    """RW-16 — validate the ``;``-separated congressional follow allowlist AT STARTUP.

    WHY EAGER. ``copytrade.policy.parse_follow_list`` already refuses an unparseable entry, but
    it is called at PLAN time — hours or days after the operator edited ``.env``. Deferring the
    check there means a misspelled name ("Pelosi Nancy P" with the comma dropped, a stray
    ``,`` separator that tears every name in half) surfaces either as a dead feature or, worse,
    as a follow set that quietly omits the one member the operator meant to follow. An empty
    follow list follows NOBODY by design, so "parsed to nothing" and "configured nothing" are
    indistinguishable downstream — which is precisely why the distinction has to be made here,
    while the operator is still looking at the file they just changed.

    Returns the CANONICAL RAW TEXT (``;``-joined entries), not slugs: ``policy_from_settings``
    re-parses this field, so it must stay in the shape ``parse_follow_list`` accepts. Duplicate
    spellings of one member ("Pelosi, Nancy" / "PELOSI, NANCY P.") collapse to the first
    spelling seen, because they normalize to one slug and keeping both would misreport the
    follow count in every log line and status view.

    ``copytrade`` is imported INSIDE the function on purpose: a ``config.settings`` import is
    on every module's path in this app, and it must not drag the copy engine in behind it. The
    import only happens when the operator actually configured a follow list.
    """
    if raw is None or not str(raw).strip():
        return ""
    text = str(raw)
    if len(text) > _CONGRESS_FOLLOW_MAX_RAW_CHARS:
        raise ValueError(
            f"congress follow list may contain at most {_CONGRESS_FOLLOW_MAX_RAW_CHARS} "
            "characters"
        )
    from copytrade.policy import PolicyError, normalize_filer, parse_follow_list

    entries = [entry.strip() for entry in text.split(";") if entry.strip()]
    if not entries:
        # Separators and nothing else (";;;", " ; "). Blank means "follow nobody" and is a
        # legitimate setting, but this is not blank — someone typed something and meant it. It
        # must not fail closed SILENTLY into the empty allowlist, or the operator's next
        # question is "why did it copy nobody" with no line to answer it.
        raise ValueError(
            "congress follow list contains separators but no names; leave it EMPTY to follow "
            "nobody, or write ';'-separated entries like 'Pelosi, Nancy; Tuberville, Tommy'"
        )
    try:
        parse_follow_list(";".join(entries))     # raises on an unparseable/oversized list
    except PolicyError as exc:
        # Re-raised as ValueError so pydantic reports it as a field error naming
        # COCKPIT_CONGRESS_FOLLOW, rather than as an opaque copytrade exception at boot.
        raise ValueError(str(exc)) from exc
    canonical: dict[str, str] = {}
    for entry in entries:
        canonical.setdefault(normalize_filer(entry), entry)
    return ";".join(canonical.values())


def normalize_congress_options_policy(raw: str | None) -> str:
    """RW-16 — validate ``COCKPIT_CONGRESS_OPTIONS_POLICY`` against the policy's own literals.

    Checked against ``copytrade.policy.OPTIONS_POLICIES`` rather than a copy of the strings, so
    a third policy can never exist in one file and not the other. A typo ("underlyng") must
    fail at startup: ``CopyPolicy.__post_init__`` would otherwise raise at PLAN time, turning a
    one-character config error into a feature that looks configured and silently never runs.
    """
    if raw is None or not str(raw).strip():
        return _CONGRESS_OPTIONS_DEFAULT
    from copytrade.policy import OPTIONS_POLICIES

    text = str(raw).strip().lower()
    if text not in OPTIONS_POLICIES:
        raise ValueError(
            f"congress_options_policy must be one of {sorted(OPTIONS_POLICIES)}, got {raw!r}"
        )
    return text


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

    # ---- RW-16 congressional copy-trading (docs/refactor-plan.md RW-16) ------------------
    # Congressional periodic-transaction reports (House Clerk + Senate EFD). Read the shape of
    # this feature before touching a number here: a PTR is disclosed up to 45 days AFTER the
    # trade, so this is an AWARENESS feed first (what did they file?) and a copy engine second.
    # Every knob below exists to bound what the DETERMINISTIC copy engine may do with a signal
    # that is, by construction, weeks old. No model is on this path (Inv 1/5, "execution stays
    # model-free"); ``copytrade.policy.policy_from_settings`` is the only reader.
    #
    # OFF BY DEFAULT, DELIBERATELY. Turning this on starts fetching two .gov sites AND starts
    # proposing orders, so it is opt-in: an operator who has not read the docs gets nothing.
    # Env: COCKPIT_CONGRESS_ENABLED.
    congress_enabled: bool = False
    # The declared contact string sent as User-Agent on every house.gov / senate.gov request.
    # NON-SECRET — a name and an email, the same shape and purpose as COCKPIT_SEC_USER_AGENT,
    # and published to the remote host on every single request by design. (Checked against
    # _SECRETISH: "CONGRESS_USER_AGENT" contains none of SECRET/TOKEN/KEY/CREDENTIAL/HMAC, so
    # it does NOT trip the .env guard and needs no _SECRETISH_ALLOWED entry. That list is for
    # names that DO trip the shape; adding a name that does not would be dead config that
    # misleads the next reader about which names are dangerous.)
    # Unset => the feed is NOT PROVISIONED and stays silent: main.build_congress_providers
    # refuses to build a provider that would scrape a government site anonymously (SEC's own
    # fair-access posture, and a suppressed UA is empirically how you earn a 403).
    # Env: COCKPIT_CONGRESS_USER_AGENT.
    congress_user_agent: str = ""
    # Self-imposed politeness floor in requests/sec. Neither the Clerk nor the EFD publishes a
    # documented rate cap, so this is a limit WE choose, not one we are trusted to respect —
    # the same posture as finra_rate_per_sec. 0.5/s (one request every two seconds) is already
    # generous for a feed that reads a handful of documents a day. The ``le`` ceiling bounds
    # OUR aggression: a fat-fingered 500 must not turn one config edit into a flood against a
    # .gov host. Env: COCKPIT_CONGRESS_RATE_PER_SEC.
    congress_rate_per_sec: float = Field(default=0.5, gt=0, le=5.0)
    # The follow ALLOWLIST: ``;``-separated member names ("Pelosi, Nancy; Tuberville, Tommy").
    # ``;`` and not ``,`` because a comma-separated list tears every "Last, First" in half.
    # EMPTY FOLLOWS NOBODY (copytrade.policy.CopyPolicy.follows) — there is deliberately no
    # "everyone" value, because the feed carries both chambers and a config oversight would
    # otherwise become several hundred positions. Validated eagerly; see
    # normalize_congress_follow for why that has to happen here and not at plan time.
    # Env: COCKPIT_CONGRESS_FOLLOW.
    congress_follow: str = ""
    # Refuse any disclosure whose TRADE is older than this. 45 days is the STOCK Act reporting
    # deadline, so the default means "act on a filing that arrived on time, refuse one that did
    # not". The upper bound is a year: a knob that can be set to 3650 is not a staleness gate.
    # Env: COCKPIT_CONGRESS_MAX_AGE_DAYS.
    congress_max_age_days: int = Field(default=45, ge=1, le=365)
    # Fraction of the position given up at maximum age, linearly (0.5 => a 45-day-old trade is
    # sized at half a same-day one). 0 prices a 44-day-old signal as if it were fresh, which is
    # allowed but not advised. Env: COCKPIT_CONGRESS_STALENESS_DECAY.
    congress_staleness_decay: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    # Fraction of YOUR portfolio value for a baseline (weight 1.0) copied buy. ``le=1`` because
    # a copy fraction above 100% of the portfolio is not a configuration, it is a typo.
    # Env: COCKPIT_CONGRESS_COPY_FRACTION.
    congress_copy_fraction: Decimal = Field(default=Decimal("0.02"), gt=0, le=1)
    # Hard per-intent ceiling, applied after every other sizing factor. The risk engine has its
    # own caps; this one bounds a policy misconfiguration BEFORE it reaches the engine, so the
    # two controls fail independently. Env: COCKPIT_CONGRESS_MAX_NOTIONAL_PER_TRADE.
    congress_max_notional_per_trade: Decimal = Field(default=Decimal("500"), gt=0)
    # Below this the intent is DROPPED rather than emitted: a $3 order is all cost and no
    # exposure, and it still spends a human approval. Env: COCKPIT_CONGRESS_MIN_NOTIONAL_PER_TRADE.
    congress_min_notional_per_trade: Decimal = Field(default=Decimal("25"), ge=0)
    # Ignore disclosures whose LOW amount bound is under this. The bottom PTR bucket starts at
    # $1,001, so the default admits everything and raising it filters a member's smallest
    # positions. Env: COCKPIT_CONGRESS_MIN_AMOUNT_LOW.
    congress_min_amount_low: Decimal = Field(default=Decimal("1001"), ge=0)
    # "underlying" (translate an options disclosure to a haircut position in the underlying) or
    # "ignore" (surface for awareness only, never act). There is no "copy the contract" value:
    # the risk engine does not support options as an asset type at all, and putting a hard
    # expiry on the end of a 30-45 day information lag is not a strategy.
    # Env: COCKPIT_CONGRESS_OPTIONS_POLICY.
    congress_options_policy: str = _CONGRESS_OPTIONS_DEFAULT
    # Options-derived intents are sized at this multiple of an equivalent equity copy — the
    # discount for translating a leveraged instrument into an unleveraged one. ``gt=0`` because
    # a zero haircut means "emit intents worth nothing", which is a silent no-op dressed as a
    # feature. The upper bound is ``copytrade.policy.MAX_OPTIONS_HAIRCUT`` and NOT 1: the old
    # ``le=1`` accepted the exact value ``CopyPolicy`` forbids, so a boot-time config error
    # surfaced instead as a runtime ``PolicyError`` swallowed by
    # ``brokers.paper_execution._copy_universe``'s broad except — an empty copy universe and
    # every promoted symbol silently refused at submit. Env: COCKPIT_CONGRESS_OPTIONS_HAIRCUT.
    congress_options_haircut: Decimal = Field(
        default=Decimal("0.25"), gt=0, le=MAX_OPTIONS_HAIRCUT)
    # Drop an options-derived intent whose disclosed expiry is nearer than this: by the time a
    # PTR arrives the contract may already have expired, and translating a signal whose own
    # thesis window has closed is guessing. ``ge=1`` because 0 admits a 0-DTE contract as a fresh
    # multi-week long — see CopyPolicy.options_min_days_to_expiry.
    # Env: COCKPIT_CONGRESS_OPTIONS_MIN_DAYS_TO_EXPIRY.
    congress_options_min_days_to_expiry: int = Field(default=30, ge=1, le=365)
    # Copy disclosed SALES as exits of positions actually held. ON by default and independent
    # of buys on purpose: mirroring only the buys builds a portfolio that never sells.
    # Env: COCKPIT_CONGRESS_MIRROR_SELLS.
    congress_mirror_sells: bool = True
    # Bounds on the DYNAMIC copy universe (copytrade/universe.py): how many disclosure-promoted
    # symbols may be live at once, and how long a symbol survives without a fresh disclosure.
    # Both exist so the deterministic allowlist stays small enough to reason about and current
    # enough to be honest. Env: COCKPIT_CONGRESS_MAX_UNIVERSE_SYMBOLS / ..._UNIVERSE_TTL_DAYS.
    congress_max_universe_symbols: int = Field(default=40, ge=1, le=200)
    congress_universe_ttl_days: int = Field(default=120, ge=1, le=1095)
    # The Senate EFD provider is separately opt-in and OFF by default: its search UI is
    # agreement-gated and its documents are far less machine-readable than the Clerk's, so a
    # deployment that enables the House feed must not silently acquire a second, flakier
    # scraper it never asked for. Env: COCKPIT_CONGRESS_SENATE_ENABLED.
    congress_senate_enabled: bool = False

    @field_validator("congress_follow", mode="before")
    @classmethod
    def _validate_congress_follow(cls, value):
        return normalize_congress_follow(value)

    @field_validator("congress_options_policy", mode="before")
    @classmethod
    def _validate_congress_options_policy(cls, value):
        return normalize_congress_options_policy(value)

    @model_validator(mode="after")
    def _check_congress_notional_band(self):
        """The one CROSS-FIELD copy constraint, checked at startup rather than at plan time.

        ``CopyPolicy.__post_init__`` raises on ``min > max`` — but that runs when the first
        disclosure is planned, which may be days after the edit. An inverted band is not a
        subtle mistake and it disables the whole feature, so it fails here, loudly, next to
        the file the operator just saved.
        """
        if self.congress_min_notional_per_trade > self.congress_max_notional_per_trade:
            raise ValueError(
                "congress_min_notional_per_trade "
                f"({self.congress_min_notional_per_trade}) exceeds "
                f"congress_max_notional_per_trade ({self.congress_max_notional_per_trade}): "
                "every intent would be clamped below the floor and dropped"
            )
        return self
    # ---- end RW-16 block ------------------------------------------------------------------

    # ---- RW-17 zero-touch PAPER auto-execution (the operator's "no approvals" lane) --------
    # RW-16 ends at an ``Order`` row a HUMAN adjudicates. The operator asked for the opposite:
    # "i do not want to do any approvals when this tool is running, i want it to make trades
    # for me. all i ask is for notifications." RW-17 is that lane — and it is PAPER ONLY, by
    # construction, forever.
    #
    # THIS IS NOT AN AUTO RUNG. ``auto_veto``/``auto_full`` are LIVE modes here
    # (``orchestrator.mode_ladder._LIVE_MODES``) whose gates protect real capital; none of them
    # is weakened, armed, or routed around by anything below. Every setting here configures a
    # lane that re-checks ``mode is Mode.paper`` at every resolve and reaches the broker only
    # through the paper-scoped ``OrderGateway``. Turning these knobs can never make live capital
    # reachable; if it ever could, that is the bug, not the feature.
    #
    # OFF BY DEFAULT, AND THE DEFAULT IS THE POINT. Enabling it replaces the operator's approval
    # tap with an audited auto-adjudication, so it is an explicit act — never something a
    # deployment acquires by upgrading. Env: COCKPIT_CONGRESS_AUTOEXEC.
    congress_autoexec: bool = False
    # How long a queued trade waits, notified, before it executes — the operator's ONLY lever is
    # a cancel inside this window (they chose "notify + cancel window", explicitly not instant).
    # The FLOOR IS THE RESOLVE CADENCE (``orchestrator.scheduler.AUTOEXEC_RESOLVE_SECONDS``, 60s):
    # a window shorter than the tick that resolves it is not a shorter window, it is a window
    # whose real length is decided by scheduler jitter — and a cancel window the operator cannot
    # predict is not a cancel window. ``test_autoexec_wiring`` pins this bound to that constant so
    # the two cannot drift. Env: COCKPIT_CONGRESS_AUTOEXEC_WINDOW_SECONDS.
    congress_autoexec_window_seconds: int = Field(default=300, ge=60, le=86400)
    # THE UPPER BOUND ON A TICKET'S LIFE. Most refusals on this lane are TRANSIENT by design
    # (``copytrade.autoexec.TRANSIENT_REASONS``) — a HALT clears, a breaker is reset, tomorrow's
    # budget is fresh — because a terminal refusal permanently strands the disclosure
    # (``workflows.copytrade_orders._consuming`` never re-proposes a filing that already has an
    # order row). Transient means the ticket stays live, which needs an upper bound or a ticket
    # queued during a Monday HALT is still counting down a Monday-priced order on Friday, and
    # still retrying its notification every minute. At this age the ticket is refused by name
    # (``ticket_expired``), notified and counted. Six hours is comfortably one regular session,
    # and nothing is lost by abandoning a 30-45 day-old signal that could not trade inside one.
    # Env: COCKPIT_CONGRESS_AUTOEXEC_TICKET_MAX_AGE_SECONDS.
    congress_autoexec_ticket_max_age_seconds: int = Field(default=21_600, ge=300, le=604_800)
    # ---- the budget rails that REPLACE the human ------------------------------------------
    # A human approving each trade was, incidentally, the thing bounding daily damage. These
    # four rails are its replacement, so their defaults are deliberately SMALLER than what an
    # attentive operator would have approved by hand: an unattended lane should feel tight, and
    # widening it should be a decision someone made on purpose.
    #
    # Total dollars this lane may commit in one trading day. The default is exactly ONE
    # ``congress_max_notional_per_trade`` (500): out of the box the lane may open one full-size
    # position per day and then stops, counted and NOTIFIED — never silently.
    # Env: COCKPIT_CONGRESS_AUTOEXEC_MAX_DAILY_NOTIONAL.
    congress_autoexec_max_daily_notional: Decimal = Field(default=Decimal("500"), gt=0)
    # A count rail as well as a dollar rail, because they fail differently: the dollar rail
    # bounds exposure, this one bounds how many independent mistakes one day can make (each
    # trade is its own commission, its own fill, its own position to unwind). ``ge=1`` — zero
    # would be a feature the operator switched on that can never trade, which is precisely the
    # silent-death shape this file refuses elsewhere. Env: ..._MAX_TRADES_PER_DAY.
    congress_autoexec_max_trades_per_day: int = Field(default=3, ge=1, le=50)
    # The standing bound, not the daily one: how many positions this lane may hold open at once.
    # Without it a $500/day rail still accumulates an unbounded book over a quarter, and nobody
    # tapped an approval on any of it. Env: ..._MAX_OPEN_POSITIONS.
    congress_autoexec_max_open_positions: int = Field(default=10, ge=1, le=100)
    # Per-symbol daily concentration. Half the daily budget by default, so a single name — a
    # filer who discloses one ticker several times, or an amended filing that reads as new
    # evidence — cannot consume the whole day's rail on its own. Env:
    # ..._MAX_NOTIONAL_PER_SYMBOL_PER_DAY.
    congress_autoexec_max_notional_per_symbol_per_day: Decimal = Field(
        default=Decimal("250"), gt=0)

    @model_validator(mode="after")
    def _check_congress_autoexec_is_actually_reachable(self):
        """LOUD STARTUP REFUSAL, never a quiet no-op — the operator believes it is trading.

        This is the one validator in this file whose absence would be a SAFETY defect rather
        than a config-hygiene one, and the direction is unusual: it refuses configurations that
        would trade LESS than the operator thinks, not more. With ``congress_autoexec`` on, the
        operator has stopped watching an approval queue on purpose. Every combination below
        produces a lane that boots clean, logs nothing alarming, and copies nobody:

          * ``congress_enabled`` false — no provider is built, no disclosure is ever stored, so
            the cycle has nothing to plan and the executor never sees a ticket;
          * an empty follow list — an empty allowlist follows NOBODY (never "everyone", by
            design), so every disclosure is skipped before sizing;
          * a daily rail below the minimum trade size — the first ticket's budget check can
            never pass, so every trade is refused on a rail the operator did not know they had
            set below the floor;
          * a PER-SYMBOL rail below the minimum trade size — the same defect one rail over. The
            per-symbol cap is checked against the SAME sizing floor, so if it sits under it, no
            symbol can ever take its first trade and the lane refuses everything.

        NOTE what is deliberately NOT refused here: a per-symbol rail below
        ``congress_max_notional_per_trade`` (the DEFAULT configuration — a 250 cap under a 500
        ceiling). That combination is reachable, not unreachable: trades sized under the cap go
        through, and a max-sized one is refused by name, with the arithmetic, on the operator's
        notification surface. A bounded lane that says why it stopped is the design, not a bug —
        the only unacceptable case is the one that can never trade at all, which is what the
        checks below catch.

        Each of those is indistinguishable from "Congress filed nothing this month" if it is
        allowed through, and the operator's ONLY interface is now notifications about trades
        that are not happening. So it fails here, at boot, next to the file they just saved.
        """
        if not self.congress_autoexec:
            return self
        problems: list[str] = []
        if not self.congress_enabled:
            problems.append(
                "COCKPIT_CONGRESS_ENABLED is false, so no disclosure is ever ingested and the "
                "auto-execution lane would have nothing to trade"
            )
        if not self.congress_follow:
            problems.append(
                "COCKPIT_CONGRESS_FOLLOW is empty, and an empty follow list is an empty "
                "allowlist (never 'everyone'), so every disclosure would be skipped"
            )
        if self.congress_autoexec_max_daily_notional < self.congress_min_notional_per_trade:
            problems.append(
                f"COCKPIT_CONGRESS_AUTOEXEC_MAX_DAILY_NOTIONAL "
                f"({self.congress_autoexec_max_daily_notional}) is below "
                f"COCKPIT_CONGRESS_MIN_NOTIONAL_PER_TRADE "
                f"({self.congress_min_notional_per_trade}), so the budget rail would refuse "
                "every trade the sizing engine is allowed to emit"
            )
        if (self.congress_autoexec_max_notional_per_symbol_per_day
                < self.congress_min_notional_per_trade):
            problems.append(
                f"COCKPIT_CONGRESS_AUTOEXEC_MAX_NOTIONAL_PER_SYMBOL_PER_DAY "
                f"({self.congress_autoexec_max_notional_per_symbol_per_day}) is below "
                f"COCKPIT_CONGRESS_MIN_NOTIONAL_PER_TRADE "
                f"({self.congress_min_notional_per_trade}), so no symbol could ever take its "
                "first trade and every ticket would be refused on the per-symbol rail"
            )
        if self.congress_autoexec_ticket_max_age_seconds <= self.congress_autoexec_window_seconds:
            problems.append(
                f"COCKPIT_CONGRESS_AUTOEXEC_TICKET_MAX_AGE_SECONDS "
                f"({self.congress_autoexec_ticket_max_age_seconds}) is not greater than "
                f"COCKPIT_CONGRESS_AUTOEXEC_WINDOW_SECONDS "
                f"({self.congress_autoexec_window_seconds}), so every ticket would expire "
                "before its own cancel window closed and the lane could never trade"
            )
        if problems:
            raise ValueError(
                "COCKPIT_CONGRESS_AUTOEXEC is ON but the lane cannot trade: "
                + "; ".join(problems)
                + ". Fix the configuration or set COCKPIT_CONGRESS_AUTOEXEC=false — this "
                "refuses at startup rather than running as a lane that notifies you about "
                "nothing while you believe it is trading."
            )
        return self
    # ---- end RW-17 block ------------------------------------------------------------------

    log_level: str = "INFO"


def get_settings() -> Settings:
    """Build Settings, refusing to proceed if the .env file holds a secret (Inv 4).

    The guard runs BEFORE construction on purpose. Reading the file first and complaining
    afterwards would already have loaded the secret into the process — and into any
    ``repr(settings)`` that later reaches a log line.
    """
    assert_no_secrets_in_env_file()
    return Settings()
