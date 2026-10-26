"""Regenerate ``projections/registry.json`` from the pinned sources.

Run: ``python3 -X utf8 -B -m cockpit_spine.regen_projections --write``.
The sources are WP12's recorded fake-driver run, cited at the exact commit that
contains it. Values are derived from the file bytes, never typed in.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from cockpit_spine import projections as proj

REPOSITORY = "CSL-Laboratories/ai-investment-cockpit"
X2_COMMIT = "4433a371a9fd73801b1d9d01c740a07236d5e58e"
X2_PATH = "successor/route_consumer/records/x2-fake-driver-bakeoff.json"

_SPECS = (
    ("x2-fake-driver-cells-total", "Cells in the X2 plumbing record (not a result)",
     "cells", {"kind": "len_at", "pointer": "/cells"}),
    ("x2-fake-driver-cells-accepted", "X2 plumbing cells accepted by the consumer",
     "cells", {"kind": "count_where", "pointer": "/cells", "match": {"disposition": "accepted"}}),
    ("x2-fake-driver-cells-fail-closed", "X2 plumbing cells that failed closed",
     "cells", {"kind": "count_where", "pointer": "/cells", "match": {"disposition": "fail_closed"}}),
)


def build() -> dict[str, Any]:
    data = (proj.REPO_ROOT / X2_PATH).read_bytes()
    document = json.loads(data.decode("utf-8"))
    sha = hashlib.sha256(data).hexdigest()
    entries = [
        proj.make_entry(
            projection_id=pid, label=label, value=proj.derive(document, derivation), unit=unit,
            repository=REPOSITORY, commit=X2_COMMIT, path=X2_PATH, sha256=sha,
            derivation=derivation,
        )
        for pid, label, unit, derivation in _SPECS
    ]
    return proj.registry_document(entries)


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render(build())
    if "--write" in argv:
        Path(proj.REGISTRY_PATH).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
