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

STILL PAPER-FIRST. Production may compose the fixed Alpaca PAPER transport, but the mode ladder
still boots at ``read_only`` and the transport remains unreachable until an audited transition to
``paper`` plus deterministic risk plus an allowlisted human approval. No model is on the ingest
path, and every new feed's text is neutralized at the boundary by
``ingest.normalize.to_market_event`` -> ``escape_untrusted`` (SEC-02/M3) before it can reach a
prompt.

NOT-PROVISIONED IS A FIRST-CLASS STATE. Every secret read here uses ``get_secret`` (-> ``None``)
rather than ``require_secret`` (-> raise), because a dev box with no Alpaca key must still
compose, serve, and run its other feeds. The fail-closed part is that an absent feed is
ABSENT — it registers no poller and no scheduler job, is reported as such by
``IngestRunner.status()``, and never silently degrades into "that feed had no news today".
"""
from __future__ import annotations

import json
import logging
import threading

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
CALENDAR_LOOKAHEAD_DAYS = 3     # fallback only; settings.calendar_lookahead_days is the knob


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
    backends: the ``GitCommitWitness`` head file is a same-trust-domain local MIRROR and does
    NOT satisfy the auto/live prerequisite on its own (its
    runtime read is a local file a keyless writer can overwrite). The Discord ``#audit-log``
    witness (server-stamped, out-of-domain) is the trust source; it joins once the bot token is in
    keyring AND a channel is configured. Until then ``latest()`` is None and every consult FAILS
    CLOSED — auto unreachable and the live/budget paths refuse. That is the correct posture: a
    genuine out-of-domain witness is the prose prerequisite (bot creds) SEC-11's closure ACTIVATES
    on; a git-only deployment is fail-closed, never falsely 'anchored'."""
    from bridge.anchor import GitAuditLogAnchorStore
    from bridge.witness import GitCommitWitness, discord_rest_witness
    from core.secrets import get_secret

    # BUG-051: runtime commits on whichever development branch happens to be checked out make
    # the machine-written head collide across branches. The file remains a local
    # cross-check, but it never mutates Git's index or current branch. This does not weaken the
    # trust read: this adapter is out_of_domain=False and has always been excluded from latest().
    adapters = [GitCommitWitness(settings.audit_head_file, commit_fn=False)]
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
        tool_inspector.default_decision_stage_specs(settings)
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


class AlpacaStreamError(RuntimeError):
    """The Alpaca news stream reported an ERROR control frame (auth failure, bad subscribe...).

    RAISED, never swallowed. ``AlpacaNewsConsumer.run`` treats an exception as a disconnect and
    backs off/reconnects, so a persistent auth failure surfaces as a climbing ``errors`` count
    in ``IngestRunner.status()``. Returning quietly instead would end the generator, which the
    consumer reads as a CLEAN END OF STREAM: ``emitted=0, errors=0`` — a wrong API key rendered
    as a quiet news day. That is the exact failure class this codebase exists to prevent, so the
    error frame must be louder than the silence it would otherwise become.
    """


def _news_frames(raw):
    """Parse one wire message into the news frames it carries.

    Alpaca sends a JSON ARRAY of messages per socket frame, mixing CONTROL frames
    (``success`` / ``subscription`` / ``error``) with news (``T == "n"``).

    A malformed frame yields nothing rather than raising: a parse failure is attacker-influenced
    content and must not be mistaken for a disconnect (the same reasoning as
    ``ingest/alpaca_news.py``'s per-frame try/except, which keeps hostile content from burning
    the reconnect budget).

    An ERROR control frame is the opposite case and RAISES — see ``AlpacaStreamError``. The
    distinction is deliberate: a bad frame is one item we could not read, while an error frame is
    the VENDOR telling us the session is not going to work. Only the message and code travel into
    the exception; the credentials never do (Inv 4).
    """
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    messages = decoded if isinstance(decoded, list) else [decoded]
    for message in messages:
        if isinstance(message, dict) and message.get("T") == "error":
            raise AlpacaStreamError(
                f"alpaca news stream error code={message.get('code')!r} "
                f"msg={message.get('msg')!r} (402/401 => the alpaca_key/alpaca_secret pair was "
                "rejected; re-check `python tools/verify_keys.py` and the Alpaca dashboard)"
            )
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


def _estimate(value, *, zero_is_missing: bool = False) -> str:
    """Render a vendor estimate for operator eyes, distinguishing ABSENT from ZERO.

    Finnhub returns ``0`` (not null) when it has no estimate, so the naive rendering printed
    "rev est 0" for a company with no published revenue estimate — which reads as a FORECAST OF
    ZERO REVENUE rather than "we don't know". SEC-17's principle is that the operator decides
    from what they see, so a field that states a number we do not have is worse than one that
    admits the gap.

    ``zero_is_missing`` is set for REVENUE only. A genuine 0.0 EPS estimate is perfectly
    plausible (a breakeven quarter) and must survive; a listed company reporting earnings never
    has a real revenue estimate of exactly zero.
    """
    if value is None or (zero_is_missing and value == 0):
        return "n/a"
    return str(value)


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
                "note": f"EPS est {_estimate(row.get('epsEstimate'))} · "
                        f"rev est {_estimate(row.get('revenueEstimate'), zero_is_missing=True)}"
                        f" · {row.get('hour') or 'time TBD'}",
            })
        return entries

    return fetch


def build_ingest_fetchers(settings, *, http_get=None, ws_connect=None,
                          finra_token_provider=None, finra_http_get=None,
                          finra_row_mapper=None, clock=None, session_factory=None,
                          audit_log=None, watchlist=(), cockpit_db_path=None,
                          parquet_root="data/raw", edgar_clock=None,
                          edgar_fetcher_factory=None):
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
    silently quiet. For EDGAR specifically, consult ``fetch.brew_status(day)``. RW-11's
    in-process adapter and the legacy brew reader expose the same duck-typed freshness seam,
    so NOT_RUN / DEGRADED remain distinguishable from a legitimate quiet day during the RW-12
    evidence window.

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
    from ingest.edgar_pipeline import build_in_process_edgar_fetch
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
        # The calendar is FULL-MARKET, so its window length sets how much reaches the brew's
        # triage packet (a 14-day window measured ~1500 entries / ~86k tokens on the live free
        # tier). ``calendar_lookahead_days = 0`` turns the feed OFF outright — the honest
        # setting until RW-05 can scope it to a universe rather than to every US issuer.
        days = int(getattr(settings, "calendar_lookahead_days", CALENDAR_LOOKAHEAD_DAYS))
        if days > 0:
            fetchers["calendar"] = build_earnings_calendar_fetch(
                finnhub_key.reveal(), clock=clock if clock is not None else utc_now,
                http_get=http_get, days_ahead=days)
        else:
            _log_unprovisioned("calendar", "set COCKPIT_CALENDAR_LOOKAHEAD_DAYS > 0 "
                                           "(deliberately disabled at 0)")
    else:
        _log_unprovisioned("finnhub + calendar", f"keyring set {_FINNHUB_KEY_NAME}")

    sec_user_agent = (settings.sec_user_agent or "").strip()
    if sec_user_agent and session_factory is not None and audit_log is not None \
            and cockpit_db_path is not None and edgar_clock is not None:
        from pathlib import Path

        fetchers["sec:edgar"] = build_in_process_edgar_fetch(
            session_factory=session_factory,
            settings=settings,
            tickers=watchlist,
            db_path=cockpit_db_path,
            raw_store_root=Path(parquet_root) / "sec",
            audit_log=audit_log,
            clock=edgar_clock,
            fetcher_factory=edgar_fetcher_factory,
        )
    elif sec_user_agent:
        _log_unprovisioned(
            "sec:edgar",
            "in-process EDGAR requires the composed DB, audit, clock, and watchlist context",
        )
    if fetchers["sec:edgar"] is None and settings.brew_db_path:
        fetchers["sec:edgar"] = make_brew_fetch(settings.brew_db_path,
                                                runs_dir=settings.brew_runs_dir)
    elif fetchers["sec:edgar"] is None:
        _log_unprovisioned(
            "sec:edgar",
            "set COCKPIT_SEC_USER_AGENT + COCKPIT_WATCHLIST, or legacy COCKPIT_BREW_DB_PATH",
        )

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


def build_paper_execution(state, *, client=None, watchlist_fn=None, parquet_root="data/raw"):
    """Compose the real Alpaca PAPER seam behind the existing guarded ``OrderGateway``.

    Missing credentials leave the service absent and visible as ``501 not_wired``; they never
    fall back to ``FakeAlpaca`` in production. The fixed-host HTTP client is constructed only
    after both keyring values exist. ``client`` is an injectable no-network seam for tests.
    """
    from brokers.alpaca_paper import AlpacaPaperBroker, AlpacaPaperHTTPClient
    from brokers.capability import TokenRegistry
    from brokers.gateway import OrderGateway
    from brokers.paper_execution import PaperExecutionService
    from core.secrets import get_secret
    from ingest.market_data import MarketDataService
    from orchestrator.quant_ws import SnapshotSource
    from pathlib import Path

    if client is None:
        key = get_secret(_ALPACA_KEY_NAME)
        secret = get_secret(_ALPACA_SECRET_NAME)
        if key is None or secret is None:
            _log_unprovisioned(
                "alpaca paper trading",
                f"keyring set {_ALPACA_KEY_NAME} / {_ALPACA_SECRET_NAME}",
            )
            return None
        client = AlpacaPaperHTTPClient(
            key.reveal(), secret.reveal(), clock=state.clock.now
        )

    broker = AlpacaPaperBroker(client=client)
    registry = TokenRegistry()
    gateway = OrderGateway(
        broker,
        st=state,
        token_registry=registry,
        pending_exposure_cap=state.settings.pending_exposure_cap,
        approval_ttl_seconds=state.settings.approval_ttl_seconds,
        execution_scope="paper",
    )
    symbols = frozenset(watchlist_fn()) if watchlist_fn is not None else frozenset()
    def held_symbols():
        rows = client.positions_info()
        if not isinstance(rows, list):
            raise RuntimeError("paper positions response was not a list")
        return tuple(
            str(row.get("symbol", "")).strip().upper()
            for row in rows
            if isinstance(row, dict) and row.get("symbol")
        )

    market_data = MarketDataService(
        client,
        symbols=symbols,
        clock=state.clock,
        raw_root=Path(parquet_root) / "market-data",
        snapshot_source=SnapshotSource(),
        additional_symbols_fn=held_symbols,
        audit_log=state.audit,
    )
    state.market_data = market_data
    log.info("Alpaca PAPER execution transport wired behind OrderGateway")
    return PaperExecutionService(
        state,
        broker=broker,
        gateway=gateway,
        token_registry=registry,
        quote_fn=market_data.quote if market_data is not None else client.latest_quote,
        watchlist_fn=watchlist_fn,
        market_data=market_data,
    )


def build_operator_bot(state):
    """QT-09 — the Discord COMMAND surface, or ``None`` when unprovisioned.

    Until now Discord was PUSH-ONLY. ``compose`` wired ``deliver`` (REST posts to the operator
    channels) but nothing ever called ``bridge.bot.build_bot``, so no gateway connection
    existed and no slash command could fire — ``/mode``, ``/status``, ``/killswitch`` and the
    rest were registered on a tree that was never constructed, let alone connected. The bot
    module was reachable only from its own unit tests.

    Two preconditions, both fail-closed, mirroring ``build_operator_deliver``:

      * THE BOT TOKEN. Keyring only (Invariant 4) — never env, never code.
      * A NON-EMPTY OPERATOR ALLOWLIST. This one is a safety precondition, not a convenience:
        every command re-checks ``is_authorized``, so a gateway with no allowlisted operators
        can do nothing except answer "Not authorized" to strangers. Opening a command channel
        that nobody is authorized to use is pure attack surface with no operator benefit, so
        it is not opened at all.

    Returns a zero-arg callable that BLOCKS, or ``None``. Absent, the cockpit runs exactly as
    it did before: ingest, scheduler, loopback API, and REST delivery outward. An operator
    without a token loses the command channel, not the system.

    WHAT THIS DOES NOT WIRE: the per-proposal Approve/Reject buttons. ``build_bot`` accepts a
    ``controls`` argument it has never used, and wiring the approval surface means wiring the
    token-mint path — a change to the order path itself, which is deliberately not bundled
    into a quant work package. Approvals stay where they are today.
    """
    from bridge.bot import bot_available, build_bot, run_bot
    from bridge.operators import authorized_operator_ids
    from core.secrets import get_secret

    if not bot_available():
        _log_unprovisioned("Discord command bot", "pip install discord.py")
        return None

    operators = authorized_operator_ids(state.settings)
    token = get_secret(_DISCORD_TOKEN_NAME)
    if token is None or not operators:
        _log_unprovisioned(
            "Discord command bot",
            f"keyring set {_DISCORD_TOKEN_NAME} and set COCKPIT_OPERATOR_IDS "
            "(the allowlisted Discord user ids)")
        return None

    guild_id = state.settings.guild_id
    log.info("discord command bot wired for %d operator(s); guild=%s",
             len(operators), guild_id or "GLOBAL (sync may take up to 1h to propagate)")

    def _start() -> None:
        client, tree = build_bot(state, token_registry=None)
        run_bot(client, tree, token.reveal(), guild_id=guild_id)

    return _start


def _sqlite_db_path(db_url: str):
    """Return the local file behind a SQLite URL; the RW-11 reader needs that file."""
    from pathlib import Path

    from sqlalchemy.engine import make_url

    url = make_url(db_url)
    if url.drivername != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("in-process EDGAR requires a file-backed SQLite cockpit database")
    return Path(url.database)


def compose(*, db_url: str = "sqlite:///cockpit.db", parquet_root: str = "data/raw",
            clock=None, transport=None, deliver=None, ingest_fetchers=None,
            http_get=None, ws_connect=None, finra_token_provider=None, finra_http_get=None,
            finra_row_mapper=None, paper_client=None, edgar_fetcher_factory=None):
    """Build the full application graph: ``(state, app, services)``.

    RETURN SHAPE IS UNCHANGED (a 3-tuple) ON PURPOSE. The RW-01 ingest runner is reachable as
    ``app.state.ingest``, alongside the existing ``app.state.orch`` / ``app.state.services`` —
    so every existing caller and test that unpacks ``state, app, services`` keeps working, and
    the runner is discoverable exactly where the other composed collaborators already live.

    Every seam is injectable so the WHOLE GRAPH — not a hand-rebuilt copy of it — is drivable
    with no live credential and no network: ``clock`` (a ``FrozenClock`` for a deterministic
    day), ``transport`` (scripted agent calls instead of ``claude -p`` subprocesses),
    ``deliver`` (the operator surface), ``ingest_fetchers`` (pre-built feed closures, bypassing
    the keyring read entirely), ``http_get`` / ``ws_connect`` (the network),
    ``edgar_fetcher_factory`` (recorded SEC replay instead of live HTTP), and the three FINRA
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
    # The transport swap changes only HOW the configured provider is spawned. Codex/Claude may
    # both produce schema-validated, human-gated paper proposals; the mode ladder independently
    # keeps Codex out of live/auto and every provider out of direct broker/risk authority.
    transport = transport if transport is not None else build_decision_transport(settings)
    # A static operator config is the only production watchlist source.  The runtime model can
    # read this bounded set during triage but has no mutation seam that could widen order scope.
    configured_watchlist = frozenset(settings.watchlist_symbols)

    def watchlist_fn():
        return configured_watchlist

    paper = build_paper_execution(
        state, client=paper_client, watchlist_fn=watchlist_fn, parquet_root=parquet_root
    )
    market_data = paper.market_data if paper is not None else None
    services = build_services(state, transport=transport,
                              deliver=deliver, parquet_root=parquet_root,
                              quote_fn=paper.quote if paper is not None else None,
                              book_fn=market_data.book if market_data is not None else None,
                              metrics_fn=(market_data.metrics
                                          if market_data is not None else None),
                              verify_params=(market_data.verify_params
                                             if market_data is not None else None),
                              holdings_fn=(paper.holdings_symbols if paper is not None else None),
                              watchlist_fn=watchlist_fn)
    app = create_app(
        state,
        services=services,
        paper=paper,
        watchlist=configured_watchlist,
        quant_snapshots=(market_data.snapshot_source if market_data is not None else None),
    )
    app.state.market_data = market_data
    # RW-01: ingest -> scheduler. Built AFTER the state so the pollers persist through the same
    # audited session factory, and hung off app.state so /status and run() can reach it.
    edgar_db_path = None
    if (settings.sec_user_agent or "").strip() and configured_watchlist:
        try:
            edgar_db_path = _sqlite_db_path(db_url)
        except ValueError as exc:
            # EDGAR's hardened read adapter requires a real SQLite file, but one
            # unprovisionable feed must not prevent the rest of the cockpit from booting.
            _log_unprovisioned("sec:edgar", str(exc))
    fetchers = ingest_fetchers if ingest_fetchers is not None else build_ingest_fetchers(
        settings, http_get=http_get, ws_connect=ws_connect,
        finra_token_provider=finra_token_provider, finra_http_get=finra_http_get,
        finra_row_mapper=finra_row_mapper, clock=state.clock.now,
        session_factory=state.session_factory, audit_log=state.audit,
        watchlist=configured_watchlist, cockpit_db_path=edgar_db_path,
        parquet_root=parquet_root, edgar_clock=state.clock,
        edgar_fetcher_factory=edgar_fetcher_factory,
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
    from config.settings import get_settings
    from core.logging import configure_logging
    from orchestrator.app import serve
    from orchestrator.scheduler import build_scheduler

    configure_logging(get_settings().log_level)

    state, app, services = compose()
    ingest = app.state.ingest
    build_scheduler(
        state, services=services, ingest=ingest, market_data=app.state.market_data
    ).start()
    ingest.start_streams()

    # QT-09: the Discord command surface. A DAEMON thread, for the same reason the stream
    # readers are: ``client.run`` blocks forever and so does ``serve``, so whichever ran
    # second would never start — and a daemon dies with the process, so Ctrl-C tears the
    # gateway down instead of hanging on a socket. Absent a token or an operator allowlist
    # this is None and the cockpit runs exactly as it did before.
    start_bot = build_operator_bot(state)
    if start_bot is not None:
        threading.Thread(target=start_bot, name="discord-bot", daemon=True).start()

    log.info(
        "cockpit up: mode=%s api=http://%s:%s feeds=%s",
        state.settings.mode.value,
        state.settings.api_host,
        state.settings.api_port,
        ingest.feed_names or "NONE",
    )
    serve(app, state.settings)


if __name__ == "__main__":  # pragma: no cover
    run()
