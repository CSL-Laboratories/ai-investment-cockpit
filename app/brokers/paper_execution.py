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

from bridge.approval import Rejection, record_decision, token_nonce_for_approval
from brokers.order_tuple import canonical_order_tuple
from config.models import configured_decision_runtime
from orchestrator.breakers import BreakerEngine
from orchestrator.mode_ladder import ModeLadder
from risk.engine import evaluate
from schemas.models.agent_job import AgentJob
from schemas.models.order import Order
from schemas.models.proposal_record import ProposalRecord
from schemas.models.risk_decision import RiskDecision
from workflows.proposals import build_execution_verdict, build_risk_context


@dataclass(frozen=True)
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


class PaperExecutionService:
    """Read paper account state and execute only fully-approved stored orders."""

    def __init__(self, state, *, broker, gateway, token_registry, quote_fn,
                 watchlist_fn=None, market_data=None) -> None:
        self.state = state
        self._broker = broker
        self._gateway = gateway
        self._token_registry = token_registry
        self._quote_fn = quote_fn
        self._watchlist_fn = watchlist_fn or (lambda: frozenset())
        self.market_data = market_data

    def _watchlist(self) -> frozenset[str]:
        return frozenset(str(s).upper() for s in self._watchlist_fn())

    def account(self) -> dict:
        snap = self._broker.get_positions(st=self.state, clock=self.state.clock)
        return {
            "account": "alpaca-paper",
            "cash": str(snap.cash),
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
        with self.state.session_factory() as session:
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
            for row in (order, proposal, risk_decision, agent_job):
                if row is not None:
                    session.expunge(row)
        if proposal is None or risk_decision is None or agent_job is None:
            raise PaperExecutionError(
                "incomplete_lineage",
                "order is missing its proposal, risk decision, or producing agent job",
            )
        return _Bundle(order, proposal, risk_decision, agent_job)

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
                    require_w21: bool = False):
        try:
            snapshot = self._broker.get_positions(st=self.state, clock=self.state.clock)
            quote = self._quote_fn(symbol)
            market_data = self.market_data
            verdict, book_seq, _resolved_qty, _reasons = build_execution_verdict(
                proposal_payload,
                quote_price=Decimal(str((quote or {}).get("price", 0))),
                clock=self.state.clock,
                book_fn=(market_data.book if market_data is not None else None),
                metrics_fn=(market_data.metrics if market_data is not None else None),
                verify_params=(market_data.verify_params if market_data is not None else None),
                # Deliberately no edge/borrow provider in W-21. The package that selects a
                # deterministic strategy or opens short authority must add its provider to
                # BOTH drafting and this fresh gate under a new adversarial review.
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
            )
            return evaluate(proposal_payload, ctx), snapshot
        except Exception:  # noqa: BLE001 - any vendor/data fault means no order authority
            return {
                "decision": "rejected",
                "failed_checks": ["paper_broker_or_quote_unavailable"],
            }, None

    def decide(self, order_id: str, *, actor: str, decision: str, confirm_symbol: str | None,
               request_id: str) -> dict:
        bundle = self._load_bundle(order_id)
        order, proposal, risk_decision, agent_job = (
            bundle.order, bundle.proposal, bundle.risk_decision, bundle.agent_job
        )
        choice = str(decision).strip().lower()
        if choice not in {"approve", "reject"}:
            raise PaperExecutionError("bad_decision", "decision must be approve or reject", 422)
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
            surface="local_api",
            interaction_id=request_id,
            device="loopback",
            notional=order.notional,
            token_registry=self._token_registry,
            mode=ModeLadder(self.state).current(),
        )
        if isinstance(adjudication, Rejection):
            raise PaperExecutionError(adjudication.code, adjudication.reason, 403)
        if choice == "reject":
            return {
                "status": "rejected",
                "order_id": order.id,
                "approval_id": adjudication.id,
            }

        def risk_recheck(_positions):
            result, _snap = self._fresh_risk(
                proposal.payload,
                order.symbol,
                order_qty=order.qty,
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
            clock=self.state.clock,
        )
        if not outcome.ok:
            raise PaperExecutionError(
                f"gateway_{outcome.stage}", outcome.reason or "paper gateway refused the order"
            )
        return {
            "status": "submitted",
            "order_id": order.id,
            "approval_id": adjudication.id,
            "broker_order_id": outcome.broker_order_id,
            "broker_status": outcome.broker_status,
        }
