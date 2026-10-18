# Social sentiment

Read-only snapshots from r/wallstreetbets and from X posts dropped on disk. Nothing in this package places an order. `orders_submitted` is 0.

## Sources

Reddit uses the official script-app token (`grant_type=client_credentials`) and then `GET /r/wallstreetbets/new`. The environment names are `SIDECAR_REDDIT_CLIENT_ID`, `SIDECAR_REDDIT_CLIENT_SECRET`, and `SIDECAR_REDDIT_USER_AGENT`. If any one is missing, the client raises before it touches the network. The listing is the newest 100 posts. This is not a historical archive. A post newer than `as_of` is dropped.

X is a directory of JSONL files, default `artifacts/x_inbox/`. Each line needs `id`, `created_at`, `author`, `text`, and `metrics`. A bad line is counted and skipped. A missing directory is an empty inbox. Posts newer than `as_of` are dropped. `metrics` are stored. They are not in the score (`metrics_used_in_score` is false).

## Tickers, sentiment, buzz

Cashtags and bare uppercase tokens count when they are on the flow watchlist. The common-word blocklist wins, so `$IT`, `$AI`, and the word `ALL` do not become tickers. A lowercase word does not count. One post contributes at most one mention per symbol.

The lexicon is a fixed hand-written list in `lexicon.py`. It is not Loughran-McDonald, VADER, or a fitted model. `calls` counts as bullish and `puts` as bearish, which is wrong for "sold calls" and for sarcasm. The score is `(positive - negative) / (positive + negative)` over the unique words in the post, or 0 when none hit.

Mention z uses the previous 20 stored sessions of that symbol (sample standard deviation). A name on the snapshot universe with no post is a zero, so the baseline is not only the days the name happened to appear. The buzz rule, fixed before the fixture returns were computed, is:

```text
buzz = mention_z * (1 + sentiment) / 2
```

An alert needs buzz ≥ 2, at least 5 mentions, and sentiment > 0.

A post at or before 16:00 America/New_York belongs to that calendar date. A later post belongs to the next calendar date. Snapshots are append-only: an older session cannot be written after a newer one, and a symbol-session pair is written once.

Flow alerts join on symbol and session date. `flow_score` stays empty when the flow file has no alert. The join is a column. The event study does not add it as a third trial.

## Event study

The same 1-session and 5-session close-to-close study as `sidecar.flow`, with `n_trials = 2`. The committed run does not call Reddit. It builds posts from the SPY bar calendar: one or two neutral `$SPY` posts on ordinary sessions, and eight `$SPY bullish calls breakout` posts every 30th session after a 20-session baseline, starting at session index 252. The study reads the resulting snapshots.

`examples/sentiment_event_study.json` (33 alerts, 998 snapshot rows):

| Horizon | Mean return | Hit rate | PSR | DSR | Clears Bonferroni |
|---|---:|---:|---:|---:|---|
| 1 session (primary) | 0.000704710566369 | 0.484848484848 | 0.728508831929 | 0.535281809613 | no |
| 5 sessions | 0.00580647129446 | 0.666666666667 | 0.950534709933 | 0.870826150548 | no |

The market question is unanswered. These are calendar dates with canned text. Five-session returns overlap. `threshold_status` is `not_frozen`.

```bash
python -m sidecar.sentiment snapshot --as-of 2026-10-16T20:00:00+00:00 --sources x --inbox artifacts/x_inbox --out artifacts/sentiment_snapshots.jsonl
python -m sidecar.sentiment study --out examples/sentiment_event_study.json --ledger examples/sentiment_event_trials.jsonl --snapshots examples/sentiment_snapshots.jsonl
```

The default `--sources` value is `reddit,x`, and that path refuses to run without the three Reddit variables.
