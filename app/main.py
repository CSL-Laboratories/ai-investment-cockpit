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


def _log_unprovisioned(what: str, how: str) -> str:
    """Say plainly that something is OFF, and how to turn it on.

    Not a debug line: an unprovisioned feed produces zero events forever, which is exactly the
    shape of a quiet market, and an unprovisioned surface delivers nothing, which is exactly the
    shape of a day with nothing to say. The operator must be able to tell those apart from the
    log alone — and again from ``IngestRunner.status()``, and again from the runbook's check.

    RETURNS the reason, so the caller can carry the SAME sentence into
    ``IngestRunner.mark_unprovisioned`` instead of writing a second one. A startup WARNING has
    scrolled off the console by the time anyone asks /status why a feed is quiet; two
    independently-worded explanations of one absence is how they start disagreeing.
    """
    # ASCII only: this lands on a Windows console (cp1252), where a stray em-dash renders as
    # mojibake and makes the one line an operator most needs to read look like a corruption.
    log.warning("NOT PROVISIONED: %s is OFF and will stay silent. Silence from it is NOT a "
                "quiet day. To enable: %s", what, how)
    return f"{what} is OFF. To enable: {how}"


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


def build_congress_providers(settings, *, http_get=None):
    """RW-16 — build the congressional PTR disclosure providers (House Clerk + Senate EFD).

    Returns ``(providers, None)`` when wired and ``(None, reason)`` when not — the reason being
    the same sentence that went to the log, so ``IngestRunner.status()`` and the startup
    warning can never drift into two different explanations of one absence.

    TWO INDEPENDENT SWITCHES, BOTH FAIL CLOSED:

      * ``COCKPIT_CONGRESS_ENABLED`` — off by default. This feed does not merely watch: its
        disclosures feed a deterministic copy engine that proposes orders, so it is opt-in.
      * ``COCKPIT_CONGRESS_USER_AGENT`` — a declared contact string. Without one this returns
        ``None`` rather than fetching anonymously. That is not politeness theatre: a suppressed
        UA is empirically how a .gov host answers 403, and an anonymous scraper is how a solo
        deployment gets an IP block that then looks exactly like "Congress filed nothing".

    NEVER RAISES. A composition root that dies because one optional feed's constructor moved
    would take the order path, the risk engine and the operator surface down with it — the
    per-feed fail-closed posture the whole module is built on (see ``build_ingest_fetchers``).
    Any construction failure is logged as NOT PROVISIONED, carries its ``repr`` as the reason,
    and is reported by ``IngestRunner.status()`` under ``unprovisioned``.

    THE CONSTRUCTOR IS NOT PINNED BY THE RW-16 CONTRACT — only ``name`` and
    ``fetch(*, since, today)`` are — so the kwargs below are offered, not assumed: each
    provider receives exactly the ones its signature declares (``_supported_kwargs``). The ONE
    kwarg that is mandatory is ``user_agent``: a provider that cannot accept our declared
    contact string is refused rather than constructed, because "fetch house.gov anonymously"
    is not a degraded mode of this feed, it is a different and worse one.
    """
    if not getattr(settings, "congress_enabled", False):
        return None, _log_unprovisioned(
            "congress (congressional PTR disclosures)",
            "set COCKPIT_CONGRESS_ENABLED=true (and COCKPIT_CONGRESS_USER_AGENT, and a "
            "non-empty COCKPIT_CONGRESS_FOLLOW - an empty follow list copies nobody)")
    user_agent = str(getattr(settings, "congress_user_agent", "") or "").strip()
    if not user_agent:
        return None, _log_unprovisioned(
            "congress (congressional PTR disclosures)",
            "set COCKPIT_CONGRESS_USER_AGENT to a contact string ('Cockpit you@example.com'); "
            "this feed will not fetch a .gov host without one")

    offered = {
        "user_agent": user_agent,
        # A politeness floor WE choose (neither house.gov nor efdsearch.senate.gov publishes a
        # documented cap), passed as a rate rather than a TokenBucket: the RW-16 provider
        # contract is ``fetch(*, since, today)``, which has no monotonic seam for a bucket to
        # read, so the pacing has to live inside the provider that owns the requests.
        "rate_per_sec": float(getattr(settings, "congress_rate_per_sec", 0.5)),
        "http_get": http_get,
    }
    try:
        from ingest.congress.house_clerk import HouseClerkProvider

        specs = [("house", HouseClerkProvider)]
        if getattr(settings, "congress_senate_enabled", False):
            from ingest.congress.senate_efd import SenateEfdProvider

            specs.append(("senate", SenateEfdProvider))

        providers = []
        for name, cls in specs:
            kwargs = _supported_kwargs(cls, offered)
            if "user_agent" not in kwargs:
                return None, _log_unprovisioned(
                    f"congress ({name} provider)",
                    f"{cls.__name__} does not accept user_agent=; refusing to fetch a .gov "
                    "host without a declared contact string (wiring bug, not a config gap)")
            providers.append(cls(**kwargs))
    except Exception as exc:            # noqa: BLE001 - one optional feed never kills compose
        return None, _log_unprovisioned(
            "congress (congressional PTR disclosures)",
            f"fix the provider wiring: {exc!r}")

    if not getattr(settings, "congress_follow", ""):
        # Explicitly NOT a refusal to build. The follow list gates the COPY ENGINE, not the
        # feed: with it empty the disclosures are still fetched, stored and visible (Engine 1,
        # awareness), and ``CopyPolicy.follows`` copies nobody. Saying so once at startup stops
        # "why did it propose nothing" from being a mystery later.
        log.warning("congress: COCKPIT_CONGRESS_FOLLOW is empty, so disclosures will be "
                    "INGESTED but copied for NOBODY (an empty follow list is an empty "
                    "allowlist, never 'everyone').")
    log.info("congress providers wired: %s (ua=%r, %.2f req/s)",
             [getattr(p, "name", type(p).__name__) for p in providers],
             user_agent, offered["rate_per_sec"])
    return tuple(providers), None


def _supported_kwargs(target, offered: dict) -> dict:
    """The subset of ``offered`` that ``target``'s signature actually accepts.

    The composition root is the one place that has to survive an interface it does not own:
    RW-16 pins the providers' ``fetch``, not their ``__init__``. Passing an unexpected kwarg
    would raise ``TypeError`` inside ``compose()``; passing none would build a provider with no
    contact string. So we offer what we have and let the callee's declared signature decide —
    then the CALLER checks that the load-bearing ones survived (see ``user_agent`` above).
    A ``**kwargs`` provider gets everything.
    """
    import inspect

    try:
        params = inspect.signature(target).parameters
    except (TypeError, ValueError):     # builtins / C-implemented callables have no signature
        return dict(offered)
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return dict(offered)
    return {k: v for k, v in offered.items() if k in params}


class CongressPollAdapter:
    """RW-16 — adapts ``ingest.congress.poller.CongressPoller`` to the poll-feed contract.

    TWO SHAPES, ONE SEAM, AND THIS IS WHERE THEY MEET. Every WP3 poller is
    ``poll(now, on_event) -> int``: the runner owns the sink and hands it in per tick.
    ``CongressPoller`` is ``poll() -> PollResult`` with ``on_event`` and ``store`` bound at
    CONSTRUCTION. Both are defensible; they are not the same, and neither may be renegotiated
    here. The composition root is the correct home for the difference — teaching
    ``orchestrator/ingest_runner.py`` about one feed's constructor would put a feed-specific
    branch inside the generic driver, and changing either signature would break a pinned
    contract for the agent who owns it.

    NOTHING IS SUMMED AWAY. ``PollResult`` carries the counted outcomes this feed exists to
    make visible (documents seen, parse errors, skips) and ``IngestRunner._signals`` reads
    ``counters`` / ``progress_signal`` / ``strand_signal`` / ``cursor`` off the poller object it
    was handed. So this object FORWARDS all four rather than reducing a poll to its emitted
    count: a count that reaches nobody is the same as no count at all, and a congressional feed
    that parsed 40 documents into 0 usable disclosures must never read as "nothing was filed".

    THE POLLER IS BUILT LAZILY, ON THE FIRST TICK. Its ``on_event`` is the runner's
    ``PersistSink``, which does not exist until ``add_poller`` has run. It is rebuilt if a
    DIFFERENT sink ever arrives (a test driving one adapter through two runners), because a
    poller silently persisting through the previous run's sink is the kind of bug that only
    shows up as events landing in the wrong place.
    """

    # Fields a PollResult might carry the emitted count in. Checked in order; the contract
    # pins the type's existence and that it counts, not its field names.
    _EMITTED_FIELDS = ("emitted", "events_emitted", "emitted_events", "events", "persisted")

    def __init__(self, providers, *, store, clock, dedup=None, poller_factory=None):
        self._providers = tuple(providers)
        self._store = store
        self._clock = clock
        self._dedup = dedup
        self._factory = poller_factory
        self._poller = None
        self._bound_sink = None
        self._result_counts: dict = {}
        self._shape_signal: str | None = None
        # The adapter's OWN non-emitting outcomes. Named and counted for the same reason every
        # poller in ingest/ names its drops: an uncounted skip is a silent one.
        self._counters = {"adapter_polls": 0, "adapter_unreadable_result": 0,
                          "adapter_rebound_sink": 0}

    # ---- the poll-feed contract ---------------------------------------------------------
    def poll(self, now, on_event) -> int:
        """One tick. ``now`` is the runner's MONOTONIC reading and is deliberately unused: the
        congressional providers are date-driven (``fetch(*, since, today)``) and take their
        calendar from the injected wall clock, never from a number that reads as 1970 when
        parsed as a date (``ingest/clock_contract.py``)."""
        del now
        poller = self._poller_for(on_event)
        self._counters["adapter_polls"] += 1
        result = poller.poll()
        self._result_counts = _poll_result_counts(result)
        return self._emitted_from(result)

    def _poller_for(self, on_event):
        if self._poller is not None and self._bound_sink is on_event:
            return self._poller
        if self._poller is not None:
            self._counters["adapter_rebound_sink"] += 1
        factory = self._factory
        if factory is None:
            from ingest.congress.poller import CongressPoller

            factory = CongressPoller
        kwargs = _supported_kwargs(factory, {
            "on_event": on_event, "store": self._store,
            "clock": self._clock, "dedup": self._dedup,
        })
        # THE LOAD-BEARING KWARGS MUST HAVE SURVIVED THE FILTER. ``_supported_kwargs`` drops
        # anything the callee's signature does not declare, and its own docstring makes the
        # caller responsible for checking that the ones that matter came through (the provider
        # path does exactly this for ``user_agent``). This path had no such check, and the two
        # seams it silently drops are the two that carry the evidence:
        #
        #   * ``on_event`` — the emit sink. Dropped, every disclosure is parsed and then
        #     reaches nobody.
        #   * ``store``    — the persistence seam. Dropped, the poller builds with store=None
        #     and NOTHING IS EVER WRITTEN.
        #
        # Either one would render a wiring bug as a feed that polls cleanly, counts documents
        # and produces a tidy zero — "the congressional feed was quiet today" — which is
        # precisely the failure mode this package's counters exist to make impossible. So a
        # missing seam is a REFUSAL, not a degraded mode. Raising here is the correct shape:
        # ``IngestRunner.poll`` catches it, counts it, and reports the feed as ``status="error"``
        # with the repr, exactly as the note below describes.
        missing = [k for k in ("on_event", "store") if k not in kwargs]
        if missing:
            raise TypeError(
                f"{getattr(factory, '__name__', factory)!r} does not accept {missing}; refusing "
                "to build a congressional poller that would parse filings and then silently "
                "drop them (wiring bug, not a config gap)"
            )
        # A construction failure PROPAGATES: ``IngestRunner.poll`` catches it, counts it, and
        # reports the feed as ``status="error"`` with the repr. Swallowing it into a 0 here
        # would render a wiring bug as a quiet day, which is the one outcome this file's
        # docstring forbids.
        self._poller = factory(self._providers, **kwargs)
        self._bound_sink = on_event
        return self._poller

    def _emitted_from(self, result) -> int:
        """The emitted count, or 0 with the shortfall NAMED rather than assumed."""
        for field_name in self._EMITTED_FIELDS:
            value = getattr(result, field_name, None)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return int(value)
            if isinstance(value, (list, tuple, set, frozenset)):
                return len(value)
        self._counters["adapter_unreadable_result"] += 1
        self._shape_signal = (
            f"congress PollResult carried no readable emitted count "
            f"(looked for {list(self._EMITTED_FIELDS)}, saw {sorted(self._result_counts)})"
        )
        return 0

    # ---- the signals IngestRunner._signals reads ----------------------------------------
    @property
    def counters(self) -> dict:
        merged = dict(self._counters)
        inner = getattr(self._poller, "counters", None)
        if isinstance(inner, dict):
            merged.update(inner)
        merged.update(self._result_counts)
        return merged

    @property
    def progress_signal(self):
        inner = getattr(self._poller, "progress_signal", None)
        both = [s for s in (inner, self._shape_signal) if s]
        return "; ".join(both) if both else None

    @property
    def strand_signal(self):
        return getattr(self._poller, "strand_signal", None)

    @property
    def cursor(self):
        return getattr(self._poller, "cursor", None)


def _poll_result_counts(result) -> dict:
    """Flatten a ``PollResult`` into named integer counters for ``status()``.

    Shape-tolerant on purpose (the RW-16 contract pins that the result COUNTS, not what its
    fields are called): ints pass through, collections become their length, and anything else
    is dropped rather than stringified into a status payload. ``IngestRunner._signals`` filters
    the zeroes, so a clean poll stays quiet and a poll that dropped 12 documents does not.
    """
    fields = getattr(result, "__dict__", None) or {}
    counts: dict = {}
    for name, value in fields.items():
        if name.startswith("_"):
            continue
        if isinstance(value, bool):
            counts[name] = int(value)
        elif isinstance(value, int):
            counts[name] = value
        elif isinstance(value, (list, tuple, set, frozenset, dict)):
            counts[f"{name}_count"] = len(value)
    return counts


class IngestFetchers(dict):
    """The per-feed closure map, plus the REASON each absent feed is absent.

    A plain ``{feed: closure | None}`` says a feed is off but never why, so the reason lived
    only in a startup WARNING that has scrolled away by the time anyone reads /status.
    Subclassing ``dict`` keeps every existing caller, test and injected fixture working
    unchanged (a plain dict is still accepted everywhere — ``build_ingest_runner`` reads this
    attribute with ``getattr``).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unprovisioned: dict[str, str] = {}

    def absent(self, feed: str, reason: str) -> None:
        """Record WHY ``feed`` has no closure. The value stays ``None``, so no caller can
        mistake a recorded reason for a working feed."""
        self[feed] = None
        self.unprovisioned[feed] = reason


def build_ingest_fetchers(settings, *, http_get=None, ws_connect=None,
                          finra_token_provider=None, finra_http_get=None,
                          finra_row_mapper=None, clock=None):
    """W20 + RW-01 — build the production ingest fetch CLOSURES. All I/O lives in these
    factories, never in the pure ingest modules (which take an injected ``fetch``).

    Returns one entry per feed, keyed by the ``orchestrator.ingest_runner`` feed name::

        {"alpaca:benzinga": connect   | None,   # a connect() factory (the WS stream)
         "finnhub":         fetch     | None,
         "sec:edgar":       fetch     | None,
         "calendar":        fetch     | None,
         "finra":           fetch     | None,
         "congress":        providers | None}   # RW-16: a tuple of DisclosureProviders

    A None value means NOT PROVISIONED, and a caller MUST NOT read it as "no events" —
    ``build_ingest_runner`` registers no poller for it and records the REASON on the runner
    (``IngestFetchers.unprovisioned`` -> ``IngestRunner.mark_unprovisioned``), so the feed is
    visibly, explicably absent rather than silently quiet. For EDGAR specifically, consult
    ``fetch.brew_status(day)``
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

    fetchers = IngestFetchers({"alpaca:benzinga": None, "finnhub": None, "sec:edgar": None,
                               "calendar": None, "finra": None, "congress": None})

    alpaca_key = get_secret(_ALPACA_KEY_NAME)
    alpaca_secret = get_secret(_ALPACA_SECRET_NAME)
    if alpaca_key is not None and alpaca_secret is not None:
        fetchers["alpaca:benzinga"] = build_alpaca_news_connect(
            alpaca_key.reveal(), alpaca_secret.reveal(), ws_connect=ws_connect)
    else:
        fetchers.absent("alpaca:benzinga", _log_unprovisioned(
            "alpaca:benzinga", f"keyring set {_ALPACA_KEY_NAME} / {_ALPACA_SECRET_NAME}"))

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
            fetchers.absent("calendar", _log_unprovisioned(
                "calendar", "set COCKPIT_CALENDAR_LOOKAHEAD_DAYS > 0 "
                            "(deliberately disabled at 0)"))
    else:
        reason = _log_unprovisioned("finnhub + calendar", f"keyring set {_FINNHUB_KEY_NAME}")
        fetchers.absent("finnhub", reason)
        fetchers.absent("calendar", reason)

    if settings.brew_db_path:
        fetchers["sec:edgar"] = make_brew_fetch(settings.brew_db_path,
                                                runs_dir=settings.brew_runs_dir)
    else:
        fetchers.absent("sec:edgar", _log_unprovisioned(
            "sec:edgar", "set COCKPIT_BREW_DB_PATH (+ COCKPIT_BREW_RUNS_DIR)"))

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
        fetchers.absent("finra", _log_unprovisioned(
            "finra", "set COCKPIT_FINRA_ENDPOINT + COCKPIT_FINRA_USER_AGENT and "
                     "pass finra_token_provider/finra_http_get to compose()"))

    # RW-16: the congressional disclosure providers. Off unless BOTH switches are set, and
    # never fatal — see build_congress_providers.
    congress, congress_reason = build_congress_providers(settings, http_get=http_get)
    if congress:
        fetchers["congress"] = congress
    else:
        fetchers.absent("congress", congress_reason or "congress providers were not built")
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

    RW-16 adds the congressional feed, behind ``CongressPollAdapter`` (its poller has the other
    signature — see that class), and closes the loop on absence: every feed WITHOUT a closure is
    recorded on the runner with the reason it is off, so ``status()`` answers "unprovisioned or
    quiet?" without anyone having to find the startup log.
    """
    from ingest.alpaca_news import AlpacaNewsConsumer
    from ingest.calendars import CalendarPoller
    from ingest.edgar_brew import EdgarBrewPoller
    from ingest.finnhub_poll import FinnhubPoller
    from ingest.finra_short_interest import FinraShortInterestPoller
    from orchestrator.ingest_runner import (
        FEED_ALPACA_NEWS,
        FEED_CALENDAR,
        FEED_CONGRESS,
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
    if fetchers.get(FEED_CONGRESS):
        # ``store`` is the audited session factory the rest of the graph writes through, so a
        # persisted disclosure lands in the SAME database as the events, orders and audit chain
        # that will later cite it (Inv 8: raw inputs stored separately from summaries, but in
        # one replayable store). ``clock`` is the same tz-aware wall clock the EDGAR and FINRA
        # pollers take — one contract, now three seams.
        runner.add_poller(FEED_CONGRESS, CongressPollAdapter(
            fetchers[FEED_CONGRESS], store=state.session_factory, clock=wall_clock))

    # Every feed we KNOW about and did not wire, with the reason (RW-16). Absence is now
    # reported, not merely observable: an operator reading /status sees "congress: OFF because
    # COCKPIT_CONGRESS_USER_AGENT is unset" instead of a feed list that simply lacks a name
    # they were looking for. Falls back to a generic reason for an injected plain-dict
    # ``fetchers`` (every test does that), which is still better than nothing at all.
    recorded = getattr(fetchers, "unprovisioned", {}) or {}
    for feed, value in sorted(fetchers.items()):
        if value is None and feed not in runner.polls and feed not in runner.streams:
            runner.mark_unprovisioned(feed, recorded.get(
                feed, "no fetch closure was built for this feed (see the startup log)"))

    log.info("ingest wired: poll feeds=%s stream feeds=%s unprovisioned=%s",
             sorted(runner.polls) or "NONE", sorted(runner.streams) or "NONE",
             sorted(runner.unprovisioned) or "NONE")
    return runner


# =========================================================================================
# RW-17 — the PAPER auto-execution lane (the copy CYCLE is now scheduled; read this first)
# =========================================================================================
# WHAT CHANGED, AND WHAT DID NOT.
#
# RW-16 left the copy CYCLE unscheduled and said so here: "a cron job would quietly manufacture
# proposals off a 30-45 day-old signal into an approval queue nobody asked to fill." The
# operator has REVERSED that, in their own words: "i do not want to do any approvals when this
# tool is running, i want it to make trades for me. all i ask is for notifications." So the
# queue is now asked for, and RW-17 adjudicates it without them — on PAPER, inside a cancel
# window, against budget rails, with every outcome notified.
#
# WHAT DID NOT CHANGE IS EVERYTHING THAT PROTECTS REAL CAPITAL:
#
#   * ``producing_runtime`` is still ``"deterministic:copytrade"`` and
#     ``assert_execution_runtime`` still REFUSES it in review_live/auto_veto/auto_full, so the
#     cycle scheduled below emits NOTHING in any live rung. SEC-08 is untouched and RW-16-LIVE
#     stays open.
#   * The auto rungs are still LIVE modes with their own gates (SEC-11 anchor, four weeks of
#     history, 30 adjudicated proposals). Nothing here arms one, and nothing here needs one:
#     this lane runs at ``mode == paper`` and re-checks that at every resolve.
#   * The gateway this lane submits through is the PAPER-scoped one built in
#     ``build_paper_execution``. ``_ORDER_AUTHORITY_BY_SCOPE`` maps ``paper`` to the paper
#     transport and the auto rungs to the LIVE one; reusing the existing gateway rather than
#     constructing a second one is what keeps Invariant 1's single order path single.
#   * The approval step is REPLACED, not removed. The executor writes a real, persisted,
#     HMAC-signed adjudication the gateway re-loads and re-verifies — the same shape
#     ``bridge/veto.py`` uses for ``auto_veto``. There is no bypass and no second path.
#
# ALL-OR-NOTHING, AND OFF BY DEFAULT. ``build_paper_autoexec`` fails closed to ABSENT unless
# every part of the lane exists: the opt-in flag, a paper gateway, a notification seam, and a
# valid copy policy. A half-built lane is the one shape forbidden — a lane that trades but
# cannot notify is precisely what the operator did not ask for. When it is absent, NO copy-cycle
# job and NO resolve job are registered, so a congress-awareness-only deployment keeps exactly
# the RW-16 posture it had: the cycle stays operator-triggered and creates nothing on a timer.


class AutoExecWiring:
    """The composed RW-17 lane, or the REASON it is absent — the same device ``IngestFetchers``
    uses for feeds, for the same reason.

    An absent auto-execution lane produces zero trades forever, which is shaped exactly like a
    month in which no followed member filed anything. The operator's only interface is now
    notifications, so "nothing happened" has to be answerable as either "nothing was disclosed"
    or "the lane is OFF because X" — never left to be inferred from silence. ``status()``
    mirrors ``IngestRunner.status()``'s ``unprovisioned`` shape so a reader (and
    ``GET /status``, via ``app.state.autoexec``) sees the same vocabulary it already knows.
    """

    def __init__(self, executor=None, *, reason: str | None = None, window_seconds: int = 0,
                 budget: dict | None = None, notify=None) -> None:
        self.executor = executor
        self.reason = reason
        self.window_seconds = window_seconds
        self.budget = dict(budget or {})
        # Kept so the DAILY DIGEST job can reach the typed notifier. The executor's own seam is
        # deliberately the narrow ``notify(event, payload) -> bool``; the digest carries
        # ``DigestTrade``/``DigestRefusal`` rows, which that signature cannot express.
        self.notify = notify

    @property
    def enabled(self) -> bool:
        return self.executor is not None

    def status(self) -> dict:
        if self.executor is None:
            return {"kind": "not_provisioned", "reason": self.reason or "not composed"}
        return {
            "kind": "armed",
            # Stated on every read, because it is the claim the whole package rests on. The
            # executor re-derives it at resolve time; this is the composition-time answer.
            "execution_scope": "paper",
            "cancel_window_seconds": self.window_seconds,
            "budget": dict(self.budget),
        }


# Factory names ``copytrade.notify`` may expose for the delivery seam. The composition root is
# the one place that has to survive an interface it does not own (the same posture as
# ``_supported_kwargs`` above): the module is owned elsewhere, its factory name is not pinned by
# the RW-17 contract, and guessing wrong must be a NAMED refusal rather than a silent lane with
# no notifications.
_AUTOEXEC_NOTIFY_FACTORIES = ("build_notifier", "build_notify", "build_copy_notifier",
                              "make_notifier", "build_autoexec_notify")


def _notify_channel_ids(settings):
    """The configured logical-channel map, or ``None`` when there is nothing to check against.

    ``None`` means "this deployment's ``deliver`` was not built from these settings" — the
    injected-seam shape every test uses, and the shape a future non-Discord adapter will use. It
    is not a hole in production: a settings object with no channel ids at all makes
    ``build_operator_deliver`` return None, and a lane with no ``deliver`` is already refused."""
    try:
        from bridge.deliver import channel_ids_from_settings
    except Exception:                       # noqa: BLE001 - never fatal to compose()
        return None
    return channel_ids_from_settings(settings) or None


def build_autoexec_notify(settings, deliver, *, clock=None, st=None):
    """RW-17 — the notification seam the auto-execution lane reports through.

    Returns ``(notify, None)`` or ``(None, reason)``. Routed through ``copytrade.notify``, which
    renders deterministically from stored facts and posts via the existing ``bridge/deliver.py``
    seam — never a hardwired Discord type, because the operator surface is abstracted (CLAUDE.md)
    and Discord is one adapter among several.

    ``st`` is offered so ``copytrade.notify`` can read the stored ``RiskDecision`` row back by
    id when it renders the queued message's risk block. SEC-17 requires that block to come from
    the record and from nowhere else, and a composition root that withheld the only way to read
    the record would leave the renderer no honest source for it.

    NO DELIVER MEANS NO LANE. This returns a reason rather than a working notifier when
    ``deliver`` is None, and the caller treats that as fatal to the whole lane. That is stricter
    than the morning brief (which is computed, stored and audited even when it reaches nobody)
    and it must be: a brief that reaches nobody loses the push, while a TRADE that reaches
    nobody is an unannounced position in the operator's account. The executor independently
    refuses to resolve a ticket whose notification was never confirmed delivered, so an
    undeliverable lane would in any case be a queue that never fires and never explains itself.
    """
    if deliver is None:
        return None, _log_unprovisioned(
            "congress paper auto-execution (no notification seam)",
            f"keyring set {_DISCORD_TOKEN_NAME} and set COCKPIT_BRIEF_CHANNEL_ID; an "
            "auto-executing lane that cannot tell you what it did will not be composed")
    try:
        from copytrade import notify as _notify
    except Exception as exc:                # noqa: BLE001 - never fatal to compose()
        return None, _log_unprovisioned(
            "congress paper auto-execution (notification module)",
            f"copytrade.notify is not importable: {exc!r}")

    # EVERY CHANNEL THIS LANE USES MUST BE MAPPED — "a deliver exists" is not the same question.
    #
    # ``build_deliver`` needs only ONE channel id to be a working callable, and it fails closed
    # PER CHANNEL: an unmapped logical channel returns False. ``CHANNEL_BY_EVENT`` routes
    # ``queued`` to #proposals and ``submitted``/``refused``/``cancelled`` to #alerts, so a
    # proposals-only configuration produced a lane that armed cleanly, confirmed its queued
    # notice, executed the trade — and then could never deliver a submitted, refused or
    # cancelled message again. Every named refusal, which is this lane's only health signal,
    # would reach a log line and nothing else. That is exactly the half-built lane
    # ``build_paper_autoexec`` refuses to compose for the gateway and the gate-(d) seam, so it is
    # refused here too.
    #
    # An EMPTY map is not checked here and does not need to be: with no channel configured at
    # all, ``build_operator_deliver`` returns None and the branch above has already refused. A
    # non-empty map that is missing a channel is the dangerous case, because it looks configured.
    configured = _notify_channel_ids(settings)
    if configured is not None:
        missing = sorted(set(_notify.CHANNEL_BY_EVENT.values()) - set(configured))
        if missing:
            return None, _log_unprovisioned(
                "congress paper auto-execution (unmapped notification channels)",
                f"the lane posts to {sorted(set(_notify.CHANNEL_BY_EVENT.values()))} but "
                f"{missing} have no channel id configured; set the matching "
                "COCKPIT_*_CHANNEL_ID. A lane that can announce a trade and then never report "
                "what it did is not composed")

    offered = {"deliver": deliver, "settings": settings, "clock": clock, "st": st}
    for name in _AUTOEXEC_NOTIFY_FACTORIES:
        factory = getattr(_notify, name, None)
        if factory is None:
            continue
        try:
            return factory(**_supported_kwargs(factory, offered)), None
        except Exception as exc:            # noqa: BLE001
            return None, _log_unprovisioned(
                "congress paper auto-execution (notification seam)",
                f"copytrade.notify.{name} refused to build: {exc!r}")
    return None, _log_unprovisioned(
        "congress paper auto-execution (notification seam)",
        f"copytrade.notify exposes none of {list(_AUTOEXEC_NOTIFY_FACTORIES)}; refusing to "
        "compose a lane that would trade without telling you (wiring bug, not a config gap)")


def _paper_scoped_gateway(paper):
    """The PAPER-scoped ``OrderGateway`` the ``PaperExecutionService`` was composed with.

    Returns ``(gateway, None)`` or ``(None, reason)``. Two properties are checked, and the
    second one is the single most important line in this module:

      1. it is the SAME gateway object the human gate already submits through, not a second one
         built here. Invariant 1 is "one order path"; two gateway instances over one broker is
         two paths that merely happen to agree today.
      2. its ``execution_scope`` is literally ``"paper"``. ``_ORDER_AUTHORITY_BY_SCOPE`` maps
         ``paper`` to ``{paper}`` and ``live`` to the three live/auto rungs, so a gateway with
         any other scope would put this lane's zero-touch adjudication in front of a LIVE
         transport. If that ever becomes possible, the lane must REFUSE TO EXIST rather than
         discover it at submit time. Unknown scope is refused too — an attribute we cannot read
         is not an attribute we may assume.
    """
    if paper is None:
        return None, _log_unprovisioned(
            "congress paper auto-execution (no paper transport)",
            f"keyring set {_ALPACA_KEY_NAME} / {_ALPACA_SECRET_NAME}")
    gateway = getattr(paper, "gateway", None)
    if gateway is None:
        gateway = getattr(paper, "_gateway", None)
    if gateway is None:
        return None, _log_unprovisioned(
            "congress paper auto-execution (no order gateway)",
            "PaperExecutionService exposed no gateway; refusing to build a second one "
            "(wiring bug, not a config gap)")
    scope = getattr(gateway, "execution_scope", None)
    if scope is None:
        scope = getattr(gateway, "_execution_scope", None)
    if scope != "paper":
        return None, _log_unprovisioned(
            "congress paper auto-execution (non-paper gateway)",
            f"the composed OrderGateway's execution_scope is {scope!r}, not 'paper'; this lane "
            "adjudicates without a human and may exist ONLY in front of the paper transport")
    return gateway, None


def build_paper_autoexec(state, *, paper=None, deliver=None, clock=None):
    """RW-17 — compose the zero-touch PAPER auto-execution lane, or say why it is absent.

    Returns an :class:`AutoExecWiring`, never raises, and never returns a partially-built lane.
    The preconditions, each fail-closed and each named in the log and in ``status()``:

      * ``COCKPIT_CONGRESS_AUTOEXEC`` — opt-in, default false. Turning it on is the operator's
        explicit act; ``Settings`` has already REFUSED to boot if it is on while the congress
        feed is off or the follow list is empty, so reaching here means the lane can actually
        see disclosures.
      * a PAPER-scoped ``OrderGateway`` (see ``_paper_scoped_gateway``);
      * a notification seam (see ``build_autoexec_notify``);
      * a valid ``CopyPolicy`` — a ``PolicyError`` here is a config error that would otherwise
        surface hours later as a lane that queues nothing.

    THE MODE IS NOT CHECKED HERE, DELIBERATELY. Composition happens at boot, where the ladder is
    normally ``read_only``; refusing on the boot-time mode would make the lane unbuildable on
    every real deployment. The check that matters is at RESOLVE time, where the executor
    re-reads ``ModeLadder.current()`` under the lock and voids any ticket that is not
    ``Mode.paper`` — a mode change mid-window cancels the trade rather than following it.
    """
    settings = state.settings
    if not getattr(settings, "congress_autoexec", False):
        return AutoExecWiring(reason=_log_unprovisioned(
            "congress PAPER auto-execution (zero-touch copy trading)",
            "set COCKPIT_CONGRESS_AUTOEXEC=true (opt-in; it replaces your approval tap with an "
            "audited, budget-railed, paper-only auto-adjudication)"))

    gateway, reason = _paper_scoped_gateway(paper)
    if gateway is None:
        return AutoExecWiring(reason=reason)
    notify, reason = build_autoexec_notify(settings, deliver, clock=clock, st=state)
    if notify is None:
        return AutoExecWiring(reason=reason)

    # GATE (d) IS NOT OPTIONAL, SO ITS SEAM IS NOT OPTIONAL EITHER. ``OrderGateway`` re-runs
    # risk against a fresh quote and a fresh positions snapshot immediately before the broker
    # call, and ``PaperAutoExecutor`` cannot compute that verdict itself — it has no broker and
    # no quote seam. Left unwired, the executor's default factory REJECTS every re-check
    # ('autoexec_risk_recheck_not_wired'): a lane that arms, notifies, counts down and then
    # refuses every single ticket, which an operator who has stopped watching a queue would
    # read as "nothing was disclosed today". That is the exact silence this package exists to
    # make impossible, so it is a REFUSAL TO COMPOSE rather than a degraded mode.
    risk_recheck_factory = getattr(paper, "autoexec_risk_recheck", None)
    positions_fn = getattr(paper, "positions_snapshot", None)
    if not callable(risk_recheck_factory) or not callable(positions_fn):
        return AutoExecWiring(reason=_log_unprovisioned(
            "congress PAPER auto-execution (no risk re-check seam)",
            "PaperExecutionService exposes no autoexec_risk_recheck/positions_snapshot; "
            "refusing to arm a lane that would refuse every ticket at gateway gate (d) "
            "(wiring bug, not a config gap)"))

    budget = {
        "max_daily_notional": str(settings.congress_autoexec_max_daily_notional),
        "max_trades_per_day": int(settings.congress_autoexec_max_trades_per_day),
        "max_open_positions": int(settings.congress_autoexec_max_open_positions),
        "max_notional_per_symbol_per_day":
            str(settings.congress_autoexec_max_notional_per_symbol_per_day),
    }
    window = int(settings.congress_autoexec_window_seconds)
    try:
        from copytrade.autoexec import PaperAutoExecutor
        from copytrade.policy import policy_from_settings

        offered = {
            "window_seconds": window,
            "notify": notify,
            "clock": clock if clock is not None else state.clock,
            "policy": policy_from_settings(settings),
            "risk_recheck_factory": risk_recheck_factory,
            "positions_fn": positions_fn,
        }
        kwargs = _supported_kwargs(PaperAutoExecutor, offered)
        # THE LOAD-BEARING KWARGS MUST HAVE SURVIVED THE FILTER — the same check
        # ``build_congress_providers`` makes for ``user_agent`` and ``CongressPollAdapter``
        # makes for ``on_event``/``store``, and for the same reason: ``_supported_kwargs``
        # silently drops anything the callee does not declare, and each of these three drops
        # into a DIFFERENT silent failure. No ``notify``: the lane trades without telling
        # anyone (or, because delivery gates execution, never trades and never says why). No
        # ``risk_recheck_factory``: every ticket is refused at gate (d). A silent drop here is
        # a wiring bug wearing the costume of a quiet week.
        missing = [k for k in ("notify", "risk_recheck_factory", "positions_fn")
                   if k not in kwargs]
        if missing:
            raise TypeError(
                f"PaperAutoExecutor does not accept {missing}; refusing to arm a zero-touch "
                "lane whose notification or gate-(d) seam would be silently absent"
            )
        executor = PaperAutoExecutor(state, gateway, **kwargs)
    except Exception as exc:                # noqa: BLE001 - one optional lane never kills compose
        return AutoExecWiring(reason=_log_unprovisioned(
            "congress PAPER auto-execution", f"fix the lane wiring: {exc!r}"))

    # RESTART RECOVERY, BEFORE THE LANE IS HANDED ANY NEW WORK. A ticket the PREVIOUS process
    # announced ("this WILL execute unless you cancel it") and never resolved is a promise this
    # deployment is now responsible for. ``recover_orphans`` reads the signed ticket ledger,
    # settles each one as a named, notified ``abandoned_restart`` refusal, and writes the
    # settlement back to the chain so it is idempotent. Without it, every deploy inside a cancel
    # window silently voided the trades queued in it — no execution, no refusal, no message, and
    # a ``/cancel`` that answers "unknown ticket".
    try:
        orphans = executor.recover_orphans()
    except Exception as exc:                # noqa: BLE001 - recovery never blocks the boot
        orphans = []
        log.exception("RW-17: restart recovery failed (%r); the lane is still armed", exc)
    log.warning(
        "RW-17 PAPER auto-execution is ARMED: congressional copy trades will execute on the "
        "PAPER transport %ds after you are notified, with no approval tap. Your only lever is "
        "/cancel inside that window. Rails/day: %s notional, %s trades, %s open positions, %s "
        "per symbol. Tickets abandoned by the last restart and settled now: %d.",
        window, budget["max_daily_notional"], budget["max_trades_per_day"],
        budget["max_open_positions"], budget["max_notional_per_symbol_per_day"], len(orphans),
    )
    return AutoExecWiring(executor, window_seconds=window, budget=budget, notify=notify)


def _queued_context(intent) -> dict:
    """The filing facts a queued notification is rendered from, as JSON-native values.

    Carried on ``AutoTicket.context``, which ``PaperAutoExecutor._notify_payload`` merges into
    every notification for that ticket. JSON-native (dates as ISO strings, the notional as a
    string) because the payload crosses an abstract operator surface that may serialise it, and
    ``copytrade.notify.queued_facts_from_payload`` parses them back.

    NOTHING IS INVENTED WHEN THE INTENT IS ABSENT. An empty context yields a message missing its
    load-bearing facts, ``missing_queued_facts`` refuses to render it, delivery is never
    confirmed and the ticket never fires. A trade the operator could not have judged does not
    happen — which is the right answer, and a strictly better one than a message with a
    plausible-looking blank where the disclosure lag should be.
    """
    if intent is None:
        return {}
    return {
        "filer_display": str(getattr(intent, "filer_display", "") or ""),
        "tx_date": _iso_or_empty(getattr(intent, "tx_date", None)),
        "filed_date": _iso_or_empty(getattr(intent, "filed_date", None)),
        "amount_label": str(getattr(intent, "amount_label", "") or ""),
        "sizing_reason": str(getattr(intent, "reason", "") or ""),
        "sizing_notes": [str(n) for n in (getattr(intent, "sizing_notes", ()) or ())],
        "source_key": str(getattr(intent, "source_key", "") or ""),
        "translated_from": getattr(intent, "translated_from", None),
    }


def _iso_or_empty(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else ""


def build_copy_cycle_service(state, *, quote_fn=None, autoexec=None):
    """RW-17 — the scheduler service for one congressional copy CYCLE.

    ``(st, clock) -> dict``, the same shape as every other injected scheduler service. It does
    three things and nothing else: load the disclosure window, run the deterministic cycle, and
    hand each resulting ``Order`` to the auto-executor to be QUEUED behind a cancel window.

    THE WINDOW IS THE POLICY'S, NOT A NUMBER CHOSEN HERE. ``copytrade.engine`` reads a symbol's
    contest (a followed member buying what another exited) off the WHOLE batch, ahead of every
    time-varying gate, so ``run_copy_cycle`` refuses a batch that cannot cover
    ``policy.max_age_days``. Loading exactly that window is what makes the refusal not fire —
    and when the row cap bites anyway the cycle refuses rather than planning against a batch it
    cannot trust. Neither is worked around here.

    QUEUING IS PER-ORDER AND ISOLATED. One order whose lineage cannot be bound (its
    ``RiskDecision`` row unreadable) must not cost the other orders in the same cycle their
    tickets, so each is scheduled inside its own guard and every failure is counted under a name.
    A failure means that order simply stays where ``run_copy_cycle`` left it — an un-adjudicated
    ``proposed`` row — which is the fail-closed direction: no ticket, no auto-execution.

    EVERY TICKET CARRIES ITS FILING. ``copytrade/notify.py`` REFUSES to render a queued message
    that cannot name the filer, the trade date, the disclosure lag, the disclosed range and why
    the size is what it is — and a refused render is an unconfirmed delivery, which is a ticket
    that never fires. Those facts are discrete fields on the ``CopyIntent`` the cycle planned
    from and are only woven into prose by the time they reach the persisted proposal, so the
    intent is carried across on the ticket's ``context`` rather than re-parsed out of a
    sentence later.
    """
    from schemas.models.congress_disclosure import load_disclosures
    from workflows.copytrade_orders import run_copy_cycle

    def copy_cycle(st, clock) -> dict:
        from datetime import timedelta

        from copytrade.policy import policy_from_settings
        from schemas.models.risk_decision import RiskDecision
        from storage.repositories import Repo

        policy = policy_from_settings(st.settings)
        window_start = clock.now().date() - timedelta(days=int(policy.max_age_days))
        disclosures = load_disclosures(st.session_factory, window_start=window_start)
        result = run_copy_cycle(st, policy=policy, disclosures=disclosures, clock=clock,
                                quote_fn=quote_fn)

        queued, failures = 0, {}
        if autoexec is not None:
            risk_decisions = Repo(st.session_factory, RiskDecision)
            for order in result.orders:
                try:
                    risk_decision = risk_decisions.get(order.risk_decision_id)
                    if risk_decision is None:
                        # The gateway binds the adjudication to the risk decision by id; an
                        # order whose decision we cannot read is one we cannot bind, so it is
                        # left for a human rather than queued against a decision we assumed.
                        failures["risk_decision_missing"] = \
                            failures.get("risk_decision_missing", 0) + 1
                        continue
                    autoexec.schedule(
                        order=order, risk_decision=risk_decision,
                        proposal_id=order.proposal_id,
                        context=_queued_context(result.order_intents.get(order.id)),
                    )
                    queued += 1
                except Exception as exc:    # noqa: BLE001 - one order never costs the others
                    log.exception("RW-17 could not queue copy order %s: %r",
                                  getattr(order, "id", "?"), exc)
                    failures[type(exc).__name__] = failures.get(type(exc).__name__, 0) + 1
        return {
            "proposed": result.proposed, "approved": result.approved,
            "needs_human": result.needs_human, "rejected": result.rejected,
            "skipped": len(result.skipped), "reasons": list(result.reasons),
            "queued_for_autoexec": queued, "queue_failures": failures,
        }

    return copy_cycle


def build_autoexec_resolve_service(executor):
    """RW-17 — the scheduler service for one ``resolve_due`` tick. ``(st, clock) -> list``.

    A one-line adapter on purpose: every decision about whether a due ticket may execute (mode,
    HALT, breaker, chain verification, confirmed delivery, budget rails) belongs to the executor,
    under its own lock, at the instant of firing. A wrapper that pre-filtered any of that here
    would be a second, staler copy of the gate — and the gap between the two copies is exactly
    where a ticket executes on a condition that stopped being true.
    """

    def resolve(st, clock):
        del st
        return executor.resolve_due(now=clock.now())

    return resolve


def build_autoexec_digest_service(wiring):
    """RW-17 — the scheduler service for the lane's END-OF-DAY STATEMENT. ``(st, clock) -> dict``.

    Returns ``None`` when the lane has no typed notifier to send through, so ``build_scheduler``
    registers no job rather than one that silently no-ops.

    THE ROWS COME FROM THE SIGNED CHAIN, NOT FROM ``executor.counts``. That histogram is an
    in-memory dict on one object: empty after every restart, and therefore guaranteed to
    under-report on exactly the days the digest is worth reading. ``copytrade/digest.py`` reads
    the HMAC-verified ticket ledger instead. The BUDGET section is likewise re-derived from the
    chain by ``budget_state``, which is the same number the rails themselves enforce — a digest
    quoting a different remaining-budget figure than the one that will refuse tomorrow's first
    trade would be worse than no digest.

    An UNREADABLE ledger is reported as unreadable (empty rails, which the renderer prints as
    "unavailable — the lane trades nothing"), never as a quiet day.
    """
    notifier = getattr(wiring, "notify", None)
    if notifier is None or not hasattr(notifier, "daily_digest"):
        return None

    def digest(st, clock) -> dict:
        from copytrade.budget import AutoExecBudget, budget_state
        from copytrade.digest import digest_rows

        day = clock.now().date()
        rows = digest_rows(st, day=day)
        budget = AutoExecBudget.from_settings(st.settings)
        remaining = budget_state(st, day=day).remaining(budget) if rows.readable else {}
        result = notifier.daily_digest(day=day, executed=rows.executed, refused=rows.refused,
                                       budget_remaining=remaining)
        delivered = bool(getattr(result, "confirmed", False))
        if not delivered:
            # The digest is the message that makes silence answerable, so a digest that reached
            # nobody is itself worth an error line in the file the operator greps.
            log.error("RW-17: the daily digest for %s was NOT delivered (%s)", day,
                      getattr(result, "status", "unknown"))
        return {"day": day.isoformat(), "readable": rows.readable, "error": rows.error,
                "executed": len(rows.executed), "refused": len(rows.refused),
                "delivered": delivered}

    return digest


def build_autoexec_fills_service(wiring):
    """RW-17 — the scheduler service that PRODUCES the ``filled`` event. ``(st, clock) -> dict``.

    The integration seam ``copytrade.notify.build_fill_service`` documents in its own docstring,
    and which no caller had: the renderer, the announcer, the signed "already announced" marker
    and the scheduler job (``AUTOEXEC_FILLS_CRON``) all existed, wired to nothing. Composed here
    beside the digest for the same reason: both are the lane's REPORTS, and the lane's reports
    are the operator's entire interface now that the approval queue is gone.

    Returns ``None`` when the lane has no usable notification seam, so ``build_scheduler``
    registers no job rather than one that silently drops every fill.
    """
    notify = getattr(wiring, "notify", None)
    if notify is None:
        return None
    from copytrade.notify import build_fill_service

    return build_fill_service(notify)


def build_order_status_service(paper):
    """RW-15 (minimal, paper lane) — the scheduler service for one order-status reconcile tick.

    ``(st, clock) -> dict``, or ``None`` when the composed transport exposes no status reader.

    WHY THE COMPOSITION ROOT HAS TO CARE. ``brokers/order_status_poll.py`` is the module that
    drains a resting order out of ``submitted``; without a call site the SEC-07 pending-exposure
    cap only ever goes UP, because a paper LIMIT order that does not cross acks ``accepted`` and
    no state-machine edge consumes it. RW-16 prices this lane's orders as LIMIT at the gate
    quote, so resting is the NORMAL case: unwired, the lane fills the cap and then refuses every
    subsequent order forever, with nobody in the approval queue to notice.

    NOT A SECOND ORDER PATH (Inv 1): the only broker call passed through here is a READ of an
    order's status, injected exactly the way ``orchestrator.reconcile`` injects
    ``broker_history_fn``. See that module's header for the fail-closed rules.
    """
    status_fn = getattr(paper, "order_status", None) if paper is not None else None
    if not callable(status_fn):
        return None

    def poll(st, clock) -> dict:
        from brokers.order_status_poll import run_order_status_poll

        return run_order_status_poll(st, clock, status_fn=status_fn)

    return poll


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


def build_paper_execution(state, *, client=None, watchlist_fn=None):
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
    log.info("Alpaca PAPER execution transport wired behind OrderGateway")
    return PaperExecutionService(
        state,
        broker=broker,
        gateway=gateway,
        token_registry=registry,
        quote_fn=client.latest_quote,
        watchlist_fn=watchlist_fn,
    )


def build_operator_bot(state, autoexec=None):
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

    RW-17 threads the auto-execution lane through so ``/cancel`` has something to cancel. That
    is a VETO surface, not an approval surface: the operator never has to act for a trade to
    proceed, which is the whole point of the lane. With no lane composed the command still
    registers and answers plainly that nothing is queued — a command that silently disappears
    is indistinguishable from a bot that is not responding, which is the worst possible thing
    for the one control that stops a trade.
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

    guild_id = getattr(state.settings, "guild_id", None)
    log.info("discord command bot wired for %d operator(s); guild=%s",
             len(operators), guild_id or "GLOBAL (sync may take up to 1h to propagate)")

    def _start() -> None:
        client, tree = build_bot(state, token_registry=None, autoexec=autoexec)
        run_bot(client, tree, token.reveal(), guild_id=guild_id)

    return _start


def compose(*, db_url: str = "sqlite:///cockpit.db", parquet_root: str = "data/raw",
            clock=None, transport=None, deliver=None, ingest_fetchers=None,
            http_get=None, ws_connect=None, finra_token_provider=None, finra_http_get=None,
            finra_row_mapper=None, paper_client=None):
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
    # The transport swap changes only HOW the configured provider is spawned. Codex/Claude may
    # both produce schema-validated, human-gated paper proposals; the mode ladder independently
    # keeps Codex out of live/auto and every provider out of direct broker/risk authority.
    transport = transport if transport is not None else build_decision_transport(settings)
    # A static operator config is the only production watchlist source.  The runtime model can
    # read this bounded set during triage but has no mutation seam that could widen order scope.
    configured_watchlist = frozenset(settings.watchlist_symbols)

    def watchlist_fn():
        return configured_watchlist

    paper = build_paper_execution(state, client=paper_client, watchlist_fn=watchlist_fn)
    services = build_services(state, transport=transport,
                              deliver=deliver, parquet_root=parquet_root,
                              quote_fn=paper.quote if paper is not None else None,
                              holdings_fn=(paper.holdings_symbols if paper is not None else None),
                              watchlist_fn=watchlist_fn)
    app = create_app(state, services=services, paper=paper, watchlist=configured_watchlist)
    # RW-01: ingest -> scheduler. Built AFTER the state so the pollers persist through the same
    # audited session factory, and hung off app.state so /status and run() can reach it.
    fetchers = ingest_fetchers if ingest_fetchers is not None else build_ingest_fetchers(
        settings, http_get=http_get, ws_connect=ws_connect,
        finra_token_provider=finra_token_provider, finra_http_get=finra_http_get,
        finra_row_mapper=finra_row_mapper, clock=state.clock.now,
    )
    app.state.ingest = build_ingest_runner(state, parquet_root=parquet_root, fetchers=fetchers,
                                           settings=settings, clock=clock)
    # RW-17: the PAPER auto-execution lane. Hung off app.state next to the ingest runner (the
    # place composed collaborators live and /status reads), and its two scheduler services are
    # added to the SAME registry the workflow services use — so ``build_scheduler`` registers
    # the copy-cycle and resolve jobs if and only if the lane was fully composed. Absent, this
    # adds no key, no job runs, and the cockpit behaves exactly as it did under RW-16.
    app.state.autoexec = build_paper_autoexec(state, paper=paper, deliver=deliver, clock=clock)
    if app.state.autoexec.enabled:
        executor = app.state.autoexec.executor
        services["copy_cycle"] = build_copy_cycle_service(
            state, quote_fn=paper.quote if paper is not None else None, autoexec=executor)
        services["autoexec_resolve"] = build_autoexec_resolve_service(executor)
        # The end-of-day statement. Registered here rather than being optional, because it is
        # the ONE message that arrives whether or not anything happened — see
        # ``build_autoexec_digest_service`` and ``scheduler.run_autoexec_digest``.
        digest = build_autoexec_digest_service(app.state.autoexec)
        if digest is not None:
            services["autoexec_digest"] = digest
        # The other half of "submitted". ``copytrade.notify`` could always RENDER a ``filled``
        # message and, until this line, nothing ever produced one — so the operator's last word
        # on every trade was that it had been sent to the broker, which for a resting LIMIT order
        # (this lane's normal shape) is not the same fact as a position. ``build_fill_service``
        # returns None when there is no usable seam, so an unusable notifier gets NO job rather
        # than one that silently drops fills.
        fills = build_autoexec_fills_service(app.state.autoexec)
        if fills is not None:
            services["autoexec_fills"] = fills
    # RW-15 (minimal): the order-status reconcile tick. NOT gated on the auto-exec lane — a
    # resting order placed by the HUMAN paper path ratchets the SEC-07 pending-exposure cap in
    # exactly the same way, and the cap is global. Composed whenever a paper transport with a
    # status reader exists; absent, no job is registered and the posture is unchanged.
    status_poll = build_order_status_service(paper)
    if status_poll is not None:
        services["order_status_poll"] = status_poll
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
    build_scheduler(state, services=services, ingest=ingest).start()
    ingest.start_streams()

    # QT-09: the Discord command surface. A DAEMON thread, for the same reason the stream
    # readers are: ``client.run`` blocks forever and so does ``serve``, so whichever ran
    # second would never start — and a daemon dies with the process, so Ctrl-C tears the
    # gateway down instead of hanging on a socket. Absent a token or an operator allowlist
    # this is None and the cockpit runs exactly as it did before.
    autoexec = getattr(app.state, "autoexec", None)
    start_bot = build_operator_bot(
        state, autoexec=autoexec.executor if autoexec is not None else None)
    if start_bot is not None:
        threading.Thread(target=start_bot, name="discord-bot", daemon=True).start()

    log.info(
        "cockpit up: mode=%s api=http://%s:%s feeds=%s autoexec=%s",
        state.settings.mode.value,
        state.settings.api_host,
        state.settings.api_port,
        ingest.feed_names or "NONE",
        autoexec.status() if autoexec is not None else "not_composed",
    )
    serve(app, state.settings)


if __name__ == "__main__":  # pragma: no cover
    run()
