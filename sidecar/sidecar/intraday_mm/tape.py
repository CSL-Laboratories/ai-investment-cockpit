"""Quote and trade tape, including a parquet round-trip for replay."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Event:
    ts_ms: int
    kind: str
    symbol: str
    bid: float | None = None
    ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    price: float | None = None
    size: float | None = None


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "pyarrow is required to record a session. Install the sidecar package dependencies."
        ) from exc
    return pa, pq


def write_tape(path: Path, events: list[Event]) -> None:
    pa, pq = _pyarrow()
    rows = [
        {
            "ts_ms": int(event.ts_ms),
            "kind": event.kind,
            "symbol": event.symbol,
            "bid": event.bid,
            "ask": event.ask,
            "bid_size": event.bid_size,
            "ask_size": event.ask_size,
            "price": event.price,
            "size": event.size,
        }
        for event in events
    ]
    schema = pa.schema(
        [
            ("ts_ms", pa.int64()),
            ("kind", pa.string()),
            ("symbol", pa.string()),
            ("bid", pa.float64()),
            ("ask", pa.float64()),
            ("bid_size", pa.float64()),
            ("ask_size", pa.float64()),
            ("price", pa.float64()),
            ("size", pa.float64()),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def read_tape(path: Path) -> list[Event]:
    _pa, pq = _pyarrow()
    table = pq.read_table(path)
    events = []
    for row in table.to_pylist():
        events.append(
            Event(
                ts_ms=int(row["ts_ms"]),
                kind=str(row["kind"]),
                symbol=str(row["symbol"]),
                bid=row["bid"],
                ask=row["ask"],
                bid_size=row["bid_size"],
                ask_size=row["ask_size"],
                price=row["price"],
                size=row["size"],
            )
        )
    events.sort(key=lambda event: (event.ts_ms, 0 if event.kind == "quote" else 1))
    return events
