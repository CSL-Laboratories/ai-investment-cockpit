"""RW-10 (GR-10) — `app/filings/PROVENANCE.md` is enforced, not merely asserted.

COCKPIT-AUTHORED. PROVENANCE.md makes two claims that a reader has to take on trust:

  1. every vendored ``.py`` differs from upstream ONLY by the documented import-prefix
     rename map — no logic was touched on the way in; and
  2. the recorded pins are real commits that actually contain those files.

Documentation that says "we changed nothing else" is worth exactly as much as the last
person's discipline. This file turns both claims into a test, so a hand-edit to vendored
source — the specific way a vendored tree rots — fails here instead of being discovered
during a future re-sync, when the upstream diff mysteriously stops applying.

WHY IT COMPARES AGAINST THE PINNED SHA, NOT UPSTREAM'S WORKING TREE. Upstream is actively
developed (the SEC client gained +393 lines in the three weeks before this vendoring). A
test that diffed against ``origin/main`` would start failing the moment Chaandan or Samir
committed anything — punishing the cockpit for upstream's progress and training everyone to
ignore it. Against the pin it can only fail for the reason we care about: THIS tree drifted.

SKIPPED, NOT FAILED, WHEN THE SIBLING REPO IS ABSENT. Upstream is a separate private repo;
CI and a fresh clone will not have it. A hard failure there would be a test asserting the
developer's directory layout, so absence skips — and ``test_pins_are_wellformed`` still runs
unconditionally, because a malformed pin is detectable without the repo and is exactly what
a careless PROVENANCE edit produces.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2]
FILINGS = APP / "filings"
PROVENANCE = FILINGS / "PROVENANCE.md"
UPSTREAM = APP.parents[1] / "edgar-morning-brew"

# The rename map from PROVENANCE.md, applied in REVERSE to recover upstream's text.
# raw_store must be reversed FIRST — the same ordering constraint the forward map has.
_REVERSE_MAP: list[tuple[str, str]] = [
    (r"\bfilings\.raw_store\b", "app.storage.raw_store"),
    (r"\bfilings\.edgar\b", "app.edgar"),
    (r"\bfilings\.parse\b", "app.parse"),
    (r"\bfilings\.delta\b", "app.delta"),
    (r"\bfilings\.cite\b", "app.cite"),
    (r"\bcore\.clock\b", "app.core.clock"),
    (r"\bcore\.ids\b", "app.core.ids"),
    (r"\bschemas\.models\b", "app.schemas.models"),
    (r"\bconfig\.settings\b", "app.config.settings"),
]

# Vendored path -> upstream path. `filings/__init__.py` is cockpit-authored (it documents the
# vendoring itself) and is deliberately NOT in this map.
_UPSTREAM_PATH = {"raw_store.py": "app/storage/raw_store.py"}


def _upstream_path(rel: str) -> str:
    return _UPSTREAM_PATH.get(rel, f"app/{rel}")


def _unrename(text: str) -> str:
    for pattern, replacement in _REVERSE_MAP:
        text = re.sub(pattern, replacement, text)
    return text


def _vendored_files() -> list[str]:
    return sorted(
        p.relative_to(FILINGS).as_posix()
        for p in FILINGS.rglob("*.py")
        if "__pycache__" not in p.parts and p.name != "__init__.py"
    )


def _pins() -> dict[str, str]:
    """Parse the pin table out of PROVENANCE.md -> {vendored path: sha}."""
    rows = re.findall(
        r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*`([0-9a-f]{40})`\s*\|",
        PROVENANCE.read_text(encoding="utf-8"),
        re.M,
    )
    return {vendored: sha for vendored, _upstream, sha in rows}


def _pin_for(rel: str) -> str | None:
    """The pin governing a vendored file (its package's pin, or its own row)."""
    pins = _pins()
    for key, sha in pins.items():
        stripped = key[len("filings/"):] if key.startswith("filings/") else key
        if rel == stripped or rel.startswith(stripped.rstrip("/") + "/"):
            return sha
    return None


# ---------------------------------------------------------------------------------------
# runs everywhere — no upstream repo required
# ---------------------------------------------------------------------------------------
def test_pins_are_wellformed():
    pins = _pins()
    assert pins, "PROVENANCE.md has no parseable pin table — the vendoring is unpinned"
    for path, sha in pins.items():
        assert re.fullmatch(r"[0-9a-f]{40}", sha), f"{path} pin is not a full SHA: {sha}"


def test_every_vendored_package_has_a_pin():
    """A file nobody pinned cannot be re-synced, and will silently rot."""
    unpinned = [rel for rel in _vendored_files() if _pin_for(rel) is None]
    assert unpinned == [], f"vendored files with no PROVENANCE pin: {unpinned}"


def test_provenance_documents_the_rename_map_this_test_uses():
    """If the map here and the map in the doc drift apart, the doc stops being the source."""
    text = PROVENANCE.read_text(encoding="utf-8")
    for pattern, upstream_mod in _REVERSE_MAP:
        vendored_mod = pattern.replace(r"\b", "").replace(r"\.", ".")
        assert vendored_mod in text, f"rename-map row missing from PROVENANCE.md: {vendored_mod}"
        assert upstream_mod in text, f"rename-map row missing from PROVENANCE.md: {upstream_mod}"


# ---------------------------------------------------------------------------------------
# needs the sibling repo — skips cleanly without it
# ---------------------------------------------------------------------------------------
def _git_show(sha: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(UPSTREAM), "show", f"{sha}:{path}"],
        capture_output=True, timeout=30,
    )
    return None if proc.returncode != 0 else proc.stdout.decode("utf-8")


upstream_required = pytest.mark.skipif(
    not (UPSTREAM / ".git").exists() or shutil.which("git") is None,
    reason="sibling edgar-morning-brew checkout (or git) not present — provenance diff skipped",
)


@upstream_required
def test_vendored_source_differs_from_the_pin_only_by_the_rename_map():
    """THE PROPERTY. Reverse the documented renames; the result must be upstream, byte for byte.

    A failure here means someone edited vendored source in place. The fix is to move the
    change into an adapter outside app/filings/, or — if it genuinely belongs upstream — to
    send it to upstream and re-pin. It is NOT to update this test.
    """
    drifted: list[str] = []
    for rel in _vendored_files():
        sha = _pin_for(rel)
        original = _git_show(sha, _upstream_path(rel))
        if original is None:
            drifted.append(f"{rel}: not found at pinned {sha[:12]}")
            continue
        local = _unrename((FILINGS / rel).read_text(encoding="utf-8"))
        if local.replace("\r\n", "\n") != original.replace("\r\n", "\n"):
            drifted.append(f"{rel}: content differs from pinned {sha[:12]}")
    assert drifted == [], (
        "vendored source drifted from its pin (edit the adapter, not the vendored tree): "
        + "; ".join(drifted)
    )


@upstream_required
def test_pinned_commits_exist_upstream():
    for path, sha in _pins().items():
        proc = subprocess.run(
            ["git", "-C", str(UPSTREAM), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True, timeout=30,
        )
        assert proc.returncode == 0, f"pin for {path} is not a commit upstream: {sha}"
