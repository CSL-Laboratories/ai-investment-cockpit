"""WP8 + RW-01 — the composition root (contract Owner B 'main.py').

Implements docs/build-scaffold-ultracode.md WP8 (production entrypoint: nothing before WP8
wired ``create_app``/``build_scheduler`` outside tests) and SEC-05/Inv 4 (the audit-chain
HMAC key lives ONLY in keyring — get-or-create via ``core.secrets``; it never appears in
env, config, prompts, or logs). ``compose()`` builds the engine/session factory,
``OrchestratorState``, the WP8 service registry, the RW-01 ingest runner, and the FastAPI app;
``run()`` additionally starts the APScheduler jobs, the ingest stream readers, and uvicorn
(loopback-only).

RW-01 — WHAT CHANGED, AND WHY IT MATTERS (docs/refactor-plan.md §5)
--------------------------------------------------------------------
Before RW-01 the cockpit was fixture-driven: ``build_ingest_fetchers`` was called by NOTHING,
no poller had a production driver, ``workflows.events.persist_events`` was reachable only from
``tests/``, and ``compose`` passed no ``deliver``, so a computed morning brief was stored,
audited, and then went nowhere. Three wires close that:

  1. ``build_ingest_fetchers`` now covers ALL FIVE feeds (it was EDGAR + FINRA only), each
     fail-closed to ``None`` when its secret/config is absent;
  2. ``build_ingest_runner`` turns those closures into live pollers on an ``IngestRunner``,
     which ``build_scheduler`` schedules and ``run()`` streams;
  3. ``build_operator_deliver`` supplies the ``deliver`` seam so ``#brief`` reaches Discord.

STILL PAPER-FIRST. Nothing here touches ``risk/``, ``brokers/`` or the order path; the mode
ladder still boots at ``read_only`` (``config/settings.py``), no model is on the ingest path,
and every new feed's text is neutralized at the boundary by ``ingest.normalize.to_market_event``
-> ``escape_untrusted`` (SEC-02/M3) before it can reach a prompt.

NOT-PROVISIONED IS A FIRST-CLASS STATE. Every secret read here uses ``get_secret`` (-> ``None``)
rather than ``require_secret`` (-> raise), because a dev box with no Alpaca key must still
compose, serve, and run its other feeds. The fail-closed part is that an absent feed is
ABSENT — it registers no poller and no scheduler job, is reported as such by
``IngestRunner.status()``, and never silently degrades into "that feed had no news today".
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("cockpit.main")

_HMAC_SECRET_NAME = "audit-hmac-key"
# Keyring entry names — these MUST match docs/setup/accounts-and-keys.md §5 exactly, since that
# table is what the operator types into `keyring set`. (RW-01 fixed a real mismatch here: this
# constant read "discord-bot-token" with HYPHENS while the operator provisions
# "discord_bot_token" with UNDERSCORES, so the SEC-11 #audit-log witness and every Discord post
# would have stayed dark even after correct provisioning. One spelling, taken from the doc.)
_DISCORD_TOKEN_NAME = "discord_bot_token"   # keyring-only (Inv 4); #audit-log witness + delivery
_ALPACA_KEY_NAME = "alpaca_key"
_ALPACA_SECRET_NAME = "alpaca_secret"
_FINNHUB_KEY_NAME = "finnhub_key"

# Feed endpoints. Non-secret; the credential always travels in a HEADER, never in the query
# string, so a raised urllib error (whose repr carries the URL) can never leak a key into a log.
ALPACA_NEWS_WS = "wss://stream.data.alpaca.markets/v1beta1/news"
FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/news"
FINNHUB_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"
CALENDAR_LOOKAHEAD_DAYS = 14


def _hmac_key() -> bytes:
    """Get-or-create the audit-chain HMAC key in keyring (Inv 4: never in env/files)."""
    import secrets as _stdlib_secrets

    from core.secrets import get_secret, set_secret

    existing = get_secret(_HMAC_SECRET_NAME)
    if existing is None:
        set_secret(_HMAC_SECRET_NAME, _stdlib_secrets.token_hex(32))
        existing = get_secret(_HMAC_SECRET_NAME)
    return existing.reveal().encode("utf-8")


def _build_anchor_store(settings):
    """SEC-11 (W10, hardened by the G10 red-team 2026-07-08): the durable audit witness. ALWAYS
    a non-None ``GitAuditLogAnchorStore`` — NEVER ``FileAnchorStore``. But the store's TRUST read
    (``latest()``, which the gate/mode/gateway/budget consult) counts ONLY genuinely OUT-OF-DOMAIN
    backends: the ``GitCommitWitness`` (git head file) is a same-trust-domain MIRROR + reviewed-
    history detection layer and does NOT satisfy the auto/live prerequisite on its own (its
    runtime read is a local file a keyless writer can overwrite). The Discord ``#audit-log``
    witness (server-stamped, out-of-domain) is the trust source; it joins once the bot token is in
    keyring AND a channel is configured. Until then ``latest()`` is None and every consult FAILS
    CLOSED — auto unreachable and the live/budget paths refuse. That is the correct posture: a
    genuine out-of-domain witness is the prose prerequisite (bot creds) SEC-11's closure ACTIVATES
    on; a git-only deployment is fail-closed, never falsely 'anchored'."""
    from bridge.anchor import GitAuditLogAnchorStore
    from bridge.witness import GitCommitWitness, discord_rest_witness
    from core.secrets import get_secret

    adapters = [GitCommitWitness(settings.audit_head_file)]   # mirror + reviewed-history detection
    token = get_secret(_DISCORD_TOKEN_NAME)
    if token is not None and settings.audit_log_channel_id is not None:
        adapters.append(discord_rest_witness(settings.audit_log_channel_id, token.reveal()))
    return GitAuditLogAnchorStore(*adapters)


def run_startup_security_checks(settings):
    """W11 (SEC-05 / SEC-06) — fail-closed startup checks invoked by ``compose()`` BEFORE the app
    graph is built: (1) the decision-agent account provably cannot read the bearer stores
    (``account_guard``), and (2) every canonical decision-stage job resolves to a tool/MCP-STARVED
    argv (``tool_inspector``). RAISES (``CredentialGuardError`` / ``ToolStarvationError``) on any
    violation — the cockpit refuses to serve a decision/order path it cannot vouch for. Returns a
    summary for observability. In dev (no low-priv account configured) the credential control is
    reported INACTIVE, not passed — and a WARNING is logged, because a live/auto deployment MUST
    provision it (the ACL check + spawn-under-account is only ACTIVE once ``decision_agent_account``
    is set and the account-launching transport is wired; the runner fails closed otherwise)."""
    import logging

    from agent_runner import account_guard, tool_inspector

    cred = account_guard.assert_credential_starved(settings)
    tool_inspector.assert_all_decision_specs_tool_starved(
        tool_inspector.default_decision_stage_specs()
    )
    if not cred.enforced:
        logging.getLogger("cockpit.security").warning(
            "SEC-05 credential starvation INACTIVE: no decision_agent_account configured; decision "
            "agents run under the orchestrator token. Provision the low-priv account before any "
            "live/auto order path (docs/setup/low-priv-agent-account.md)."
        )
    return {"credential_guard_enforced": cred.enforced, "decision_tools_starved": True}


def build_decision_transport(settings):
    """SEC-05 — the transport decision-stage agents spawn through. When a low-priv account is
    configured, an account-launching transport (so the child runs under it, not the orchestrator);
    else the default (dev). The runner independently fails closed if a configured account is not
    applied at the spawn, so a mis-wired transport can never run a decision job privileged."""
    from agent_runner.transport import LowPrivSubprocessTransport, SubprocessTransport

    account = getattr(settings, "decision_agent_account", None)
    return LowPrivSubprocessTransport(account) if account else SubprocessTransport()


def _no_broker_history(order_id: str) -> list:
    """SEC-13 default broker-history reader for startup reconciliation. NO live broker read
    exists until W17 wires the MCP order-history seam, so this returns ``[]`` (no history) —
    a FRESH empty DB has no unresolved intents, so reconciliation is clean (``ok=True``). Once
    W17 lands, this is replaced by the live read so a crashed-mid-submit intent can be healed."""
    return []


def _startup_reconcile(state) -> None:
    """SEC-13 — reconcile the two-phase ledger against the broker + domain tables at startup.
    On a NOT-``ok`` verdict we LOG the alert (already emitted by the injected alerter) and keep
    serving: reconciliation SURFACES a completeness defect, it does not gate boot. A fresh empty
    DB reconciles clean, so the ``compose`` smoke test stays green."""
    import logging

    from orchestrator.reconcile import reconcile_startup

    log = logging.getLogger("cockpit.reconcile")
    report = reconcile_startup(state, broker_history_fn=_no_broker_history,
                               alerter=log.warning)
    if not report.ok:
        log.warning(
            "SEC-13 startup reconciliation is NOT ok: healed=%s alerted=%s orphan_results=%s "
            "uncovered=%s — investigate the two-phase/completeness gaps above.",
            report.healed, report.alerted, report.orphan_results, report.uncovered,
        )


def _urllib_json_get(url: str, *, headers=None, params=None, timeout: float = 20.0):
    """Stdlib JSON GET. Injected everywhere as ``http_get`` so no ingest module imports a client.

    Stdlib ``urllib`` (not httpx) matches ``bridge/witness.py:discord_rest_witness`` and keeps
    the composition root's dependency surface unchanged. Credentials belong in ``headers``: a
    ``urllib.error.HTTPError`` repr includes the request URL, so a key placed in ``params``
    would land in any log line that formats the exception.
    """
    import urllib.parse
    import urllib.request

    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    class _Response:            # mirrors the ``.json()`` shape make_finra_fetch expects
        @staticmethod
        def json():
            return payload

    return _Response()


def build_alpaca_news_connect(api_key: str, api_secret: str, *, url: str = ALPACA_NEWS_WS,
                              ws_connect=None):
    """RW-01 — the ``connect() -> iterable-of-frames`` factory ``AlpacaNewsConsumer`` takes.

    Authenticates, subscribes to the wildcard firehose (``ingest.alpaca_news.SUBSCRIBE``), and
    yields ONLY news frames (``T == "n"``). Control frames (``success`` / ``error`` /
    ``subscription``) are dropped here rather than downstream, so the consumer's dedup and
    reconnect accounting only ever sees actual news.

    The generator RAISES on a dropped connection — that is the contract
    ``AlpacaNewsConsumer.run`` is built on (it backs off and reconnects), so nothing here may
    swallow a socket error into a clean end-of-stream: doing so would turn a dead feed into a
    silent one. The credentials live in this closure only (Inv 4) and are sent in the auth
    frame, never logged.
    """
    from ingest.alpaca_news import SUBSCRIBE

    def connect():
        connector = ws_connect
        if connector is None:
            from websockets.sync.client import connect as ws_sync_connect

            connector = ws_sync_connect
        with connector(url) as socket:
            socket.send(json.dumps({"action": "auth", "key": api_key, "secret": api_secret}))
            socket.send(json.dumps(SUBSCRIBE))
            for raw in socket:
                for frame in _news_frames(raw):
                    yield frame

    return connect


def _log_unprovisioned(what: str, how: str) -> None:
    """Say plainly that something is OFF, and how to turn it on.

    Not a debug line: an unprovisioned feed produces zero events forever, which is exactly the
    shape of a quiet market, and an unprovisioned surface delivers nothing, which is exactly the
    shape of a day with nothing to say. The operator must be able to tell those apart from the
    log alone — and again from ``IngestRunner.status()``, and again from the runbook's check.
    """
    # ASCII only: this lands on a Windows console (cp1252), where a stray em-dash renders as
    # mojibake and makes the one line an operator most needs to read look like a corruption.
    log.warning("NOT PROVISIONED: %s is OFF and will stay silent. Silence from it is NOT a "
                "quiet day. To enable: %s", what, how)


def _news_frames(raw):
    """Parse one wire message into the news frames it carries. Alpaca sends a JSON ARRAY of
    messages per socket frame; a malformed frame yields nothing rather than raising, because a
    parse failure is attacker-influenced content and must not be mistaken for a disconnect."""
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    messages = decoded if isinstance(decoded, list) else [decoded]
    return [m for m in messages if isinstance(m, dict) and m.get("T") == "n"]


def build_finnhub_news_fetch(api_key: str, *, http_get=None, category: str = "general"):
    """RW-01 — the ``fetch(min_id, now) -> list[dict]`` closure ``FinnhubPoller`` takes.

    The key travels in the ``X-Finnhub-Token`` HEADER, deliberately not the documented
    ``?token=`` query parameter: the query form would put the key inside every logged URL and
    inside a ``urllib`` error repr (Inv 4). A non-list payload (an error object, a rate-limit
    body) returns ``[]`` for this tick — the poller's cursor then holds, so nothing is skipped.
    """
    getter = http_get if http_get is not None else _urllib_json_get
    headers = {"X-Finnhub-Token": api_key, "Accept": "application/json"}

    def fetch(min_id, now):
        payload = getter(FINNHUB_NEWS_URL, headers=headers,
                         params={"category": category, "minId": int(min_id or 0)}).json()
        return payload if isinstance(payload, list) else []

    return fetch


def build_earnings_calendar_fetch(api_key: str, *, clock, http_get=None,
                                  days_ahead: int = CALENDAR_LOOKAHEAD_DAYS):
    """RW-01 — the ``fetch() -> list[dict]`` closure ``CalendarPoller`` takes.

    Re-reads a FORWARD WINDOW ``[today, today + days_ahead]`` on every poll and maps Finnhub's
    ``earningsCalendar`` rows onto the ``{symbol, date, note}`` shape ``pull_calendar`` expects.
    Re-fetching the whole window each time is intended: a calendar is a re-published snapshot
    whose entries get REVISED, and the poller de-dupes on entry identity (see
    ``ingest/calendars.py`` — "NO CURSOR HERE, AND THAT IS CORRECT").

    ``clock`` is the tz-aware wall clock (``state.clock.now``) — never ``poll``'s monotonic
    ``now``, which is ~1970 when read as a date (``ingest/clock_contract.py``).
    """
    from datetime import timedelta

    getter = http_get if http_get is not None else _urllib_json_get
    headers = {"X-Finnhub-Token": api_key, "Accept": "application/json"}

    def fetch():
        today = clock().date()
        payload = getter(FINNHUB_EARNINGS_URL, headers=headers, params={
            "from": today.isoformat(),
            "to": (today + timedelta(days=days_ahead)).isoformat(),
        }).json()
        rows = payload.get("earningsCalendar") if isinstance(payload, dict) else None
        entries = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            # Untrusted vendor text. It is NOT escaped here: it is escaped ONCE, at the single
            # boundary every feed shares (``to_market_event`` -> ``escape_untrusted``, called
            # by ``pull_calendar``). Escaping twice would double-encode; escaping here instead
            # would put a second, drifting copy of the boundary in the composition root.
            entries.append({
                "symbol": row.get("symbol"),
                "date": row.get("date"),
                "note": f"EPS est {row.get('epsEstimate')} · rev est {row.get('revenueEstimate')}"
                        f" · {row.get('hour') or 'time TBD'}",
            })
        return entries

    return fetch


def build_ingest_fetchers(settings, *, http_get=None, ws_connect=None,
                          finra_token_provider=None, finra_http_get=None,
                          finra_row_mapper=None, clock=None):
    """W20 + RW-01 — build the production ingest fetch CLOSURES. All I/O lives in these
    factories, never in the pure ingest modules (which take an injected ``fetch``).

    Returns one entry per feed, keyed by the ``orchestrator.ingest_runner`` feed name::

        {"alpaca:benzinga": connect | None,   # a connect() factory (the WS stream)
         "finnhub":         fetch   | None,
         "sec:edgar":       fetch   | None,
         "calendar":        fetch   | None,
         "finra":           fetch   | None}

    A None value means NOT PROVISIONED, and a caller MUST NOT read it as "no events" —
    ``build_ingest_runner`` registers no poller for it, so it is visibly absent rather than
    silently quiet. For EDGAR specifically, consult ``fetch.brew_status(day)``
    (``ingest/edgar_brew.py:make_brew_fetch``), whose NOT_PROVISIONED / NOT_RUN / DEGRADED
    statuses are deliberately distinguishable from a legitimate quiet day; the runner wires that
    reader into ``status()`` for exactly this reason.

    Secrets are read with ``get_secret`` (never ``require_secret``): a box with no Alpaca key
    must still compose and run its other feeds. What must never happen is a HALF-BUILT feed, so
    each one is all-or-nothing — Alpaca needs BOTH key and secret, FINRA needs endpoint + UA +
    token provider + http_get, or they stay None.

    FINRA's ``token_provider`` (OAuth2 client_credentials — credentials are keyring-only, Inv 4)
    and ``http_get`` are still injected by the CALLER rather than built here: its dataset URL and
    wire schema are console-gated and unverifiable from any primary source
    (``ingest/finra_short_interest.py``), so this module refuses to invent them.
    """
    from core.secrets import get_secret
    from ingest.edgar_brew import make_brew_fetch
    from ingest.finra_short_interest import make_finra_fetch

    fetchers = {"alpaca:benzinga": None, "finnhub": None, "sec:edgar": None,
                "calendar": None, "finra": None}

    alpaca_key = get_secret(_ALPACA_KEY_NAME)
    alpaca_secret = get_secret(_ALPACA_SECRET_NAME)
    if alpaca_key is not None and alpaca_secret is not None:
        fetchers["alpaca:benzinga"] = build_alpaca_news_connect(
            alpaca_key.reveal(), alpaca_secret.reveal(), ws_connect=ws_connect)
    else:
        _log_unprovisioned("alpaca:benzinga",
                           f"keyring set {_ALPACA_KEY_NAME} / {_ALPACA_SECRET_NAME}")

    finnhub_key = get_secret(_FINNHUB_KEY_NAME)
    if finnhub_key is not None:
        # ONE key serves both the news poller and the earnings calendar (accounts-and-keys §2).
        # ``clock`` DEFAULTS to real tz-aware UTC rather than gating the calendar on being
        # passed one: a feed you silently lose by omitting an argument is the same class of
        # trap as a control you disable by not typing it (ingest/edgar_brew.read_brew_run_outcome).
        from ingest.clock_contract import utc_now

        fetchers["finnhub"] = build_finnhub_news_fetch(finnhub_key.reveal(), http_get=http_get)
        fetchers["calendar"] = build_earnings_calendar_fetch(
            finnhub_key.reveal(), clock=clock if clock is not None else utc_now,
            http_get=http_get)
    else:
        _log_unprovisioned("finnhub + calendar", f"keyring set {_FINNHUB_KEY_NAME}")

    if settings.brew_db_path:
        fetchers["sec:edgar"] = make_brew_fetch(settings.brew_db_path,
                                                runs_dir=settings.brew_runs_dir)
    else:
        _log_unprovisioned("sec:edgar", "set COCKPIT_BREW_DB_PATH (+ COCKPIT_BREW_RUNS_DIR)")

    if settings.finra_endpoint and settings.finra_user_agent and finra_token_provider \
            and finra_http_get:
        from ingest.ratelimit import TokenBucket

        fetchers["finra"] = make_finra_fetch(
            endpoint=settings.finra_endpoint,
            user_agent=settings.finra_user_agent,
            token_provider=finra_token_provider,
            http_get=finra_http_get,
            row_mapper=finra_row_mapper,
        )
        # The bucket is the POLLER's, not the fetch closure's. It rides on the closure only so
        # the later wiring stays a one-liner; the CONTRACT is that the caller passes it through:
        #     FinraShortInterestPoller(fetch, bucket=fetch.bucket)
        # Nothing reads this attribute otherwise. The rate is a self-imposed politeness floor,
        # NOT a verified FINRA cap (settings.finra_rate_per_sec). Capacity == rate so it starts
        # empty and takes no birth burst (ingest/ratelimit.py TokenBucket).
        fetchers["finra"].bucket = TokenBucket(rate_per_sec=settings.finra_rate_per_sec,
                                               capacity=settings.finra_rate_per_sec)
    else:
        _log_unprovisioned("finra", "set COCKPIT_FINRA_ENDPOINT + COCKPIT_FINRA_USER_AGENT and "
                                    "pass finra_token_provider/finra_http_get to compose()")
    return fetchers


def build_ingest_runner(state, *, parquet_root, fetchers, settings=None, clock=None,
                        monotonic=None):
    """RW-01 — turn the fetch closures into LIVE pollers on an ``IngestRunner``.

    This is the wire that was missing: ``build_ingest_fetchers`` produced closures nothing
    consumed, and the WP3 pollers had no production driver. Each provisioned feed gets its
    poller here; ``build_scheduler(ingest=runner)`` then gives it a cron job and ``run()``
    starts the stream reader.

    ONE WALL CLOCK, BOTH SEAMS. ``EdgarBrewPoller(clock=...)`` and
    ``FinraShortInterestPoller(clock=...)`` take the SAME kind of object — a zero-arg callable
    returning a tz-AWARE datetime, validated by ``ingest.clock_contract.resolve_wall_clock``.
    ``state.clock.now`` is exactly that, so one object drives both, which is the whole point of
    that module ("one convention, two pollers"). It is NEVER ``poll``'s monotonic ``now``.

    FINRA's bucket is passed through from the closure per its documented contract
    (``FinraShortInterestPoller(fetch, bucket=fetch.bucket)``) — the rate lives with the
    poller, not the fetch.
    """
    from ingest.alpaca_news import AlpacaNewsConsumer
    from ingest.calendars import CalendarPoller
    from ingest.edgar_brew import EdgarBrewPoller
    from ingest.finnhub_poll import FinnhubPoller
    from ingest.finra_short_interest import FinraShortInterestPoller
    from orchestrator.ingest_runner import (
        FEED_ALPACA_NEWS,
        FEED_CALENDAR,
        FEED_EDGAR,
        FEED_FINNHUB,
        FEED_FINRA,
        IngestRunner,
    )

    clock = clock if clock is not None else state.clock
    wall_clock = clock.now                      # tz-aware; the ONE clock-contract seam object
    runner = IngestRunner(state, parquet_root=parquet_root, clock=clock, monotonic=monotonic)

    if fetchers.get(FEED_ALPACA_NEWS) is not None:
        runner.add_stream(FEED_ALPACA_NEWS, AlpacaNewsConsumer(fetchers[FEED_ALPACA_NEWS]))
    if fetchers.get(FEED_FINNHUB) is not None:
        runner.add_poller(FEED_FINNHUB, FinnhubPoller(fetchers[FEED_FINNHUB]))
    if fetchers.get(FEED_EDGAR) is not None:
        brew_fetch = fetchers[FEED_EDGAR]
        runner.add_poller(
            FEED_EDGAR, EdgarBrewPoller(brew_fetch, clock=wall_clock),
            # The feed's OWN freshness verdict. Without it, a NOT_PROVISIONED / NOT_RUN /
            # DEGRADED brew is indistinguishable from a quiet EDGAR day — the exact confusion
            # ingest/edgar_brew.py exists to prevent.
            status_fn=getattr(brew_fetch, "brew_status", None),
        )
    if fetchers.get(FEED_CALENDAR) is not None:
        runner.add_poller(FEED_CALENDAR, CalendarPoller(fetchers[FEED_CALENDAR]))
    if fetchers.get(FEED_FINRA) is not None:
        finra_fetch = fetchers[FEED_FINRA]
        runner.add_poller(FEED_FINRA, FinraShortInterestPoller(
            finra_fetch, bucket=getattr(finra_fetch, "bucket", None), clock=wall_clock))

    log.info("ingest wired: poll feeds=%s stream feeds=%s",
             sorted(runner.polls) or "NONE", sorted(runner.streams) or "NONE")
    return runner


def build_operator_deliver(settings):
    """RW-01 — the ``deliver(channel, payload) -> bool`` seam, or ``None`` when unprovisioned.

    ``None`` is the honest not-wired state: ``morning_brew`` reports ``delivered: false`` and
    the brief is still computed, stored and audited, so an operator without a bot token loses
    the push, not the work. What must not happen is a ``deliver`` that returns True having
    posted nowhere — see ``bridge/deliver.py``.
    """
    from bridge.deliver import channel_ids_from_settings, discord_rest_deliver
    from core.secrets import get_secret

    channel_ids = channel_ids_from_settings(settings)
    token = get_secret(_DISCORD_TOKEN_NAME)
    if token is None or not channel_ids:
        _log_unprovisioned(
            "operator delivery (Discord)",
            f"keyring set {_DISCORD_TOKEN_NAME} and set COCKPIT_BRIEF_CHANNEL_ID "
            "(the #morning-brew channel id)")
        return None
    log.info("operator delivery wired for channels: %s", sorted(channel_ids))
    return discord_rest_deliver(channel_ids, token.reveal())


def compose(*, db_url: str = "sqlite:///cockpit.db", parquet_root: str = "data/raw",
            clock=None, transport=None, deliver=None, ingest_fetchers=None,
            http_get=None, ws_connect=None, finra_token_provider=None, finra_http_get=None,
            finra_row_mapper=None):
    """Build the full application graph: ``(state, app, services)``.

    RETURN SHAPE IS UNCHANGED (a 3-tuple) ON PURPOSE. The RW-01 ingest runner is reachable as
    ``app.state.ingest``, alongside the existing ``app.state.orch`` / ``app.state.services`` —
    so every existing caller and test that unpacks ``state, app, services`` keeps working, and
    the runner is discoverable exactly where the other composed collaborators already live.

    Every seam is injectable so the WHOLE GRAPH — not a hand-rebuilt copy of it — is drivable
    with no live credential and no network: ``clock`` (a ``FrozenClock`` for a deterministic
    day), ``transport`` (scripted agent calls instead of ``claude -p`` subprocesses),
    ``deliver`` (the operator surface), ``ingest_fetchers`` (pre-built feed closures, bypassing
    the keyring read entirely), ``http_get`` / ``ws_connect`` (the network), and the three FINRA
    parameters whose values are console-gated and cannot be invented here.

    That list is deliberately long. Before RW-01 the system test had to MIRROR ``compose`` with
    fakes (``tests/system/harness.build_system``) because compose itself took nothing, so the
    thing under test was a replica and the real composition root stayed unexercised. Omit every
    seam and you get the production wiring, which fails CLOSED per feed on a missing secret
    rather than refusing to boot.
    """
    from config.settings import get_settings
    from core.clock import default_clock
    from orchestrator.app import create_app
    from orchestrator.halt import default_halt_path, ensure_halt_dir
    from orchestrator.state import OrchestratorState
    from storage.engine import create_all, make_engine, make_session_factory
    from workflows.services import build_services

    settings = get_settings()
    clock = clock if clock is not None else default_clock
    # SEC-04 (R3-S3): create the HALT sentinel's PARENT dir so an ABSENT sentinel resolves to the
    # true not-found signal (Windows winerror 2 => not-halted) and the fail-closed winerror-2
    # invariant does not trip on the happy path; a dropped mount still fails closed.
    ensure_halt_dir(default_halt_path())
    run_startup_security_checks(settings)   # SEC-05/06: fail closed before building the graph
    engine = make_engine(db_url)
    create_all(engine)
    session_factory = make_session_factory(engine)
    state = OrchestratorState.build(session_factory, hmac_key=_hmac_key(), settings=settings,
                                    clock=clock,
                                    anchor_store=_build_anchor_store(settings))
    _startup_reconcile(state)   # SEC-13: heal/alert the two-phase ledger before wiring services
    # RW-01: the operator surface. Without this the brew was computed, stored, audited — and
    # then delivered nowhere, because compose passed no `deliver` at all.
    deliver = deliver if deliver is not None else build_operator_deliver(settings)
    # SEC-05: decision-stage agents spawn under the low-priv account when one is configured.
    # Inv 2 is NOT relaxed by the injectable transport: the runtime pin lives in the model map
    # (config/models.yaml -> runtime_for) and is enforced by the agent runner + the arm gate, so
    # a transport swap changes HOW a decision job is spawned, never WHICH runtime may decide.
    transport = transport if transport is not None else build_decision_transport(settings)
    services = build_services(state, transport=transport,
                              deliver=deliver, parquet_root=parquet_root)
    app = create_app(state, services=services)
    # RW-01: ingest -> scheduler. Built AFTER the state so the pollers persist through the same
    # audited session factory, and hung off app.state so /status and run() can reach it.
    fetchers = ingest_fetchers if ingest_fetchers is not None else build_ingest_fetchers(
        settings, http_get=http_get, ws_connect=ws_connect,
        finra_token_provider=finra_token_provider, finra_http_get=finra_http_get,
        finra_row_mapper=finra_row_mapper, clock=state.clock.now,
    )
    app.state.ingest = build_ingest_runner(state, parquet_root=parquet_root, fetchers=fetchers,
                                           settings=settings, clock=clock)
    return state, app, services


def run() -> None:  # pragma: no cover - production entrypoint; the composed graph IS under test
    """Start the cockpit: ingest + scheduler jobs + the loopback-only API.

    ``compose()`` already ran the W11 SEC-05/06 startup security checks (fail closed). The W11
    always-on order tracer (``brokers/order_tracer.install``) is installed at the LIVE order-path
    composition seam, which is Phase-3-gated (``robinhood_mcp`` is a stub until W17) — the live
    ``agentic_place`` path itself REFUSES to run untraced, so no order can reach the broker before
    the tracer guards it.

    ORDER IS LOAD-BEARING: streams start BEFORE ``serve``, because ``serve`` blocks forever. The
    stream readers are daemon threads (``IngestRunner.start_streams``), so Ctrl-C / a service
    stop tears them down with the process rather than hanging on a blocked socket read.

    RW-01 keeps ``run()`` itself a thin, unbranching assembly — the parts it assembles are each
    under test (``compose`` in tests/workflows/test_main_compose.py, the composed market day in
    tests/system/test_rw01_market_day.py), so what is left here is the sequence, not logic.
    """
    from orchestrator.app import serve
    from orchestrator.scheduler import build_scheduler

    state, app, services = compose()
    ingest = app.state.ingest
    build_scheduler(state, services=services, ingest=ingest).start()
    ingest.start_streams()
    serve(app, state.settings)


if __name__ == "__main__":  # pragma: no cover
    run()
