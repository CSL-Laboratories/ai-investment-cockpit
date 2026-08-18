"""RW-19 read-only event-source characterization helpers.

This module measures recorded market-data evidence. It does not schedule work, mutate the
production database, call a model, or touch the order path. Raw vendor responses stay separate
from the derived summary (Invariant 8), and every summary binds the raw files by SHA-256.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, deque
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 2
DEFAULT_THRESHOLDS_BPS = (10, 25, 50, 100)


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _movement_bps(bar: dict) -> float | None:
    opened = _finite_number(bar.get("o"))
    closed = _finite_number(bar.get("c"))
    if opened is None or closed is None or opened <= 0:
        return None
    return abs((closed - opened) / opened) * 10_000


def _percentile(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _max_window_count(timestamps: list[datetime], seconds: float) -> int:
    window: deque[datetime] = deque()
    largest = 0
    for timestamp in sorted(timestamps):
        while window and (timestamp - window[0]).total_seconds() > seconds:
            window.popleft()
        window.append(timestamp)
        largest = max(largest, len(window))
    return largest


def characterize_identity_records(records: Iterable[dict]) -> dict:
    """Summarize stable IDs, corrections, bursts, and source-to-receive latency."""
    source_records = list(records)
    valid = []
    for record in source_records:
        identity = record.get("identity")
        received = _parse_timestamp(record.get("received_at"))
        if identity is None or received is None:
            continue
        valid.append({**record, "identity": str(identity), "_received": received})

    identities = Counter(record["identity"] for record in valid)
    hashes: dict[str, set[str]] = {}
    for record in valid:
        payload_hash = record.get("payload_hash")
        if isinstance(payload_hash, str):
            hashes.setdefault(record["identity"], set()).add(payload_hash)
    corrections = sum(1 for values in hashes.values() if len(values) > 1)

    timestamps = sorted(record["_received"] for record in valid)
    interarrival = [
        (current - previous).total_seconds()
        for previous, current in zip(timestamps, timestamps[1:])
    ]
    latencies = []
    for record in valid:
        source = _parse_timestamp(record.get("source_ts"))
        if source is not None:
            latency = (record["_received"] - source).total_seconds()
            if latency >= 0:
                latencies.append(latency)

    return {
        "frames": len(valid),
        "unique_identities": len(identities),
        "duplicate_frames": sum(count - 1 for count in identities.values()),
        "corrected_identities": corrections,
        "invalid_records": len(source_records) - len(valid),
        "max_burst": {
            "1s": _max_window_count(timestamps, 1.0),
            "60s": _max_window_count(timestamps, 60.0),
        },
        "interarrival_seconds": {
            "p50": _percentile(interarrival, 0.50),
            "p95": _percentile(interarrival, 0.95),
            "p99": _percentile(interarrival, 0.99),
            "max": max(interarrival) if interarrival else None,
        },
        "source_to_receive_seconds": {
            "samples": len(latencies),
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": max(latencies) if latencies else None,
        },
    }


def compare_identity_surfaces(stream: Iterable[dict], rest: Iterable[dict]) -> dict:
    """Compare stable source IDs across a contemporaneous WebSocket and REST window."""
    stream_ids = {str(row["identity"]) for row in stream if row.get("identity") is not None}
    rest_ids = {str(row["identity"]) for row in rest if row.get("identity") is not None}
    overlap = stream_ids & rest_ids
    return {
        "stream_identities": len(stream_ids),
        "rest_identities": len(rest_ids),
        "overlap": len(overlap),
        "stream_only": len(stream_ids - rest_ids),
        "rest_only": len(rest_ids - stream_ids),
        "stream_recall_of_rest": None if not rest_ids else len(overlap) / len(rest_ids),
    }


def _indexed(rows: Iterable[dict]) -> dict[str, dict]:
    indexed = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        timestamp = _parse_timestamp(row.get("t"))
        if timestamp is not None:
            indexed[timestamp.isoformat()] = row
    return indexed


def _within(rows: dict[str, list[dict]], start: datetime, end: datetime) -> dict[str, list[dict]]:
    filtered: dict[str, list[dict]] = {}
    for symbol, values in rows.items():
        kept = []
        for row in values:
            try:
                timestamp = datetime.fromisoformat(str(row.get("t")).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if timestamp.tzinfo is not None and start <= timestamp < end:
                kept.append(row)
        filtered[symbol] = kept
    return filtered


def regular_session_bounds(day: date) -> tuple[datetime, datetime]:
    """Return the verified NYSE regular session as UTC-aware bounds."""
    from orchestrator.market_calendar import EASTERN, MARKET_OPEN, close_time_for

    close = close_time_for(day)
    start = datetime(
        day.year, day.month, day.day, MARKET_OPEN.hour, MARKET_OPEN.minute, tzinfo=EASTERN
    )
    end = datetime(day.year, day.month, day.day, close.hour, close.minute, tzinfo=EASTERN)
    return start.astimezone(UTC), end.astimezone(UTC)


def compare_bar_feeds(
    iex: dict[str, list[dict]],
    sip: dict[str, list[dict]],
    *,
    thresholds_bps: Iterable[int] = DEFAULT_THRESHOLDS_BPS,
) -> dict:
    """Compare IEX minute bars to delayed SIP as the consolidated reference surface.

    A threshold hit is an absolute open-to-close move within one minute. This is a measurement
    sweep, not a production trigger. Recall answers: of SIP minute bars crossing the threshold,
    how many same-minute IEX bars also crossed it? Missing IEX minutes count as false negatives.
    """
    thresholds = tuple(sorted(set(int(value) for value in thresholds_bps)))
    if not thresholds or thresholds[0] <= 0:
        raise ValueError("thresholds_bps must contain positive integers")

    symbols = sorted(set(iex) | set(sip))
    by_symbol: dict[str, dict] = {}
    aggregate = {
        threshold: {"sip_triggers": 0, "iex_hits": 0, "false_negatives": 0}
        for threshold in thresholds
    }
    all_close_errors: list[float] = []
    total_iex_volume = 0.0
    total_sip_volume = 0.0

    for symbol in symbols:
        iex_rows = _indexed(iex.get(symbol, []))
        sip_rows = _indexed(sip.get(symbol, []))
        overlap = sorted(set(iex_rows) & set(sip_rows))
        if iex_rows and sip_rows and not overlap:
            raise ValueError(
                f"{symbol} has non-empty IEX and SIP samples but zero timestamp overlap"
            )
        close_errors: list[float] = []
        for timestamp in overlap:
            iex_close = _finite_number(iex_rows[timestamp].get("c"))
            sip_close = _finite_number(sip_rows[timestamp].get("c"))
            if iex_close is not None and sip_close is not None and sip_close > 0:
                close_errors.append(abs(iex_close - sip_close) / sip_close * 10_000)
        all_close_errors.extend(close_errors)

        iex_volume = sum(_finite_number(row.get("v")) or 0 for row in iex_rows.values())
        sip_volume = sum(_finite_number(row.get("v")) or 0 for row in sip_rows.values())
        total_iex_volume += iex_volume
        total_sip_volume += sip_volume

        threshold_summary: dict[str, dict] = {}
        for threshold in thresholds:
            sip_trigger_times = {
                timestamp
                for timestamp, bar in sip_rows.items()
                if (_movement_bps(bar) or 0) >= threshold
            }
            iex_hit_times = {
                timestamp
                for timestamp in sip_trigger_times
                if timestamp in iex_rows and (_movement_bps(iex_rows[timestamp]) or 0) >= threshold
            }
            sip_triggers = len(sip_trigger_times)
            iex_hits = len(iex_hit_times)
            false_negatives = sip_triggers - iex_hits
            missing_minutes = sum(timestamp not in iex_rows for timestamp in sip_trigger_times)
            below_threshold_minutes = false_negatives - missing_minutes
            threshold_summary[str(threshold)] = {
                "sip_triggers": sip_triggers,
                "iex_hits": iex_hits,
                "false_negatives": false_negatives,
                "missing_iex_minutes": missing_minutes,
                "present_below_threshold": below_threshold_minutes,
                "recall": None if sip_triggers == 0 else iex_hits / sip_triggers,
            }
            aggregate[threshold]["sip_triggers"] += sip_triggers
            aggregate[threshold]["iex_hits"] += iex_hits
            aggregate[threshold]["false_negatives"] += false_negatives
            aggregate[threshold].setdefault("missing_iex_minutes", 0)
            aggregate[threshold].setdefault("present_below_threshold", 0)
            aggregate[threshold]["missing_iex_minutes"] += missing_minutes
            aggregate[threshold]["present_below_threshold"] += below_threshold_minutes

        by_symbol[symbol] = {
            "iex_minutes": len(iex_rows),
            "sip_minutes": len(sip_rows),
            "overlap_minutes": len(overlap),
            "sip_only_minutes": len(set(sip_rows) - set(iex_rows)),
            "iex_volume": int(iex_volume),
            "sip_volume": int(sip_volume),
            "iex_volume_fraction": None if sip_volume <= 0 else iex_volume / sip_volume,
            "close_error_bps": {
                "p50": _percentile(close_errors, 0.50),
                "p95": _percentile(close_errors, 0.95),
                "p99": _percentile(close_errors, 0.99),
                "max": max(close_errors) if close_errors else None,
            },
            "thresholds_bps": threshold_summary,
        }

    aggregate_thresholds = {}
    for threshold, values in aggregate.items():
        triggers = values["sip_triggers"]
        aggregate_thresholds[str(threshold)] = {
            **values,
            "recall": None if triggers == 0 else values["iex_hits"] / triggers,
        }

    return {
        "symbols": symbols,
        "by_symbol": by_symbol,
        "aggregate": {
            "iex_volume": int(total_iex_volume),
            "sip_volume": int(total_sip_volume),
            "iex_volume_fraction": (
                None if total_sip_volume <= 0 else total_iex_volume / total_sip_volume
            ),
            "close_error_bps": {
                "p50": _percentile(all_close_errors, 0.50),
                "p95": _percentile(all_close_errors, 0.95),
                "p99": _percentile(all_close_errors, 0.99),
                "max": max(all_close_errors) if all_close_errors else None,
            },
            "thresholds_bps": aggregate_thresholds,
        },
    }


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _write_json(path: Path, value: Any) -> str:
    encoded = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded + b"\n")
    return hashlib.sha256(encoded).hexdigest()


def verify_evidence_manifest(manifest_path: Path) -> dict:
    """Verify every raw/summary hash bound by an RW-19 manifest."""
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checked = []
    failures = []
    items = []
    raw = manifest.get("raw")
    if isinstance(raw, dict):
        items.extend((f"raw.{name}", item) for name, item in raw.items())
    summary = manifest.get("summary")
    if isinstance(summary, dict):
        items.append(("summary", summary))
    for label, item in items:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            failures.append({"item": label, "reason": "invalid manifest item"})
            continue
        target = (manifest_path.parent / item["path"]).resolve()
        try:
            target.relative_to(manifest_path.parent.resolve())
        except ValueError:
            failures.append({"item": label, "reason": "path escapes capture directory"})
            continue
        try:
            payload = target.read_bytes().rstrip(b"\n")
        except OSError as exc:
            failures.append({"item": label, "reason": type(exc).__name__})
            continue
        actual = hashlib.sha256(payload).hexdigest()
        checked.append(label)
        if actual != item.get("sha256"):
            failures.append({"item": label, "reason": "sha256 mismatch"})
    return {"ok": bool(checked) and not failures, "checked": checked, "failures": failures}


def characterize_market_frames(rows: Iterable[dict]) -> dict:
    """Summarize frame types, source timestamps, and receive cadence without trigger policy."""
    source_rows = list(rows)
    type_counts: Counter[str] = Counter()
    records = []
    for item in source_rows:
        message = item.get("message") if isinstance(item, dict) else None
        if not isinstance(message, dict):
            continue
        message_type = message.get("T")
        if not isinstance(message_type, str):
            continue
        type_counts[message_type] += 1
        identity = ":".join((message_type, str(message.get("S")), str(message.get("t"))))
        records.append({
            "identity": identity,
            "payload_hash": hashlib.sha256(_canonical_bytes(message)).hexdigest(),
            "source_ts": message.get("t"),
            "received_at": item.get("received_at"),
        })
    return {
        "wire_frames": len(source_rows),
        "data_messages": len(records),
        "types": dict(sorted(type_counts.items())),
        "timing": characterize_identity_records(records),
    }


def write_bar_evidence(
    root: Path,
    *,
    observation_date: date,
    captured_at: datetime,
    symbols: Iterable[str],
    iex: dict[str, list[dict]],
    sip: dict[str, list[dict]],
    environment: dict[str, Any],
    thresholds_bps: Iterable[int] = DEFAULT_THRESHOLDS_BPS,
) -> Path:
    """Write one immutable raw/summary capture directory and return its manifest path."""
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("captured_at must be timezone-aware")
    scope = tuple(sorted(set(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip())))
    if not scope:
        raise ValueError("an explicit non-empty symbol universe is required")
    capture_id = captured_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    capture_root = Path(root) / observation_date.isoformat() / capture_id
    if capture_root.exists():
        raise FileExistsError(f"capture already exists: {capture_root}")

    # Validate and derive BEFORE the first write. A zero-overlap or malformed comparison must
    # not leave an unbound raw directory that looks like a capture but has no manifest.
    session_start, session_end = regular_session_bounds(observation_date)
    summary = {
        "full_utc_day": compare_bar_feeds(iex, sip, thresholds_bps=thresholds_bps),
        "regular_session": compare_bar_feeds(
            _within(iex, session_start, session_end),
            _within(sip, session_start, session_end),
            thresholds_bps=thresholds_bps,
        ),
        "regular_session_bounds": {
            "start": session_start.isoformat(),
            "end_exclusive": session_end.isoformat(),
        },
    }
    iex_hash = _write_json(capture_root / "raw" / "alpaca-iex-1min.json", iex)
    sip_hash = _write_json(capture_root / "raw" / "alpaca-sip-1min.json", sip)
    summary_hash = _write_json(capture_root / "summary.json", summary)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "work_package": "RW-19",
        "capture_kind": "alpaca-historical-minute-bars",
        "captured_at": captured_at.astimezone(UTC).isoformat(),
        "observation_date": observation_date.isoformat(),
        "universe": list(scope),
        "sample_size": {
            feed: sum(len(rows) for rows in payload.values())
            for feed, payload in (("iex", iex), ("sip", sip))
        },
        "environment": environment,
        "raw": {
            "iex": {"path": "raw/alpaca-iex-1min.json", "sha256": iex_hash},
            "sip": {"path": "raw/alpaca-sip-1min.json", "sha256": sip_hash},
        },
        "summary": {"path": "summary.json", "sha256": summary_hash},
        "source_terms": {
            "market_data": "https://docs.alpaca.markets/us/docs/about-market-data-api",
            "feed_comparison": "https://docs.alpaca.markets/us/docs/market-data-faq",
        },
        "limitations": [
            "Historical minute bars measure coverage and threshold recall, not wire latency.",
            "Thresholds are a measurement sweep and are not production trigger policy.",
            "Delayed SIP history is a reference surface; current credentials do not imply live SIP.",
        ],
    }
    return_path = capture_root / "manifest.json"
    _write_json(return_path, manifest)
    return return_path


def _news_identity_rows(stream_rows: Iterable[dict]) -> list[dict]:
    records = []
    for item in stream_rows:
        message = item.get("message") if isinstance(item, dict) else None
        if not isinstance(message, dict):
            continue
        records.append({
            "identity": message.get("id"),
            "payload_hash": hashlib.sha256(_canonical_bytes(message)).hexdigest(),
            "source_ts": message.get("updated_at") or message.get("created_at"),
            "received_at": item.get("received_at"),
        })
    return records


def characterize_news_reconnect(
    before_rows: Iterable[dict],
    after_rows: Iterable[dict],
    rest_rows: Iterable[dict],
    *,
    disconnected_at: datetime,
    reconnected_at: datetime,
) -> dict:
    """Measure identity continuity and REST recovery across one deliberate news gap."""
    if any(
        value.tzinfo is None or value.utcoffset() is None
        for value in (disconnected_at, reconnected_at)
    ):
        raise ValueError("reconnect timestamps must be timezone-aware")
    disconnected_at = disconnected_at.astimezone(UTC)
    reconnected_at = reconnected_at.astimezone(UTC)
    if reconnected_at < disconnected_at:
        raise ValueError("reconnected_at must not precede disconnected_at")

    before_rows = list(before_rows)
    after_rows = list(after_rows)
    rest_rows = list(rest_rows)
    before = _news_identity_rows(before_rows)
    after = _news_identity_rows(after_rows)
    before_ids = {str(row["identity"]) for row in before if row.get("identity") is not None}
    after_ids = {str(row["identity"]) for row in after if row.get("identity") is not None}
    stream_ids = before_ids | after_ids
    rest_ids = {
        str(row["id"])
        for row in rest_rows
        if isinstance(row, dict) and row.get("id") is not None
    }

    hashes: dict[str, set[str]] = {}
    for row in (*before, *after):
        identity = row.get("identity")
        payload_hash = row.get("payload_hash")
        if identity is not None and isinstance(payload_hash, str):
            hashes.setdefault(str(identity), set()).add(payload_hash)
    cross_segment_corrections = sum(
        1
        for identity in before_ids & after_ids
        if len(hashes.get(identity, set())) > 1
    )

    blind_window_rest_ids = set()
    for row in rest_rows:
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        source_ts = _parse_timestamp(row.get("updated_at") or row.get("created_at"))
        if source_ts is not None and disconnected_at <= source_ts <= reconnected_at:
            blind_window_rest_ids.add(str(row["id"]))

    late_after_ids = set()
    for row in after_rows:
        message = row.get("message") if isinstance(row, dict) else None
        if not isinstance(message, dict) or message.get("id") is None:
            continue
        source_ts = _parse_timestamp(message.get("updated_at") or message.get("created_at"))
        if source_ts is not None and disconnected_at <= source_ts <= reconnected_at:
            late_after_ids.add(str(message["id"]))

    return {
        "observed_disconnect_seconds": (reconnected_at - disconnected_at).total_seconds(),
        "before_identities": len(before_ids),
        "after_identities": len(after_ids),
        "cross_segment_overlap": len(before_ids & after_ids),
        "cross_segment_corrected_identities": cross_segment_corrections,
        "combined_stream_identities": len(stream_ids),
        "rest_identities": len(rest_ids),
        "combined_stream_recall_of_rest": (
            None if not rest_ids else len(stream_ids & rest_ids) / len(rest_ids)
        ),
        "blind_window_rest_identities": len(blind_window_rest_ids),
        "blind_window_delivered_after_reconnect": len(blind_window_rest_ids & late_after_ids),
        "blind_window_rest_recoverable": len(blind_window_rest_ids - stream_ids),
    }


def write_news_evidence(
    root: Path,
    *,
    window_start: datetime,
    window_end: datetime,
    symbols: Iterable[str],
    stream_rows: list[dict],
    rest_rows: list[dict],
    environment: dict[str, Any],
) -> Path:
    """Write an immutable news WebSocket/REST identity capture."""
    if any(value.tzinfo is None or value.utcoffset() is None for value in (window_start, window_end)):
        raise ValueError("capture window must be timezone-aware")
    if window_end <= window_start:
        raise ValueError("capture window end must follow its start")
    scope = tuple(sorted(set(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip())))
    if not scope:
        raise ValueError("an explicit non-empty symbol universe is required")
    capture_id = window_start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    capture_root = Path(root) / "news" / window_start.date().isoformat() / capture_id
    if capture_root.exists():
        raise FileExistsError(f"capture already exists: {capture_root}")

    stream_hash = _write_json(capture_root / "raw" / "alpaca-news-ws.json", stream_rows)
    rest_hash = _write_json(capture_root / "raw" / "alpaca-news-rest.json", rest_rows)
    stream_identity = _news_identity_rows(stream_rows)
    rest_identity = [{"identity": row.get("id")} for row in rest_rows if isinstance(row, dict)]
    summary = {
        "stream": characterize_identity_records(stream_identity),
        "ws_rest_identity": compare_identity_surfaces(stream_identity, rest_identity),
    }
    summary_hash = _write_json(capture_root / "summary.json", summary)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "work_package": "RW-19",
        "capture_kind": "alpaca-news-ws-rest",
        "window_start": window_start.astimezone(UTC).isoformat(),
        "window_end": window_end.astimezone(UTC).isoformat(),
        "universe": list(scope),
        "sample_size": {"websocket": len(stream_rows), "rest": len(rest_rows)},
        "environment": environment,
        "raw": {
            "websocket": {"path": "raw/alpaca-news-ws.json", "sha256": stream_hash},
            "rest": {"path": "raw/alpaca-news-rest.json", "sha256": rest_hash},
        },
        "summary": {"path": "summary.json", "sha256": summary_hash},
        "source_terms": {
            "websocket": "https://docs.alpaca.markets/us/docs/streaming-real-time-news",
            "rest": "https://docs.alpaca.markets/us/docs/historical-news-data",
        },
        "limitations": [
            "A zero-frame window is an insufficient sample, not evidence of stream correctness.",
            "Article IDs test identity equivalence; they do not prove correction ordering alone.",
            "Raw article content is local evidence and must not be committed or redistributed.",
        ],
    }
    manifest_path = capture_root / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def write_news_reconnect_evidence(
    root: Path,
    *,
    window_start: datetime,
    disconnected_at: datetime,
    reconnected_at: datetime,
    window_end: datetime,
    symbols: Iterable[str],
    before_rows: list[dict],
    after_rows: list[dict],
    rest_rows: list[dict],
    environment: dict[str, Any],
) -> Path:
    """Write an immutable two-segment news reconnect and REST-backfill capture."""
    timestamps = (window_start, disconnected_at, reconnected_at, window_end)
    if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
        raise ValueError("capture and reconnect timestamps must be timezone-aware")
    if not window_start < disconnected_at <= reconnected_at < window_end:
        raise ValueError("news reconnect timestamps are not monotonic")
    scope = tuple(
        sorted(set(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()))
    )
    if not scope:
        raise ValueError("an explicit non-empty symbol universe is required")
    capture_id = window_start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    capture_root = Path(root) / "news-reconnect" / window_start.date().isoformat() / capture_id
    if capture_root.exists():
        raise FileExistsError(f"capture already exists: {capture_root}")

    reconnect = characterize_news_reconnect(
        before_rows,
        after_rows,
        rest_rows,
        disconnected_at=disconnected_at,
        reconnected_at=reconnected_at,
    )
    before_identity = _news_identity_rows(before_rows)
    after_identity = _news_identity_rows(after_rows)
    combined_identity = [*before_identity, *after_identity]
    rest_identity = [{"identity": row.get("id")} for row in rest_rows if isinstance(row, dict)]
    summary = {
        "before": characterize_identity_records(before_identity),
        "after": characterize_identity_records(after_identity),
        "combined_stream": characterize_identity_records(combined_identity),
        "ws_rest_identity": compare_identity_surfaces(combined_identity, rest_identity),
        "reconnect": reconnect,
    }

    before_hash = _write_json(capture_root / "raw" / "alpaca-news-ws-before.json", before_rows)
    after_hash = _write_json(capture_root / "raw" / "alpaca-news-ws-after.json", after_rows)
    rest_hash = _write_json(capture_root / "raw" / "alpaca-news-rest.json", rest_rows)
    summary_hash = _write_json(capture_root / "summary.json", summary)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "work_package": "RW-19",
        "capture_kind": "alpaca-news-reconnect-ws-rest",
        "window_start": window_start.astimezone(UTC).isoformat(),
        "disconnected_at": disconnected_at.astimezone(UTC).isoformat(),
        "reconnected_at": reconnected_at.astimezone(UTC).isoformat(),
        "window_end": window_end.astimezone(UTC).isoformat(),
        "universe": list(scope),
        "sample_size": {
            "websocket_before": len(before_rows),
            "websocket_after": len(after_rows),
            "rest": len(rest_rows),
        },
        "environment": environment,
        "raw": {
            "websocket_before": {
                "path": "raw/alpaca-news-ws-before.json",
                "sha256": before_hash,
            },
            "websocket_after": {
                "path": "raw/alpaca-news-ws-after.json",
                "sha256": after_hash,
            },
            "rest": {"path": "raw/alpaca-news-rest.json", "sha256": rest_hash},
        },
        "summary": {"path": "summary.json", "sha256": summary_hash},
        "source_terms": {
            "websocket": "https://docs.alpaca.markets/us/docs/streaming-real-time-news",
            "rest": "https://docs.alpaca.markets/us/docs/historical-news-data",
        },
        "limitations": [
            "One deliberate reconnect cannot establish a production loss rate.",
            "No observed correction is not evidence that corrections never occur.",
            "REST exposes the latest article representation and may not preserve update history.",
            "Raw article content is local evidence and must not be committed or redistributed.",
        ],
    }
    manifest_path = capture_root / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def write_market_stream_evidence(
    root: Path,
    *,
    window_start: datetime,
    window_end: datetime,
    feed: str,
    symbols: Iterable[str],
    rows: list[dict],
    environment: dict[str, Any],
) -> Path:
    """Write an immutable bounded Alpaca market-WebSocket capture."""
    if any(value.tzinfo is None or value.utcoffset() is None for value in (window_start, window_end)):
        raise ValueError("capture window must be timezone-aware")
    if window_end <= window_start:
        raise ValueError("capture window end must follow its start")
    if feed not in {"test", "iex", "sip"}:
        raise ValueError("feed must be test, iex, or sip")
    scope = tuple(sorted(set(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip())))
    if not scope:
        raise ValueError("an explicit non-empty symbol universe is required")
    capture_id = window_start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    capture_root = Path(root) / "market" / window_start.date().isoformat() / capture_id
    if capture_root.exists():
        raise FileExistsError(f"capture already exists: {capture_root}")
    raw_hash = _write_json(capture_root / "raw" / f"alpaca-{feed}-ws.json", rows)
    summary = characterize_market_frames(rows)
    summary_hash = _write_json(capture_root / "summary.json", summary)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "work_package": "RW-19",
        "capture_kind": "alpaca-market-websocket",
        "window_start": window_start.astimezone(UTC).isoformat(),
        "window_end": window_end.astimezone(UTC).isoformat(),
        "feed": feed,
        "universe": list(scope),
        "sample_size": {"wire_frames": len(rows)},
        "environment": environment,
        "raw": {
            "websocket": {"path": f"raw/alpaca-{feed}-ws.json", "sha256": raw_hash},
        },
        "summary": {"path": "summary.json", "sha256": summary_hash},
        "source_terms": {
            "websocket": "https://docs.alpaca.markets/us/v1.1/docs/streaming-market-data",
        },
        "limitations": [
            "The test feed proves wire/auth/capture behavior only, never production density.",
            "A bounded IEX/SIP capture cannot prove REST backfill identity without comparison.",
            "Synthetic message identity T:S:t is a measurement key, not vendor idempotency.",
        ],
    }
    manifest_path = capture_root / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def write_capability_evidence(
    root: Path,
    *,
    captured_at: datetime,
    symbol: str,
    probes: dict[str, dict],
    environment: dict[str, Any],
) -> Path:
    """Write raw, hash-bound market-data entitlement probes and a small derived summary."""
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("captured_at must be timezone-aware")
    clean_symbol = str(symbol).strip().upper()
    if not clean_symbol:
        raise ValueError("an explicit probe symbol is required")
    capture_id = captured_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    capture_root = Path(root) / "capabilities" / captured_at.date().isoformat() / capture_id
    if capture_root.exists():
        raise FileExistsError(f"capture already exists: {capture_root}")
    raw_hash = _write_json(capture_root / "raw" / "alpaca-latest-quotes.json", probes)
    summary = {
        name: {
            "status_code": probe.get("status_code"),
            "available": probe.get("status_code") == 200,
            "source_ts": (
                ((probe.get("body") or {}).get("quote") or {}).get("t")
                if isinstance(probe.get("body"), dict) else None
            ),
        }
        for name, probe in sorted(probes.items())
    }
    summary_hash = _write_json(capture_root / "summary.json", summary)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "work_package": "RW-19",
        "capture_kind": "alpaca-market-data-capabilities",
        "captured_at": captured_at.astimezone(UTC).isoformat(),
        "universe": [clean_symbol],
        "environment": environment,
        "raw": {
            "latest_quotes": {
                "path": "raw/alpaca-latest-quotes.json", "sha256": raw_hash,
            },
        },
        "summary": {"path": "summary.json", "sha256": summary_hash},
        "source_terms": {
            "market_data": "https://docs.alpaca.markets/us/docs/about-market-data-api",
            "feed_comparison": "https://docs.alpaca.markets/us/docs/market-data-faq",
        },
        "limitations": [
            "Capabilities are account-specific and must be re-probed after entitlement changes.",
            "A successful latest quote proves access at one instant, not stream reliability.",
        ],
    }
    manifest_path = capture_root / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def utc_day_bounds(day: date) -> tuple[str, str]:
    """Return a full UTC-day interval; historical captures intentionally reject today."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()
