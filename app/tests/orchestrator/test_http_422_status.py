"""CSL-227: the ceiling rejection uses the RFC 9110 status name.

Starlette keeps the old 422 alias only as a deprecated name. Reading it emits
``StarletteDeprecationWarning``. The numeric code stays 422.
"""
from __future__ import annotations

from pathlib import Path

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
