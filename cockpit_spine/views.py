"""One view for every surface: typed lines, and the rule that numbers are linked.

The CLI and the loopback view render the same ``Line`` list, so a number that
cannot be shown here cannot be shown anywhere. Each line has a kind:

* ``id``, ``time``, ``digest``: identifiers and stamps, in fixed shapes;
* ``code``: a short machine reason made of letters and underscores only;
* ``term``: one literal order term of the proposal under review, from a fixed
  label list. It is the thing being decided, not a claim about the world;
* ``figure``: a measured or derived number. Its value is a verified
  published-tier projection link, or the withheld marker. Nothing else;
* ``text``: fixed words written by this code. No digit is allowed in it;
* ``quoted``: model-authored prose, shown after every numeral is masked.

``validate_lines`` is run on every response, not only in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cockpit_spine.projections import WITHHELD, Figure, Registry, render_figure

KINDS = ("id", "time", "digest", "code", "term", "figure", "text", "quoted")
TERM_LABELS = frozenset(
    {"symbol", "side", "asset_type", "order_type", "quantity", "notional", "time_horizon"}
)
MAX_QUOTED = 280
_ID = re.compile(r"^[A-Za-z0-9:/#._@\-]{1,200}$")
_NUMERIC_ONLY = re.compile(r"^[0-9.,+\-]+$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z_]{0,63}$")
_TERM_VALUE = re.compile(r"^[A-Za-z0-9._\-]{1,32}$|^none$")
_LINK = re.compile(r"^(?P<value>\S+) (?P<unit>[a-z]+) \[projection:(?P<pid>[a-z0-9._-]+)@(?P<digest>sha256:[0-9a-f]{64})\]$")
_DIGIT = re.compile(r"[0-9]")
_NUMERAL = re.compile(r"[0-9]+(?:[.,][0-9]+)*")
UNRECOGNIZED = "unrecognized_code"


class ViewError(Exception):
    """A line that would put an unlinked number on a screen."""


@dataclass(frozen=True)
class Line:
    kind: str
    label: str
    value: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "label": self.label, "value": self.value}


def mask_numerals(text: str) -> str:
    return _NUMERAL.sub("[n]", text)


def quoted(label: str, text: object) -> Line:
    raw = text if isinstance(text, str) else ""
    flat = " ".join("".join(c if c.isprintable() else " " for c in raw).split())
    return Line("quoted", label, mask_numerals(flat)[:MAX_QUOTED])


def code(label: str, value: object) -> Line:
    text = value if isinstance(value, str) and _CODE.match(value) else UNRECOGNIZED
    return Line("code", label, text)


def figure(label: str, item: Figure | None) -> Line:
    return Line("figure", label, render_figure(item))


def term(label: str, value: object) -> Line:
    text = "none" if value is None else str(value)
    return Line("term", label, text)


def validate_lines(lines: list[Line], registry: Registry) -> None:
    for item in lines:
        where = f"{item.kind}/{item.label}"
        if item.kind not in KINDS or not isinstance(item.value, str):
            raise ViewError(f"{where}: bad kind or value")
        if not item.label or _DIGIT.search(item.label):
            raise ViewError(f"{where}: label must be words without digits")
        value = item.value
        if item.kind == "text" and _DIGIT.search(value):
            raise ViewError(f"{where}: fixed text carries a digit")
        elif item.kind == "quoted":
            if _DIGIT.search(value) or len(value) > MAX_QUOTED:
                raise ViewError(f"{where}: quoted text carries an unmasked numeral")
        elif item.kind == "id":
            if not _ID.match(value) or _NUMERIC_ONLY.match(value):
                raise ViewError(f"{where}: not an identifier")
        elif item.kind == "time":
            if not _TIME.match(value):
                raise ViewError(f"{where}: not a timestamp")
        elif item.kind == "digest":
            if not _DIGEST.match(value):
                raise ViewError(f"{where}: not a digest")
        elif item.kind == "code":
            if not _CODE.match(value):
                raise ViewError(f"{where}: not a code")
        elif item.kind == "term":
            if item.label not in TERM_LABELS or not _TERM_VALUE.match(value):
                raise ViewError(f"{where}: not a recognized order term")
        elif item.kind == "figure":
            _validate_figure(where, value, registry)


def _validate_figure(where: str, value: str, registry: Registry) -> None:
    if value == WITHHELD:
        return
    match = _LINK.match(value)
    if match is None:
        raise ViewError(f"{where}: a number without a projection link")
    try:
        projection = registry.get(match["pid"])
    except Exception:
        raise ViewError(f"{where}: unknown projection") from None
    if (projection.digest != match["digest"] or projection.unit != match["unit"]
            or str(projection.value) != match["value"]):
        raise ViewError(f"{where}: displayed number differs from its projection")


def render_text(lines: list[Line]) -> str:
    return "\n".join(f"{item.label}: {item.value}" for item in lines) + "\n"
