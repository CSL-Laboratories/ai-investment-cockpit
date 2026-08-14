"""Provision immutable W-23 evidence while the cockpit is read-only.

Examples (run from the repository root):

  python tools/provision_merger_edge.py --db-url sqlite:///app/cockpit.db \
      --raw-root app/data/raw calibration --document calibration.json \
      --cohort cohort.json --day 2026-08-14 --key cash-merger-30d-v1

  python tools/provision_merger_edge.py --db-url sqlite:///app/cockpit.db \
      --raw-root app/data/raw deal --document deal.json

The JSON files are operator-reviewed local evidence. This tool never calls a model or broker,
never changes mode, and refuses to write unless the audited mode is ``read_only``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from filings.raw_store import RawStore                                  # noqa: E402
from config.settings import get_settings                               # noqa: E402
from core.clock import default_clock                                    # noqa: E402
from main import _build_anchor_store, _hmac_key, run_startup_security_checks  # noqa: E402
from orchestrator.halt import default_halt_path, ensure_halt_dir        # noqa: E402
from orchestrator.mode_ladder import ModeLadder                         # noqa: E402
from orchestrator.state import OrchestratorState                        # noqa: E402
from storage.engine import create_all, make_engine, make_session_factory  # noqa: E402
from strategies.merger_edge import (                                    # noqa: E402
    calibration_statistics,
    record_calibration_evidence,
    record_deal_evidence,
    write_calibration_cohort,
)


def _json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Provision signed, immutable W-23 cash-merger evidence (read-only mode only)."
    )
    parser.add_argument("--db-url", required=True, help="cockpit SQLAlchemy database URL")
    parser.add_argument("--raw-root", required=True, help="cockpit raw-data root")
    sub = parser.add_subparsers(dest="action", required=True)

    calibration = sub.add_parser("calibration", help="write raw cohort + signed calibration")
    calibration.add_argument("--document", required=True, help="calibration JSON object")
    calibration.add_argument("--cohort", required=True, help="raw cohort JSON array")
    calibration.add_argument("--day", required=True, help="cohort snapshot date (YYYY-MM-DD)")
    calibration.add_argument("--key", required=True, help="write-once cohort key")

    deal = sub.add_parser("deal", help="sign an operator-reviewed SEC deal record")
    deal.add_argument("--document", required=True, help="deal JSON object")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    raw_root = Path(args.raw_root).resolve()
    settings = get_settings()
    ensure_halt_dir(default_halt_path())
    run_startup_security_checks(settings)
    engine = make_engine(args.db_url)
    create_all(engine)
    state = OrchestratorState.build(
        make_session_factory(engine),
        hmac_key=_hmac_key(),
        settings=settings,
        clock=default_clock,
        anchor_store=_build_anchor_store(settings),
    )
    if ModeLadder(state).current().value != "read_only":
        raise RuntimeError("evidence provisioning is allowed only while audited mode is read_only")

    if args.action == "calibration":
        records = _json(args.cohort)
        if not isinstance(records, list):
            raise ValueError("cohort JSON must be an array")
        ref, digest = write_calibration_cohort(raw_root, args.day, args.key, records)
        document = _json(args.document)
        document.update(calibration_statistics(records))
        document.update({"cohort_ref": ref, "cohort_content_hash": digest})
        row = record_calibration_evidence(
            state, document, raw_root=raw_root, actor="operator_cli"
        )
    else:
        document = _json(args.document)
        accessions = document.get("source_accessions", [])
        raw_store = RawStore(raw_root / "sec", min_free_bytes=0)
        document["filing_hashes"] = {
            str(accession): raw_store.stored_hash(str(accession))
            for accession in accessions
        }
        row = record_deal_evidence(state, document, raw_root=raw_root, actor="operator_cli")

    verified = state.audit.verify_chain()
    if not verified.ok:
        raise RuntimeError("audit chain did not verify after evidence provisioning")
    print(f"recorded {row.id}; content_hash={row.content_hash}; audit_head={verified.head_seq}")
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    raise SystemExit(main())
