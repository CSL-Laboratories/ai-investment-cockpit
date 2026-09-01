# W-22 awareness soak evidence — 2026-09-01

Prospective / observation-only. Openings fail-closed. Not RW-15. Not a soak rewrite.

## Pin
- Soak worktree: `/workspace/cockpit-soak`
- Detached SHA: `1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4`
- API: `http://127.0.0.1:8787`
- Uptime at snapshot: ~33 minutes after last bounce (process continued through day)

## Mode / health
- mode: `{'mode': 'read_only', 'armed_at': None, 'armed_until': None, 'armed_by': None}`
- watch_only: `True` (openings still fail-closed; no OrderGateway forge)
- paper_performance.ok: `True` equity=100000 drawdown=0
- audit_chain: ok=True head_seq=769
- market_events: 376
- feeds: ['finnhub', 'sec:edgar', 'alpaca:benzinga']

## Watchlist / quant
- COCKPIT_WATCHLIST: AAPL,MSFT,NVDA
- market_data.symbols/quoted/metrics: ['AAPL', 'MSFT', 'NVDA'] / ['AAPL', 'MSFT', 'NVDA'] / ['AAPL', 'MSFT', 'NVDA']
- expected_edge_source: unavailable — cash_merger_30d.v1: operator strategy watchlist is empty

## Discord
- Bot authenticated as Odysseus#1159 (probe)
- `#market-briefing` visible; `probe_credentials.py --post` PASS via bridge.deliver
- Guild/operator/channel ids provisioned in gitignored app/.env

## Intentional offs
- Calendar lookahead 0 for first smoke
- COCKPIT_MERGER_WATCHLIST empty (no forge / no awareness tickers as merger authority)

## Wake note
- Admin 05:30 one-shot never fired (`lastRunAt` null); hand-start covered bring-up; routine deleted

## Artifacts
- Live snapshot JSON (operator machine): status-snapshot-2026-09-01.json captured beside this note in the evidence PR if included
