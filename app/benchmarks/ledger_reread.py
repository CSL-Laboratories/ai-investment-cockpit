"""Time full-ledger re-reads. Measurement only; production behavior is unchanged.

From app/:

    python benchmarks/ledger_reread.py
    python benchmarks/ledger_reread.py --rows 10000 100000 --samples 21 --output result.json

Times ``DeepBudget.try_consume``, ``compute_gate``, and ``GET /status`` against a
synthetic HMAC audit ledger of the requested sizes. Reports nearest-rank p50 and
p95, the same index ``benchmarks.compare`` uses for quartiles.

This script is not imported by ``benchmarks/cases.py`` and it is not under pytest's
``testpaths``, so neither the pull-request hot-path job nor ``python -m pytest``
runs it. A 100k-row ledger is too large for that suite.

Wall times describe this fixture on this host. They are not a service-level objective.
No operator configuration, credentials, network, or broker is used.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import inspect
import json
import os
import platform
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

# Same nearest-rank index as benchmarks.compare.rel_iqr (round(fraction * (n - 1))).
P50 = 0.50
P95 = 0.95

# Historical escalation day. The timed try_consume uses a different day and an
# explicit limit of 0, so these rows are scanned and none of them are same-day spend.
_HISTORICAL_DAY = "2020-01-02"
_CALL_DAY = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)
_BENCH_KEY = b"ledger-reread-bench"
_API_KEY = "ledger-reread-bench-key"
_SEED_BATCH = 2000


def nearest_rank(samples: list[float], fraction: float) -> float:
    """Nearest-rank percentile. Index is ``round(fraction * (n - 1))``."""
    if not samples:
        raise ValueError("no samples")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be in [0, 1]")
    ordered = sorted(samples)
    return ordered[int(round(fraction * (len(ordered) - 1)))]


def _check_percentiles() -> None:
    """Lock the p50/p95 index so a drift in the formula fails this script."""
    samples = [float(i) for i in range(1, 22)]
    if nearest_rank(samples, P50) != 11.0 or statistics.median(samples) != 11.0:
        raise RuntimeError("p50 nearest-rank drifted from the 21-sample median")
    if nearest_rank(samples, P95) != 20.0:
        raise RuntimeError("p95 nearest-rank drifted")


def _scrub_cockpit_env() -> None:
    """Drop ambient operator config so the fixture uses Settings defaults."""
    for name in list(os.environ):
        if name.startswith("COCKPIT_"):
            del os.environ[name]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--rows", type=int, nargs="+", default=[10_000, 100_000])
    parser.add_argument("--samples", type=int, default=21)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.samples < 1 or args.warmups < 0 or min(args.rows) < 1:
        parser.error("rows and samples must be >= 1 and warmups must be >= 0")
    if len(set(args.rows)) != len(args.rows):
        parser.error("row counts must be unique")
    if not (args.app_dir / "workflows" / "budget.py").is_file():
        parser.error("--app-dir must contain workflows/budget.py")
    return args


def _row_spec(index: int, deep: str, notification: str, research: str) -> tuple[str, dict, dict]:
    """Cycle the three event types the measured calls actually select."""
    token = f"row-{index}"
    kind = index % 3
    if kind == 0:
        return (
            deep,
            {"event_id": token},
            {
                "date": _HISTORICAL_DAY,
                "event_id": token,
                "symbol": "AAPL",
                "reason": "quote_move:4.0",
                "corroborated": True,
            },
        )
    if kind == 1:
        return (
            notification,
            {"symbol": "AAPL"},
            {"delivered": False, "symbol": "AAPL", "fill_id": token},
        )
    return (
        research,
        {"claim_id": token},
        {"claim_id": token, "delivered": False, "empirical_pass_claimed": False},
    )


def _seed(audit, session_factory, rows: int, deep: str, notification: str, research: str) -> dict[str, int]:
    counts = {deep: 0, notification: 0, research: 0}
    index = 0
    started = time.perf_counter()
    last = None
    while index < rows:
        end = min(index + _SEED_BATCH, rows)
        with session_factory() as session, session.begin():
            while index < end:
                event_type, refs, payload = _row_spec(index, deep, notification, research)
                last = audit.append_in(
                    session, event_type, actor="bench", refs=refs, payload=payload,
                )
                counts[event_type] += 1
                index += 1
        audit.observe_committed(last)
        print(
            f"seeded {index}/{rows} in {time.perf_counter() - started:.1f}s",
            file=sys.stderr,
            flush=True,
        )
    return counts


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _measure_size(rows: int, samples: int, warmups: int) -> list[dict]:
    from datetime import date

    from fastapi.testclient import TestClient

    from config.settings import Settings
    from core.clock import FrozenClock
    from orchestrator.app import create_app
    from orchestrator.auth import API_KEY_HEADER, ApiKeyAuth
    from orchestrator.gate import GateThresholds, compute_gate
    from orchestrator.state import EVENT_PAPER_NOTIFICATION, OrchestratorState
    from storage.engine import create_all, make_engine, make_session_factory
    from workflows.budget import DeepBudget
    from workflows.constants import EVENT_DEEP_ESCALATION, EVENT_RESEARCH_UPDATE

    clock = FrozenClock(_CALL_DAY)
    call_day = date(2026, 7, 7)
    with TemporaryDirectory(prefix="ledger-reread-") as temporary:
        engine = make_engine(f"sqlite:///{(Path(temporary) / 'ledger.db').as_posix()}")
        try:
            create_all(engine)
            session_factory = make_session_factory(engine)
            state = OrchestratorState.build(
                session_factory,
                hmac_key=_BENCH_KEY,
                settings=Settings(_env_file=None),
                clock=clock,
            )
            counts = _seed(
                state.audit,
                session_factory,
                rows,
                EVENT_DEEP_ESCALATION,
                EVENT_PAPER_NOTIFICATION,
                EVENT_RESEARCH_UPDATE,
            )
            checked = state.audit.verify_chain()
            if not checked.ok or checked.head_seq != rows:
                raise RuntimeError(checked.error or f"fixture head {checked.head_seq} != {rows}")

            budget = DeepBudget(state, limit=0)
            thresholds = GateThresholds()
            app = create_app(state, auth=ApiKeyAuth(key_provider=lambda: _API_KEY))
            headers = {API_KEY_HEADER: _API_KEY}

            def time_try_consume() -> None:
                # limit=0 still verifies the chain and re-reads every deep-escalation
                # row, then returns False. The ledger size stays exactly ``rows``.
                consumed = budget.try_consume(
                    day=call_day,
                    event_id="bench-probe",
                    symbol="AAPL",
                    reason="quote_move:4.0",
                    corroborated=True,
                )
                if consumed is not False:
                    raise RuntimeError("try_consume appended a row; ledger size moved")

            def time_compute_gate() -> None:
                result = compute_gate(state, "auto_veto", now=_CALL_DAY, thresholds=thresholds)
                metrics = result.metrics
                if result.unlockable or metrics.get("chain_ok") is not True or metrics.get("anchor_ok") is not False:
                    raise RuntimeError(
                        "compute_gate left the post-scan anchor fail-closed path: "
                        f"unlockable={result.unlockable} reasons={result.reasons} metrics={metrics}"
                    )

            def time_status(client: TestClient) -> None:
                response = client.get("/status", headers=headers)
                if response.status_code != 200:
                    raise RuntimeError(f"/status returned {response.status_code}: {response.text[:300]}")
                body = response.json()
                chain = body.get("audit_chain") or {}
                notifications = (body.get("paper_fill_notifications") or {}).get("total")
                research = (body.get("research_update") or {}).get("total")
                if (
                    chain.get("ok") is not True
                    or chain.get("head_seq") != rows
                    or notifications != counts[EVENT_PAPER_NOTIFICATION]
                    or research != counts[EVENT_RESEARCH_UPDATE]
                ):
                    raise RuntimeError(
                        "/status did not report the seeded ledger: "
                        f"chain={chain} notifications={notifications} research={research}"
                    )

            names = ("DeepBudget.try_consume", "compute_gate", "GET /status")
            recorded: dict[str, list[float]] = {name: [] for name in names}
            with TestClient(app) as client:
                def invoke(name: str) -> None:
                    if name == "DeepBudget.try_consume":
                        time_try_consume()
                    elif name == "compute_gate":
                        time_compute_gate()
                    else:
                        time_status(client)

                for iteration in range(warmups + samples):
                    order = names[iteration % 3:] + names[: iteration % 3]
                    for name in order:
                        gc.collect()
                        started = time.perf_counter()
                        invoke(name)
                        elapsed = time.perf_counter() - started
                        if iteration >= warmups:
                            recorded[name].append(elapsed)
                    if iteration >= warmups:
                        print(
                            f"rows={rows} sample {iteration - warmups + 1}/{samples}",
                            file=sys.stderr,
                            flush=True,
                        )
            head_seq, _head_hash = state.audit.head()
            if head_seq != rows:
                raise RuntimeError(f"ledger grew during timing: head {head_seq} != {rows}")
        finally:
            engine.dispose()

    measurements = []
    for name in names:
        values = recorded[name]
        measurements.append({
            "rows": rows,
            "operation": name,
            "samples": len(values),
            "p50_ms": round(nearest_rank(values, P50) * 1000, 3),
            "p95_ms": round(nearest_rank(values, P95) * 1000, 3),
            "median_ms": round(statistics.median(values) * 1000, 3),
            "samples_ms": [round(value * 1000, 3) for value in values],
            "deep_escalation_rows": counts[EVENT_DEEP_ESCALATION],
            "paper_notification_rows": counts[EVENT_PAPER_NOTIFICATION],
            "research_update_rows": counts[EVENT_RESEARCH_UPDATE],
        })
    return measurements


def _markdown_table(measurements: list[dict]) -> str:
    lines = [
        "| Ledger rows | Operation | p50 (ms) | p95 (ms) |",
        "| ---: | --- | ---: | ---: |",
    ]
    for row in measurements:
        lines.append(
            f"| {row['rows']} | {row['operation']} | {row['p50_ms']:.3f} | {row['p95_ms']:.3f} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _check_percentiles()
    _scrub_cockpit_env()
    app_dir = args.app_dir.resolve()
    sys.path.insert(0, str(app_dir))
    from importlib.metadata import version

    from orchestrator.app import create_app
    from orchestrator.gate import compute_gate
    from workflows.budget import DeepBudget

    measurements: list[dict] = []
    for rows in args.rows:
        print(f"measuring {rows} ledger rows", file=sys.stderr, flush=True)
        measurements.extend(_measure_size(rows, args.samples, args.warmups))

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "sqlite": sqlite3.sqlite_version,
            "sqlalchemy": version("sqlalchemy"),
        },
        "method": {
            "rows": list(args.rows),
            "samples": args.samples,
            "warmups_per_size": args.warmups,
            "percentile": "nearest-rank index round(p*(n-1)), same as benchmarks.compare.rel_iqr",
            "order": "rotated each sample so no call is always first",
            "gc": "gc.collect() before each timed call",
            "fixture": (
                "temporary SQLite WAL; exactly N signed audit rows cycling "
                "deep_escalation / paper_fill_notification / research_update; "
                "market_event table empty; external anchor not wired"
            ),
            "try_consume": (
                "DeepBudget(limit=0).try_consume(corroborated=True) on 2026-07-07. "
                "Verifies the full chain and re-reads every deep_escalation row, "
                "then returns False without appending."
            ),
            "compute_gate": (
                "compute_gate(auto_veto) with no anchor store. Both full-ledger "
                "scans run, then the call fail-closes on the missing anchor."
            ),
            "status": (
                "Authenticated GET /status through FastAPI TestClient. Includes "
                "ModeLadder.current verify_chain, the handler verify_chain, and "
                "verify_entry on every paper-notification and research-update row."
            ),
            "behavior_changes": "none",
            "sources_sha256": {
                "workflows/budget.py": _sha256(Path(inspect.getfile(DeepBudget))),
                "orchestrator/gate.py": _sha256(Path(inspect.getfile(compute_gate))),
                "orchestrator/app.py": _sha256(Path(inspect.getfile(create_app))),
            },
        },
        "measurements": measurements,
        "table": _markdown_table(measurements),
    }
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    print(result["table"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
