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

from dataclasses import dataclass, replace

import logging

from bridge.approval import Rejection, record_decision, token_nonce_for_approval
from brokers.order_tuple import canonical_order_tuple
from config.models import configured_decision_runtime
from copytrade.policy import policy_from_settings
from copytrade.universe import current_universe
from orchestrator.breakers import BreakerEngine
from orchestrator.mode_ladder import ModeLadder
from risk.engine import evaluate
from schemas.models.agent_job import AgentJob
from schemas.models.order import Order
from schemas.models.proposal_record import ProposalRecord
from schemas.models.risk_decision import RiskDecision
from workflows.copytrade_orders import COPYTRADE_RUNTIME, UNIVERSE_BOUNDS_REF
from workflows.proposals import build_risk_context

log = logging.getLogger(__name__)


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
                 watchlist_fn=None) -> None:
        self.state = state
        self._broker = broker
        self._gateway = gateway
        self._token_registry = token_registry
        self._quote_fn = quote_fn
        self._watchlist_fn = watchlist_fn or (lambda: frozenset())

    def _watchlist(self, *, producing_runtime: str | None = None,
                   agent_job: AgentJob | None = None) -> frozenset[str]:
        """The SEC-15 allowlist this submit-time re-check judges against.

        The static operator watchlist, PLUS — for a copy-path order only — the audited copy
        universe. Without the union this re-check consulted a strictly NARROWER allowlist than
        the one the draft passed: ``workflows.copytrade_orders`` evaluates against
        ``static_watchlist | copytrade.universe.current_universe(...)``, so every promoted
        symbol (which is every copy-traded symbol, by definition — the point of the feed is
        that it surfaces names the operator did not pick) passed at draft and was GUARANTEED to
        fail here. The human gate refused the order it had just been asked to approve.

        This is NOT a widening of SEC-15. The static list is untouched, and the universe is the
        same audited, HMAC-signed, bounded, TTL-expiring promotion lane the draft read, read
        from the same authority — see ``copytrade/universe.py``. The union is applied ONLY when
        the order's own stored lineage names the deterministic copy producer, so a
        model-authored proposal is judged against exactly the allowlist it always was.
        """
        static = frozenset(str(s).upper() for s in self._watchlist_fn())
        if producing_runtime != COPYTRADE_RUNTIME:
            return static
        return static | self._copy_universe(agent_job)

    def _copy_universe(self, agent_job: AgentJob | None = None) -> frozenset[str]:
        """The promoted copy universe, under THE BOUNDS THAT DRAFTED THE ORDER, or the EMPTY set.

        Same chain, same authority — and now the same BOUNDS. Re-deriving them from ``Settings``
        read the same audited entries through a different ``universe_ttl_days`` /
        ``max_universe_symbols``, so the submit-time allowlist could be strictly WIDER than the
        one that authorised the draft: a cycle run with ``universe_ttl_days=1`` drafted an NVDA
        order and, five days later, the drafting policy's universe was empty while the
        settings-derived one (TTL 120) still admitted NVDA. ``copytrade/universe.py`` names that
        hazard by name; the bounds are therefore recorded on the proposal's own lineage
        (``copytrade_orders.UNIVERSE_BOUNDS_REF``) and read back from it here.

        Fail closed in the only direction available here: no recorded bounds, or an unreadable or
        unverifiable promotion chain, yields nothing. That can refuse an order that should have
        passed but can never admit one that should not — including an order drafted before the
        bounds were recorded, which is the correct answer for a proposal whose drafting authority
        is unknown.
        """
        bounds = (agent_job.input_refs or {}).get(UNIVERSE_BOUNDS_REF) if agent_job else None
        if not isinstance(bounds, dict):
            log.warning("paper execution: the copy proposal records no universe bounds; SEC-15 "
                        "falls back to the static watchlist")
            return frozenset()
        try:
            drafting = replace(
                policy_from_settings(self.state.settings),
                universe_ttl_days=int(bounds["universe_ttl_days"]),
                max_universe_symbols=int(bounds["max_universe_symbols"]),
            )
            return frozenset(current_universe(self.state, drafting))
        except Exception as exc:  # noqa: BLE001 - an unreadable universe is an empty universe
            log.warning("paper execution: copy universe unavailable (%s: %s); "
                        "SEC-15 falls back to the static watchlist", type(exc).__name__, exc)
            return frozenset()

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

    # ---- RW-17: the two seams the zero-touch PAPER lane composes against -----------------
    # ``copytrade/autoexec.py`` adjudicates without a human but still has to satisfy gateway
    # gate (d), which re-runs risk against a FRESH quote and a FRESH positions snapshot
    # immediately before the broker call. Neither of those lives in the executor (it has no
    # broker and no quote seam, deliberately — it is not a market-data component), and neither
    # may be faked: an auto-executing lane that skipped gate (d) would be the single order path
    # with one of its gates removed for exactly the caller least entitled to it.
    #
    # So the lane borrows THIS service's re-check — the same ``_fresh_risk`` the human gate
    # uses, against the same allowlist, the same snapshot and the same quote. One
    # implementation of "is this order still acceptable right now", used by both approvers.

    def positions_snapshot(self):
        """The fresh reconciled positions handed to gate (d). Raises on a broker fault, which
        the gateway turns into a refusal — an order path that cannot see the account is one
        that must not place an order."""
        return self._broker.get_positions(st=self.state, clock=self.state.clock)

    def order_status(self, broker_order_id: str) -> dict:
        """RW-15 — the composed transport's order-status READER, injected into
        ``brokers.order_status_poll`` as its ``status_fn``.

        Deliberately a bare delegation with no gate of its own: this reads an order's status and
        can neither place nor amend one, and the poll validates every reply through the gateway's
        own ACK machinery before a single state moves. Raises when the transport cannot answer,
        which the poll turns into a named ``read_failed`` that leaves the order OPEN — the
        fail-closed direction, since an order left open only ever refuses more trading.
        """
        return self._broker.order_status(broker_order_id)

    def autoexec_risk_recheck(self, order):
        """``(order) -> callable(positions) -> verdict`` for the RW-17 lane's gate (d).

        FAILS CLOSED AS A VERDICT, NEVER AS AN EXCEPTION. This factory is called inside the
        executor's locked firing block; raising there would abort a whole ``resolve_due`` tick
        and leave the remaining tickets unresolved and uncounted. An order whose lineage cannot
        be loaded therefore comes back as a REJECTED verdict naming why, which the gateway
        refuses and the executor counts and notifies — the same outcome, reported instead of
        thrown.
        """
        try:
            bundle = self._load_bundle(order.id)
        except Exception as exc:            # noqa: BLE001 - an unreadable lineage is a rejection
            log.warning("paper execution: no lineage for auto-execution of order %s (%s: %s); "
                        "gate (d) will REJECT", getattr(order, "id", "?"),
                        type(exc).__name__, exc)

            def _rejected(_positions):
                return {"decision": "rejected",
                        "failed_checks": ["autoexec_lineage_unreadable"]}

            return _rejected

        def _recheck(_positions):
            result, _snapshot = self._fresh_risk(
                bundle.proposal.payload, bundle.order.symbol,
                bundle.agent_job.runtime, bundle.agent_job,
            )
            return result

        return _recheck

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

    def _fresh_risk(self, proposal_payload: dict, symbol: str, producing_runtime=None,
                    agent_job=None):
        try:
            snapshot = self._broker.get_positions(st=self.state, clock=self.state.clock)
            quote = self._quote_fn(symbol)
            ctx = build_risk_context(
                self.state, quote=quote,
                watchlist=self._watchlist(producing_runtime=producing_runtime,
                                          agent_job=agent_job),
            )
            return evaluate(proposal_payload, ctx), snapshot
        except Exception:  # noqa: BLE001 - any vendor/data fault means no order authority
            return {
                "decision": "rejected",
                "failed_checks": ["paper_broker_or_quote_unavailable"],
            }, None

    def _assert_producing_runtime(self, agent_job: AgentJob) -> None:
        """Who is allowed to have produced an order that a human may submit to the broker.

        Two admissible producers, and the difference between them is the whole reason this is
        a function rather than a set membership test:

          * THE CONFIGURED DECISION RUNTIME (``claude`` / ``codex``). Unchanged. The proposal
            must come from the runtime this deployment is currently configured to decide with,
            so flipping the provider switch cannot resurrect a stale proposal from the other
            one. This is the Invariant-2 / SEC-08 binding for MODEL-authored orders.
          * THE DETERMINISTIC COPY PRODUCER (``COPYTRADE_RUNTIME``). There is no model in its
            provenance at all, so "is it the configured DECISION runtime" is not a question
            that has an answer for it — and answering "no" meant a copy order could be drafted
            and then never adjudicated by the only wired human gate.

        The deterministic branch is admitted ONLY in ``paper``, re-checked here rather than
        relying on the ladder check further down, so the allowance cannot survive a future
        reordering of this method. SEC-08 is untouched and still bites twice above this layer:
        ``run_copy_cycle`` refuses to build anything in a live/auto rung, and
        ``OrderGateway`` re-runs ``assert_execution_runtime`` at submit with this same string.

        A job that did not finish is refused in both branches — an order whose producer
        crashed is an order whose provenance is a guess.
        """
        runtime = str(agent_job.runtime)
        if agent_job.status != "done":
            raise PaperExecutionError(
                "runtime_not_allowed",
                "paper execution requires a proposal from a run that completed",
            )
        if runtime == COPYTRADE_RUNTIME:
            if ModeLadder(self.state).current().value != "paper":
                raise PaperExecutionError(
                    "paper_mode_required",
                    "the deterministic copy path is paper-only (SEC-08)",
                )
            return
        selected_runtime = configured_decision_runtime(self.state.settings)
        if runtime not in {"claude", "codex"} or runtime != selected_runtime:
            raise PaperExecutionError(
                "runtime_not_allowed",
                "paper execution requires a successful proposal from the currently configured "
                f"decision runtime ({selected_runtime})",
            )

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
        self._assert_producing_runtime(agent_job)
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
            fresh, _snapshot = self._fresh_risk(proposal.payload, order.symbol,
                                                agent_job.runtime, agent_job)
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
            result, _snap = self._fresh_risk(proposal.payload, order.symbol,
                                             agent_job.runtime, agent_job)
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
