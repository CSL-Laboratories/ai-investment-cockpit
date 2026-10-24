"""RW-19 deterministic tests for read-only event-source evidence."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta, timezone

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


# Baseline manifest bytes from the pre-refactor tree at 94862b62. These are
# fixed oracles, not recomputed from the helpers this PR extracts.
_WRITER_CASES = (
    pytest.param(write_bar_evidence, None,
                 "194b89d462a14cd774550215b6bac94f0475ad4135eea9c7a3e38cb38e424981", id="bars"),
    pytest.param(write_news_evidence, "news",
                 "d47bc6116ce130cfbef3d82a079974cb1ad28044f8e45d6759f699dd4937a945", id="news"),
    pytest.param(write_news_reconnect_evidence, "news-reconnect",
                 "8c96fc6e1e5a55fca0e2a406b1be63129533551b350f1f80f2a69eda8f82a95b", id="reconnect"),
    pytest.param(write_market_stream_evidence, "market",
                 "51a34036228dad725133a5ff28422717a9fcf3f82a63757b367582841cf79e71", id="market"),
    pytest.param(write_capability_evidence, "capabilities",
                 "f845955509b489fa04a056d26d5278cc6c33015520fb895314134f87827220b9", id="capabilities"),
)


def _writer_arguments(writer, root):
    # Local date is Aug 11; the UTC capture ID is Aug 12. Bars intentionally
    # use a third date supplied by the operator rather than either clock date.
    start = datetime(2026, 8, 11, 23, 59, tzinfo=timezone(timedelta(hours=-7)))
    kwargs = {"root": root, "environment": {"paper_only": True, "label": "caf\u00e9"}}
    if writer is write_capability_evidence:
        return {**kwargs, "captured_at": start, "symbol": " aapl ", "probes": {}}
    kwargs["symbols"] = (" msft ", "AAPL", "aapl", " ", "MSFT")
    if writer is write_bar_evidence:
        return {
            **kwargs, "observation_date": date(2026, 8, 10), "captured_at": start,
            "iex": {"AAPL": []}, "sip": {"AAPL": []},
        }
    kwargs.update(window_start=start, window_end=start + timedelta(minutes=2))
    if writer is write_news_evidence:
        return {**kwargs, "stream_rows": [], "rest_rows": []}
    if writer is write_news_reconnect_evidence:
        return {
            **kwargs, "disconnected_at": start + timedelta(seconds=10),
            "reconnected_at": start + timedelta(seconds=15),
            "before_rows": [], "after_rows": [], "rest_rows": [],
        }
    return {**kwargs, "feed": "test", "rows": []}


@pytest.mark.parametrize("writer,kind,manifest_sha256", _WRITER_CASES)
def test_all_writer_layouts_and_manifest_bytes_match_baseline(
    tmp_path, writer, kind, manifest_sha256,
):
    manifest_path = writer(**_writer_arguments(writer, tmp_path))
    expected_root = tmp_path if kind is None else tmp_path / kind
    day = "2026-08-10" if kind is None else "2026-08-11"
    assert manifest_path == expected_root / day / "20260812T065900Z" / "manifest.json"
    encoded = manifest_path.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == manifest_sha256
    manifest = json.loads(encoded)
    assert manifest["universe"] == (["AAPL"] if kind == "capabilities" else ["AAPL", "MSFT"])
    assert manifest["environment"] == {"paper_only": True, "label": "caf\u00e9"}
    assert verify_evidence_manifest(manifest_path)["ok"] is True
    expected_files = {"manifest.json", "summary.json"}
    for item in (*manifest["raw"].values(), manifest["summary"]):
        relative = item["path"]
        payload = (manifest_path.parent / relative).read_bytes()
        assert payload.endswith(b"\n") and not payload.endswith(b"\n\n")
        assert hashlib.sha256(payload[:-1]).hexdigest() == item["sha256"]
        expected_files.add(relative)
    assert {
        path.relative_to(manifest_path.parent).as_posix()
        for path in manifest_path.parent.rglob("*") if path.is_file()
    } == expected_files


@pytest.mark.parametrize("writer,kind,manifest_sha256", _WRITER_CASES)
def test_all_writers_reject_same_second_capture_without_changing_evidence(
    tmp_path, writer, kind, manifest_sha256,
):
    kwargs = _writer_arguments(writer, tmp_path)
    manifest_path = writer(**kwargs)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    # A different offset and subsecond precision still denote the same capture
    # second. Keep the local calendar date so this targets the same directory.
    stamp_key = "captured_at" if "captured_at" in kwargs else "window_start"
    kwargs[stamp_key] = kwargs[stamp_key].astimezone(timezone(timedelta(hours=-8))).replace(
        microsecond=123456,
    )
    kwargs["environment"] = {"label": "must not overwrite"}
    with pytest.raises(FileExistsError) as error:
        writer(**kwargs)
    assert str(error.value) == f"capture already exists: {manifest_path.parent}"
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("writer,kind,manifest_sha256", _WRITER_CASES)
def test_all_writer_timestamp_guards_preserve_messages_before_writes(
    tmp_path, writer, kind, manifest_sha256,
):
    kwargs = _writer_arguments(writer, tmp_path)
    stamp_keys = [key for key, value in kwargs.items() if isinstance(value, datetime)]
    message = (
        "captured_at" if "captured_at" in kwargs
        else "capture and reconnect timestamps" if "disconnected_at" in kwargs
        else "capture window"
    )
    for key in stamp_keys:
        with pytest.raises(ValueError) as error:
            writer(**{**kwargs, key: kwargs[key].replace(tzinfo=None)})
        assert str(error.value) == f"{message} must be timezone-aware"
        assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("writer,kind,manifest_sha256", _WRITER_CASES)
def test_all_writers_reject_blank_symbol_scope_before_writes(
    tmp_path, writer, kind, manifest_sha256,
):
    kwargs = _writer_arguments(writer, tmp_path)
    if writer is write_capability_evidence:
        kwargs["symbol"] = " \t "
        message = "an explicit probe symbol is required"
    else:
        kwargs["symbols"] = ("", " \t ")
        message = "an explicit non-empty symbol universe is required"
    with pytest.raises(ValueError) as error:
        writer(**kwargs)
    assert str(error.value) == message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("naive_key", ("disconnected_at", "reconnected_at"))
def test_reconnect_characterizer_preserves_timezone_validation_message(naive_key):
    kwargs = {
        "disconnected_at": datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
        "reconnected_at": datetime(2026, 8, 12, 14, 1, tzinfo=UTC),
    }
    kwargs[naive_key] = kwargs[naive_key].replace(tzinfo=None)
    with pytest.raises(ValueError) as error:
        characterize_news_reconnect([], [], [], **kwargs)
    assert str(error.value) == "reconnect timestamps must be timezone-aware"
