"""QT-03 — feed gates: point-in-time ordering, determinism, and replay provenance.

The properties pinned:

  * EVENTS ARE ORDERED BY ARRIVAL (``recv_ts``), NEVER BY EXCHANGE STAMP. Ordering by the
    venue's stamp lets a strategy react to a book state that had not yet reached it — look-
    ahead measured in microseconds, which at market-making frequency is the entire edge.
  * OUT-OF-ORDER DATA RAISES rather than being silently sorted. Reordering a capture hides a
    defect in the pipeline whose latency the engine exists to characterise.
  * THE SYNTHETIC FEED IS DETERMINISTIC and internally consistent: it never emits a cancel
    for an order it did not add, and never leaves its own book crossed.
  * A REPLAY DATASET DECLARES WHAT ITS NUMBERS MEAN. Without per-order ids, queue positions
    are estimates biased optimistic, and the provenance record says so.
"""
from __future__ import annotations

import pytest

from quant.book.lob import LimitOrderBook
from quant.events import BookAction, BookUpdate, Side, Trade
from quant.feed import REPLAY_COLUMNS, FeedOrderingError, validate_ordering
from quant.feed.replay import ReplayFeed, ReplaySchemaError, write_replay
from quant.feed.synthetic import SyntheticFeed, SyntheticParams
from quant.runner import apply_market_event


def _update(seq: int, recv: int, *, exchange: int | None = None, symbol: str = "S") -> BookUpdate:
    exchange = recv - 100 if exchange is None else exchange
    return BookUpdate(symbol=symbol, seq=seq, exchange_ts=exchange, recv_ts=recv,
                      ingest_ts=recv + 10, action=BookAction.ADD, side=Side.BUY,
                      price_ticks=100, qty=100, order_ref=seq)


# ---- point-in-time ordering ---------------------------------------------------------------


def test_in_order_events_pass_through_untouched():
    events = [_update(1, 1_000), _update(2, 2_000), _update(3, 3_000)]
    assert list(validate_ordering(events)) == events


def test_arrival_order_regression_raises():
    with pytest.raises(FeedOrderingError, match="before the previous"):
        list(validate_ordering([_update(1, 2_000), _update(2, 1_000)]))


def test_ordering_is_judged_on_recv_ts_not_exchange_ts():
    """THE point-in-time test. These two events arrive in order but were stamped by the
    venue out of order — which is ordinary clock skew and must NOT be an error. If this
    raised, callers would 'fix' it by sorting on exchange_ts, which is the look-ahead."""
    events = [
        _update(1, 1_000, exchange=900),
        _update(2, 2_000, exchange=800),      # earlier exchange stamp, later arrival
    ]
    assert list(validate_ordering(events)) == events


def test_a_sequence_gap_backwards_raises_because_absence_is_invisible_in_time():
    with pytest.raises(FeedOrderingError, match="went backwards"):
        list(validate_ordering([_update(5, 1_000), _update(4, 2_000)]))


def test_sequence_is_checked_per_symbol_not_globally():
    """Sequence numbers are per-symbol on real venues; a global check would fail on any
    multi-symbol capture."""
    events = [_update(10, 1_000, symbol="AAA"), _update(1, 2_000, symbol="BBB")]
    assert list(validate_ordering(events)) == events


def test_validation_raises_at_the_offending_event_not_after_the_whole_file():
    stream = validate_ordering([_update(1, 1_000), _update(2, 500), _update(3, 9_000)])
    assert next(stream).seq == 1
    with pytest.raises(FeedOrderingError):
        next(stream)


# ---- the synthetic feed ----------------------------------------------------------------------


def test_the_synthetic_feed_is_deterministic_from_its_seed():
    """Two runs must be byte-identical, so a strategy change is measurable as a diff rather
    than as the difference of two noisy averages."""
    a = list(SyntheticFeed(symbols=("S",), n_events=300, seed=11))
    b = list(SyntheticFeed(symbols=("S",), n_events=300, seed=11))
    assert a == b


def test_different_seeds_produce_different_streams():
    """CONTROL: a generator that ignored its seed would pass the determinism test."""
    a = list(SyntheticFeed(symbols=("S",), n_events=300, seed=11))
    b = list(SyntheticFeed(symbols=("S",), n_events=300, seed=12))
    assert a != b


def test_the_synthetic_feed_satisfies_its_own_ordering_rule():
    """A feed that violated point-in-time ordering would make every downstream test vacuous."""
    assert len(list(validate_ordering(SyntheticFeed(symbols=("S",), n_events=2_000, seed=5)))) == 2_000


def test_the_synthetic_feed_models_three_distinct_timestamps():
    """Setting them equal would be easier and would make every latency test in the suite
    vacuous — the feed would guarantee the property the engine is supposed to measure."""
    events = list(SyntheticFeed(symbols=("S",), n_events=200, seed=5))
    assert all(e.exchange_ts < e.recv_ts < e.ingest_ts for e in events)
    assert len({e.feed_latency_ns for e in events}) > 1      # jittered, not a constant
    assert len({e.decode_latency_ns for e in events}) > 1


def test_the_synthetic_feed_never_cancels_an_order_it_did_not_add():
    """It keeps a REAL book, not an approximate shadow. A shadow drifts out of step with its
    own events and emits phantom cancels indistinguishable from genuine dropped messages."""
    added: set[int] = set()
    for event in SyntheticFeed(symbols=("S",), n_events=3_000, seed=9):
        if isinstance(event, BookUpdate):
            if event.action is BookAction.ADD:
                added.add(event.order_ref)
            elif event.action is BookAction.CANCEL:
                assert event.order_ref in added
                added.discard(event.order_ref)


def test_replaying_the_synthetic_feed_leaves_an_uncrossed_book_with_bounded_depth():
    """Both properties matter. A permanently crossed book makes every mid and reservation
    price downstream meaningless; unbounded depth pins the touch so realized volatility
    collapses toward zero while the latent fair value walks normally."""
    book = LimitOrderBook("S")
    desyncs = 0
    for event in SyntheticFeed(symbols=("S",), n_events=6_000, seed=4):
        try:
            apply_market_event(book, event)
        except KeyError:
            desyncs += 1
    book.check_integrity()
    assert not book.is_crossed
    assert desyncs == 0
    assert 5 <= len(book) <= 200


def test_the_synthetic_feed_produces_trades_adds_and_cancels():
    """A generator that only ever added would leave the matcher untested by this feed."""
    kinds = {"add": 0, "cancel": 0, "trade": 0}
    for event in SyntheticFeed(symbols=("S",), n_events=3_000, seed=6):
        if isinstance(event, Trade):
            kinds["trade"] += 1
        elif event.action is BookAction.ADD:
            kinds["add"] += 1
        elif event.action is BookAction.CANCEL:
            kinds["cancel"] += 1
    assert all(count > 0 for count in kinds.values()), kinds


def test_multi_symbol_generation_keeps_per_symbol_sequences():
    events = list(SyntheticFeed(symbols=("AAA", "BBB"), n_events=500, seed=3))
    assert {e.symbol for e in events} == {"AAA", "BBB"}
    for symbol in ("AAA", "BBB"):
        seqs = [e.seq for e in events if e.symbol == symbol]
        assert seqs == sorted(seqs)
        assert seqs == list(range(1, len(seqs) + 1))


def test_a_quiet_generator_still_stabilises_depth():
    feed = SyntheticFeed(symbols=("S",), n_events=4_000, seed=8,
                         params=SyntheticParams(target_resting_orders=15))
    book = LimitOrderBook("S")
    for event in feed:
        apply_market_event(book, event)
    assert len(book) <= 60


# ---- replay round trip ------------------------------------------------------------------------


def test_a_synthetic_run_round_trips_through_the_parquet_schema(tmp_path):
    """The handoff from the C decoder is this schema. Round-tripping it here is what keeps
    the writer and reader honest without needing a real capture committed to the repo."""
    pytest.importorskip("pyarrow")
    original = list(SyntheticFeed(symbols=("S",), n_events=400, seed=2))
    path = tmp_path / "capture.parquet"
    provenance = write_replay(path, original)

    assert provenance.rows == len(original)
    assert provenance.has_order_refs
    assert provenance.queue_model == "exact"
    assert provenance.caveat() is None

    replayed = list(ReplayFeed(path=path))
    assert len(replayed) == len(original)
    for before, after in zip(original, replayed):
        assert type(before) is type(after)
        assert (before.symbol, before.seq) == (after.symbol, after.seq)
        assert before.exchange_ts == after.exchange_ts
        assert before.recv_ts == after.recv_ts
        assert before.price_ticks == after.price_ticks
        assert before.qty == after.qty


def test_replay_restamps_ingest_ts_because_decode_cost_belongs_to_THIS_run(tmp_path):
    pytest.importorskip("pyarrow")
    original = list(SyntheticFeed(symbols=("S",), n_events=50, seed=2))
    path = tmp_path / "capture.parquet"
    write_replay(path, original)
    replayed = list(ReplayFeed(path=path))
    assert all(e.ingest_ts == e.recv_ts for e in replayed)
    assert any(e.ingest_ts != e.recv_ts for e in original)


def test_a_capture_without_order_refs_declares_its_queue_numbers_are_estimates(tmp_path):
    """A queue-position number whose reliability is unstated is worse than no number,
    because it will be quoted."""
    pytest.importorskip("pyarrow")
    trades = [
        Trade(symbol="S", seq=i, exchange_ts=1_000 + i, recv_ts=1_100 + i, ingest_ts=1_110 + i,
              price_ticks=100, qty=100, aggressor=Side.BUY)
        for i in range(10)
    ]
    path = tmp_path / "aggregate.parquet"
    provenance = write_replay(path, trades)
    assert not provenance.has_order_refs
    assert provenance.queue_model == "aggregate-optimistic"
    assert "OPTIMISTIC" in provenance.caveat()


def test_replay_captures_are_write_once(tmp_path):
    pytest.importorskip("pyarrow")
    path = tmp_path / "capture.parquet"
    write_replay(path, list(SyntheticFeed(symbols=("S",), n_events=20, seed=1)))
    with pytest.raises(FileExistsError, match="write-once"):
        write_replay(path, list(SyntheticFeed(symbols=("S",), n_events=20, seed=1)))


def test_a_dataset_missing_a_required_column_is_refused_with_the_column_named(tmp_path):
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "bad.parquet"
    pq.write_table(pa.table({"symbol": ["S"], "seq": [1]}), path)
    with pytest.raises(ReplaySchemaError, match="missing required column"):
        list(ReplayFeed(path=path))


def test_the_content_hash_changes_with_the_data(tmp_path):
    """Provenance must actually identify the dataset, or 'which data produced this
    tearsheet' has an answer that is the same for every tearsheet."""
    pytest.importorskip("pyarrow")
    one = write_replay(tmp_path / "a.parquet", list(SyntheticFeed(n_events=30, seed=1)))
    two = write_replay(tmp_path / "b.parquet", list(SyntheticFeed(n_events=30, seed=2)))
    assert one.content_hash != two.content_hash
    assert len(one.content_hash) == 64


def test_the_replay_schema_is_declared_in_exactly_one_place():
    """The decoder writing Parquet and the reader consuming it share REPLAY_COLUMNS, so a
    column rename cannot become a wrong number in a two-language pipeline."""
    assert "exchange_ts" in REPLAY_COLUMNS and "recv_ts" in REPLAY_COLUMNS
    assert "order_ref" in REPLAY_COLUMNS
