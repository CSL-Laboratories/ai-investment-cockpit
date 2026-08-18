"""RW-19 deterministic tests for read-only event-source evidence."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime

import pytest

from ingest.characterization import (
    characterize_news_reconnect,
    characterize_market_frames,
    characterize_identity_records,
    compare_bar_feeds,
    compare_identity_surfaces,
    regular_session_bounds,
    verify_evidence_manifest,
    write_bar_evidence,
    write_capability_evidence,
    write_market_stream_evidence,
    write_news_evidence,
    write_news_reconnect_evidence,
)


def _bar(ts, opened, closed, volume):
    return {"t": ts, "o": opened, "c": closed, "v": volume}


def test_iex_threshold_recall_counts_missing_and_under_threshold_minutes():
    sip = {"AAPL": [
        _bar("2026-08-11T13:30:00Z", 100, 100.50, 1_000),
        _bar("2026-08-11T13:31:00Z", 100, 101.00, 2_000),
        _bar("2026-08-11T13:32:00Z", 100, 100.75, 3_000),
    ]}
    iex = {"AAPL": [
        _bar("2026-08-11T13:30:00Z", 100, 100.40, 10),
        _bar("2026-08-11T13:31:00Z", 100, 101.10, 20),
    ]}

    result = compare_bar_feeds(iex, sip, thresholds_bps=(50,))
    threshold = result["aggregate"]["thresholds_bps"]["50"]

    assert threshold == {
        "sip_triggers": 3,
        "iex_hits": 1,
        "false_negatives": 2,
        "missing_iex_minutes": 1,
        "present_below_threshold": 1,
        "recall": pytest.approx(1 / 3),
    }
    assert result["by_symbol"]["AAPL"]["sip_only_minutes"] == 1
    assert result["aggregate"]["iex_volume_fraction"] == pytest.approx(30 / 6_000)


def test_invalid_or_empty_bar_values_never_create_false_triggers():
    result = compare_bar_feeds(
        {"AAPL": [_bar("t1", "bad", 101, 5)]},
        {"AAPL": [_bar("t1", 0, 101, 50)]},
        thresholds_bps=(10,),
    )
    assert result["aggregate"]["thresholds_bps"]["10"]["sip_triggers"] == 0
    invalid_close = compare_bar_feeds(
        {"AAPL": [_bar("2026-08-11T13:30:00Z", 100, "bad", 5)]},
        {"AAPL": [_bar("2026-08-11T13:30:00Z", 100, "bad", 50)]},
        thresholds_bps=(10,),
    )
    assert invalid_close["aggregate"]["thresholds_bps"]["10"]["sip_triggers"] == 0


def test_bar_index_normalizes_equivalent_offsets_and_refuses_true_zero_overlap():
    equivalent = compare_bar_feeds(
        {"AAPL": [_bar("2026-08-11T09:30:00-04:00", 100, 101, 5)]},
        {"AAPL": [_bar("2026-08-11T13:30:00Z", 100, 101, 50)]},
        thresholds_bps=(10,),
    )
    assert equivalent["by_symbol"]["AAPL"]["overlap_minutes"] == 1
    with pytest.raises(ValueError, match="zero timestamp overlap"):
        compare_bar_feeds(
            {"AAPL": [_bar("2026-08-11T13:30:00Z", 100, 101, 5)]},
            {"AAPL": [_bar("2026-08-11T14:30:00Z", 100, 101, 50)]},
            thresholds_bps=(10,),
        )


def test_regular_session_bounds_use_verified_eastern_dst_and_close():
    start, end = regular_session_bounds(date(2026, 8, 11))
    assert start.isoformat() == "2026-08-11T13:30:00+00:00"
    assert end.isoformat() == "2026-08-11T20:00:00+00:00"


def test_identity_characterization_counts_corrections_bursts_and_latency():
    rows = [
        {"identity": "n1", "payload_hash": "a", "source_ts": "2026-08-12T14:00:00Z",
         "received_at": "2026-08-12T14:00:01Z"},
        {"identity": "n1", "payload_hash": "b", "source_ts": "2026-08-12T14:00:00Z",
         "received_at": "2026-08-12T14:00:01.500000Z"},
        {"identity": "n2", "payload_hash": "c", "source_ts": "2026-08-12T14:00:01Z",
         "received_at": "2026-08-12T14:00:02Z"},
        {"identity": None, "received_at": "2026-08-12T14:00:03Z"},
    ]
    result = characterize_identity_records(rows)
    assert result["frames"] == 3
    assert result["unique_identities"] == 2
    assert result["duplicate_frames"] == 1
    assert result["corrected_identities"] == 1
    assert result["invalid_records"] == 1
    assert result["max_burst"] == {"1s": 3, "60s": 3}
    assert result["source_to_receive_seconds"]["p95"] == pytest.approx(1.45)


def test_identity_surface_comparison_treats_updates_as_one_source_identity():
    stream = [{"identity": "1"}, {"identity": "1"}, {"identity": "2"}]
    rest = [{"identity": 1}, {"identity": 3}]
    assert compare_identity_surfaces(stream, rest) == {
        "stream_identities": 2,
        "rest_identities": 2,
        "overlap": 1,
        "stream_only": 1,
        "rest_only": 1,
        "stream_recall_of_rest": 0.5,
    }


def test_evidence_writer_separates_raw_and_summary_and_hash_binds_them(tmp_path):
    iex = {"AAPL": [_bar("2026-08-11T13:30:00Z", 100, 100.1, 10)]}
    sip = {"AAPL": [_bar("2026-08-11T13:30:00Z", 100, 100.2, 100)]}
    manifest_path = write_bar_evidence(
        tmp_path,
        observation_date=date(2026, 8, 11),
        captured_at=datetime(2026, 8, 12, 2, 0, tzinfo=UTC),
        symbols=("AAPL",),
        iex=iex,
        sip=sip,
        environment={"paper_only": True},
        thresholds_bps=(10,),
    )

    manifest = json.loads(manifest_path.read_text())
    assert manifest["universe"] == ["AAPL"]
    assert manifest["sample_size"] == {"iex": 1, "sip": 1}
    for section, name in (("raw", "iex"), ("raw", "sip"), ("summary", None)):
        item = manifest[section][name] if name else manifest[section]
        payload = (manifest_path.parent / item["path"]).read_bytes().rstrip(b"\n")
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
    assert (manifest_path.parent / "raw" / "alpaca-iex-1min.json").is_file()
    assert (manifest_path.parent / "summary.json").is_file()


def test_evidence_writer_refuses_overwrite_and_implicit_universe(tmp_path):
    kwargs = {
        "root": tmp_path,
        "observation_date": date(2026, 8, 11),
        "captured_at": datetime(2026, 8, 12, 2, 0, tzinfo=UTC),
        "symbols": ("AAPL",),
        "iex": {"AAPL": []},
        "sip": {"AAPL": []},
        "environment": {},
    }
    write_bar_evidence(**kwargs)
    with pytest.raises(FileExistsError):
        write_bar_evidence(**kwargs)
    with pytest.raises(ValueError, match="universe"):
        write_bar_evidence(**{**kwargs, "captured_at": datetime(2026, 8, 12, 3, tzinfo=UTC),
                              "symbols": ()})


def test_news_evidence_binds_raw_surfaces_and_compares_article_ids(tmp_path):
    start = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)
    stream = [{
        "received_at": "2026-08-12T14:00:02Z",
        "message": {
            "id": 7,
            "created_at": "2026-08-12T14:00:01Z",
            "updated_at": "2026-08-12T14:00:01Z",
            "headline": "fixture",
        },
    }]
    manifest_path = write_news_evidence(
        tmp_path,
        window_start=start,
        window_end=datetime(2026, 8, 12, 14, 1, tzinfo=UTC),
        symbols=("AAPL",),
        stream_rows=stream,
        rest_rows=[{"id": 7}, {"id": 8}],
        environment={"paper_only": True},
    )
    summary = json.loads((manifest_path.parent / "summary.json").read_text())
    assert summary["stream"]["source_to_receive_seconds"]["p50"] == 1.0
    assert summary["ws_rest_identity"] == {
        "stream_identities": 1,
        "rest_identities": 2,
        "overlap": 1,
        "stream_only": 0,
        "rest_only": 1,
        "stream_recall_of_rest": 0.5,
    }
    manifest = json.loads(manifest_path.read_text())
    assert manifest["sample_size"] == {"websocket": 1, "rest": 2}


def test_news_reconnect_counts_cross_segment_correction_and_rest_recovery():
    disconnected = datetime(2026, 8, 12, 14, 0, 10, tzinfo=UTC)
    reconnected = datetime(2026, 8, 12, 14, 0, 15, tzinfo=UTC)
    before = [{
        "received_at": "2026-08-12T14:00:09Z",
        "message": {
            "id": 7,
            "created_at": "2026-08-12T14:00:08Z",
            "updated_at": "2026-08-12T14:00:08Z",
            "headline": "before",
        },
    }]
    after = [
        {
            "received_at": "2026-08-12T14:00:16Z",
            "message": {
                "id": 7,
                "created_at": "2026-08-12T14:00:08Z",
                "updated_at": "2026-08-12T14:00:15Z",
                "headline": "corrected",
            },
        },
        {
            "received_at": "2026-08-12T14:00:16Z",
            "message": {
                "id": 8,
                "created_at": "2026-08-12T14:00:12Z",
                "updated_at": "2026-08-12T14:00:12Z",
                "headline": "late delivery",
            },
        },
    ]
    rest = [
        {"id": 7, "created_at": "2026-08-12T14:00:08Z", "updated_at": "2026-08-12T14:00:15Z"},
        {"id": 8, "created_at": "2026-08-12T14:00:12Z", "updated_at": "2026-08-12T14:00:12Z"},
        {"id": 9, "created_at": "2026-08-12T14:00:13Z", "updated_at": "2026-08-12T14:00:13Z"},
    ]

    result = characterize_news_reconnect(
        before,
        after,
        rest,
        disconnected_at=disconnected,
        reconnected_at=reconnected,
    )

    assert result == {
        "observed_disconnect_seconds": 5.0,
        "before_identities": 1,
        "after_identities": 2,
        "cross_segment_overlap": 1,
        "cross_segment_corrected_identities": 1,
        "combined_stream_identities": 2,
        "rest_identities": 3,
        "combined_stream_recall_of_rest": pytest.approx(2 / 3),
        "blind_window_rest_identities": 3,
        "blind_window_delivered_after_reconnect": 2,
        "blind_window_rest_recoverable": 1,
    }


def test_news_reconnect_writer_hash_binds_all_three_surfaces(tmp_path):
    start = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)
    disconnected = datetime(2026, 8, 12, 14, 1, tzinfo=UTC)
    reconnected = datetime(2026, 8, 12, 14, 1, 5, tzinfo=UTC)
    end = datetime(2026, 8, 12, 14, 2, tzinfo=UTC)
    manifest_path = write_news_reconnect_evidence(
        tmp_path,
        window_start=start,
        disconnected_at=disconnected,
        reconnected_at=reconnected,
        window_end=end,
        symbols=("AAPL",),
        before_rows=[],
        after_rows=[],
        rest_rows=[],
        environment={"paper_only": True},
    )

    manifest = json.loads(manifest_path.read_text())
    assert manifest["capture_kind"] == "alpaca-news-reconnect-ws-rest"
    assert manifest["sample_size"] == {
        "websocket_before": 0,
        "websocket_after": 0,
        "rest": 0,
    }
    assert verify_evidence_manifest(manifest_path) == {
        "ok": True,
        "checked": ["raw.rest", "raw.websocket_after", "raw.websocket_before", "summary"],
        "failures": [],
    }


def test_market_frames_keep_types_and_measure_source_delivery_timing():
    rows = [
        {"received_at": "2026-08-12T14:00:01Z",
         "message": {"T": "q", "S": "FAKEPACA", "t": "2026-08-12T14:00:00Z"}},
        {"received_at": "2026-08-12T14:00:02Z",
         "message": {"T": "b", "S": "FAKEPACA", "t": "2026-08-12T14:00:01Z"}},
    ]
    result = characterize_market_frames(rows)
    assert result["types"] == {"b": 1, "q": 1}
    assert result["timing"]["source_to_receive_seconds"]["p50"] == 1.0


def test_manifest_verifier_detects_tampering_and_path_escape(tmp_path):
    start = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)
    manifest_path = write_market_stream_evidence(
        tmp_path, window_start=start, window_end=datetime(2026, 8, 12, 14, 1, tzinfo=UTC),
        feed="test", symbols=("FAKEPACA",), rows=[], environment={"paper_only": True},
    )
    assert verify_evidence_manifest(manifest_path)["ok"] is True
    summary = manifest_path.parent / "summary.json"
    summary.write_text("{}")
    bad = verify_evidence_manifest(manifest_path)
    assert bad["ok"] is False
    assert bad["failures"] == [{"item": "summary", "reason": "sha256 mismatch"}]

    manifest = json.loads(manifest_path.read_text())
    manifest["summary"]["path"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest))
    escaped = verify_evidence_manifest(manifest_path)
    assert {failure["reason"] for failure in escaped["failures"]} >= {
        "path escapes capture directory"
    }


def test_capability_evidence_derives_availability_from_raw_probe(tmp_path):
    manifest_path = write_capability_evidence(
        tmp_path,
        captured_at=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
        symbol="AAPL",
        probes={
            "latest_quote_iex": {
                "status_code": 200, "body": {"quote": {"t": "2026-08-12T13:59:59Z"}}
            },
            "latest_quote_sip": {"status_code": 403, "body": {"message": "not permitted"}},
        },
        environment={"paper_only": True},
    )
    summary = json.loads((manifest_path.parent / "summary.json").read_text())
    assert summary["latest_quote_iex"] == {
        "status_code": 200, "available": True, "source_ts": "2026-08-12T13:59:59Z"
    }
    assert summary["latest_quote_sip"] == {
        "status_code": 403, "available": False, "source_ts": None
    }
    assert verify_evidence_manifest(manifest_path)["ok"] is True
