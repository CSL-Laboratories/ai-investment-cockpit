"""Hermetic tests for the prospective, plan-bound RW-19 news reconnect collector."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "tools" / "rw19_news_reconnect_plan_bound.py"
SPEC = importlib.util.spec_from_file_location("rw19_news_reconnect_plan_bound_tool", TOOL)
rw19 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rw19
SPEC.loader.exec_module(rw19)

SOURCE_COMMIT = "a" * 40
NOW = datetime(2026, 8, 25, 16, 0, tzinfo=UTC)
UNIVERSE = ["AAPL", "AVGO", "INTC", "META", "MRVL", "MSFT", "MU", "NVDA", "SNDK", "SPCX", "TSLA"]


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def write_json(path: Path, value) -> str:
    payload = canonical(value) + b"\n"
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def plan(tmp_path: Path, **changes) -> tuple[Path, str, dict]:
    value = {
        "schema_version": 1,
        "package": "RW-19",
        "scope": "prospective-news-reconnect-plan-only",
        "authority": "none",
        "rw19_gate": "OPEN_NOT_CLOSED",
        "dec07_status": "open",
        "source_commit": SOURCE_COMMIT,
        "authored_at": (NOW - timedelta(hours=1)).isoformat(),
        "selected_by": "Operator prospective catalyst-window selection; independent of outcomes.",
        "selection_rule": (
            "Select one issuer-announced catalyst window prospectively from official issuer "
            "relations material, before looking at Alpaca news outcomes. Retain the complete "
            "fixed 11-symbol awareness universe and every received frame; do not select or "
            "discard by article presence, identity, correction, content, or later result."
        ),
        "observation_window": {
            "scheduled_start": NOW.isoformat(),
            "start_tolerance_seconds": 10,
            "segment_seconds": 5,
            "gap_seconds": 1,
            "maximum_wall_seconds": 60,
        },
        "universe_symbols": UNIVERSE,
        "resource_limits": {
            "maximum_frames_per_segment": 20,
            "maximum_frame_bytes": 20_000,
            "maximum_control_frames_per_segment": 10,
            "maximum_rest_pages": 3,
            "maximum_rest_rows": 100,
            "maximum_rest_page_bytes": 50_000,
            "maximum_rest_total_bytes": 100_000,
            "maximum_rest_seconds": 30,
            "request_timeout_seconds": 5,
        },
        "output_paths": {
            "capture_directory": str(tmp_path / "capture"),
            "aborted_directory": str(tmp_path / "aborted"),
            "publication_rule": "isolated write-once",
        },
        "safety_preflight": {"required": True, "maximum_age_minutes": 15},
        "classification_contract": {
            "empty": "INSUFFICIENT_SAMPLE_NOT_QUIET_DAY",
            "nonempty": "OBSERVATION_ONLY_DEC07_OPEN",
            "failed": "FAILED_CAPTURE",
        },
        "known_blockers": list(rw19.KNOWN_BLOCKERS),
    }
    value.update(changes)
    path = tmp_path / "plan.json"
    return path, write_json(path, value), value


def preflight(tmp_path: Path, **changes) -> Path:
    value = {
        "schema_version": 1,
        "package": "RW-19",
        "scope": "prospective-news-reconnect-safety-preflight",
        "checked_at": (NOW - timedelta(minutes=1)).isoformat(),
        "source_commit": SOURCE_COMMIT,
        "configured_mode": "read_only",
        "effective_mode": "read_only",
        "persisted_mode": None,
        "expected_edge_available": False,
        "merger_execution_watchlist_count": 0,
        "merger_edge_rows": 0,
        "merger_selection_rows": 0,
        "proposal_rows": 0,
        "risk_decision_rows": 0,
        "approval_rows": 0,
        "order_rows": 0,
        "audit_chain_valid": True,
        "halt_absent": True,
        "alpaca_credentials_configured": True,
        "cockpit_service_processes": 0,
        "api_port_listeners": 0,
        "worktree_clean": True,
        "main_equals_origin_main": True,
        "awareness_universe_symbols": UNIVERSE,
    }
    value.update(changes)
    path = tmp_path / "preflight.json"
    write_json(path, value)
    return path


def source_state():
    return {"head": SOURCE_COMMIT, "origin_main": SOURCE_COMMIT, "worktree_clean": True}


class Socket:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self, timeout):
        assert timeout > 0
        if self.frames:
            frame = self.frames.pop(0)
            if isinstance(frame, BaseException):
                raise frame
            return frame
        raise TimeoutError


class Connector:
    def __init__(self, sockets):
        self.sockets = list(sockets)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.sockets.pop(0)


def success_socket(article_id: int | None = None) -> Socket:
    frames = [
        '[{"T":"success","msg":"connected"}]',
        '[{"T":"success","msg":"authenticated"}]',
        '[{"T":"subscription","news":' + json.dumps(UNIVERSE) + "}]",
    ]
    if article_id is not None:
        frames.append(json.dumps([{
            "T": "n",
            "id": article_id,
            "created_at": "2026-08-25T16:00:00Z",
            "updated_at": "2026-08-25T16:00:00Z",
            "symbols": ["NVDA"],
        }]))
    return Socket(frames)


class StreamResponse:
    def __init__(self, payload, *, status_code=200, request_id="request-1"):
        self.body = canonical(payload)
        self.status_code = status_code
        self.headers = {"X-Request-ID": request_id}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        midpoint = len(self.body) // 2
        yield self.body[:midpoint]
        yield self.body[midpoint:]


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, method, url, *, params):
        self.requests.append((method, url, dict(params)))
        return self.responses.pop(0)


class Clock:
    def __init__(self, start=NOW):
        self.value = start - timedelta(milliseconds=100)

    def __call__(self):
        self.value += timedelta(milliseconds=100)
        return self.value


def client_factory(responses, holder):
    def factory(**kwargs):
        holder["kwargs"] = kwargs
        holder["client"] = Client(responses)
        return holder["client"]
    return factory


def test_refuses_bad_plan_hash_before_credentials_or_output(tmp_path):
    plan_path, _plan_hash, _ = plan(tmp_path)
    credential_reads = []
    with pytest.raises(rw19.EvidenceError, match="plan hash"):
        rw19.collect(
            plan_path,
            preflight(tmp_path),
            tmp_path / "capture",
            expected_plan_sha256="0" * 64,
            credential_loader=lambda: credential_reads.append(True),
            source_state=source_state(),
            clock=Clock(),
        )
    assert credential_reads == []
    assert not (tmp_path / "capture").exists()
    assert not (tmp_path / "aborted").exists()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"effective_mode": "paper"}, "read_only"),
        ({"order_rows": 1}, "nonzero"),
        ({"audit_chain_valid": False}, "fail-closed"),
        ({"alpaca_credentials_configured": False}, "fail-closed"),
        ({"awareness_universe_symbols": UNIVERSE[:-1]}, "universe"),
        ({"checked_at": (NOW - timedelta(minutes=16)).isoformat()}, "older than 15"),
    ],
)
def test_preflight_failures_are_fail_closed_before_credentials(tmp_path, change, message):
    plan_path, plan_hash, _ = plan(tmp_path)
    credential_reads = []
    with pytest.raises(rw19.EvidenceError, match=message):
        rw19.collect(
            plan_path,
            preflight(tmp_path, **change),
            tmp_path / "capture",
            expected_plan_sha256=plan_hash,
            credential_loader=lambda: credential_reads.append(True),
            source_state=source_state(),
            clock=Clock(),
        )
    assert credential_reads == []


def test_segment_requires_ordered_authentication_and_exact_subscription():
    socket = Socket([
        '[{"T":"success","msg":"connected"}]',
        '[{"T":"success","msg":"authenticated"}]',
        '[{"T":"subscription","news":["AAPL"]}]',
    ])
    with pytest.raises(rw19.CaptureFailure, match="subscription") as error:
        rw19._capture_news_segment(
            connector=Connector([socket]),
            key="fixture-key",
            secret="fixture-secret",
            symbols=tuple(UNIVERSE),
            duration_seconds=5,
            limits={
                "maximum_frames_per_segment": 20,
                "maximum_frame_bytes": 20_000,
                "maximum_control_frames_per_segment": 10,
            },
            row_sink=lambda _row: None,
            control_sink=lambda _row: None,
            clock=Clock(),
            monotonic=lambda: 0,
        )
    assert error.value.failure_class == "socket_subscribe"
    assert socket.sent == [
        {"action": "auth", "key": "fixture-key", "secret": "fixture-secret"},
        {"action": "subscribe", "news": UNIVERSE},
    ]


def test_segment_buffers_news_arriving_before_subscription_ack():
    article = {
        "T": "n",
        "id": 77,
        "created_at": "2026-08-25T16:00:00Z",
        "updated_at": "2026-08-25T16:00:00Z",
    }
    socket = Socket([
        '[{"T":"success","msg":"connected"}]',
        '[{"T":"success","msg":"authenticated"}]',
        json.dumps([article]),
        '[{"T":"subscription","news":' + json.dumps(UNIVERSE) + "}]",
    ])
    rows = []
    controls = []
    rw19._capture_news_segment(
        connector=Connector([socket]),
        key="fixture-key",
        secret="fixture-secret",
        symbols=tuple(UNIVERSE),
        duration_seconds=5,
        limits={
            "maximum_frames_per_segment": 20,
            "maximum_frame_bytes": 20_000,
            "maximum_control_frames_per_segment": 10,
        },
        row_sink=rows.append,
        control_sink=controls.append,
        clock=Clock(),
        monotonic=lambda: 0,
    )
    assert [row["message"]["id"] for row in rows] == [77]
    assert controls[-1]["message"] == {"T": "subscription", "news": UNIVERSE}


@pytest.mark.parametrize(
    ("limit_change", "failure_class"),
    [
        ({"maximum_frames_per_segment": 3}, "frame_cap"),
        ({"maximum_frame_bytes": 5}, "frame_bytes"),
    ],
)
def test_segment_resource_caps_fail_closed(limit_change, failure_class):
    limits = {
        "maximum_frames_per_segment": 20,
        "maximum_frame_bytes": 20_000,
        "maximum_control_frames_per_segment": 10,
    }
    limits.update(limit_change)
    with pytest.raises(rw19.CaptureFailure) as error:
        rw19._capture_news_segment(
            connector=Connector([success_socket(1)]),
            key="fixture-key",
            secret="fixture-secret",
            symbols=tuple(UNIVERSE),
            duration_seconds=5,
            limits=limits,
            row_sink=lambda _row: None,
            control_sink=lambda _row: None,
            clock=Clock(),
            monotonic=lambda: 0,
        )
    assert error.value.failure_class == failure_class


def test_segment_rejects_backwards_wall_clock():
    times = iter([NOW, NOW + timedelta(seconds=1), NOW - timedelta(seconds=1)])
    with pytest.raises(rw19.CaptureFailure) as error:
        rw19._capture_news_segment(
            connector=Connector([success_socket()]),
            key="fixture-key",
            secret="fixture-secret",
            symbols=tuple(UNIVERSE),
            duration_seconds=5,
            limits={
                "maximum_frames_per_segment": 20,
                "maximum_frame_bytes": 20_000,
                "maximum_control_frames_per_segment": 10,
            },
            row_sink=lambda _row: None,
            control_sink=lambda _row: None,
            clock=times.__next__,
            monotonic=lambda: 0,
        )
    assert error.value.failure_class == "clock_backwards"


def test_news_before_subscription_is_not_mislabeled_as_control():
    socket = Socket([
        '[{"T":"success","msg":"connected"}]',
        '[{"T":"n","id":1}]',
    ])
    controls = []
    with pytest.raises(rw19.CaptureFailure) as error:
        rw19._capture_news_segment(
            connector=Connector([socket]),
            key="fixture-key",
            secret="fixture-secret",
            symbols=tuple(UNIVERSE),
            duration_seconds=5,
            limits={
                "maximum_frames_per_segment": 20,
                "maximum_frame_bytes": 20_000,
                "maximum_control_frames_per_segment": 10,
            },
            row_sink=lambda _row: None,
            control_sink=controls.append,
            clock=Clock(),
            monotonic=lambda: 0,
        )
    assert error.value.failure_class == "socket_protocol"
    assert [row["message"]["T"] for row in controls] == ["success"]


def test_rest_rejects_repeated_token_and_is_page_bounded():
    client = Client([
        StreamResponse({"news": [{"id": 1}], "next_page_token": "repeat"}),
        StreamResponse({"news": [{"id": 2}], "next_page_token": "repeat"}),
    ])
    with pytest.raises(rw19.CaptureFailure, match="token") as error:
        rw19._fetch_news_rest(
            client,
            symbols=tuple(UNIVERSE),
            start=NOW,
            end=NOW + timedelta(minutes=1),
            limits={
                "maximum_rest_pages": 3,
                "maximum_rest_rows": 100,
                "maximum_rest_page_bytes": 50_000,
                "maximum_rest_total_bytes": 100_000,
                "maximum_rest_seconds": 30,
            },
            secrets=("fixture-key", "fixture-secret"),
            row_sink=lambda _row: None,
            page_sink=lambda _row: None,
            clock=Clock(),
            monotonic=iter([0, 1, 2, 3, 4]).__next__,
        )
    assert error.value.failure_class == "rest_token"
    assert len(client.requests) == 2
    assert client.requests[1][2]["page_token"] == "repeat"


@pytest.mark.parametrize(
    ("responses", "limit_change", "ticks", "failure_class"),
    [
        ([StreamResponse({"news": [{"id": 1}], "next_page_token": "next"})],
         {"maximum_rest_pages": 1}, range(20), "rest_page_cap"),
        ([StreamResponse({"news": [{"id": 1}, {"id": 2}]})],
         {"maximum_rest_rows": 1}, range(20), "rest_row_cap"),
        ([StreamResponse({"news": [{"id": 1, "headline": "x" * 200}]})],
         {"maximum_rest_page_bytes": 20}, range(20), "rest_page_bytes"),
        ([StreamResponse({"news": []})],
         {"maximum_rest_seconds": 1}, [0, 2], "rest_deadline"),
    ],
)
def test_rest_resource_caps_fail_closed(responses, limit_change, ticks, failure_class):
    limits = {
        "maximum_rest_pages": 3,
        "maximum_rest_rows": 100,
        "maximum_rest_page_bytes": 50_000,
        "maximum_rest_total_bytes": 100_000,
        "maximum_rest_seconds": 30,
    }
    limits.update(limit_change)
    with pytest.raises(rw19.CaptureFailure) as error:
        rw19._fetch_news_rest(
            Client(responses),
            symbols=tuple(UNIVERSE),
            start=NOW,
            end=NOW + timedelta(minutes=1),
            limits=limits,
            secrets=("fixture-key", "fixture-secret"),
            row_sink=lambda _row: None,
            page_sink=lambda _row: None,
            clock=Clock(),
            monotonic=iter(ticks).__next__,
        )
    assert error.value.failure_class == failure_class


def test_strict_json_and_jsonl_reject_ambiguous_or_truncated_input(tmp_path):
    with pytest.raises(rw19.EvidenceError, match="duplicate"):
        rw19._strict_json(b'{"same":1,"same":2}', "fixture")
    path = tmp_path / "truncated.jsonl"
    path.write_bytes(b'{"row":1}')
    with pytest.raises(rw19.EvidenceError, match="truncated"):
        rw19._read_jsonl(path, "fixture")


def test_atomic_publish_refuses_existing_target_and_preserves_staging(tmp_path):
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    staging.mkdir()
    target.mkdir()
    (staging / "rows.jsonl").write_text("preserved\n", encoding="utf-8")
    with pytest.raises(rw19.EvidenceError, match="already exists"):
        rw19._publish(staging, target)
    assert (staging / "rows.jsonl").read_text(encoding="utf-8") == "preserved\n"


def test_complete_capture_publishes_and_immediately_verifies(tmp_path):
    plan_path, plan_hash, _ = plan(tmp_path)
    connector = Connector([success_socket(101), success_socket(102)])
    holder = {}
    result = rw19.collect(
        plan_path,
        preflight(tmp_path),
        tmp_path / "capture",
        expected_plan_sha256=plan_hash,
        credential_loader=lambda: ("fixture-key", "fixture-secret"),
        connector=connector,
        client_factory=client_factory([
            StreamResponse({"news": [{
                "id": 103,
                "created_at": "2026-08-25T16:00:00Z",
                "updated_at": "2026-08-25T16:00:00Z",
            }], "next_page_token": None})
        ], holder),
        source_state=source_state(),
        source_state_reader=source_state,
        clock=Clock(),
        monotonic=iter(range(100)).__next__,
        sleeper=lambda _seconds: None,
    )
    assert result["status"] == "complete"
    assert result["classification"] == "OBSERVATION_ONLY_DEC07_OPEN"
    assert result["sample_size"] == {
        "websocket_before": 1,
        "websocket_after": 1,
        "rest": 1,
    }
    assert result["authority"] == "none"
    assert result["rw19_gate"] == "OPEN_NOT_CLOSED"
    assert result["dec07_status"] == "open"
    assert holder["client"].requests[0][0] == "GET"
    assert holder["client"].requests[0][2]["include_content"] == "false"
    assert rw19.verify(tmp_path / "capture", expected_plan_sha256=plan_hash)["ok"] is True
    assert not (tmp_path / "aborted").exists()


def test_empty_complete_capture_is_insufficient_not_a_quiet_day(tmp_path):
    plan_path, plan_hash, _ = plan(tmp_path)
    result = rw19.collect(
        plan_path,
        preflight(tmp_path),
        tmp_path / "capture",
        expected_plan_sha256=plan_hash,
        credential_loader=lambda: ("fixture-key", "fixture-secret"),
        connector=Connector([success_socket(), success_socket()]),
        client_factory=client_factory([
            StreamResponse({"news": [], "next_page_token": None})
        ], {}),
        source_state=source_state(),
        source_state_reader=source_state,
        clock=Clock(),
        monotonic=iter(range(100)).__next__,
        sleeper=lambda _seconds: None,
    )
    assert result["classification"] == "INSUFFICIENT_SAMPLE_NOT_QUIET_DAY"
    assert result["summary"]["reconnect"]["combined_stream_recall_of_rest"] is None


def test_post_capture_source_drift_is_tombstoned(tmp_path):
    plan_path, plan_hash, _ = plan(tmp_path)
    dirty = {**source_state(), "worktree_clean": False}
    result = rw19.collect(
        plan_path,
        preflight(tmp_path),
        tmp_path / "capture",
        expected_plan_sha256=plan_hash,
        credential_loader=lambda: ("fixture-key", "fixture-secret"),
        connector=Connector([success_socket(101), success_socket(102)]),
        client_factory=client_factory([StreamResponse({"news": []})], {}),
        source_state=source_state(),
        source_state_reader=lambda: dirty,
        clock=Clock(),
        monotonic=iter(range(100)).__next__,
        sleeper=lambda _seconds: None,
    )
    assert result["status"] == "aborted"
    assert result["failure_class"] == "evidence_failure"
    assert not (tmp_path / "capture").exists()


def test_overall_wall_deadline_is_enforced(tmp_path):
    plan_path, plan_hash, _ = plan(tmp_path)
    ticks = iter([*range(14), 100])
    result = rw19.collect(
        plan_path,
        preflight(tmp_path),
        tmp_path / "capture",
        expected_plan_sha256=plan_hash,
        credential_loader=lambda: ("fixture-key", "fixture-secret"),
        connector=Connector([success_socket(), success_socket()]),
        client_factory=client_factory([StreamResponse({"news": []})], {}),
        source_state=source_state(),
        source_state_reader=source_state,
        clock=Clock(),
        monotonic=ticks.__next__,
        sleeper=lambda _seconds: None,
    )
    assert result["status"] == "aborted"
    assert result["failure_class"] == "wall_deadline"


def test_second_segment_failure_publishes_partial_tombstone_without_final_capture(tmp_path):
    plan_path, plan_hash, _ = plan(tmp_path)
    failed = Socket([
        '[{"T":"success","msg":"connected"}]',
        '[{"T":"error","code":500,"msg":"vendor secret fixture-secret"}]',
    ])
    result = rw19.collect(
        plan_path,
        preflight(tmp_path),
        tmp_path / "capture",
        expected_plan_sha256=plan_hash,
        credential_loader=lambda: ("fixture-key", "fixture-secret"),
        connector=Connector([success_socket(101), failed]),
        client_factory=lambda **_kwargs: pytest.fail("REST must not run"),
        source_state=source_state(),
        source_state_reader=source_state,
        clock=Clock(),
        monotonic=iter(range(100)).__next__,
        sleeper=lambda _seconds: None,
    )
    assert result["status"] == "aborted"
    assert result["classification"] == "FAILED_CAPTURE"
    assert result["failure_class"] == "secret_reflection"
    assert not (tmp_path / "capture").exists()
    tombstone = tmp_path / "aborted"
    assert tombstone.is_dir()
    assert rw19.verify_aborted(tombstone, expected_plan_sha256=plan_hash)["ok"] is True
    all_bytes = b"".join(path.read_bytes() for path in tombstone.rglob("*") if path.is_file())
    assert b"fixture-secret" not in all_bytes
    before = rw19._read_jsonl(tombstone / "raw" / "ws-before.jsonl", "test")
    assert [row["message"]["id"] for row in before] == [101]


def test_keyboard_interrupt_publishes_partial_tombstone(tmp_path):
    plan_path, plan_hash, _ = plan(tmp_path)
    interrupted = Socket([
        '[{"T":"success","msg":"connected"}]',
        KeyboardInterrupt(),
    ])
    result = rw19.collect(
        plan_path,
        preflight(tmp_path),
        tmp_path / "capture",
        expected_plan_sha256=plan_hash,
        credential_loader=lambda: ("fixture-key", "fixture-secret"),
        connector=Connector([success_socket(101), interrupted]),
        client_factory=lambda **_kwargs: pytest.fail("REST must not run"),
        source_state=source_state(),
        source_state_reader=source_state,
        clock=Clock(),
        monotonic=iter(range(100)).__next__,
        sleeper=lambda _seconds: None,
    )
    assert result["status"] == "aborted"
    assert result["failure_class"] == "interrupted"
    assert rw19.verify_aborted(
        tmp_path / "aborted", expected_plan_sha256=plan_hash
    )["ok"] is True


def test_secondary_tombstone_failure_preserves_recovery_staging(tmp_path, monkeypatch):
    plan_path, plan_hash, _ = plan(tmp_path)
    failed = Socket([
        '[{"T":"success","msg":"connected"}]',
        '[{"T":"error","code":500,"msg":"unavailable"}]',
    ])
    original = rw19._write_json

    def fail_failure_artifact(path, value):
        if path.name == rw19.FAILURE_NAME:
            raise OSError("fixture disk full")
        return original(path, value)

    monkeypatch.setattr(rw19, "_write_json", fail_failure_artifact)
    with pytest.raises(OSError, match="fixture disk full"):
        rw19.collect(
            plan_path,
            preflight(tmp_path),
            tmp_path / "capture",
            expected_plan_sha256=plan_hash,
            credential_loader=lambda: ("fixture-key", "fixture-secret"),
            connector=Connector([success_socket(101), failed]),
            client_factory=lambda **_kwargs: pytest.fail("REST must not run"),
            source_state=source_state(),
            source_state_reader=source_state,
            clock=Clock(),
            monotonic=iter(range(100)).__next__,
            sleeper=lambda _seconds: None,
        )
    recoveries = list(tmp_path.glob("aborted.recovery.*"))
    assert len(recoveries) == 1
    before = rw19._read_jsonl(recoveries[0] / "raw" / "ws-before.jsonl", "test")
    assert [row["message"]["id"] for row in before] == [101]


def test_pre_capture_setup_failure_leaves_no_staging_directory(tmp_path, monkeypatch):
    plan_path, plan_hash, _ = plan(tmp_path)
    preflight_path = preflight(tmp_path)
    original = Path.write_bytes

    def fail_plan_copy(path, payload):
        if path.name == rw19.PLAN_NAME:
            raise OSError("fixture disk full")
        return original(path, payload)

    monkeypatch.setattr(Path, "write_bytes", fail_plan_copy)
    with pytest.raises(OSError, match="fixture disk full"):
        rw19.collect(
            plan_path,
            preflight_path,
            tmp_path / "capture",
            expected_plan_sha256=plan_hash,
            credential_loader=lambda: ("fixture-key", "fixture-secret"),
            source_state=source_state(),
            clock=Clock(),
        )
    assert list(tmp_path.glob(".rw19-news-reconnect.*")) == []


def test_secret_reflection_in_rest_body_is_never_persisted(tmp_path):
    plan_path, plan_hash, _ = plan(tmp_path)
    holder = {}
    result = rw19.collect(
        plan_path,
        preflight(tmp_path),
        tmp_path / "capture",
        expected_plan_sha256=plan_hash,
        credential_loader=lambda: ("fixture-key", "fixture-secret"),
        connector=Connector([success_socket(101), success_socket(102)]),
        client_factory=client_factory([
            StreamResponse({"news": [{"id": 1, "headline": "fixture-secret"}]})
        ], holder),
        source_state=source_state(),
        source_state_reader=source_state,
        clock=Clock(),
        monotonic=iter(range(100)).__next__,
        sleeper=lambda _seconds: None,
    )
    assert result["failure_class"] == "secret_reflection"
    all_bytes = b"".join(
        path.read_bytes() for path in (tmp_path / "aborted").rglob("*") if path.is_file()
    )
    assert b"fixture-secret" not in all_bytes


def test_verifier_rejects_extra_artifact(tmp_path):
    plan_path, plan_hash, _ = plan(tmp_path)
    rw19.collect(
        plan_path,
        preflight(tmp_path),
        tmp_path / "capture",
        expected_plan_sha256=plan_hash,
        credential_loader=lambda: ("fixture-key", "fixture-secret"),
        connector=Connector([success_socket(), success_socket()]),
        client_factory=client_factory([StreamResponse({"news": []})], {}),
        source_state=source_state(),
        source_state_reader=source_state,
        clock=Clock(),
        monotonic=iter(range(100)).__next__,
        sleeper=lambda _seconds: None,
    )
    (tmp_path / "capture" / "unexpected.txt").write_text("tamper", encoding="utf-8")
    with pytest.raises(rw19.EvidenceError, match="artifact set"):
        rw19.verify(tmp_path / "capture", expected_plan_sha256=plan_hash)


def test_verifier_rejects_reordered_capture_timestamps(tmp_path):
    plan_path, plan_hash, _ = plan(tmp_path)
    rw19.collect(
        plan_path,
        preflight(tmp_path),
        tmp_path / "capture",
        expected_plan_sha256=plan_hash,
        credential_loader=lambda: ("fixture-key", "fixture-secret"),
        connector=Connector([success_socket(), success_socket()]),
        client_factory=client_factory([StreamResponse({"news": []})], {}),
        source_state=source_state(),
        source_state_reader=source_state,
        clock=Clock(),
        monotonic=iter(range(100)).__next__,
        sleeper=lambda _seconds: None,
    )
    manifest_path = tmp_path / "capture" / rw19.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["disconnected_at"] = (NOW + timedelta(hours=1)).isoformat()
    sidecar_hash = write_json(manifest_path, manifest)
    (tmp_path / "capture" / rw19.MANIFEST_HASH_NAME).write_text(
        sidecar_hash + "\n", encoding="ascii"
    )
    with pytest.raises(rw19.EvidenceError, match="timestamp ordering"):
        rw19.verify(tmp_path / "capture", expected_plan_sha256=plan_hash)


def test_relative_or_repository_output_is_refused_before_credentials(tmp_path):
    for capture, aborted, message in (
        ("relative-capture", "relative-aborted", "absolute"),
        (str(ROOT / "forbidden-capture"), str(ROOT / "forbidden-aborted"), "outside"),
    ):
        plan_path, plan_hash, _ = plan(tmp_path, output_paths={
            "capture_directory": capture,
            "aborted_directory": aborted,
            "publication_rule": "isolated write-once",
        })
        reads = []
        with pytest.raises(rw19.EvidenceError, match=message):
            rw19.collect(
                plan_path,
                preflight(tmp_path),
                Path(capture),
                expected_plan_sha256=plan_hash,
                credential_loader=lambda: reads.append(True),
                source_state=source_state(),
                clock=Clock(),
            )
        assert reads == []


def test_cli_turns_non_ascii_sidecar_into_clean_refusal(tmp_path, capsys):
    plan_path, plan_hash, _ = plan(tmp_path)
    rw19.collect(
        plan_path,
        preflight(tmp_path),
        tmp_path / "capture",
        expected_plan_sha256=plan_hash,
        credential_loader=lambda: ("fixture-key", "fixture-secret"),
        connector=Connector([success_socket(), success_socket()]),
        client_factory=client_factory([StreamResponse({"news": []})], {}),
        source_state=source_state(),
        source_state_reader=source_state,
        clock=Clock(),
        monotonic=iter(range(100)).__next__,
        sleeper=lambda _seconds: None,
    )
    (tmp_path / "capture" / rw19.MANIFEST_HASH_NAME).write_bytes(b"\xff")
    exit_code = rw19.main([
        "verify",
        "--package-dir", str(tmp_path / "capture"),
        "--expected-plan-sha256", plan_hash,
    ])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "UnicodeDecodeError" in captured.err
    assert "Traceback" not in captured.err


def test_verifier_rejects_symlinked_package_root_when_supported(tmp_path):
    plan_path, plan_hash, _ = plan(tmp_path)
    rw19.collect(
        plan_path,
        preflight(tmp_path),
        tmp_path / "capture",
        expected_plan_sha256=plan_hash,
        credential_loader=lambda: ("fixture-key", "fixture-secret"),
        connector=Connector([success_socket(), success_socket()]),
        client_factory=client_factory([StreamResponse({"news": []})], {}),
        source_state=source_state(),
        source_state_reader=source_state,
        clock=Clock(),
        monotonic=iter(range(100)).__next__,
        sleeper=lambda _seconds: None,
    )
    link = tmp_path / "capture-link"
    try:
        link.symlink_to(tmp_path / "capture", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    with pytest.raises(rw19.EvidenceError, match="link or reparse"):
        rw19.verify(link, expected_plan_sha256=plan_hash)


def test_tool_imports_no_runtime_or_order_path_module():
    imported = set()
    for node in ast.walk(ast.parse(TOOL.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    forbidden = ("brokers", "config", "main", "orchestrator", "risk", "sqlalchemy", "storage")
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported
        for prefix in forbidden
    )
