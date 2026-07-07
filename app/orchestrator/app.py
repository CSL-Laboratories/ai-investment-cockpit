"""WP5 — the orchestrator's FastAPI shell (draft-2 §"The orchestrator API").

``create_app(state)`` builds the loopback-only control-plane API the Discord bridge drives.
Every route requires the API key (``auth.ApiKeyAuth``, §6). This shell is deliberately THIN:
job/brief/proposal EXECUTION lands in WP6/WP7/WP8 — those handlers read stored rows or
return ``501 not_wired``. The load-bearing endpoints implemented here are the ones that move
the deterministic core already in the tree: ``/mode`` (ladder + arm guard), ``/limits``
(SEC-15 privileged loosening), ``/killswitch``, and ``/status``.

CRITICAL (Invariant 1): NO handler places an order or imports ``brokers/``. The order path
is unreachable from the API surface; the CI call-site gate must stay green.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

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
from schemas.models.market_event import MarketEvent
from storage.repositories import Repo

from .auth import ApiKeyAuth
from .breakers import BreakerEngine
from .gate import compute_gate
from .killswitch import killswitch as do_killswitch
from .mode_ladder import IllegalTransition, ModeLadder

_AUTO_MODES = frozenset({Mode.auto_veto, Mode.auto_full})

# ---- limits (SEC-15) -------------------------------------------------------------------
# Limits are audited (source of truth = latest EVENT_LIMITS_CHANGE), never a mutable table.
EVENT_LIMITS_CHANGE = "limits_change"
# Which direction LOOSENS a limit: +1 == a larger value loosens; -1 == a smaller value does.
_LIMIT_LOOSEN_DIR = {
    "max_notional": 1,
    "daily_loss_limit": 1,
    "max_positions": 1,
    "concentration_pct": 1,
    "triage_threshold": -1,          # a LOWER triage threshold widens what escalates (SEC-15)
    "deep_model_daily_budget": 1,
}
_DEFAULT_FUNDED_BALANCE = Decimal("25000")   # [est.] outer bound; overridable via settings


def _dec(x) -> Decimal:
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


def _funded_balance(settings) -> Decimal:
    return _dec(getattr(settings, "funded_balance", _DEFAULT_FUNDED_BALANCE))


def _default_limits(settings) -> dict:
    fb = _funded_balance(settings)
    return {
        "max_notional": str(getattr(settings, "max_notional", min(Decimal("1000"), fb))),
        "daily_loss_limit": str(getattr(settings, "daily_loss_limit", min(Decimal("500"), fb))),
        "max_positions": int(getattr(settings, "max_positions", 10)),
        "concentration_pct": str(getattr(settings, "concentration_pct", Decimal("0.25"))),
        "triage_threshold": int(settings.triage_threshold),
        "deep_model_daily_budget": int(settings.deep_model_daily_budget),
    }


def _hard_bounds(settings) -> dict:
    """The most-permissive value each field may take (a hard config ceiling derived from the
    funded balance; a floor for a lower-loosens field). No ``/limits`` call may cross it."""
    fb = _funded_balance(settings)
    return {
        "max_notional": fb,                 # cannot risk more than the funded balance (O1)
        "daily_loss_limit": fb,
        "max_positions": Decimal("50"),
        "concentration_pct": Decimal("1"),  # 100%
        "triage_threshold": Decimal("0"),   # floor: 0 (lower loosens)
        "deep_model_daily_budget": Decimal("50"),
    }


def _is_loosening(field: str, new, cur) -> bool:
    d = _LIMIT_LOOSEN_DIR[field]
    nv, cv = _dec(new), _dec(cur)
    return nv > cv if d > 0 else nv < cv


def _exceeds_bound(field: str, value, bounds: dict) -> bool:
    d = _LIMIT_LOOSEN_DIR[field]
    v, b = _dec(value), _dec(bounds[field])
    return v > b if d > 0 else v < b


def _current_limits(state) -> dict:
    with state.session_factory() as s:
        row = s.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == EVENT_LIMITS_CHANGE)
            .order_by(AuditEntry.seq.desc())
            .limit(1)
        ).scalar_one_or_none()
    limits = _default_limits(state.settings)
    if row and (row.payload or {}).get("limits"):
        limits.update(row.payload["limits"])
    return limits


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
    runtime: str | None = None
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


def create_app(state, *, auth: ApiKeyAuth | None = None) -> FastAPI:
    """Build the FastAPI app bound to an ``OrchestratorState``. ``auth`` is injectable for
    tests; production defaults to the keyring-backed key guard (every route requires it)."""
    guard = auth if auth is not None else ApiKeyAuth()
    app = FastAPI(title="AI Investment Cockpit — orchestrator", dependencies=[Depends(guard)])
    app.state.orch = state

    # ---- jobs (execution: WP6) ---------------------------------------------------------
    @app.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
    def create_job(body: JobCreate):
        aliases = load_model_map()
        if body.model not in aliases:
            raise HTTPException(status_code=422, detail=f"unknown model alias {body.model!r}")
        runtime = body.runtime or runtime_for(body.model)
        pinned = aliases[body.model].get("pinned", body.model)
        job = Repo(state.session_factory, AgentJob).add(
            id=new_id("job"), runtime=runtime, model=body.model, model_pinned=pinned,
            prompt_template=body.prompt_template, prompt_hash="", input_refs=body.input_refs,
            schema_id=body.schema_id, status="queued", created_at=state.clock.now(),
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
        return _not_wired("/brief/today", "WP8")

    @app.post("/brief/run")
    def brief_run():
        return _not_wired("/brief/run", "WP8")

    # ---- proposals (WP6 writes adjudications; WP7 executes) -----------------------------
    @app.get("/proposals")
    def list_proposals():
        # No proposal store is wired yet (WP8 produces them); return an honest empty list.
        return {"proposals": []}

    @app.post("/proposals/{proposal_id}/decision")
    def decide_proposal(proposal_id: str, body: DecisionRequest):
        return _not_wired(f"/proposals/{proposal_id}/decision", "WP6")

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
        breakers: dict = {}
        for r in brk_rows:                       # last row per name wins (created_at asc)
            breakers[r.breaker_name] = {
                "tripped": bool(r.tripped),
                "reason": r.reason,
                "at": (r.tripped_at or r.cleared_at or r.created_at).isoformat(),
            }
        return {
            "mode": _mode_view(ladder),
            "usage": usage,
            "watch_only": breaker_engine.is_watch_only(),
            "breakers": breakers,
            "ingest": {
                "market_events": event_count,
                "latest_event_ts": market_events.ingested_at.isoformat() if market_events else None,
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
        # NL -> predicate compile is an LLM step (SEC-19) wired in WP8; never here.
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
