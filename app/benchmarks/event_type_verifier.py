"""Compare the frozen pre-RW24 verifier with the current production implementation.

From app/: python benchmarks/event_type_verifier.py --rounds 3 --output result.json
When copied elsewhere, pass --app-dir /path/to/repository/app. All database state is
generated in a temporary directory; no operator configuration or credentials are used.
Wall times describe this synthetic fixture, not an end-to-end triage service level.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import platform
import sqlite3
import statistics
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from types import SimpleNamespace

from sqlalchemy import event as sql_event, select


def _baseline_signed_event_type(state, event_id: str, *, session=None) -> str | None:
    """Frozen signed_event_type body from pre-RW24 budget.py (2026-09-07)."""
    if not event_id:
        return None
    with (nullcontext(session) if session is not None else state.session_factory()) as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_INGEST_BATCH)
        ).scalars().all()
    for e in entries:
        types = (e.payload or {}).get("event_types") or {}
        if event_id in types:
            if not state.audit.verify_entry(e):
                return None
            return str(types[event_id])
    return None


def _baseline_make_event_type_verifier(state):
    """Frozen make_event_type_verifier body; only its helper name is changed."""
    cache: dict = {}

    def _verify(event) -> bool:
        eid = getattr(event, "id", None)
        if eid not in cache:
            cache[eid] = _baseline_signed_event_type(state, eid)
        signed = cache[eid]
        return signed is not None and signed == getattr(event, "event_type", None)
    return _verify


def _fixture(directory: Path, ingest_rows: int, other_rows: int):
    engine = make_engine(f"sqlite:///{(directory / f'rows-{ingest_rows}.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    clock = FrozenClock(datetime(2026, 7, 7, 14, tzinfo=timezone.utc))
    audit = AuditLog(sf, b"isolated-rw24-benchmark-key", clock)
    with sf() as session, session.begin():
        for index in range(ingest_rows):
            last = audit.append_in(
                session, EVENT_INGEST_BATCH,
                payload={"event_types": {f"event-{index}": "filing"}},
            )
        for index in range(other_rows):
            last = audit.append_in(session, "benchmark_unrelated", payload={"index": index})
    audit.observe_committed(last)
    return SimpleNamespace(session_factory=sf, audit=audit, anchor_store=None), engine


def _sample(state, engine, factory, candidates):
    selects = 0

    def count_selects(_conn, _cursor, statement, _params, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    sql_event.listen(engine, "before_cursor_execute", count_selects)
    try:
        start = perf_counter()
        verify = factory(state)  # A fresh verifier lifetime for every sample.
        accepted = [verify(candidate) for candidate in candidates]
        elapsed_ms = (perf_counter() - start) * 1000
        if not all(accepted):
            raise AssertionError("valid fixture binding was refused")
        return elapsed_ms, selects
    finally:
        sql_event.remove(engine, "before_cursor_execute", count_selects)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--rows", type=int, nargs="+", default=[100, 1000])
    parser.add_argument("--candidates", type=int, nargs="+", default=[30, 100])
    parser.add_argument("--non-ingest-rows", type=int, default=0)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (min(args.rows + args.candidates) < 1 or args.rounds < 1 or args.warmups < 0
            or args.non_ingest_rows < 0 or max(args.candidates) > min(args.rows)):
        parser.error("positive row/candidate/round counts required; candidates must fit each ledger")
    if not (args.app_dir / "workflows" / "budget.py").is_file():
        parser.error("--app-dir must contain workflows/budget.py")
    return args


def main():
    args = _parse_args()
    sys.path.insert(0, str(args.app_dir.resolve()))
    # Direct storage construction avoids Settings, compose(), keyring, and any real state.
    global AuditEntry, EVENT_INGEST_BATCH, FrozenClock, AuditLog
    global make_engine, create_all, make_session_factory
    from core.clock import FrozenClock
    from schemas.models.audit_entry import AuditEntry
    from storage.audit import AuditLog
    from storage.engine import create_all, make_engine, make_session_factory
    from workflows.budget import make_event_type_verifier
    from workflows.constants import EVENT_INGEST_BATCH

    measurements = []
    factories = {
        "frozen_pre_rw24": _baseline_make_event_type_verifier,
        "production": make_event_type_verifier,
    }
    with TemporaryDirectory(prefix="rw24-binding-benchmark-") as temporary:
        for rows in args.rows:
            state, engine = _fixture(Path(temporary), rows, args.non_ingest_rows)
            try:
                for count in args.candidates:
                    candidates = [
                        SimpleNamespace(
                            id=f"event-{index * (rows - 1) // max(1, count - 1)}",
                            event_type="filing",
                        ) for index in range(count)
                    ]
                    samples = {name: [] for name in factories}
                    for iteration in range(args.warmups + args.rounds):
                        order = list(factories)
                        if iteration % 2:
                            order.reverse()  # Avoid giving one variant every cold first run.
                        for name in order:
                            result = _sample(state, engine, factories[name], candidates)
                            if iteration >= args.warmups:
                                samples[name].append(result)
                    for name, values in samples.items():
                        measurements.append({
                            "ingest_rows": rows,
                            "non_ingest_rows": args.non_ingest_rows,
                            "candidates": count,
                            "variant": name,
                            "median_ms": round(statistics.median(v[0] for v in values), 3),
                            "samples_ms": [round(v[0], 3) for v in values],
                            "select_counts": [v[1] for v in values],
                        })
            finally:
                engine.dispose()

    baseline_source = (inspect.getsource(_baseline_signed_event_type)
                       + inspect.getsource(_baseline_make_event_type_verifier))
    production_file = Path(inspect.getfile(make_event_type_verifier))
    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
            "sqlalchemy": version("sqlalchemy"),
        },
        "method": {
            "rounds": args.rounds,
            "warmups_per_scenario": args.warmups,
            "order": "alternating",
            "fixture": "temporary SQLite WAL; one signed binding per ingest row",
            "external_anchor": "not wired; witness I/O is excluded",
            "lifetime": "fresh verifier for every warmup and timed sample",
            "baseline_sha256": hashlib.sha256(baseline_source.encode()).hexdigest(),
            "production_budget_sha256": hashlib.sha256(production_file.read_bytes()).hexdigest(),
        },
        "measurements": measurements,
    }
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
