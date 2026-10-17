"""CSL-227: the ceiling rejection uses the RFC 9110 status name.

Starlette keeps the old 422 alias only as a deprecated name. Reading it emits
``StarletteDeprecationWarning``. The numeric code stays 422.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Split so this module can be scanned with the rest of app/ and still not match itself.
_DEPRECATED_HTTP_422 = "HTTP_422_" + "UNPROCESSABLE_ENTITY"
_CURRENT_HTTP_422 = "HTTP_422_UNPROCESSABLE_CONTENT"

APP = Path(__file__).resolve().parents[2]


def _deprecated_http_422_offenders(root: Path) -> list[str]:
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in path.parts for part in (".git", "__pycache__")):
            continue
        if _DEPRECATED_HTTP_422 in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(root).as_posix())
    return offenders


def test_app_does_not_use_deprecated_http_422_alias():
    offenders = _deprecated_http_422_offenders(APP)
    assert not offenders, (
        "deprecated HTTP 422 alias found under app/: "
        + ", ".join(offenders)
        + ". Use "
        + _CURRENT_HTTP_422
    )


def test_ceiling_rejection_uses_rfc9110_status_name():
    source = (APP / "orchestrator" / "app.py").read_text(encoding="utf-8")
    assert "status_code=status." + _CURRENT_HTTP_422 in source


def test_deprecated_http_422_guard_detects_a_hit(tmp_path: Path):
    rogue = tmp_path / "rogue.py"
    rogue.write_text("status_code = status." + _DEPRECATED_HTTP_422 + "\n", encoding="utf-8")
    assert _deprecated_http_422_offenders(tmp_path) == ["rogue.py"]


# Quoted so a comment that merely mentions the floor does not satisfy the check.
_STARLETTE_FLOOR = '"starlette>=0.48.0"'


def _declares_starlette_floor(text: str) -> bool:
    return _STARLETTE_FLOOR in text


def _version_at_least(version: str, minimum: tuple[int, ...]) -> bool:
    numbers: list[int] = []
    for part in version.split("."):
        digits = ""
        for char in part:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            return False
        numbers.append(int(digits))
    while len(numbers) < len(minimum):
        numbers.append(0)
    return tuple(numbers[: len(minimum)]) >= minimum


def test_project_requires_starlette_with_rfc9110_status_names():
    """The quoted ``>=`` token is intentional. A comment, or a rewrapped
    install line that drops the quotes, must not satisfy pyproject, CLAUDE.md,
    or the Windows bootstrap."""
    root = APP.parent
    paths = (
        APP / "pyproject.toml",
        root / "CLAUDE.md",
        root / "tools" / "bootstrap_windows.ps1",
    )
    missing = [
        str(path.relative_to(root))
        for path in paths
        if not _declares_starlette_floor(path.read_text(encoding="utf-8"))
    ]
    assert not missing, missing
    requirements = (root / ".github/actions/run-gates/requirements.txt").read_text(encoding="utf-8")
    pins = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip().startswith("starlette==")
    ]
    assert len(pins) == 1, pins
    assert _version_at_least(pins[0].split("==", 1)[1], (0, 48, 0))


def test_starlette_floor_check_ignores_a_comment():
    assert not _declares_starlette_floor("# starlette>=0.48.0 is explicit\n")
    assert _declares_starlette_floor('pip install "starlette>=0.48.0"\n')


def test_ceiling_breach_returns_422_and_disarms_to_review_live(tmp_path: Path):
    """A ceiling breach stays HTTP 422 and reports the disarm. Starlette < 0.48
    would raise AttributeError here and the operator would see a 500."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from datetime import datetime, timezone

    from fastapi import status
    from fastapi.testclient import TestClient

    from config.settings import Mode, Settings
    from core.clock import FrozenClock
    from orchestrator.app import create_app
    from orchestrator.auth import API_KEY_HEADER, ApiKeyAuth
    from orchestrator.gate import GateResult
    from orchestrator.mode_ladder import ModeLadder
    from orchestrator.state import OrchestratorState
    from storage.engine import create_all, make_engine, make_session_factory

    assert status.HTTP_422_UNPROCESSABLE_CONTENT == 422

    key = "csl227-api-key-123456"
    engine = make_engine(f"sqlite:///{(tmp_path / 'csl227.db').as_posix()}")
    create_all(engine)
    state = OrchestratorState.build(
        make_session_factory(engine),
        hmac_key=b"csl227",
        settings=Settings(),
        clock=FrozenClock(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)),
    )
    ladder = ModeLadder(state)
    ladder.set_mode(Mode.paper, actor="op")
    ladder.set_mode(Mode.review_live, actor="op")
    armed = ladder.arm_auto_mode(
        Mode.auto_veto,
        decision_stage_runtimes=["claude", "claude"],
        gate_result=GateResult(unlockable=True, target="auto_veto"),
        operator="op",
    )
    assert armed.armed and ladder.current() is Mode.auto_veto

    client = TestClient(create_app(state, auth=ApiKeyAuth(key_provider=lambda: key)))
    response = client.put(
        "/limits",
        headers={API_KEY_HEADER: key},
        json={"max_notional": "30000", "actor": "op", "confirm": True},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["rejected"] == "ceiling_exceeded"
    assert body["mode_after"] == "review_live"
    assert client.get("/mode", headers={API_KEY_HEADER: key}).json()["mode"] == "review_live"
