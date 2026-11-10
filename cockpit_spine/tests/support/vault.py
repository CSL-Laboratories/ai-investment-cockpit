"""Temporary projection vault for the operator tests.

Same stand-in as ``test_projections._SyntheticVault``: fifteen accepted cells
and five that failed closed, written under a temp checkout and cited by a
registry built with ``make_entry``. These are not the csl-research evidence
bytes, and nothing here calls ``load_registry()`` without a ``vault_root``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from cockpit_spine import projections as proj

SYNTHETIC_COMMIT = "c" * 40
SYNTHETIC_PATH = "synthetic/cells.json"
_SPECS = (
    ("x2-fake-driver-cells-total", "Cells in the synthetic record", "cells",
     {"kind": "len_at", "pointer": "/cells"}),
    ("x2-fake-driver-cells-accepted", "Synthetic cells accepted", "cells",
     {"kind": "count_where", "pointer": "/cells", "match": {"disposition": "accepted"}}),
    ("x2-fake-driver-cells-fail-closed", "Synthetic cells that failed closed", "cells",
     {"kind": "count_where", "pointer": "/cells", "match": {"disposition": "fail_closed"}}),
)


def synthetic_record() -> bytes:
    """A tiny stand-in for the vault record. Not the vault bytes."""

    document = {
        "kind": "synthetic-projection-record",
        "cells": [{"disposition": "accepted"}] * 15 + [{"disposition": "fail_closed"}] * 5,
    }
    return (json.dumps(document, sort_keys=True) + "\n").encode("utf-8")


def build_synthetic_registry() -> tuple[Path, Path, proj.Registry]:
    """Return ``(vault_root, registry_path, registry)``. The caller deletes both dirs."""

    data = synthetic_record()
    vault = Path(tempfile.mkdtemp(prefix="vault-synth-"))
    target = vault / SYNTHETIC_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    document = json.loads(data.decode("utf-8"))
    sha = hashlib.sha256(data).hexdigest()
    entries = [
        proj.make_entry(
            projection_id=pid, label=label, value=proj.derive(document, derivation),
            unit=unit, repository=proj.VAULT_REPOSITORY, commit=SYNTHETIC_COMMIT,
            path=SYNTHETIC_PATH, sha256=sha, derivation=derivation,
        )
        for pid, label, unit, derivation in _SPECS
    ]
    directory = Path(tempfile.mkdtemp(prefix="reg-synth-"))
    registry_path = directory / "registry.json"
    registry_path.write_text(json.dumps(proj.registry_document(entries)), encoding="utf-8")
    try:
        registry = proj.load_registry(registry_path, vault_root=vault)
    except Exception:
        discard_synthetic_registry(vault, registry_path)
        raise
    return vault, registry_path, registry


def discard_synthetic_registry(vault: Path, registry_path: Path) -> None:
    shutil.rmtree(vault, ignore_errors=True)
    shutil.rmtree(registry_path.parent, ignore_errors=True)
