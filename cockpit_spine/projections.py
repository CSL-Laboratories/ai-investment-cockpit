"""Published-tier projections: the only way a measured number may be displayed.

A projection is a thin, digest-pinned record of one number. "Published tier"
here means the minimal reading that can be checked offline:

* the number comes from a source that is public at a repository, a 40-hex
  commit and a path (the commit is a citation: see below);
* the source bytes are pinned by sha256, and the registry refuses to load if
  they changed;
* the stated value is recomputed from those bytes by a named derivation and
  must equal the stated value, so a number nobody can reproduce never shows.

The sha256 is the only pin this module enforces. It does not run git, so it
does not prove that the cited commit contains those bytes; the commit says where
a reader should look. A test checks the blob at the cited commit whenever that
commit is reachable in the clone, and fails instead of skipping when
``COCKPIT_REQUIRE_COMMIT_PIN=1`` (use it where history is deep enough).

A number is displayed only through ``Registry.render`` or ``render_id``. A
``Figure`` is a plain value anyone can construct, and so is a ``Registry``:
rendering re-checks the figure against the entry, and it also re-checks each
entry digest and the registry digest. A hand-built registry whose value does
not match those digests renders as withheld.

The wider two-tier definition (X4) lives in the WP11 spine memo on
csl-research, which the author of this module could not read. Treat this as
a candidate reading for Samir and Dan to confirm or replace.

The product repository never reads the vault: a projection cites a record, it
does not fetch one.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from successor.route_consumer.profile import canonical_digest

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
REGISTRY_PATH = PACKAGE_DIR / "projections" / "registry.json"

PUBLISHED = "published"
REGISTRY_VERSION = 1
WITHHELD = "[number withheld: no published-tier projection]"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_INDEX = re.compile(r"^(?:0|[1-9][0-9]*)$")
_DERIVATION_KEYS = {
    "value_at": frozenset({"kind", "pointer"}),
    "len_at": frozenset({"kind", "pointer"}),
    "count_where": frozenset({"kind", "pointer", "match"}),
}
_DERIVATIONS = tuple(_DERIVATION_KEYS)
_ENTRY_KEYS = frozenset(
    {"projection_id", "tier", "label", "value", "unit", "source", "derivation", "digest"}
)
_SOURCE_KEYS = frozenset({"repository", "commit", "path", "sha256"})


class ProjectionError(Exception):
    """A projection or registry that is not trustworthy."""


class UnlinkedNumberError(ProjectionError):
    """A number was offered for display without a verified projection."""


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _same(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def _pointer_get(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ProjectionError(f"bad pointer {pointer!r}")
    node = document
    for raw in pointer[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and _INDEX.match(part) and int(part) < len(node):
            node = node[int(part)]
        else:
            raise ProjectionError(f"pointer {pointer!r} does not resolve")
    return node


def derive(document: Any, derivation: dict[str, Any]) -> int | float:
    """Recompute a projection's value from its source document."""

    kind = derivation.get("kind")
    if kind not in _DERIVATIONS:
        raise ProjectionError(f"unknown derivation {kind!r}")
    unused = set(derivation) - _DERIVATION_KEYS[kind]
    if unused:
        raise ProjectionError(f"{kind}: unexpected derivation keys {sorted(unused)}")
    target = _pointer_get(document, derivation.get("pointer", ""))
    if kind == "value_at":
        value = target
    elif not isinstance(target, list):
        raise ProjectionError("derivation target is not a list")
    elif kind == "len_at":
        value = len(target)
    else:
        match = derivation.get("match")
        if not isinstance(match, dict) or not match:
            raise ProjectionError("count_where needs a non-empty match")
        value = sum(
            1 for item in target
            if isinstance(item, dict)
            and all(k in item and _same(item[k], v) for k, v in match.items())
        )
    if not _is_number(value):
        raise ProjectionError("derived value is not a finite number")
    return value


@dataclass(frozen=True)
class Projection:
    projection_id: str
    label: str
    value: int | float
    unit: str
    source_repository: str
    source_commit: str
    source_path: str
    source_sha256: str
    derivation: dict[str, Any]
    digest: str

    def entry(self) -> dict[str, Any]:
        return {
            "projection_id": self.projection_id,
            "tier": PUBLISHED,
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "source": {
                "repository": self.source_repository,
                "commit": self.source_commit,
                "path": self.source_path,
                "sha256": self.source_sha256,
            },
            "derivation": self.derivation,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class Figure:
    """A number that is allowed on a screen, bound to the projection that backs it."""

    projection_id: str
    value: int | float
    unit: str
    label: str
    projection_digest: str

    def link(self) -> str:
        return f"projection:{self.projection_id}@{self.projection_digest}"


def _text(item: Projection) -> str:
    return f"{item.value} {item.unit} [projection:{item.projection_id}@{item.digest}]"


def render_figure(figure: object, registry: Registry) -> str:
    """The one text form of a displayed number.

    The figure must match the registry's entry on id, value (and its type), unit,
    label and digest. Anything else, including a forged or stale figure, is withheld.
    """

    return registry.render(figure)


def _entry_digest(entry: dict[str, Any]) -> str:
    return canonical_digest({k: v for k, v in entry.items() if k != "digest"})


def _read_source(root: Path, relative: str) -> bytes:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise ProjectionError(f"{relative}: leaves the repository") from None
    try:
        return candidate.read_bytes()
    except OSError:
        raise ProjectionError(f"{relative}: unreadable") from None


def _build(raw: Any, root: Path) -> Projection:
    if not isinstance(raw, dict):
        raise ProjectionError("projection entry is not an object")
    pid = raw.get("projection_id")
    if not isinstance(pid, str) or not _ID.match(pid):
        raise ProjectionError(f"bad projection id {pid!r}")
    if set(raw) != _ENTRY_KEYS:
        raise ProjectionError(f"{pid}: unexpected or missing entry keys")
    if raw.get("tier") != PUBLISHED:
        raise ProjectionError(f"{pid}: tier is not {PUBLISHED!r}")
    source = raw.get("source")
    if not isinstance(source, dict) or set(source) != _SOURCE_KEYS:
        raise ProjectionError(f"{pid}: source missing or has unexpected keys")
    commit = source.get("commit")
    sha = source.get("sha256")
    if not isinstance(commit, str) or not _COMMIT.match(commit):
        raise ProjectionError(f"{pid}: source commit is not a full 40-hex sha")
    if not isinstance(sha, str) or not _SHA256.match(sha):
        raise ProjectionError(f"{pid}: source sha256 malformed")
    for key in ("repository", "path"):
        if not isinstance(source.get(key), str) or not source[key]:
            raise ProjectionError(f"{pid}: source {key} missing")
    value = raw.get("value")
    if not _is_number(value):
        raise ProjectionError(f"{pid}: value is not a finite number")
    for key in ("label", "unit"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise ProjectionError(f"{pid}: {key} missing")
    derivation = raw.get("derivation")
    if not isinstance(derivation, dict):
        raise ProjectionError(f"{pid}: derivation missing")
    if raw.get("digest") != _entry_digest(raw):
        raise ProjectionError(f"{pid}: entry digest does not match its content")

    data = _read_source(root, source["path"])
    if hashlib.sha256(data).hexdigest() != sha:
        raise ProjectionError(f"{pid}: source bytes differ from the pinned sha256")
    try:
        document = json.loads(data.decode("utf-8"))
    except ValueError:
        raise ProjectionError(f"{pid}: source is not JSON") from None
    recomputed = derive(document, derivation)
    if recomputed != value:
        raise ProjectionError(f"{pid}: stated {value} but the source gives {recomputed}")
    return Projection(
        projection_id=pid, label=raw["label"], value=value, unit=raw["unit"],
        source_repository=source["repository"], source_commit=commit,
        source_path=source["path"], source_sha256=sha, derivation=derivation,
        digest=raw["digest"],
    )


class Registry:
    def __init__(self, projections: dict[str, Projection], digest: str) -> None:
        self._projections = projections
        self.digest = digest
        # load_registry is the path that verifies source bytes. A caller who
        # builds a Registry by hand still cannot display a number unless every
        # entry digest and the registry digest agree with the values inside.
        self._verified = _registry_digests_hold(projections, digest)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._projections))

    def get(self, projection_id: str) -> Projection:
        if not self._verified:
            raise UnlinkedNumberError(f"{projection_id}: registry digests do not verify")
        try:
            return self._projections[projection_id]
        except KeyError:
            raise UnlinkedNumberError(f"{projection_id}: no such published projection") from None

    def figure(self, projection_id: str) -> Figure:
        item = self.get(projection_id)
        return Figure(item.projection_id, item.value, item.unit, item.label, item.digest)

    def render(self, figure: object) -> str:
        if not self._verified or not isinstance(figure, Figure):
            return WITHHELD
        item = self._projections.get(figure.projection_id)
        if item is None or not _matches(figure, item):
            return WITHHELD
        return _text(item)

    def render_id(self, projection_id: str) -> str:
        if not self._verified:
            return WITHHELD
        item = self._projections.get(projection_id) if isinstance(projection_id, str) else None
        return WITHHELD if item is None else _text(item)


def _registry_digests_hold(projections: object, digest: object) -> bool:
    if not isinstance(projections, dict) or not projections or not isinstance(digest, str):
        return False
    try:
        entries = []
        for key in sorted(projections):
            item = projections[key]
            if not isinstance(item, Projection) or item.projection_id != key:
                return False
            entry = item.entry()
            if item.digest != _entry_digest(entry):
                return False
            entries.append(entry)
        expected = canonical_digest(
            {"registry_version": REGISTRY_VERSION, "projections": entries}
        )
        return digest == expected
    except (TypeError, ValueError, AttributeError):
        return False


def _matches(figure: Figure, item: Projection) -> bool:
    return (
        figure.projection_id == item.projection_id
        and _same(figure.value, item.value)
        and figure.unit == item.unit
        and figure.label == item.label
        and figure.projection_digest == item.digest
    )


def load_registry(path: Path = REGISTRY_PATH, root: Path = REPO_ROOT) -> Registry:
    """Load and fully verify a registry. Any defect raises; there is no partial load."""

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ProjectionError("registry unreadable") from None
    if not isinstance(raw, dict) or raw.get("registry_version") != REGISTRY_VERSION:
        raise ProjectionError("registry version unsupported")
    entries = raw.get("projections")
    if not isinstance(entries, list) or not entries:
        raise ProjectionError("registry has no projections")
    built: dict[str, Projection] = {}
    for entry in entries:
        item = _build(entry, Path(root))
        if item.projection_id in built:
            raise ProjectionError(f"{item.projection_id}: duplicate id")
        built[item.projection_id] = item
    expected = canonical_digest({"registry_version": REGISTRY_VERSION,
                                 "projections": [built[k].entry() for k in sorted(built)]})
    if raw.get("registry_digest") != expected:
        raise ProjectionError("registry digest does not match its entries")
    return Registry(built, expected)


def make_entry(
    *, projection_id: str, label: str, value: int | float, unit: str, repository: str,
    commit: str, path: str, sha256: str, derivation: dict[str, Any],
) -> dict[str, Any]:
    """Build a registry entry with its digest. Used by the regeneration script and tests."""

    entry: dict[str, Any] = {
        "projection_id": projection_id, "tier": PUBLISHED, "label": label, "value": value,
        "unit": unit,
        "source": {"repository": repository, "commit": commit, "path": path, "sha256": sha256},
        "derivation": derivation,
    }
    entry["digest"] = _entry_digest(entry)
    return entry


def registry_document(entries: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(entries, key=lambda e: e["projection_id"])
    return {
        "registry_version": REGISTRY_VERSION,
        "projections": ordered,
        "registry_digest": canonical_digest(
            {"registry_version": REGISTRY_VERSION, "projections": ordered}
        ),
    }
