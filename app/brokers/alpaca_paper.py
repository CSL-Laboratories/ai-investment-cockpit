"""WP7 — the Alpaca paper-trading broker adapter (SEC-03).

``AlpacaPaperBroker`` is the concrete broker seam the ``OrderGateway`` holds PRIVATELY. Its
HTTP client is INJECTABLE (``client=`` in ``__init__``); the default is a small in-process
``FakeAlpaca`` simulator so unit tests need no network and no credentials. The live adapter
would swap in an ``alpaca-py`` REST client behind the same ``submit_order`` /
``get_account`` / ``get_positions`` surface — this file never opens a socket.

``submit_order(canonical_tuple)`` accepts ONLY the canonical resolved-order tuple from
``brokers.order_tuple`` (the gateway computes it), so the adapter can never be handed an
un-validated order. ``get_account`` / ``get_positions`` produce a ``PortfolioSnapshot``; when
an ``OrchestratorState`` is supplied they persist it through the audited state-change path
(Inv 6).
"""
from __future__ import annotations

from decimal import Decimal

from core.clock import default_clock
from core.ids import new_id
from schemas.models.portfolio_snapshot import PortfolioSnapshot

EVENT_PORTFOLIO_SNAPSHOT = "portfolio_snapshot"


class FakeAlpaca:
    """Deterministic in-process stand-in for the Alpaca paper REST API. No network.

    Orders are ACKNOWLEDGED (``accepted``, unfilled) rather than instantly filled, so the
    order-lifecycle FSM (submitted -> partially_filled/filled) is exercised by explicit
    downstream fill events rather than hidden inside the adapter. Account/positions return a
    fixed, seedable snapshot."""

    def __init__(self, account: str = "paper", cash="100000.00", buying_power="200000.00",
                 holdings=None, positions=None) -> None:
        self.account = account
        self.cash = cash
        self.buying_power = buying_power
        self.holdings = holdings if holdings is not None else []
        self.positions = positions if positions is not None else []
        self.submitted: list[dict] = []

    def submit_order(self, canonical: dict) -> dict:
        oid = new_id("alp")
        self.submitted.append({"broker_order_id": oid, **canonical})
        return {
            "broker_order_id": oid,
            "status": "accepted",
            "filled_qty": Decimal("0"),
            "avg_fill_price": None,
        }

    def account_info(self) -> dict:
        return {"account": self.account, "cash": self.cash,
                "buying_power": self.buying_power, "holdings": list(self.holdings)}

    def positions_info(self) -> list:
        return list(self.positions)


class AlpacaPaperBroker:
    def __init__(self, client=None) -> None:
        # Injectable seam; default a no-network simulator (SEC-03: a private, in-process seam).
        self._client = client if client is not None else FakeAlpaca()

    def submit_order(self, canonical_tuple: dict) -> dict:
        """Submit the canonical resolved-order tuple. Returns
        ``{broker_order_id, status, filled_qty, avg_fill_price}``."""
        return self._client.submit_order(canonical_tuple)

    def _snapshot(self, *, clock) -> PortfolioSnapshot:
        info = self._client.account_info()
        now = (clock or default_clock).now()
        return PortfolioSnapshot(
            id=new_id("snap"),
            account=info.get("account", "paper"),
            cash=info.get("cash", "0"),
            buying_power=info.get("buying_power", "0"),
            exposure=info.get("exposure"),
            holdings=info.get("holdings", []),
            open_orders=info.get("open_orders", []),
            ts=now,
            created_at=now,
        )

    def get_account(self, *, st=None, clock=None) -> PortfolioSnapshot:
        """Fetch the account snapshot. When ``st`` is supplied, persist it through the audited
        state-change path (Inv 6); otherwise return an unpersisted row."""
        snap = self._snapshot(clock=clock)
        if st is not None:
            st.audit.record_state_change(snap, EVENT_PORTFOLIO_SNAPSHOT, actor="alpaca_paper",
                                         payload={"account": snap.account})
        return snap

    def get_positions(self, *, st=None, clock=None) -> PortfolioSnapshot:
        """Fetch positions as a snapshot (holdings populated from the client). Persisted
        through the audited path when ``st`` is supplied."""
        snap = self._snapshot(clock=clock)
        snap.holdings = self._client.positions_info()
        if st is not None:
            st.audit.record_state_change(snap, EVENT_PORTFOLIO_SNAPSHOT, actor="alpaca_paper",
                                         payload={"account": snap.account, "kind": "positions"})
        return snap
