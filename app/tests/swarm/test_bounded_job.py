"""RW-34 bounded-job controller. Fake children first; one real admitted fixture."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

import pytest

ROOT = Path(__file__).resolve().parents[3]
JOB_PATH = ROOT / "docs/research/swarm-tools/bounded_job.py"
CHILD_PATH = ROOT / "docs/research/swarm-tools/json_child.py"
BRIEF = ROOT / "docs/research/swarm-briefs/rw34-json-child-fixture.md"
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # @dataclass looks up cls.__module__ in sys.modules during decoration.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


job = _load("bounded_job", JOB_PATH)


class FakeTransport:
    def __init__(self, result: job.ProcResult):
        self.result = result
        self.calls: list[dict] = []

    def run(self, argv, input_bytes, timeout_s, cwd, env, *, output_limit, cancel_event=None):
        self.calls.append({
            "argv": list(argv),
            "cwd": Path(cwd),
            "env": dict(env),
            "input_bytes": input_bytes,
            "output_limit": output_limit,
        })
        if cancel_event is not None and cancel_event.is_set():
            return job.ProcResult(None, b"", b"", False, True)
        if "SECRET" in env or "CURSOR_AGENT" in env or "COCKPIT_ALPACA" in env:
            pytest.fail("child inherited forbidden environment")
        return self.result


def _envelope(**updates):
    brief = "docs/research/swarm-briefs/rw34-json-child-fixture.md"
    digest = hashlib.sha256(BRIEF.read_bytes()).hexdigest()
    payload = {
        "job_id": "rw34-json-child-fixture",
        "claim_id": "RW-34",
        "attempt": 1,
        "role": "leaf",
        "principal": "cloud-head",
        "disclosure": "public-tracked",
        "brief_path": brief,
        "input_paths": [brief],
        "input_sha256": {brief: digest},
        "policy_hash": job.profile_digest(),
        "schema_kind": "leaf",
        "deadline_utc": "2026-09-15T18:00:00+00:00",
        "timeout_seconds": 20,
        "output_byte_limit": 32768,
        "reservation_id": "res-1",
        "budget_scope": "rw34-cloud-json-child",
        "remaining_usd": "0",
        "reserved_usd": "0",
        "profile_id": job.PROFILE_ID,
        "acquisition_allowlist": [],
    }
    payload.update(updates)
    return payload


def _controller(tmp_path, transport=None, max_concurrent=1):
    return job.Controller(
        tmp_path / "run",
        transport=transport,
        now_fn=lambda: NOW,
        max_concurrent=max_concurrent,
    )


def _final(payload: dict) -> bytes:
    return (json.dumps({"type": "final", "payload": payload}) + "\n").encode("utf-8")


def test_json_child_has_no_shell_or_network_imports():
    source = CHILD_PATH.read_text(encoding="utf-8")
    for name in ("subprocess", "socket", "urllib", "requests", "httpx", "pathlib"):
        assert f"import {name}" not in source
        assert f"from {name}" not in source


def test_subprocess_transport_drains_closed_stdin_stdout(tmp_path):
    script = tmp_path / "echo_child.py"
    script.write_text("import sys; sys.stdout.write('hello-from-child\\n')\n", encoding="utf-8")
    result = job.SubprocessTransport().run(
        [sys.executable, str(script)],
        b"",
        5,
        tmp_path,
        job.child_env(),
        output_limit=1024,
    )
    assert result.returncode == 0
    assert result.timed_out is False
    assert b"hello-from-child" in result.stdout


def test_missing_admission_refuses_before_spawn(tmp_path):
    transport = FakeTransport(job.ProcResult(0, b"should-not-run", b"", False, False))
    with pytest.raises(job.AdmissionRefused, match="missing"):
        _controller(tmp_path, transport).run({"job_id": "x"})
    assert transport.calls == []


def test_expired_admission_refuses_before_spawn(tmp_path):
    transport = FakeTransport(job.ProcResult(0, b"should-not-run", b"", False, False))
    with pytest.raises(job.AdmissionRefused, match="expired"):
        _controller(tmp_path, transport).run(
            _envelope(deadline_utc="2026-09-15T11:00:00+00:00")
        )
    assert transport.calls == []


def test_changed_inputs_refused(tmp_path):
    transport = FakeTransport(job.ProcResult(0, b"", b"", False, False))
    with pytest.raises(job.AdmissionRefused, match="hashes"):
        _controller(tmp_path, transport).run(
            _envelope(input_sha256={
                "docs/research/swarm-briefs/rw34-json-child-fixture.md": "0" * 64
            })
        )
    assert transport.calls == []


def test_admission_key_aligns_windows_path_str_with_posix_maps():
    posix = "docs/research/swarm-briefs/rw34-json-child-fixture.md"
    windows = str(PureWindowsPath(posix))
    assert "\\" in windows
    digest = hashlib.sha256(BRIEF.read_bytes()).hexdigest()
    assert {windows: digest} != {posix: digest}
    assert {job.admission_key(windows): digest} == {posix: digest}
    assert {job.admission_key(PureWindowsPath(posix)): digest} == {posix: digest}


def test_backslash_input_sha256_keys_admit_when_content_matches(tmp_path):
    posix = "docs/research/swarm-briefs/rw34-json-child-fixture.md"
    digest = hashlib.sha256(BRIEF.read_bytes()).hexdigest()
    payload = {
        "job_id": "rw34-json-child-fixture",
        "claim_id": "RW-34",
        "attempt": 1,
        "role": "leaf",
        "brief_path": posix,
        "status": "success",
        "empirical_pass_claimed": False,
        "route_family": "ignored",
        "result": {"summary": "ok", "findings": [{"id": "a", "claim": "cited"}]},
    }
    transport = FakeTransport(job.ProcResult(0, _final(payload), b"", False, False))
    receipt = _controller(tmp_path, transport).run(_envelope(
        input_sha256={str(PureWindowsPath(posix)): digest},
    ))
    assert receipt.status == "success"
    assert transport.calls


def test_changed_profile_refused(tmp_path):
    transport = FakeTransport(job.ProcResult(0, b"", b"", False, False))
    with pytest.raises(job.AdmissionRefused, match="profile hash"):
        _controller(tmp_path, transport).run(_envelope(policy_hash="0" * 64))
    assert transport.calls == []


def test_paid_inference_and_acquisitions_refused(tmp_path):
    transport = FakeTransport(job.ProcResult(0, b"", b"", False, False))
    with pytest.raises(job.AdmissionRefused, match="zero inference"):
        _controller(tmp_path, transport).run(_envelope(reserved_usd="1"))
    with pytest.raises(job.AdmissionRefused, match="acquisitions"):
        _controller(tmp_path, transport).run(_envelope(acquisition_allowlist=["https://example"]))
    assert transport.calls == []


def test_second_gate_without_distinct_family_is_refused(tmp_path):
    transport = FakeTransport(job.ProcResult(0, b"", b"", False, False))
    with pytest.raises(job.AdmissionRefused, match="synthesizer"):
        _controller(tmp_path, transport).run(
            _envelope(role="muse-second-gate", schema_kind="synthesis")
        )
    assert transport.calls == []


def test_inspect_argv_rejects_capability_flags():
    argv = job.build_argv(python="python3", role="leaf", output_limit=1024)
    extra = argv + ["--mcp-config", "x.json"]
    with pytest.raises(job.ProfileError):
        job.inspect_argv(extra, role="leaf", output_limit=1024)
    with pytest.raises(job.ProfileError):
        job.inspect_argv(["python3", str(CHILD_PATH), "--role", "leaf", "--output-limit", "1", "--auto"],
                         role="leaf", output_limit=1)


def test_child_env_strips_credentials_and_cursor_config():
    env = job.child_env({
        "PATH": "/bin",
        "CURSOR_AGENT": "1",
        "OPENAI_API_KEY": "x",
        "COCKPIT_ALPACA_KEY": "y",
        "HOME": "/home/ubuntu",
        "LANG": "C",
    })
    assert "PATH" in env
    assert "CURSOR_AGENT" not in env
    assert "OPENAI_API_KEY" not in env
    assert "COCKPIT_ALPACA_KEY" not in env


def test_concurrent_cap_refuses_second_job(tmp_path):
    unknown = FakeTransport(job.ProcResult(0, b"", b"", False, False))
    first = _controller(tmp_path, unknown, max_concurrent=1)
    receipt = first.run(_envelope())
    assert receipt.status == "unknown" and receipt.hold is True
    second = FakeTransport(job.ProcResult(0, b"nope", b"", False, False))
    with pytest.raises(job.AdmissionRefused, match="concurrent"):
        job.Controller(
            tmp_path / "run",
            transport=second,
            now_fn=lambda: NOW,
            max_concurrent=1,
        ).run(_envelope(job_id="other", reservation_id="res-2"))
    assert second.calls == []


def test_duplicate_attempt_and_output_refused(tmp_path):
    payload = {
        "job_id": "fixture-leaf",
        "claim_id": "RW-34",
        "attempt": 1,
        "role": "leaf",
        "brief_path": "docs/research/swarm-briefs/rw34-json-child-fixture.md",
        "status": "success",
        "empirical_pass_claimed": False,
        "route_family": "ignored",
        "result": {"summary": "ok", "findings": [{"id": "a", "claim": "cited"}]},
    }
    transport = FakeTransport(job.ProcResult(0, _final(payload), b"", False, False))
    controller = _controller(tmp_path, transport)
    first = controller.run(_envelope())
    assert first.status == "success"
    with pytest.raises(job.AdmissionRefused, match="exclusive output"):
        controller.run(_envelope())


def test_timeout_and_cancel_do_not_write_filings(tmp_path):
    timeout = FakeTransport(job.ProcResult(None, b"partial", b"", True, False))
    timed = _controller(tmp_path, timeout).run(_envelope())
    assert timed.status == "timeout" and timed.filing_sha256 is None and timed.hold is False
    cancel = FakeTransport(job.ProcResult(None, b"partial", b"", False, False))
    event = threading.Event()
    event.set()
    cancelled = _controller(tmp_path, cancel).run(_envelope(attempt=2, reservation_id="res-c"),
                                                  cancel_event=event)
    assert cancelled.status == "cancelled" and cancelled.filing_sha256 is None


def test_oversized_malformed_and_unknown_streams(tmp_path):
    over = FakeTransport(job.ProcResult(0, b"x" * 40000, b"", False, False))
    oversized = _controller(tmp_path, over).run(_envelope())
    assert oversized.status == "refusal" and oversized.filing_sha256 is None
    bad = FakeTransport(job.ProcResult(0, b"{not-json", b"", False, False))
    malformed = _controller(tmp_path, bad).run(_envelope(attempt=2, reservation_id="r2"))
    assert malformed.status == "refusal"
    empty = FakeTransport(job.ProcResult(0, b"", b"", False, False))
    unknown = _controller(tmp_path, empty).run(_envelope(attempt=3, reservation_id="r3"))
    assert unknown.status == "unknown" and unknown.hold is True


def test_empirical_pass_is_quarantined(tmp_path):
    payload = {
        "job_id": "rw34-json-child-fixture",
        "claim_id": "RW-34",
        "attempt": 1,
        "role": "leaf",
        "brief_path": "docs/research/swarm-briefs/rw34-json-child-fixture.md",
        "status": "success",
        "empirical_pass_claimed": True,
        "route_family": "x",
        "result": {"summary": "nope"},
    }
    transport = FakeTransport(job.ProcResult(0, _final(payload), b"", False, False))
    receipt = _controller(tmp_path, transport).run(_envelope())
    assert receipt.status == "refusal"
    assert receipt.filing_sha256 is None
    assert receipt.empirical_pass_claimed is False


def test_sandbox_writes_are_not_filings(tmp_path):
    payload = {
        "job_id": "rw34-json-child-fixture",
        "claim_id": "RW-34",
        "attempt": 1,
        "role": "leaf",
        "brief_path": "docs/research/swarm-briefs/rw34-json-child-fixture.md",
        "status": "success",
        "empirical_pass_claimed": False,
        "route_family": "ignored",
        "result": {"summary": "ok", "findings": [{"id": "a", "claim": "cited"}]},
    }

    class JunkTransport(FakeTransport):
        def run(self, argv, input_bytes, timeout_s, cwd, env, *, output_limit, cancel_event=None):
            Path(cwd, "escaped.json").write_text("junk", encoding="utf-8")
            return super().run(
                argv, input_bytes, timeout_s, cwd, env,
                output_limit=output_limit, cancel_event=cancel_event,
            )

    transport = JunkTransport(job.ProcResult(0, _final(payload), b"", False, False))
    receipt = _controller(tmp_path, transport).run(_envelope())
    assert receipt.status == "success"
    filing = Path(receipt.paths["filing"]).read_text(encoding="utf-8")
    assert "junk" not in filing
    sandbox_junk = transport.calls[0]["cwd"] / "escaped.json"
    assert sandbox_junk.read_text(encoding="utf-8") == "junk"


def test_controller_overwrites_child_family_and_runs_first_gate(tmp_path):
    leaf_payload = {
        "job_id": "wrong",
        "claim_id": "wrong",
        "attempt": 9,
        "role": "leaf",
        "brief_path": "docs/research/swarm-briefs/rw34-json-child-fixture.md",
        "status": "success",
        "empirical_pass_claimed": False,
        "route_family": "untrusted-child-claim",
        "result": {"summary": "ok", "findings": [{"id": "a", "claim": "cited"}]},
    }
    leaf = _controller(tmp_path, FakeTransport(job.ProcResult(0, _final(leaf_payload), b"", False, False)))
    leaf_receipt = leaf.run(_envelope())
    assert leaf_receipt.route_family == job.ROUTE_FAMILY
    filing = json.loads(Path(leaf_receipt.paths["filing"]).read_text(encoding="utf-8"))
    assert filing["job_id"] == "rw34-json-child-fixture"
    assert filing["route_family"] == job.ROUTE_FAMILY
    review_payload = {
        "job_id": "review",
        "claim_id": "RW-34",
        "attempt": 1,
        "role": "first-gate-review",
        "subject_filing_path": leaf_receipt.paths["filing"],
        "status": "success",
        "empirical_pass_claimed": False,
        "verdict": "pass-with-dissent",
        "route_family": "untrusted-child-claim",
        "result": {
            "summary": "same-family first gate is useful, not independent",
            "findings": [{"severity": "note", "claim": "family bound by controller"}],
        },
    }
    review = job.Controller(
        tmp_path / "run",
        transport=FakeTransport(job.ProcResult(0, _final(review_payload), b"", False, False)),
        now_fn=lambda: NOW,
        max_concurrent=1,
    )
    review_receipt = review.run(_envelope(
        job_id="rw34-first-gate",
        attempt=1,
        reservation_id="res-review",
        role="first-gate-review",
        schema_kind="review",
        subject_filing_path=leaf_receipt.paths["filing"],
    ))
    assert review_receipt.status == "success"
    assert review_receipt.route_family == job.ROUTE_FAMILY
    reviewed = json.loads(Path(review_receipt.paths["filing"]).read_text(encoding="utf-8"))
    assert reviewed["verdict"] == "pass-with-dissent"
    assert reviewed["empirical_pass_claimed"] is False


def test_real_admitted_json_child_fixture(tmp_path):
    controller = job.Controller(tmp_path / "run", now_fn=lambda: NOW)
    receipt = controller.run(_envelope())
    assert receipt.status == "success"
    assert receipt.route_family == job.ROUTE_FAMILY
    assert receipt.usage_class == "measured"
    assert receipt.observed_usd == "0"
    assert receipt.empirical_pass_claimed is False
    filing = json.loads(Path(receipt.paths["filing"]).read_text(encoding="utf-8"))
    assert filing["result"]["findings"]
    raw = Path(receipt.paths["raw"]).read_bytes()
    assert receipt.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert job.inspect_argv(receipt.argv, role="leaf", output_limit=32768) is None
    review = job.Controller(tmp_path / "run", now_fn=lambda: NOW)
    review_receipt = review.run(_envelope(
        job_id="rw34-first-gate",
        attempt=1,
        reservation_id="res-review",
        role="first-gate-review",
        schema_kind="review",
        subject_filing_path=receipt.paths["filing"],
    ))
    assert review_receipt.status == "success"
    assert review_receipt.route_family == job.ROUTE_FAMILY
    reviewed = json.loads(Path(review_receipt.paths["filing"]).read_text(encoding="utf-8"))
    assert reviewed["verdict"] == "pass-with-dissent"
    assert reviewed["empirical_pass_claimed"] is False


def test_cli_unknown_flags_do_not_echo_secrets(tmp_path, capsys):
    envelope = tmp_path / "envelope.json"
    envelope.write_text(json.dumps(_envelope()), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        job.main(["run", "--envelope", str(envelope), "--run-dir", str(tmp_path / "out"),
                  "--api-key=secret-marker"])
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "secret-marker" not in output.err + output.out


def test_preview_does_not_spawn(tmp_path, capsys, monkeypatch):
    def unexpected(*_a, **_k):
        pytest.fail("preview spawned a child")

    monkeypatch.setattr(job.subprocess, "Popen", unexpected)
    envelope = tmp_path / "envelope.json"
    envelope.write_text(json.dumps(_envelope()), encoding="utf-8")
    assert job.main(["preview", "--envelope", str(envelope), "--run-dir", str(tmp_path / "out")]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["child_spawned"] is False
    assert preview["dispatch_enabled"] is True
    assert preview["profile_id"] == job.PROFILE_ID
