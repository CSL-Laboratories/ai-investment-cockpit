"""Human-gated execution service for REAL Alpaca Paper Trading.

This is the production composition seam that was missing from the fixture-only WP7 build. It
does not create proposals and never decides what to trade. It can only execute an already
persisted, deterministic-risk-approved, unflagged proposal after an allowlisted human submits an
exact-symbol confirmation through an authenticated operator surface.

The broker remains private inside ``OrderGateway``. Every execution goes through the existing
risk -> paper-mode -> breaker -> signed approval -> single-use capability -> pending-exposure
checks before the fixed Alpaca PAPER transport is reached.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from bridge.approval import Rejection, record_decision, token_nonce_for_approval
from bridge.operators import is_authorized
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from brokers.order_state_machine import transition
from brokers.paper_performance import EASTERN, reconstruct_paper_performance
from config.models import configured_decision_runtime
from core.proposal_authority import proposal_payload_hash
from orchestrator.breakers import BreakerEngine, BreakerSignals, BreakerThresholds
from orchestrator.limits import current_limits
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_PAPER_NOTIFICATION
from risk.engine import evaluate
from schemas.models.agent_job import AgentJob
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from schemas.models.proposal_record import ProposalRecord
from schemas.models.risk_decision import RiskDecision
from workflows.constants import EVENT_AGENT_JOB, EVENT_PROPOSAL
from workflows.proposals import build_execution_verdict, build_risk_context


@dataclass
class PaperExecutionError(RuntimeError):
    code: str
    message: str
    status_code: int = 409

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class _Bundle:
    order: Order
    proposal: ProposalRecord
    risk_decision: RiskDecision
    agent_job: AgentJob
    analysis_vendor: str | None = None
    analysis_generation: int | None = None
    analysis_authority_error: str | None = None
    order_authority_error: str | None = None


class PaperExecutionService:
    """Read paper account state and execute only fully-approved stored orders."""

    def __init__(self, state, *, broker, gateway, token_registry, quote_fn,
                 watchlist_fn=None, market_data=None, order_reconciler=None,
                 deliver=None, edge_provider=None, require_performance: bool = False) -> None:
        self.state = state
        self._broker = broker
        self._gateway = gateway
        self._token_registry = token_registry
        self._quote_fn = quote_fn
        self._watchlist_fn = watchlist_fn or (lambda: frozenset())
        self.market_data = market_data
        self.order_reconciler = order_reconciler
        self._deliver = deliver
        self._edge_provider = edge_provider
        if require_performance:
            self.state.paper_performance_required = True

    def order_history(self, order_id: str) -> list[dict]:
        if self.order_reconciler is None:
            return []
        return self.order_reconciler.order_history(order_id)

    def reconcile_orders(self):
        if self.order_reconciler is None:
            raise PaperExecutionError(
                "reconciliation_not_wired", "paper order reconciliation is not wired", 503
            )
        return self.order_reconciler.poll()

    def _notify(self, channel: str, payload: dict) -> bool:
        if self._deliver is None:
            return False
        try:
            return bool(self._deliver(channel, payload))
        except Exception:  # noqa: BLE001 - notification failure never rewinds broker state
            return False

    def _notify_fill(self, payload: dict) -> bool:
        delivered = self._notify("#proposals", payload)
        self.state.audit.append(
            EVENT_PAPER_NOTIFICATION,
            actor="paper_execution",
            refs={"order_id": payload.get("order_id")},
            payload={**payload, "channel": "#proposals", "delivered": delivered},
        )
        return delivered

    def sample_performance(self) -> dict:
        """Persist equity, reconstruct signed P&L signals, and evaluate breakers."""
        try:
            self._broker.get_positions(st=self.state, clock=self.state.clock)
            return self._refresh_performance()
        except Exception as exc:  # noqa: BLE001 - missing broker evidence disables orders
            from brokers.paper_performance import PaperPerformance

            performance = PaperPerformance(
                False,
                self.state.clock.now().astimezone(EASTERN).date().isoformat(),
                reason=f"paper performance unavailable: {type(exc).__name__}",
            )
            return self._refresh_performance(performance)

    def _refresh_performance(self, performance=None) -> dict:
        """Consume the just-signed account observation at every authority boundary."""
        engine = BreakerEngine(self.state)
        if performance is None:
            performance = reconstruct_paper_performance(self.state)
        self.state.paper_performance = performance
        events = []
        if not performance.ok:
            if not engine.is_watch_only("paper_performance"):
                events.append(engine.enter_watch_only(
                    "paper_performance",
                    performance.reason or "paper performance unavailable",
                    actor="paper_performance",
                ))
        else:
            if engine.is_watch_only("paper_performance"):
                event, _mode = engine.recover_from_watch_only(
                    "paper_performance",
                    ModeLadder(self.state).current(),
                    actor="paper_performance",
                )
                events.append(event)
            thresholds = BreakerThresholds(
                daily_equity_drawdown_limit=Decimal(str(
                    current_limits(self.state)["daily_loss_limit"]
                ))
            )
            events.extend(engine.evaluate_and_trip(
                BreakerSignals(
                    equity_drawdown_today=performance.equity_drawdown_today,
                    consecutive_losses=performance.consecutive_losses,
                ),
                thresholds,
                actor="paper_performance",
            ))
        delivered = [
            self._notify(event.channel, {
                "kind": event.kind,
                "breaker": event.breaker,
                "reason": event.reason,
                "mode_after": event.mode_after,
                "at": event.at.isoformat(),
            })
            for event in events
        ]
        result = performance.as_dict()
        result["system_events"] = len(events)
        result["events_delivered"] = delivered
        return result

    def _watchlist(self) -> frozenset[str]:
        return frozenset(str(s).upper() for s in self._watchlist_fn())

    def account(self) -> dict:
        snap = self._broker.get_positions(st=self.state, clock=self.state.clock)
        return {
            "account": "alpaca-paper",
            "cash": str(snap.cash),
            "equity": None if snap.equity is None else str(snap.equity),
            "buying_power": str(snap.buying_power),
            "exposure": None if snap.exposure is None else str(snap.exposure),
            "holdings": list(snap.holdings or []),
            "as_of": snap.ts.isoformat(),
        }

    def holdings_symbols(self) -> frozenset[str]:
        try:
            account = self.account()
        except Exception:  # noqa: BLE001 - unavailable broker data fails closed to no holdings
            return frozenset()
        return frozenset(
            str(row.get("symbol", "")).upper()
            for row in account["holdings"]
            if row.get("symbol")
        )

    def quote(self, symbol: str) -> dict:
        return self._quote_fn(symbol)

    def _load_bundle(self, order_id: str) -> _Bundle:
        with self.state.session_factory() as session, session.begin():
            verified = self.state.audit.verify_session(session)
            if not verified.ok:
                raise PaperExecutionError(
                    "lineage_unverified",
                    "paper order lineage cannot be trusted because the audit chain failed "
                    f"verification: {verified.error or 'unknown error'}",
                )
            order = session.get(Order, order_id)
            if order is None:
                raise PaperExecutionError("not_found", "paper order not found", 404)
            proposal = session.get(ProposalRecord, order.proposal_id)
            risk_decision = session.get(RiskDecision, order.risk_decision_id)
            agent_job = (
                session.get(AgentJob, proposal.job_id)
                if proposal is not None and proposal.job_id
                else None
            )
            authority_entries = session.execute(
                select(AuditEntry)
                .where(AuditEntry.event_type.in_([EVENT_AGENT_JOB, EVENT_PROPOSAL]))
                .order_by(AuditEntry.seq)
            ).scalars().all()
            for row in (order, proposal, risk_decision, agent_job):
                if row is not None:
                    session.expunge(row)
        if proposal is None or risk_decision is None or agent_job is None:
            raise PaperExecutionError(
                "incomplete_lineage",
                "order is missing its proposal, risk decision, or producing agent job",
            )
        authority_error: str | None = None
        vendor: str | None = None
        generation: int | None = None
        order_authority_error = None
        proposal_entries = [
            entry for entry in authority_entries
            if entry.event_type == EVENT_PROPOSAL
            and (entry.refs or {}).get("model") == ProposalRecord.__tablename__
            and (entry.refs or {}).get("row_id") == proposal.id
        ]
        job_entries = [
            entry for entry in authority_entries
            if entry.event_type == EVENT_AGENT_JOB
            and (entry.refs or {}).get("model") == AgentJob.__tablename__
            and (entry.refs or {}).get("row_id") == agent_job.id
        ]
        if len(proposal_entries) != 1 or len(job_entries) != 1:
            authority_error = "lineage requires exactly one signed proposal and agent-job event"
        else:
            proposal_payload = proposal_entries[0].payload or {}
            job_payload = job_entries[0].payload or {}
            vendor = job_payload.get("runtime")
            generation = job_payload.get("generation")
            row_generation = (agent_job.input_refs or {}).get("_generation")
            try:
                bound = (
                    proposal_payload.get("proposal_payload_hash") == proposal_payload_hash(proposal.payload)
                    and proposal_payload.get("order_tuple_hash") == order_tuple_hash(self._resolved(order))
                    and proposal_payload.get("order_tuple_hash") == order.order_tuple_hash
                    and proposal_payload.get("status") == proposal.status
                    and proposal_payload.get("flagged") is proposal.flagged
                    and proposal_payload.get("symbol") == proposal.symbol == order.symbol
                    and proposal_payload.get("side") == proposal.side == order.side
                )
            except (TypeError, ValueError):
                bound = False
            if not bound:
                order_authority_error = "proposal body or resolved order lacks its original signed binding"
            if (
                proposal_payload.get("proposal_id") != proposal.id
                or proposal_payload.get("job_id") != proposal.job_id
                or proposal_payload.get("risk_decision_id") != proposal.risk_decision_id
                or proposal_payload.get("order_id") != proposal.order_id
                or proposal.risk_decision_id != risk_decision.id
                or proposal.order_id != order.id
                or order.proposal_id != proposal.id
                or order.risk_decision_id != risk_decision.id
            ):
                authority_error = "signed proposal lineage does not match the persisted order bundle"
            elif (
                not isinstance(vendor, str)
                or not vendor
                or vendor != agent_job.runtime
                or job_payload.get("status") != agent_job.status
                or isinstance(generation, bool)
                or not isinstance(generation, int)
                or generation < 0
                or row_generation != generation
            ):
                authority_error = "signed agent generation/runtime does not match its persisted row"
        return _Bundle(
            order,
            proposal,
            risk_decision,
            agent_job,
            analysis_vendor=vendor,
            analysis_generation=generation,
            analysis_authority_error=authority_error,
            order_authority_error=order_authority_error,
        )

    def _assert_fresh_analysis(self, bundle: _Bundle) -> None:
        if bundle.order_authority_error is not None:
            raise PaperExecutionError(
                "order_authority_unavailable", bundle.order_authority_error,
            )
        if bundle.analysis_authority_error is not None:
            raise PaperExecutionError(
                "analysis_authority_unavailable",
                "paper approval requires signed proposal-to-agent generation authority: "
                f"{bundle.analysis_authority_error}",
            )
        try:
            BreakerEngine(self.state).assert_fresh_analysis(
                bundle.analysis_vendor,
                bundle.analysis_generation,
            )
        except Exception as exc:  # noqa: BLE001 - malformed/stale authority refuses approval
            raise PaperExecutionError(
                "stale_analysis",
                "paper approval requires analysis from the current vendor generation",
            ) from exc

    @staticmethod
    def _resolved(order: Order) -> dict:
        return canonical_order_tuple(
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            order_type=order.order_type,
            limit_price=order.limit_price,
            tif=order.tif,
        )

    def preview(self, order_id: str) -> dict:
        bundle = self._load_bundle(order_id)
        order, proposal, risk_decision, agent_job = (
            bundle.order, bundle.proposal, bundle.risk_decision, bundle.agent_job
        )
        return {
            "order_id": order.id,
            "proposal_id": proposal.id,
            "symbol": order.symbol,
            "side": order.side,
            "qty": str(order.qty),
            "order_type": order.order_type,
            "limit_price": None if order.limit_price is None else str(order.limit_price),
            "tif": order.tif,
            "notional": None if order.notional is None else str(order.notional),
            "state": order.state,
            "proposal_status": proposal.status,
            "flagged": bool(proposal.flagged),
            "risk_decision": risk_decision.decision,
            "failed_checks": list(risk_decision.failed_checks or []),
            "producing_runtime": agent_job.runtime,
            "mode": ModeLadder(self.state).current().value,
        }

    def _fresh_risk(self, proposal_payload: dict, symbol: str, *, order_qty=None,
                    require_w21: bool = False, resolved_order=None):
        try:
            snapshot = self._broker.get_positions(st=self.state, clock=self.state.clock)
            if (getattr(self.state, "paper_performance_required", False)
                    or getattr(self.state, "paper_performance", None) is not None):
                if not self._refresh_performance()["ok"]:
                    raise ValueError("fresh signed performance unavailable")
                engine = BreakerEngine(self.state)
                if engine.is_watch_only() or engine.active_trips():
                    raise ValueError("fresh performance raised an active safety breaker")
            market_data = self.market_data
            observation = market_data.observation(symbol) if market_data is not None else None
            quote = dict(observation.quote) if observation is not None else self._quote_fn(symbol)
            # BUG-116: both gates price the exact immutable order the human will sign.
            # Proposal sizing instructions are not a second sizing authority at approval.
            evaluated = dict(proposal_payload)
            if resolved_order is not None:
                evaluated.update({
                    "symbol": resolved_order["symbol"], "side": resolved_order["side"],
                    "order_type": resolved_order["order_type"],
                    "quantity": resolved_order["qty"], "notional": None,
                })
                order_qty = resolved_order["qty"]
            verdict, book_seq, _resolved_qty, _reasons = build_execution_verdict(
                evaluated,
                quote_price=Decimal(str((quote or {}).get("price", 0))),
                clock=self.state.clock,
                book_fn=(market_data.book if market_data is not None else None),
                market_observation=observation,
                observation_fn=(market_data.observation if market_data is not None else None),
                metrics_fn=(market_data.metrics if market_data is not None else None),
                verify_params=(market_data.verify_params if market_data is not None else None),
                edge_provider=self._edge_provider,
                quote=quote,
                quantity_override=order_qty,
            )
            # A persisted W-21 verdict proves this order was drafted on the composed cost
            # path. Losing the service's market-data attachment later must not silently turn
            # approval-time risk back into the legacy additive posture.
            require_composed = market_data is not None or require_w21
            ctx = build_risk_context(
                self.state, quote=quote, watchlist=self._watchlist(),
                quant_verdict=verdict, quant_book_seq=book_seq,
                require_quant_verdict=require_composed,
                require_net_edge=require_composed,
                snapshot=snapshot,
            )
            if resolved_order is not None:
                price = max(ctx.quote_price, Decimal(str(resolved_order.get("limit_price") or 0)))
                evaluated["notional"] = str(Decimal(str(order_qty)) * price)
            return evaluate(evaluated, ctx), snapshot
        except Exception:  # noqa: BLE001 - any vendor/data fault means no order authority
            return {
                "decision": "rejected",
                "failed_checks": ["paper_broker_or_quote_unavailable"],
            }, None

    def decide(self, order_id: str, *, actor: str, decision: str, confirm_symbol: str | None,
               request_id: str, surface: str = "local_api") -> dict:
        bundle = self._load_bundle(order_id)
        order, proposal, risk_decision, agent_job = (
            bundle.order, bundle.proposal, bundle.risk_decision, bundle.agent_job
        )
        choice = str(decision).strip().lower()
        if choice not in {"approve", "reject", "veto"}:
            raise PaperExecutionError(
                "bad_decision", "decision must be approve, reject, or veto", 422
            )
        if surface not in {"local_api", "discord"}:
            raise PaperExecutionError("bad_surface", "unrecognized operator surface", 422)
        # Refuse foreign actors before quote/account I/O; record_decision repeats this check at
        # the capability-mint boundary and writes the canonical unauthorized audit event.
        if not is_authorized(actor, self.state.settings):
            adjudication = record_decision(
                self.state,
                proposal_id=proposal.id,
                risk_decision=risk_decision,
                resolved_order=self._resolved(order),
                actor_user_id=actor,
                user_decision=choice,
                surface=surface,
                interaction_id=request_id,
                device="loopback" if surface == "local_api" else "discord",
                notional=order.notional,
                token_registry=self._token_registry,
                mode=ModeLadder(self.state).current(),
            )
            if isinstance(adjudication, Rejection):
                raise PaperExecutionError(adjudication.code, adjudication.reason, 403)
            raise PaperExecutionError("unauthorized", "actor is not authorized", 403)
        if choice == "approve":
            # BUG-113: verify the HMAC-bound proposal -> AgentJob generation before any
            # approval capability is minted. Reject/veto remains available to terminalize an
            # old proposal safely; only order authority requires freshness.
            self._assert_fresh_analysis(bundle)
        if proposal.flagged or proposal.status != "draft":
            raise PaperExecutionError(
                "proposal_not_executable",
                "only an unflagged draft proposal may enter paper execution",
            )
        if risk_decision.decision != "approved":
            raise PaperExecutionError(
                "risk_not_approved", "the persisted deterministic risk decision is not approved"
            )
        if order.state not in {"proposed", "approved"}:
            raise PaperExecutionError(
                "order_not_submittable", f"order state {order.state!r} is not submittable"
            )
        selected_runtime = configured_decision_runtime(self.state.settings)
        if (agent_job.status != "done" or agent_job.runtime not in {"claude", "codex"}
                or agent_job.runtime != selected_runtime):
            raise PaperExecutionError(
                "runtime_not_allowed",
                "paper execution requires a successful proposal from the currently configured "
                f"decision runtime ({selected_runtime})",
            )
        if ModeLadder(self.state).current().value != "paper":
            raise PaperExecutionError(
                "paper_mode_required", "cockpit must be in audited paper mode"
            )
        resolved = self._resolved(order)

        if choice == "approve":
            if str(confirm_symbol or "").strip().upper() != order.symbol.upper():
                raise PaperExecutionError(
                    "symbol_confirmation_required",
                    f"type the exact symbol {order.symbol.upper()} to approve this paper order",
                    422,
                )
            fresh, _snapshot = self._fresh_risk(
                proposal.payload,
                order.symbol,
                order_qty=order.qty,
                resolved_order=resolved,
                require_w21=risk_decision.execution_verdict is not None,
            )
            if fresh.get("decision") != "approved":
                raise PaperExecutionError(
                    "fresh_risk_failed",
                    "fresh deterministic risk check refused the paper order",
                )

        adjudication = record_decision(
            self.state,
            proposal_id=proposal.id,
            risk_decision=risk_decision,
            resolved_order=resolved,
            actor_user_id=actor,
            user_decision=choice,
            surface=surface,
            interaction_id=request_id,
            device="loopback" if surface == "local_api" else "discord",
            notional=order.notional,
            token_registry=self._token_registry,
            mode=ModeLadder(self.state).current(),
        )
        if isinstance(adjudication, Rejection):
            raise PaperExecutionError(adjudication.code, adjudication.reason, 403)
        if (
            adjudication.proposal_id != proposal.id
            or adjudication.risk_decision_id != risk_decision.id
            or adjudication.order_tuple_hash != order.order_tuple_hash
            or adjudication.user_decision != choice
        ):
            raise PaperExecutionError(
                "interaction_conflict",
                "interaction id is already bound to a different adjudication",
                409,
            )
        if choice in {"reject", "veto"}:
            order.approval_id = adjudication.id
            transition(
                self.state,
                order,
                "rejected",
                actor=f"{surface}:{actor}",
                reason=f"operator {choice}",
                clock=self.state.clock,
            )
            return {
                "status": "vetoed" if choice == "veto" else "rejected",
                "order_id": order.id,
                "approval_id": adjudication.id,
            }

        def risk_recheck(_positions):
            result, _snap = self._fresh_risk(
                proposal.payload,
                order.symbol,
                order_qty=order.qty,
                resolved_order=resolved,
                require_w21=risk_decision.execution_verdict is not None,
            )
            return result

        latest = self._broker.get_positions(st=self.state, clock=self.state.clock)
        outcome = self._gateway.place_equity_order(
            order,
            approval=adjudication,
            risk_decision=risk_decision,
            ladder=ModeLadder(self.state),
            breaker_engine=BreakerEngine(self.state),
            risk_recheck=risk_recheck,
            positions=list(latest.holdings or []),
            token_nonce=token_nonce_for_approval(adjudication.id),
            producing_runtime=agent_job.runtime,
            analysis_vendor=bundle.analysis_vendor,
            analysis_generation=bundle.analysis_generation,
            clock=self.state.clock,
        )
        if not outcome.ok:
            raise PaperExecutionError(
                f"gateway_{outcome.stage}", outcome.reason or "paper gateway refused the order"
            )
        result = {
            "status": "submitted",
            "order_id": order.id,
            "approval_id": adjudication.id,
            "broker_order_id": outcome.broker_order_id,
            "broker_status": outcome.broker_status,
        }
        if outcome.broker_status == "filled":
            result["fill_notification_delivered"] = self._notify_fill({
                "kind": "paper_fill_confirmation",
                "order_id": order.id,
                "symbol": order.symbol,
                "side": order.side,
                "qty": str(order.qty),
                "broker_order_id": outcome.broker_order_id,
                "broker_status": outcome.broker_status,
            })
        return result
