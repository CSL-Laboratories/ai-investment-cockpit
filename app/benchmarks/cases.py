"""Fixed-work samples for the paths every order and model output touch.

Iteration counts are the production (scale=1) workload. One call is one sample.
Counts are tuned so a full same-runner comparison stays well under a minute.
``COCKPIT_BENCH_SCALE`` shrinks the inner loops for unit tests; the CI comparer
sets it only from an explicit flag and refuses that flag on GitHub Actions.
"""
from __future__ import annotations

import os
from typing import Callable

# Inner-loop sizes at scale 1. Tuned on a Linux VM so one round of all cases
# is a few tenths of a second and ten samples a side stay under the job budget.
RISK_ITERS = 800
ORDER_ITERS = 7500
SCHEMA_ITERS = 40
SCRUB_ITERS = 600
AUDIT_APPENDS = 40

CASE_NAMES = (
    "risk_evaluate",
    "order_tuple_hash",
    "schema_validate_proposal",
    "outbound_scrub",
    "audit_append_verify",
)


def scaled(count: int) -> int:
    raw = os.environ.get("COCKPIT_BENCH_SCALE", "")
    if raw == "":
        return count
    try:
        scale = float(raw)
    except ValueError as exc:
        raise RuntimeError("COCKPIT_BENCH_SCALE must be a number") from exc
    if not 0 < scale <= 1:
        raise RuntimeError("COCKPIT_BENCH_SCALE must be in (0, 1]")
    return max(1, int(round(count * scale)))


def _risk_fixtures():
    from decimal import Decimal
    from types import SimpleNamespace

    from risk.engine import RiskContext

    decimal = Decimal
    verdict = SimpleNamespace(
        book_seq=7,
        computed_at_ns=1_000_000_000,
        verdict="ok",
        expected_cost_bps=decimal("4.5"),
        net_edge_state="sufficient",
    )
    ctx = RiskContext(
        max_notional=decimal("10000"),
        max_positions=10,
        concentration_pct=decimal("0.5"),
        daily_loss_limit=decimal("1000"),
        quote_staleness_s=30.0,
        max_spread_pct=decimal("0.02"),
        pending_exposure_cap=decimal("50000"),
        cash=decimal("50000"),
        buying_power=decimal("50000"),
        holdings={
            "AAPL": {"market_value": decimal("5000"), "qty": decimal("25"), "side": "long"},
            "MSFT": {"market_value": decimal("4000"), "qty": decimal("10"), "side": "long"},
            "NVDA": {"market_value": decimal("3000"), "qty": decimal("8"), "side": "long"},
            "AMZN": {"market_value": decimal("2500"), "qty": decimal("12"), "side": "long"},
        },
        open_positions=4,
        realized_loss_today=decimal("0"),
        pending_notional=decimal("0"),
        watchlist={"GOOG", "META"},
        quote_price=decimal("190"),
        quote_age_s=2.0,
        quote_spread_pct=decimal("0.001"),
        mode="paper",
        quant_verdict=verdict,
        require_quant_verdict=True,
        max_execution_cost_bps=decimal("100"),
        quant_book_seq=7,
        quant_verdict_now_ns=1_000_000_000 + 1_000,
        require_net_edge=True,
        pending_sell_quantities={},
    )
    buy = {
        "proposal_id": "p1",
        "symbol": "AAPL",
        "side": "buy",
        "asset_type": "equity",
        "order_type": "limit",
        "time_horizon": "short",
        "thesis": "t",
        "invalidation": "i",
        "sizing_basis": "fixed-fraction",
        "source_refs": [],
        "notional": "1000",
    }
    sell = {**buy, "proposal_id": "p2", "side": "sell", "quantity": "1", "notional": None}
    rules = (
        {
            "id": "r-aapl",
            "predicate": {"side": "buy"},
            "symbols_scope": ["AAPL", "MSFT"],
            "worst_case_notional": "5000",
        },
        {
            "id": "r-sell",
            "predicate": {"side": "sell"},
            "symbols_scope": ["AAPL"],
            "worst_case_notional": "5000",
        },
    )
    return buy, sell, ctx, rules


def risk_evaluate() -> None:
    """Inv 1 / Inv 5: ``risk.evaluate`` on a buy and a backed sell, including the
    execution-verdict and auto-rule branches."""
    from risk.engine import evaluate

    if not hasattr(risk_evaluate, "_fx"):
        risk_evaluate._fx = _risk_fixtures()  # type: ignore[attr-defined]
    buy, sell, ctx, rules = risk_evaluate._fx  # type: ignore[attr-defined]
    for _ in range(scaled(RISK_ITERS)):
        bought = evaluate(buy, ctx, rules)
        sold = evaluate(sell, ctx, rules)
        if bought["decision"] != "approved" or sold["decision"] != "approved":
            raise RuntimeError(
                "risk bench left the approved path: "
                f"buy={bought['decision']} {bought['failed_checks']} {bought['warnings']}; "
                f"sell={sold['decision']} {sold['failed_checks']} {sold['warnings']}"
            )


def order_tuple_hash() -> None:
    """SEC-07: canonical resolved-order hash checked at approval and submit."""
    from brokers.order_tuple import order_tuple_hash as hash_order

    fields = {
        "symbol": "aapl",
        "side": "buy",
        "qty": "10.50",
        "order_type": "limit",
        "limit_price": "190.25",
        "tif": "day",
    }
    for _ in range(scaled(ORDER_ITERS)):
        digest = hash_order(fields)
        if len(digest) != 64:
            raise RuntimeError("order tuple hash was not sha256")


def schema_validate_proposal() -> None:
    """Inv 3: ``jsonschema.validate`` on a TradeProposal, the call ``_validate`` makes."""
    import jsonschema

    from workflows.schema_loader import load_schema

    schema = load_schema("TradeProposal")
    body = {
        "proposal_id": "prop_01HZX3",
        "symbol": "AAPL",
        "side": "buy",
        "asset_type": "equity",
        "order_type": "limit",
        "time_horizon": "short",
        "thesis": "Beat-and-raise into the print; gap-fill setup holding VWAP.",
        "invalidation": "Close below 182.00 invalidates the continuation thesis.",
        "sizing_basis": "fixed-fraction: 1% account risk to the invalidation level",
        "source_refs": ["parquet:2026-07-06/AAPL#42", "parquet:2026-07-06/AAPL#57"],
        "quantity": 100,
        "notional": None,
        "generated_by": {"runtime": "claude", "model": "opus"},
    }
    for _ in range(scaled(SCHEMA_ITERS)):
        jsonschema.validate(body, schema)


_SCRUB_TEXT = (
    "AAPL held the 182 VWAP after the print. Cost basis notes stay local. "
    "Peer move: TSLA 500 shares on a rumor, not our book. "
    "Working level is $190.25 with 120 shares if the add is filled. "
    "Account 123456789 must not survive the scrub. "
) * 2


def outbound_scrub() -> None:
    """SEC-09: deterministic scrub of model free text before store, audit, or post."""
    from workflows.scrubber import scrub_text

    for _ in range(scaled(SCRUB_ITERS)):
        result = scrub_text(_SCRUB_TEXT, subject_symbols=frozenset({"AAPL"}))
        if not result.blocked or "[acct]" not in result.clean:
            raise RuntimeError("scrub bench missed the blocking account pattern")


class AuditAppendVerify:
    """Inv 6: HMAC append plus ``verify_chain`` / ``verify_entry`` on a fresh in-memory chain.

    Table creation and the between-sample wipe sit outside the timed call. Each
    sample uses a new ``AuditLog`` so the in-process erasure floor starts at zero.
    """

    def __init__(self) -> None:
        self._engine = None
        self._sf = None
        self._audit_log = None

    def _ensure(self) -> None:
        if self._engine is not None:
            return
        import sys
        import types
        from pathlib import Path

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from schemas.models.base import Base

        # storage/__init__.py imports the Parquet store (pyarrow). That import is
        # not on the append/verify path, and pulling it in would add a large wheel
        # to this job. Register the package first so storage.audit loads alone.
        storage_dir = Path(sys.modules["schemas.models.base"].__file__).resolve().parents[2] / "storage"
        if "storage" not in sys.modules:
            package = types.ModuleType("storage")
            package.__path__ = [str(storage_dir)]
            package.__package__ = "storage"
            sys.modules["storage"] = package
        from storage.audit import AuditLog

        self._audit_log = AuditLog
        self._engine = create_engine(
            "sqlite://",
            future=True,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self._engine)
        self._sf = sessionmaker(bind=self._engine, future=True, expire_on_commit=False)

    def reset(self) -> None:
        self._ensure()
        from sqlalchemy import delete

        from schemas.models.audit_entry import AuditEntry
        from storage.audit import audit_meta

        with self._engine.begin() as conn:
            conn.execute(delete(AuditEntry))
            conn.execute(delete(audit_meta))

    def __call__(self) -> None:
        self._ensure()
        log = self._audit_log(self._sf, hmac_key=b"bench-hmac-key")
        row = None
        for i in range(scaled(AUDIT_APPENDS)):
            row = log.append(
                "bench_event",
                actor="bench",
                refs={"symbol": "AAPL"},
                payload={"i": i, "symbol": "AAPL"},
            )
        result = log.verify_chain()
        if row is None or not result.ok or not log.verify_entry(row):
            raise RuntimeError(result.error or "audit bench failed")


audit_append_verify = AuditAppendVerify()

CASES: list[tuple[str, Callable[[], None]]] = [
    ("risk_evaluate", risk_evaluate),
    ("order_tuple_hash", order_tuple_hash),
    ("schema_validate_proposal", schema_validate_proposal),
    ("outbound_scrub", outbound_scrub),
    ("audit_append_verify", audit_append_verify),
]

if tuple(name for name, _fn in CASES) != CASE_NAMES:
    raise RuntimeError("CASE_NAMES drifted from CASES")
