"""Research pass that turns the sidecar into decision-packet features.

``plan`` does not read ``Settings.mode``. Paper today and an autonomous rung
later call the same function: the output is evidence, and ``admits_order`` on
a run card is always false because this module has no order path.

Sidecar calls are appended to the audit log with the same cost fields the
decision path records (estimated tokens, the context cap, an unpriced local
rate). A missing binary is an unavailable audit row and an empty batch.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from ingest.dedup import Dedup
from research.vibe_trading.features import (
    FeatureLine,
    load_research_config,
    project_alpha_bench,
    project_contracts,
    project_etf,
    project_holdings,
    scan_run_tree,
    to_events,
    trim_lines,
)
from research.vibe_trading.mcp_client import (
    SidecarProtocolError,
    SidecarTimeout,
    SidecarUnavailable,
    StdioMcpClient,
)
from research.vibe_trading.policy import (
    SidecarToolDenied,
    assert_strategy_id,
    prepare_arguments,
)
from workflows.context_limit import context_token_cap, estimate_tokens
from workflows.events import persist_events

log = logging.getLogger(__name__)

EVENT_SIDECAR_CALL = "sidecar_call"


@dataclass(frozen=True)
class ResearchRequest:
    """One research pass. There is no mode field; execution mode is not an input."""

    strategy_id: str | None = None
    run_root: str | None = None


@dataclass(frozen=True)
class ResearchBatch:
    status: str
    events: tuple[dict, ...]
    calls: tuple[dict, ...]
    dropped: int


@dataclass(frozen=True)
class PublishResult:
    status: str
    stored: tuple
    calls: tuple[dict, ...]
    dropped: int


@dataclass
class _Attempt:
    tool: str
    status: str
    duration_ms: int
    arguments: dict
    lines: list[FeatureLine]


class ResearchAdapter:
    """Call the research sidecar and return normalized market-event dicts.

    ``transport`` is the test seam. Production builds a stdio MCP client the
    first time a call is allowed. The client is not constructed when the
    adapter is disabled.
    """

    def __init__(self, settings, *, transport=None):
        self._settings = settings
        self._transport = transport
        self._client = None
        self._down = False

    def plan(self, request: ResearchRequest | None = None) -> tuple[tuple[str, dict], ...]:
        """Tool calls for this pass, in a stable order. Does not read the mode."""
        request = request or ResearchRequest()
        settings = self._settings
        calls: list[tuple[str, dict]] = [(
            "alpha_bench",
            {
                "zoo": settings.vibe_trading_bench_zoo,
                "universe": settings.vibe_trading_bench_universe,
                "period": settings.vibe_trading_bench_period,
                "top": int(settings.vibe_trading_bench_top),
            },
        )]
        symbols = tuple(sorted(settings.watchlist_symbols))[:int(settings.vibe_trading_max_symbols)]
        for symbol in symbols:
            calls.append(("get_institutional_holdings", {"mode": "ticker_holders", "ticker": symbol}))
        for etf in settings.vibe_trading_etf_symbols:
            calls.append(("etf_holdings", {"mode": "holdings", "symbol": etf}))
        for query in settings.vibe_trading_event_query_list:
            calls.append(("prediction_market", {"mode": "search", "query": query, "limit": 5}))
        return tuple(calls)

    def collect(self, request: ResearchRequest | None = None, *,
                audit=None, clock=None) -> ResearchBatch:
        request = request or ResearchRequest()
        if not getattr(self._settings, "vibe_trading_enabled", False):
            return ResearchBatch("disabled", (), (), 0)
        self._down = False
        attempts = self._run(request)
        ts = self._timestamp(clock)
        budget = min(
            int(self._settings.vibe_trading_token_budget),
            context_token_cap(self._settings),
        )
        cap = context_token_cap(self._settings)
        produced = [line for attempt in attempts for line in attempt.lines]
        kept, dropped = trim_lines(produced, budget_tokens=budget)
        kept_ids = {id(line) for line in kept}
        events = tuple(to_events(kept, ts=ts, dedup=Dedup()))
        calls = tuple(
            self._audit(audit, attempt, kept_ids=kept_ids, cap=cap, budget=budget)
            for attempt in attempts
        )
        statuses = {attempt.status for attempt in attempts}
        if not attempts:
            status = "ok"
        elif statuses <= {"ok"}:
            status = "ok"
        elif "ok" in statuses:
            status = "partial"
        elif statuses <= {"unavailable"}:
            status = "unavailable"
        else:
            status = "partial" if events else "quarantined"
        return ResearchBatch(status, events, calls, dropped)

    def publish(self, state, request: ResearchRequest | None = None, *,
                parquet_root=None) -> PublishResult:
        """Persist the batch through the existing ingest path so the next decision window sees it.

        Grounding refuses non-parquet refs, so a publish that would feed proposals
        requires a parquet root. The ingest poller always passes the composed root.
        """
        batch = self.collect(request, audit=state.audit, clock=state.clock)
        if not batch.events:
            return PublishResult(batch.status, (), batch.calls, batch.dropped)
        if parquet_root is None:
            return PublishResult(batch.status, (), batch.calls, batch.dropped)
        stored = persist_events(
            state, list(batch.events), parquet_root=parquet_root, clock=state.clock,
        ).stored
        return PublishResult(batch.status, tuple(stored), batch.calls, batch.dropped)

    def _run(self, request: ResearchRequest) -> list[_Attempt]:
        settings = self._settings
        symbols = tuple(sorted(settings.watchlist_symbols))
        universe = frozenset(symbols)
        attempts: list[_Attempt] = []
        planned = self.plan(request)
        deadline = time.monotonic() + float(settings.vibe_trading_timeout_seconds)
        for tool, args in planned:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                attempts.append(_Attempt(tool, "unavailable", 0, args, []))
                continue
            payload, attempt = self._invoke(tool, args, timeout_s=remaining)
            if payload is not None and attempt.status == "ok":
                projected = self._project(tool, args, payload, universe=universe)
                if projected is None:
                    attempt.status = "quarantined"
                    attempt.lines = []
                else:
                    attempt.lines = projected
            attempts.append(attempt)
        if request.strategy_id is not None or request.run_root is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                attempts.append(_Attempt("backtest", "unavailable", 0, {}, []))
            else:
                attempts.append(self._vet(request, universe, timeout_s=remaining))
        return attempts

    @staticmethod
    def _project(tool: str, args: dict, payload: dict, *,
                 universe: frozenset[str]) -> list[FeatureLine] | None:
        if tool == "alpha_bench":
            return project_alpha_bench(
                payload, zoo=args["zoo"], universe=args["universe"], period=args["period"],
            )
        if tool == "get_institutional_holdings":
            return project_holdings(payload, ticker=args["ticker"])
        if tool == "etf_holdings":
            return project_etf(payload, etf=args["symbol"], universe=universe)
        if tool == "prediction_market":
            return project_contracts(payload, query=args["query"])
        return None

    def _vet(self, request: ResearchRequest, universe: frozenset[str],
             timeout_s: float | None = None) -> _Attempt:
        started = time.monotonic()
        try:
            strategy_id = assert_strategy_id(request.strategy_id or "")
            if request.run_root is None:
                raise SidecarToolDenied("backtest")
            run_dir = _resolve_run_dir(Path(request.run_root), strategy_id)
        except SidecarToolDenied:
            return _Attempt("backtest", "denied", _millis(started), {}, [])
        config_path = run_dir / "config.json"
        config = load_research_config(config_path)
        if config is None:
            return _Attempt("backtest", "quarantined", _millis(started), {}, [])
        reason = scan_run_tree(run_dir)
        if reason is not None:
            status = "denied" if ("forbidden" in reason or "order" in reason) else "quarantined"
            return _Attempt("backtest", status, _millis(started), {}, [])
        # Staged strategy Python runs inside the sidecar as this OS user. A scratch
        # HOME does not isolate SecretService / Keychain / Credential Manager, so
        # this path never executes operator-staged code and never republishes a
        # leftover run_card.json as fresh evidence. ``project_run_card`` stays for
        # unit tests and a future isolated runner; it is not reachable here.
        return _Attempt("backtest", "denied", _millis(started), {}, [])

    def _invoke(self, tool: str, arguments: dict, timeout_s: float | None = None
                ) -> tuple[dict | None, _Attempt]:
        started = time.monotonic()
        try:
            cleaned = prepare_arguments(tool, arguments)
        except SidecarToolDenied:
            return None, _Attempt(tool, "denied", _millis(started), {}, [])
        if self._down:
            return None, _Attempt(tool, "unavailable", _millis(started), cleaned, [])
        try:
            payload = self._call(tool, cleaned, timeout_s=timeout_s)
        except SidecarToolDenied:
            return None, _Attempt(tool, "denied", _millis(started), cleaned, [])
        except SidecarUnavailable as exc:
            self._down = True
            log.info("vibe-trading sidecar unavailable: %s", exc)
            return None, _Attempt(tool, "unavailable", _millis(started), cleaned, [])
        except (SidecarTimeout, SidecarProtocolError, OSError) as exc:
            log.info("vibe-trading sidecar call failed: %s", type(exc).__name__)
            return None, _Attempt(tool, "error", _millis(started), cleaned, [])
        if type(payload) is not dict:
            return None, _Attempt(tool, "quarantined", _millis(started), cleaned, [])
        return payload, _Attempt(tool, "ok", _millis(started), cleaned, [])

    def _call(self, tool: str, arguments: dict, timeout_s: float | None = None):
        client = self._client_for()
        saved = getattr(client, "_timeout_s", None)
        if timeout_s is not None and saved is not None:
            client._timeout_s = timeout_s
        try:
            return client.call(tool, arguments)
        finally:
            if timeout_s is not None and saved is not None:
                client._timeout_s = saved

    def _client_for(self):
        if self._transport is not None:
            return self._transport
        if self._client is None:
            self._client = StdioMcpClient(
                self._settings.vibe_trading_command,
                timeout_s=float(self._settings.vibe_trading_timeout_seconds),
            )
        return self._client

    def _audit(self, audit, attempt: _Attempt, *, kept_ids: set[int], cap: int,
               budget: int) -> dict:
        kept = [line for line in attempt.lines if id(line) in kept_ids]
        dropped = len(attempt.lines) - len(kept)
        text = "\n".join(line.text for line in kept)
        record = {
            "tool": attempt.tool,
            "status": attempt.status,
            "duration_ms": attempt.duration_ms,
            "input_tokens": estimate_tokens(json.dumps(attempt.arguments, sort_keys=True)),
            "output_tokens": estimate_tokens(text),
            "trimmed": dropped,
            "context_token_cap": cap,
            "token_budget": budget,
            "usd": None,
            "usd_scope": "unpriced",
            "rate_tier": "sidecar-local",
            "model_pinned": "vibe-trading-mcp",
            "input_token_source": "estimate",
        }
        if audit is not None:
            audit.append(EVENT_SIDECAR_CALL, actor="vibe_trading", payload=record)
        return record

    @staticmethod
    def _timestamp(clock) -> str:
        if clock is None:
            return ""
        now = clock.now()
        return now.isoformat() if hasattr(now, "isoformat") else str(now)


def _resolve_run_dir(run_root: Path, strategy_id: str) -> Path:
    root = Path(run_root).resolve()
    run_dir = (root / strategy_id).resolve()
    if run_dir.parent != root:
        raise SidecarToolDenied("backtest")
    return run_dir


def _millis(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
