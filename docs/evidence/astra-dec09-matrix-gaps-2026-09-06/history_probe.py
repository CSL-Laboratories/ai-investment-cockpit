"""Characterize older pinned source; output is not a test or release PASS."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

# The launcher explicitly selects this isolated app directory and clears settings.
sys.path.insert(0, str(Path.cwd()))

from filings.raw_store import RawStore  # noqa: E402
from schemas.models.audit_entry import AuditEntry  # noqa: E402
from storage.audit import AuditLog, audit_meta  # noqa: E402
from storage.engine import create_all, make_engine, make_session_factory  # noqa: E402
from strategies.merger_semantics import (  # noqa: E402
    CashMergerSemanticVerifier,
    SemanticEvidenceUnavailable,
    _derive_eligibility,
    _derive_outcome,
    _text,
)

CONTROL = (
    "Nasdaq. The company entered into a definitive merger. "
    "Each share of target common stock will receive $105 in cash per share. "
    "Expected closing date: 2026-10-01. Outside date: 2026-11-01. "
    "The closing remains subject to regulatory approval."
)


def main():
    observations = {"kind": "historical characterization, not test acceptance"}
    with tempfile.TemporaryDirectory(prefix="dec09-history-") as temporary:
        root = Path(temporary)
        raw_root = root / "raw"
        store = RawStore(raw_root / "sec", min_free_bytes=0)
        store.write("synthetic", CONTROL.replace(
            "will receive $105 in cash per share",
            "will receive a distribution; each share of preferred stock will receive $105 in cash per share",
        ).encode())
        document = {
            **_derive_eligibility(_text(CONTROL.encode()), amended=False),
            "source_accessions": ["synthetic"], "announcement_accession": "synthetic",
            "current_accession": "synthetic", "expected_resolution_date": "2026-10-01",
            "outside_date": "2026-11-01", "conditions": ["regulatory approval"],
        }
        verifier = CashMergerSemanticVerifier(None, raw_root=raw_root, witness=None)
        try:
            verifier._verify_deal(document)
            observations["wrong_subject_current_deal"] = "accepted"
        except SemanticEvidenceUnavailable:
            observations["wrong_subject_current_deal"] = "refused"
        for key, raw in (
            ("contracted_negation", b"It isn't true that the merger was completed."),
            ("uncontracted_negation", b"It is not true that the merger was completed."),
        ):
            try:
                observations[key] = _derive_outcome(_text(raw))
            except SemanticEvidenceUnavailable:
                observations[key] = "refused"
        engine = make_engine(f"sqlite:///{(root / 'fixture.db').as_posix()}")
        create_all(engine)
        sessions = make_session_factory(engine)
        audit = AuditLog(sessions, b"dec09-history-fixture-key")
        with sessions() as session, session.begin():
            audit.append_in(session, "fixture")
        with sessions() as session, session.begin():
            session.execute(AuditEntry.__table__.delete())
            session.execute(audit_meta.delete())
        observations["committed_transactional_erasure_reported_valid"] = audit.verify_chain().ok
        engine.dispose()
    print(json.dumps(observations, indent=2))


if __name__ == "__main__":
    main()
