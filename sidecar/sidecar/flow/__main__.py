"""Read-only flow commands: `scan` fetches data, `study` only reads the local bar file."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sidecar.flow.client import FlowDataClient
from sidecar.flow.config import MIN_VOLUME, WATCHLIST, FlowError, resolve_feed
from sidecar.flow.normalize import assemble_chain
from sidecar.flow.score import ContractView, render_markdown, score_book
from sidecar.flow.study import DEFAULT_BARS, write_null_study
from sidecar.jsonutil import write_json

NY = ZoneInfo("America/New_York")


def _load_history(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _append_history(path: Path, rows: list[dict], session: str) -> None:
    existing = _load_history(path)
    for row in existing:
        if str(row.get("session_date")) > session:
            raise FlowError("refusing to append flow history behind a later session")
        if str(row.get("session_date")) == session and row.get("kind") == "volume":
            raise FlowError("refusing to overwrite flow history for this session")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def run_scan(*, feed: str, out: Path, markdown: Path, history_path: Path, symbols: tuple[str, ...]) -> dict:
    client = FlowDataClient.from_sidecar_credentials()
    session = datetime.now(timezone.utc).astimezone(NY).date()
    start = datetime.combine(session, datetime.min.time(), tzinfo=NY).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    contracts: list[ContractView] = []
    try:
        for underlying in symbols:
            snapshots = client.snapshots(underlying, feed)
            reference = client.contracts(underlying)
            spot = client.stock_price(underlying)
            rough = assemble_chain(
                underlying=underlying,
                session_date=session,
                spot=spot,
                snapshots=snapshots,
                contracts=reference,
                trades={"trades": {}},
            )
            busy = [item.symbol for item in rough if item.volume is not None and item.volume >= MIN_VOLUME]
            trades = client.trades(
                busy,
                feed,
                start=start.isoformat(),
                end=end.isoformat(),
            )
            contracts.extend(
                assemble_chain(
                    underlying=underlying,
                    session_date=session,
                    spot=spot,
                    snapshots=snapshots,
                    contracts=reference,
                    trades=trades,
                )
            )
    finally:
        client.close()
    history = _load_history(history_path)
    scan = score_book(contracts, history, feed=feed, session_date=session)
    history_rows = scan.pop("history_rows")
    write_json(out, scan)
    markdown.write_text(render_markdown(scan), encoding="utf-8")
    _append_history(history_path, history_rows, session.isoformat())
    return scan


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Read-only unusual options scan")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--feed", default=None)
    scan.add_argument("--out", type=Path, required=True)
    scan.add_argument("--markdown", type=Path, required=True)
    scan.add_argument("--history", type=Path, required=True)
    scan.add_argument("--symbols", nargs="*", default=list(WATCHLIST))
    study = sub.add_parser("study")
    study.add_argument("--bars", type=Path, default=DEFAULT_BARS)
    study.add_argument("--out", type=Path, required=True)
    study.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "scan":
        unknown = [symbol for symbol in args.symbols if symbol not in WATCHLIST]
        if unknown:
            raise SystemExit(f"symbol not on the flow watchlist: {unknown[0]}")
        run_scan(
            feed=resolve_feed(args.feed),
            out=args.out,
            markdown=args.markdown,
            history_path=args.history,
            symbols=tuple(args.symbols),
        )
        return
    write_null_study(args.bars, args.out, args.ledger)


if __name__ == "__main__":
    main()
