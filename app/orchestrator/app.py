"""WP5 — the orchestrator's FastAPI shell (draft-2 §"The orchestrator API").

``create_app(state)`` builds the loopback-only control-plane API the Discord bridge drives.
Every route requires the API key (``auth.ApiKeyAuth``, §6). This shell is deliberately THIN:
job execution lands in WP6/WP7 — those handlers read stored rows or return ``501
not_wired``. The load-bearing endpoints implemented here are the ones that move the
deterministic core already in the tree: ``/mode`` (ladder + arm guard), ``/limits``
(SEC-15 privileged loosening), ``/killswitch``, and ``/status``.

WP8 wiring (docs/build-scaffold-ultracode.md WP8): ``create_app`` gains an optional
``services`` registry ({"brew", "intraday", "eod", "ask"} callables, built by
``workflows.services.build_services``). ``GET /brief/today`` / ``POST /brief/run`` read /
invoke through it and stay ``501 not_wired`` when it is absent (existing WP5 tests keep
passing); ``GET /proposals`` reads persisted ``ProposalRecord`` rows. The audited-limits
reader was EXTRACTED to ``orchestrator/limits.py`` (SEC-15); thin aliases below keep this
module's public surface unchanged. ``POST /automations`` stays 501 (SEC-19 deferred).

CRITICAL (Invariant 1): handlers never call a broker order method. The optional paper endpoints
delegate to a composed service in ``brokers/`` whose only dispatch is the existing guarded
``OrderGateway``; the CI call-site gate remains the mechanical backstop.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from config.models import load_model_map, runtime_for
from config.settings import Mode, get_settings
from core.ids import new_id
from schemas.models.agent_job import AgentJob
from schemas.models.audit_entry import AuditEntry
from schemas.models.automation_rule import AutomationRule
from schemas.models.breaker_state import BreakerState
from schemas.models.brief_record import BriefRecord
from schemas.models.market_event import MarketEvent
from schemas.models.proposal_record import ProposalRecord
from storage.repositories import Repo
from workflows.constants import EVENT_AGENT_JOB

from .auth import ApiKeyAuth
from .breakers import BreakerEngine
from .gate import compute_gate
from .killswitch import killswitch as do_killswitch
from .limits import (
    EVENT_LIMITS_CHANGE,
    LIMIT_LOOSEN_DIR as _LIMIT_LOOSEN_DIR,
    current_limits as _current_limits,
    funded_balance as _funded_balance,
    hard_bounds as _hard_bounds,
)
from .market_calendar import coverage_status as market_calendar_status
from .mode_ladder import IllegalTransition, ModeLadder
from .state import EVENT_PAPER_NOTIFICATION

__all__ = ["create_app", "serve", "EVENT_LIMITS_CHANGE", "_current_limits"]

_AUTO_MODES = frozenset({Mode.auto_veto, Mode.auto_full})


def _dec(x) -> Decimal:
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


def _is_loosening(field: str, new, cur) -> bool:
    d = _LIMIT_LOOSEN_DIR[field]
    nv, cv = _dec(new), _dec(cur)
    return nv > cv if d > 0 else nv < cv


def _exceeds_bound(field: str, value, bounds: dict) -> bool:
    d = _LIMIT_LOOSEN_DIR[field]
    v, b = _dec(value), _dec(bounds[field])
    return v > b if d > 0 else v < b


# ---- request bodies --------------------------------------------------------------------
class ModeUpdate(BaseModel):
    mode: str
    actor: str
    reason: str | None = None


class LimitsUpdate(BaseModel):
    max_notional: Decimal | None = None
    daily_loss_limit: Decimal | None = None
    max_positions: int | None = None
    concentration_pct: Decimal | None = None
    triage_threshold: int | None = None
    deep_model_daily_budget: int | None = None
    actor: str
    reason: str | None = None
    confirm: bool = False               # SEC-15: loosening requires explicit confirmation


class KillRequest(BaseModel):
    actor: str
    reason: str = "manual killswitch"


class JobCreate(BaseModel):
    model: str = "triage"
    runtime: Literal["claude", "codex"] | None = None
    effort: str | None = None
    prompt_template: str | None = None
    input_refs: dict = {}
    schema_id: str | None = None
    max_turns: int | None = None
    allowed_tools: list = []
    usage_budget: float | None = None


class DecisionRequest(BaseModel):
    decision: str
    actor: str
    reason: str | None = None


class PaperDecisionRequest(BaseModel):
    actor: str
    decision: Literal["approve", "reject"]
    request_id: str
    confirm_symbol: str | None = None


def _not_wired(endpoint: str, wp: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={"status": "not_wired", "endpoint": endpoint, "wired_in": wp},
    )


def _mode_view(ladder: ModeLadder) -> dict:
    row = ladder.current_row()
    return {
        "mode": ladder.current().value,
        "armed_at": row.armed_at.isoformat() if row and row.armed_at else None,
        "armed_until": row.armed_until.isoformat() if row and row.armed_until else None,
        "armed_by": row.armed_by if row else None,
    }


def create_app(state, *, auth: ApiKeyAuth | None = None,
               services: dict | None = None, ingest=None, paper=None,
               watchlist=None, quant_snapshots=None, quant_key_provider=None) -> FastAPI:
    """Build the FastAPI app bound to an ``OrchestratorState``. ``auth`` is injectable for
    tests; production defaults to the keyring-backed key guard (every route requires it).
    ``services`` is the WP8 workflow registry ({"brew","intraday","eod"}); when absent the
    brief endpoints stay honest 501s. ``ingest`` is the RW-01 ``IngestRunner``, surfaced on
    ``GET /status``; ``main.compose`` assigns it after the app is built, so this parameter is
    only for callers that already hold a runner.

    ``quant_snapshots`` is a QT-08 ``SnapshotSource``. When supplied, a READ-ONLY WebSocket
    at ``/ws/quant`` broadcasts engine state at 10Hz; when absent, no socket is registered at
    all — an orchestrator with no quant engine exposes no extra surface rather than an
    endpoint with nothing to say. The socket never reads a frame from the client, so it adds
    no control path around the key-gated, audited HTTP routes."""
    guard = auth if auth is not None else ApiKeyAuth()
    app = FastAPI(title="AI Investment Cockpit — orchestrator", dependencies=[Depends(guard)])
    app.state.orch = state
    app.state.services = services
    app.state.ingest = ingest
    app.state.paper = paper
    app.state.quant_snapshots = quant_snapshots

    # QT-09: the quant engine's operator surface. READ-ONLY and SIMULATION-ONLY — no route
    # in that module mutates cockpit state or reaches a venue. See orchestrator/quant_api.py.
    from .quant_api import register_quant_routes

    register_quant_routes(app, state)

    if quant_snapshots is not None:
        from .auth import keyring_api_key_provider
        from .quant_ws import register_quant_ws

        # The WebSocket handshake is checked EXPLICITLY rather than through the app-level
        # dependency above: relying on a silent equivalence between HTTP and WebSocket
        # dependency handling would risk an unauthenticated endpoint on a system whose whole
        # posture is "even loopback callers must hold the key".
        register_quant_ws(
            app, quant_snapshots,
            key_provider=quant_key_provider or keyring_api_key_provider,
        )
    # Freeze once at composition. Settings remains mutable for a few legacy/test seams, so reading
    # it per request would make the claimed restart-only allowlist silently runtime-mutable.
    configured_watchlist = frozenset(
        watchlist if watchlist is not None
        else getattr(state.settings, "watchlist_symbols", frozenset())
    )
    app.state.watchlist = configured_watchlist

    # ---- jobs (execution: WP6) ---------------------------------------------------------
    @app.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
    def create_job(body: JobCreate):
        aliases = load_model_map()
        if body.model not in aliases:
            raise HTTPException(status_code=422, detail=f"unknown model alias {body.model!r}")
        configured_runtime = runtime_for(body.model)
        runtime = body.runtime or configured_runtime
        if body.runtime is not None and runtime != configured_runtime:
            raise HTTPException(
                status_code=422,
                detail=(f"runtime {runtime!r} does not match model alias {body.model!r}; "
                        f"expected {configured_runtime!r}"),
            )
        pinned = aliases[body.model].get("pinned", body.model)
        # Inv 6 (SEC-13) fail-closed: a QUEUED AgentJob is a state change and MUST carry a covering
        # AuditEntry written ATOMICALLY with the row — never a bare ``Repo.add`` (which the round-2
        # completeness-gate expansion exposed: AgentJob is in _DEFAULT_MODELS, so a bare-added queued
        # job left an uncovered row that tripped reconcile ok=False on every boot). Route it through
        # record_state_change with the same EVENT_AGENT_JOB event that audits the job's later
        # completion (workflows/agent_calls.py:89), so the row and its audit entry commit together.
        job = AgentJob(
            id=new_id("job"), runtime=runtime, model=body.model, model_pinned=pinned,
            prompt_template=body.prompt_template, prompt_hash="", input_refs=body.input_refs,
            schema_id=body.schema_id, status="queued", created_at=state.clock.now(),
        )
        state.audit.record_state_change(
            job, EVENT_AGENT_JOB, actor="orchestrator.create_job",
            payload={"status": "queued", "model": body.model, "runtime": runtime,
                     "model_pinned": pinned, "schema_id": body.schema_id},
        )
        return {"id": job.id, "status": job.status, "runtime": runtime, "model_pinned": pinned}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        job = Repo(state.session_factory, AgentJob).get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {
            "id": job.id, "status": job.status, "runtime": job.runtime, "model": job.model,
            "model_pinned": job.model_pinned, "session_id": job.session_id,
            "schema_id": job.schema_id,
        }

    @app.post("/jobs/{job_id}/message")
    def message_job(job_id: str):
        return _not_wired(f"/jobs/{job_id}/message", "WP6")

    # ---- brief (brew workflow: WP8) ----------------------------------------------------
    @app.get("/brief/today")
    def brief_today():
        if not services or "brew" not in services:
            return _not_wired("/brief/today", "WP8")
        today = state.clock.now().date().isoformat()
        with state.session_factory() as s:
            row = s.execute(
                select(BriefRecord).where(BriefRecord.brief_date == today)
            ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail=f"no brief stored for {today}")
        return {
            "id": row.id, "date": row.brief_date, "degraded": bool(row.degraded),
            "job_id": row.job_id, "brief": row.payload,
        }

    @app.post("/brief/run")
    def brief_run():
        if not services or "brew" not in services:
            return _not_wired("/brief/run", "WP8")
        return services["brew"](state, state.clock)

    # ---- ingest (RW-01) ---------------------------------------------------------------
    @app.post("/ingest/poll")
    def ingest_poll(feed: str | None = None):
        """Force one ingest tick without waiting for a feed's scheduler window."""
        runner = getattr(app.state, "ingest", None)
        if runner is None:
            return _not_wired("/ingest/poll", "RW-01")
        if feed is not None and feed not in runner.polls and feed not in runner.streams:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no such feed {feed!r}; "
                    f"provisioned poll feeds: {sorted(runner.polls)}"
                ),
            )
        if feed is None:
            return {"polled": runner.poll_all()}
        if feed in runner.polls:
            return {"polled": {feed: runner.poll(feed)}}

        stream = runner.streams[feed]
        is_alive = getattr(stream.thread, "is_alive", lambda: False)
        return {
            "polled": {
                feed: {
                    "feed": feed,
                    "skipped": "stream_feed_runs_on_its_own_thread",
                    "alive": bool(is_alive()),
                }
            }
        }

    # ---- proposals (WP8 produces; WP6 adjudicates; WP7 executes) ------------------------
    @app.get("/proposals")
    def list_proposals():
        with state.session_factory() as s:
            rows = s.execute(
                select(ProposalRecord)
                .order_by(ProposalRecord.created_at.desc())
                .limit(100)
            ).scalars().all()
        return {
            "proposals": [
                {"id": r.id, "symbol": r.symbol, "side": r.side, "status": r.status,
                 "flagged": bool(r.flagged),
                 "created_at": r.created_at.isoformat() if r.created_at else None}
                for r in rows
            ]
        }

    @app.post("/proposals/{proposal_id}/decision")
    def decide_proposal(proposal_id: str, body: DecisionRequest):
        return _not_wired(f"/proposals/{proposal_id}/decision", "WP6")

    # ---- Alpaca Paper Trading (human-gated; never a direct broker call) ----------------
    @app.get("/paper/account")
    def paper_account():
        if paper is None:
            return _not_wired("/paper/account", "paper transport")
        try:
            return paper.account()
        except Exception as exc:  # noqa: BLE001 - sanitize vendor failures at the API boundary
            raise HTTPException(status_code=503, detail="Alpaca paper account unavailable") from exc

    @app.get("/watchlist")
    def watchlist():
        """Read the static operator-approved universe; there is deliberately no write route."""
        symbols = sorted(configured_watchlist)
        return {
            "symbols": symbols,
            "count": len(symbols),
            "source": "operator_config",
            "runtime_mutable": False,
        }

    @app.get("/paper/orders/{order_id}/preview")
    def paper_order_preview(order_id: str):
        if paper is None:
            return _not_wired(f"/paper/orders/{order_id}/preview", "paper transport")
        try:
            return paper.preview(order_id)
        except Exception as exc:  # noqa: BLE001 - service errors are mapped without vendor text
            code = int(getattr(exc, "status_code", 409))
            detail = str(getattr(exc, "message", "paper order preview refused"))
            raise HTTPException(status_code=code, detail=detail) from exc

    @app.post("/paper/orders/{order_id}/decision")
    def paper_order_decision(order_id: str, body: PaperDecisionRequest):
        if paper is None:
            return _not_wired(f"/paper/orders/{order_id}/decision", "paper transport")
        try:
            return paper.decide(
                order_id,
                actor=body.actor,
                decision=body.decision,
                confirm_symbol=body.confirm_symbol,
                request_id=body.request_id,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed and avoid raw broker bodies
            code = int(getattr(exc, "status_code", 409))
            detail = str(getattr(exc, "message", "paper order decision refused"))
            raise HTTPException(status_code=code, detail=detail) from exc

    # ---- mode (ladder + arm guard) -----------------------------------------------------
    @app.get("/mode")
    def get_mode():
        return _mode_view(ModeLadder(state))

    @app.put("/mode")
    def put_mode(body: ModeUpdate):
        ladder = ModeLadder(state)
        try:
            target = Mode(body.mode)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"unknown mode {body.mode!r}") from None

        if target in _AUTO_MODES:
            gate = compute_gate(state, target)
            res = ladder.arm_auto_mode(target, gate_result=gate, operator=body.actor)
            if not res.armed:
                # gate-failing or non-Claude arm -> 4xx, nothing persisted (Inv 2 / SEC-20).
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={"armed": False, "mode": res.mode.value, "reasons": res.reasons,
                             "gate": gate.metrics},
                )
            return {"armed": True, **_mode_view(ladder)}

        try:
            ladder.set_mode(target, actor=body.actor, reason=body.reason)
        except IllegalTransition as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return _mode_view(ladder)

    # ---- limits (SEC-15 privileged loosening) ------------------------------------------
    @app.get("/limits")
    def get_limits():
        bounds = _hard_bounds(state.settings)
        return {
            "limits": _current_limits(state),
            "hard_ceilings": {k: str(v) for k, v in bounds.items()},
            "funded_balance": str(_funded_balance(state.settings)),
        }

    @app.put("/limits")
    def put_limits(body: LimitsUpdate):
        current = _current_limits(state)
        bounds = _hard_bounds(state.settings)
        requested = {
            k: getattr(body, k) for k in _LIMIT_LOOSEN_DIR
            if getattr(body, k) is not None
        }
        if not requested:
            raise HTTPException(status_code=422, detail="no limit fields supplied")

        loosened = [k for k, v in requested.items() if _is_loosening(k, v, current[k])]
        over = [k for k in loosened if _exceeds_bound(k, requested[k], bounds)]
        if over:
            # SEC-15: cannot loosen beyond the hard config ceiling from the funded balance.
            # The security-hardening backlog is explicit that a ceiling-breach ATTEMPT is
            # itself a privileged-change signal: it is REJECTED **and disarms auto modes** to
            # review_live. Drop FIRST (audited via the ladder), so the 4xx never leaves
            # autonomy armed.
            mode_after = ModeLadder(state).drop_to_review_live(
                f"limits ceiling-breach attempt: {over}", actor=body.actor,
                kind="limits_ceiling_breach",
            ).value
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={"rejected": "ceiling_exceeded", "fields": over,
                         "hard_ceilings": {k: str(bounds[k]) for k in over},
                         "mode_after": mode_after},
            )
        if loosened and not body.confirm:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"rejected": "confirmation_required", "loosening": loosened,
                         "hint": "resend with confirm=true (SEC-15 privileged change)"},
            )

        new_limits = dict(current)
        for k, v in requested.items():
            new_limits[k] = str(v) if isinstance(v, Decimal) else v

        # Audit the change (append; the mode-drop below is the load-bearing state mutation).
        state.audit.append(
            EVENT_LIMITS_CHANGE, actor=body.actor,
            payload={"limits": new_limits, "loosened": loosened,
                     "reason": body.reason or "limits change"},
        )
        mode_after = None
        if loosened:
            # SEC-15: any loosening drops auto modes to review_live (never a re-arm).
            mode_after = ModeLadder(state).drop_to_review_live(
                f"limits loosened: {loosened}", actor=body.actor, kind="limits_loosened"
            ).value
        return {"limits": new_limits, "loosened": loosened, "mode_after": mode_after}

    # ---- killswitch (L7 / SEC-04) ------------------------------------------------------
    @app.post("/killswitch")
    def post_killswitch(body: KillRequest):
        res = do_killswitch(state, actor=body.actor, reason=body.reason)
        return {
            "mode_after": res.mode_after.value,
            "cancelled_count": res.cancelled_count,
            "halt_path": str(res.halt_path),
        }

    # ---- status ------------------------------------------------------------------------
    @app.get("/status")
    def get_status():
        ladder = ModeLadder(state)
        breaker_engine = BreakerEngine(state)
        usage = {
            v: {"state": w.state, "used": w.used, "generation": w.generation}
            for v, w in state.usage._w.items()
        }
        with state.session_factory() as s:
            brk_rows = s.execute(
                select(BreakerState).order_by(BreakerState.created_at)
            ).scalars().all()
            market_events = s.execute(
                select(MarketEvent).order_by(MarketEvent.ingested_at.desc()).limit(1)
            ).scalar_one_or_none()
            event_count = len(s.execute(select(MarketEvent.id)).all())
            notification_rows = s.execute(
                select(AuditEntry).where(
                    AuditEntry.event_type == EVENT_PAPER_NOTIFICATION
                ).order_by(AuditEntry.seq)
            ).scalars().all()
        breakers: dict = {}
        for r in brk_rows:                       # last row per name wins (created_at asc)
            breakers[r.breaker_name] = {
                "tripped": bool(r.tripped),
                "reason": r.reason,
                "at": (r.tripped_at or r.cleared_at or r.created_at).isoformat(),
            }
        # RW-01: the STORED totals answer "did anything land?", but not "is each feed alive?".
        # A feed that is unprovisioned, wedged, or clamped produces zero events forever — which
        # is shaped exactly like a quiet market — so the runner's per-feed health (including
        # each poller's own progress/strand signals and EDGAR's brew freshness verdict) is
        # reported alongside. Absent runner => "not_wired", never an empty-and-therefore-fine.
        runner = getattr(app.state, "ingest", None)
        feeds = runner.status() if runner is not None else {"status": "not_wired"}
        performance = getattr(state, "paper_performance", None)
        performance_status = (
            performance.as_dict()
            if performance is not None and hasattr(performance, "as_dict")
            else {
                "ok": False,
                "reason": (
                    "not_sampled"
                    if getattr(state, "paper_performance_required", False)
                    else "paper_not_wired"
                ),
            }
        )
        chain = state.audit.verify_chain()
        verified_notifications = (
            [row for row in notification_rows if state.audit.verify_entry(row)]
            if chain.ok else []
        )
        latest_notification = (
            verified_notifications[-1].payload if verified_notifications else None
        )
        return {
            "mode": _mode_view(ladder),
            "market_calendar": market_calendar_status(state.clock.now()),
            "usage": usage,
            "usage_integrity_error": state.usage.integrity_error,
            "watch_only": breaker_engine.is_watch_only(),
            "breakers": breakers,
            "paper_performance": performance_status,
            "audit_chain": {
                "ok": chain.ok,
                "head_seq": chain.head_seq,
                "error": chain.error,
            },
            "paper_fill_notifications": {
                "total": len(verified_notifications) if chain.ok else None,
                "undelivered": sum(
                    1 for row in verified_notifications
                    if not bool((row.payload or {}).get("delivered"))
                ) if chain.ok else None,
                "latest": latest_notification,
            },
            "ingest": {
                "market_events": event_count,
                "latest_event_ts": market_events.ingested_at.isoformat() if market_events else None,
                "runner": feeds,
            },
        }

    # ---- automations (compiler: WP8 / SEC-19) ------------------------------------------
    @app.get("/automations")
    def list_automations():
        rules = Repo(state.session_factory, AutomationRule).list()
        return {
            "automations": [
                {"id": r.id, "enabled": bool(r.enabled), "natural_language": r.natural_language,
                 "symbols_scope": r.symbols_scope,
                 "expires_at": r.expires_at.isoformat() if r.expires_at else None}
                for r in rules
            ]
        }

    @app.post("/automations")
    def add_automation():
        # NL -> predicate compile is an LLM step (SEC-19); deliberately deferred. Never here.
        return _not_wired("/automations", "WP8")

    return app


def serve(app: FastAPI, settings=None) -> None:
    """Run the API under uvicorn, bound loopback-only (§6). Imported lazily so the module is
    testable without uvicorn installed."""
    import uvicorn

    from .auth import assert_loopback

    settings = settings or get_settings()
    assert_loopback(settings.api_host)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
