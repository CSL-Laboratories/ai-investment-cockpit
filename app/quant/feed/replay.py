"""QT-03 — Parquet replay: normalized capture -> engine events.

THE HANDOFF FROM THE ITCH DECODER
-----------------------------------
The C decoder's job ends at a Parquet file with the columns in ``feed.REPLAY_COLUMNS``. This
module's job begins there. The schema is defined once, in ``feed/__init__.py``, so the writer
and the reader agree by construction rather than by comment — the usual failure of a
two-language pipeline is a column rename that only surfaces as a wrong number.

``exchange_ts`` and ``recv_ts`` are both REQUIRED columns. A capture that recorded only one
of them cannot support latency analysis and cannot be replayed honestly, so it is rejected
at load rather than back-filled: inventing ``recv_ts = exchange_ts`` would silently assert
zero wire latency, which is the single assumption most likely to manufacture a profitable
market-making backtest out of nothing.

WHY ``order_ref`` BEING NULL CHANGES WHAT THE RESULTS MEAN
------------------------------------------------------------
With per-order ids (ITCH has them) the book is reconstructed order by order and queue
position is EXACT. Without them — an aggregate L2 feed — the best available model is a single
anonymous block per level, and then a cancellation anywhere in that block advances our order
as if it had been ahead of us. That is optimistic, and optimistic in exactly the dimension
this engine exists to measure.

``ReplayFeed.provenance`` records which regime the dataset is in so a tearsheet can state it.
A queue-position number whose reliability is unstated is worse than no number, because it
will be quoted.

WRITE-ONCE, LIKE THE REST OF THE REPO
---------------------------------------
``storage/parquet_store.py`` already establishes write-once files with a ``content_hash`` and
a ``verify_snapshot`` re-read (Invariant 8: raw inputs stored separately, replayable). The
writer here follows the same discipline: refuse to overwrite, and hash what was written, so
"which data produced this tearsheet" has an answer that does not depend on the filesystem
mtime.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from quant.events import BookAction, BookUpdate, Event, Side, Trade
from quant.feed import REPLAY_COLUMNS, validate_ordering


class ReplaySchemaError(ValueError):
    """The dataset does not carry the columns the engine requires."""


@dataclass(frozen=True, slots=True)
class Provenance:
    """What the results from this dataset are allowed to claim."""

    path: str
    content_hash: str
    rows: int
    symbols: tuple[str, ...]
    has_order_refs: bool

    @property
    def queue_model(self) -> str:
        return "exact" if self.has_order_refs else "aggregate-optimistic"

    def caveat(self) -> str | None:
        """The sentence a tearsheet must print when queue positions are estimates."""
        if self.has_order_refs:
            return None
        return (
            "Queue positions are ESTIMATES: this dataset has no per-order ids, so each price "
            "level is modelled as one anonymous block and any cancellation within it advances "
            "our order as though it had been ahead. Fill rates and passive PnL are therefore "
            "biased OPTIMISTIC."
        )


@dataclass(slots=True)
class ReplayFeed:
    """Streams a normalized Parquet capture as engine events, in arrival order.

    Satisfies the ``Feed`` protocol, so the runner cannot tell it from ``SyntheticFeed``.
    """

    path: Path
    symbols: tuple[str, ...] = ()
    _provenance: Provenance | None = None

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    @property
    def provenance(self) -> Provenance | None:
        """Populated after the first full iteration. None before — a provenance record
        asserted before the data was read would be a claim about a file nobody opened."""
        return self._provenance

    def __iter__(self) -> Iterator[Event]:
        import pyarrow.parquet as pq

        table = pq.read_table(self.path)
        missing = [c for c in REPLAY_COLUMNS if c not in table.column_names]
        if missing:
            raise ReplaySchemaError(
                f"{self.path} is missing required column(s) {missing}. The normalized schema "
                "is REPLAY_COLUMNS in quant/feed/__init__.py — the decoder and the reader "
                f"share it so they cannot drift. Present: {table.column_names}"
            )

        rows = table.to_pylist()
        symbols = tuple(sorted({r["symbol"] for r in rows}))
        has_refs = any(r.get("order_ref") is not None for r in rows)
        self.symbols = self.symbols or symbols
        self._provenance = Provenance(
            path=str(self.path), content_hash=_hash_file(self.path), rows=len(rows),
            symbols=symbols, has_order_refs=has_refs,
        )
        yield from validate_ordering(_to_events(rows))


def _to_events(rows: Iterable[dict]) -> Iterator[Event]:
    """Row dicts -> typed events. ``ingest_ts`` is stamped HERE, at the decode boundary.

    That is what ``ingest_ts`` means: the instant OUR engine took delivery. Reading it from
    the file would record the original capture run's decode cost rather than this replay's,
    and the two are not the same measurement. Reconstructing it as ``recv_ts`` keeps a replay
    honest about being a replay — the decode latency of a historical run is a property of
    that run, not of this one.
    """
    for row in rows:
        base = {
            "symbol": row["symbol"],
            "seq": int(row["seq"]),
            "exchange_ts": int(row["exchange_ts"]),
            "recv_ts": int(row["recv_ts"]),
            "ingest_ts": int(row["recv_ts"]),
        }
        side = Side(row["side"])
        if row["kind"] == "trade":
            yield Trade(price_ticks=int(row["price_ticks"]), qty=int(row["qty"]),
                        aggressor=side, **base)
        elif row["kind"] == "book":
            ref = row.get("order_ref")
            yield BookUpdate(action=BookAction(row["action"]), side=side,
                             price_ticks=int(row["price_ticks"]), qty=int(row["qty"]),
                             order_ref=None if ref is None else int(ref), **base)
        else:
            raise ReplaySchemaError(
                f"unknown event kind {row['kind']!r}; expected 'book' or 'trade'"
            )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_replay(path: Path, events: Iterable[Event]) -> Provenance:
    """Write events to a normalized Parquet capture. WRITE-ONCE, like ``parquet_store``.

    Refuses to overwrite. A replay dataset that can be silently rewritten makes "which data
    produced this tearsheet" unanswerable, and the same discipline is already load-bearing
    for raw evidence elsewhere in the repo (Invariant 8).

    Primarily for turning a synthetic run into a fixture, and for round-trip testing the
    schema without needing a real capture in the repo.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = Path(path)
    if path.exists():
        raise FileExistsError(
            f"{path} already exists. Replay captures are write-once so a tearsheet's data "
            "provenance stays answerable; write a new path instead of overwriting."
        )

    columns: dict[str, list] = {name: [] for name in REPLAY_COLUMNS}
    symbols: set[str] = set()
    has_refs = False
    for event in events:
        symbols.add(event.symbol)
        columns["symbol"].append(event.symbol)
        columns["seq"].append(event.seq)
        columns["exchange_ts"].append(event.exchange_ts)
        columns["recv_ts"].append(event.recv_ts)
        if isinstance(event, Trade):
            columns["kind"].append("trade")
            columns["action"].append("")
            columns["side"].append(event.aggressor.value)
            columns["order_ref"].append(None)
        elif isinstance(event, BookUpdate):
            columns["kind"].append("book")
            columns["action"].append(event.action.value)
            columns["side"].append(event.side.value)
            columns["order_ref"].append(event.order_ref)
            has_refs = has_refs or event.order_ref is not None
        else:
            raise TypeError(f"cannot serialise {type(event).__name__} to a replay capture")
        columns["price_ticks"].append(event.price_ticks)
        columns["qty"].append(event.qty)

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({
            "symbol": pa.array(columns["symbol"], pa.string()),
            "seq": pa.array(columns["seq"], pa.int64()),
            "exchange_ts": pa.array(columns["exchange_ts"], pa.int64()),
            "recv_ts": pa.array(columns["recv_ts"], pa.int64()),
            "kind": pa.array(columns["kind"], pa.string()),
            "action": pa.array(columns["action"], pa.string()),
            "side": pa.array(columns["side"], pa.string()),
            "price_ticks": pa.array(columns["price_ticks"], pa.int64()),
            "qty": pa.array(columns["qty"], pa.int64()),
            "order_ref": pa.array(columns["order_ref"], pa.int64()),
        }),
        path,
    )
    return Provenance(
        path=str(path), content_hash=_hash_file(path), rows=len(columns["symbol"]),
        symbols=tuple(sorted(symbols)), has_order_refs=has_refs,
    )
