"""Regenerate ``projections/registry.json`` from the pinned vault record.

Run::

    python3 -X utf8 -B -m cockpit_spine.regen_projections --write --vault-root PATH

``PATH`` is a local checkout of CSL-Laboratories/csl-research. When
``--vault-root`` is omitted, ``CSL_RESEARCH_CHECKOUT`` is used. The loader
never fetches. Values are derived from the file bytes, never typed in.

The four source fields below are the full pin in
``successor/route_consumer/records/x2-fake-driver-bakeoff.ref.json``. The
projection test locks them to that file.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from cockpit_spine import projections as proj

# Full pin from the vault ref. Not the removed product-repo JSON.
REPOSITORY = "CSL-Laboratories/csl-research"
X2_COMMIT = "4aa2190d0ee198b7735d4396c0fb3e2a4763c3b2"
X2_PATH = "contracts/ai-investment-cockpit/evidence/x2-fake-driver-bakeoff.json"
X2_SHA256 = "0d94f066256ded639ddae11a5122b8e88ba9f29609e6dbcebbd6a55f201619b3"

_SPECS = (
    ("x2-fake-driver-cells-total", "Cells in the X2 plumbing record (not a result)",
     "cells", {"kind": "len_at", "pointer": "/cells"}),
    ("x2-fake-driver-cells-accepted", "X2 plumbing cells accepted by the consumer",
     "cells", {"kind": "count_where", "pointer": "/cells", "match": {"disposition": "accepted"}}),
    ("x2-fake-driver-cells-fail-closed", "X2 plumbing cells that failed closed",
     "cells", {"kind": "count_where", "pointer": "/cells",
               "match": {"disposition": "fail_closed"}}),
)


def _vault_arg(argv: list[str]) -> Path | None:
    if "--vault-root" not in argv:
        return None
    index = argv.index("--vault-root")
    try:
        return Path(argv[index + 1])
    except IndexError:
        raise proj.ProjectionError("--vault-root needs a path") from None


def build(vault_root: Path | None = None) -> dict[str, Any]:
    data = proj.read_cited_bytes(
        REPOSITORY, X2_PATH, root=proj.REPO_ROOT, vault_root=vault_root,
    )
    sha = hashlib.sha256(data).hexdigest()
    if sha != X2_SHA256:
        raise proj.ProjectionError("source bytes differ from the pinned sha256")
    document = json.loads(data.decode("utf-8"))
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
    try:
        text = render(build(_vault_arg(argv)))
    except proj.ProjectionError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    if "--write" in argv:
        Path(proj.REGISTRY_PATH).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
