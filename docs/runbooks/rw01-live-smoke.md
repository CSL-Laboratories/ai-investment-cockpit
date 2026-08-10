---
title: RW-01 live smoke — one real market day, end to end
type: runbook
status: active
last-verified: 2026-08-01
related:
  - refactor-plan
  - setup/accounts-and-keys
  - path-to-production
  - security-architecture
  - security-hardening-backlog
---

# RW-01 live smoke

**Operator-run.** The plumbing is built and deterministically tested
(`app/tests/system/test_rw01_market_day.py` drives the real composed pipeline against
recorded payloads). What no test can do is prove the *credentials, the guild, and the
vendors* are real. That is this runbook: provision, run one market day, confirm the brew
lands in Discord.

Budget ~30 minutes of setup the evening before, then a glance at 08:35 ET.

## What this proves — and what it does not

**Proves:** keys are valid; the feeds actually emit; events persist with a verifiable
audit chain; the brew produces a `MorningBrief`; the brief reaches your Discord;
`/status` and the out-of-band kill switch respond.

**Does not prove — and must not be read as proving:** anything about trading. The whole
run is `read_only`. No order path is reachable from anything here, no broker credential
is used, and the mode ladder never leaves its floor. The reliable ROI being tested is
**awareness**, not alpha ([[refactor-plan]] §7).

> **Cost note.** The brew makes two Claude subprocess calls (one batched triage, one deep
> brief) against your Max plan, not an API key. One run is negligible; leaving the
> scheduler up for days is not free of your plan limits.

---

## 1. Provision the secrets (keyring)

`keyring set` prompts for the value with input hidden, so nothing lands in shell history
(Invariant 4 — secrets live only in Windows Credential Manager, never in `.env`, source,
prompts, or commits).

```powershell
keyring set ai-investment-cockpit alpaca_key
keyring set ai-investment-cockpit alpaca_secret
keyring set ai-investment-cockpit finnhub_key
keyring set ai-investment-cockpit discord_bot_token
keyring set ai-investment-cockpit cockpit_api_key      # <- any long random string you invent
```

| Secret | Where it comes from | Without it |
|---|---|---|
| `alpaca_key` / `alpaca_secret` | alpaca.markets → API key + secret | the Benzinga news WebSocket does not start |
| `finnhub_key` | finnhub.io free tier | no Finnhub news **and** no earnings calendar (one key serves both) |
| `discord_bot_token` | Developer Portal → Bot → Reset/Copy Token | the brief is computed and stored but **delivered nowhere** |
| `cockpit_api_key` | you invent it — e.g. `python -c "import secrets;print(secrets.token_urlsafe(32))"` | **every** API call returns 401, including `/status` |

`cockpit_api_key` is not a vendor credential; it is the header (`X-API-Key`) that guards
the loopback API so that even a local process must hold a key. The guard fails **closed**:
unset means every route refuses, never that every caller is admitted.

**You do not need to write any of these down.** keyring is a retrievable store, not a hash —
the app reads them back on every call, and so can you:

```powershell
keyring get ai-investment-cockpit cockpit_api_key              # display it
keyring get ai-investment-cockpit cockpit_api_key | Set-Clipboard   # copy, never shown
```

For the VENDOR secrets, prefer regenerating at the vendor over retrieving: a key you had to
go looking for is a key you may have pasted somewhere. `cockpit_api_key` is the exception —
you invented it and this app is its only consumer, so retrieving it is fine. If you would
rather rotate it, `python tools/verify_keys.py --set cockpit_api_key` replaces it and the
next `python main.py` picks the new one up.

Verify a round trip prints the redaction, never the value:

```powershell
cd app
python -c "from core.secrets import require_secret; print(require_secret('finnhub_key'))"
# -> Redacted('***')
```

The audit-chain HMAC key (`audit-hmac-key`) is **not** in that list: the app mints it into
keyring itself on first `compose()`. Do not create it by hand.

## 2. Discord: private guild, bot, channels

Per [[setup/accounts-and-keys]] §3 — a **new private guild only you are in**, a bot with
**only** the Message Content intent, invited with `bot` + `applications.commands`.

Create at least one channel — call it **`#morning-brew`** — and copy its id (Developer
Mode → right-click the channel → Copy Channel ID). Optionally add `#alerts`,
`#proposals`, `#system`, and `#audit-log`.

The pipeline's channel names are **logical** (`#brief`, `#alerts`, …) and are mapped to
your ids in one place, so what you call the Discord channel is up to you. `#brief` maps to
your `#morning-brew`.

## 3. Non-secret config (environment)

```powershell
$env:COCKPIT_BRIEF_CHANNEL_ID = "<your #morning-brew channel id>"
$env:COCKPIT_OPERATOR_IDS     = "<your Discord user id>"
$env:COCKPIT_HALT_PATH        = "C:\ProgramData\ai-investment-cockpit\HALT"

# EDGAR feed — only if you run edgar-morning-brew on this box. Omit both and the feed is
# simply ABSENT (see §6); it is never silently "quiet".
$env:COCKPIT_BREW_DB_PATH  = "C:\path\to\edgar_morning_brew.db"
$env:COCKPIT_BREW_RUNS_DIR = "C:\path\to\output\runs"

# Optional: the SEC-11 out-of-domain audit witness (see §7).
$env:COCKPIT_AUDIT_LOG_CHANNEL_ID = "<your #audit-log channel id>"
```

Leave `COCKPIT_MODE` alone. It defaults to `read_only`, which is the whole point.

Pin `COCKPIT_HALT_PATH` to an **absolute** path. It must be the same path the kill-switch
process and the orchestrator both resolve; a relative one is rejected at boot precisely
because two processes with different working directories would read different files.

**FINRA stays off.** Its dataset URL and wire schema are console-gated and unverified, so
the connector refuses to invent them and no job is registered. That is correct for RW-01 —
FINRA is two-week-stale enrichment, not brew input.

**Turn the earnings calendar OFF for the first smoke:**

```powershell
[Environment]::SetEnvironmentVariable('COCKPIT_CALENDAR_LOOKAHEAD_DAYS','0','User')
```

The calendar is FULL-MARKET, and `morning_brew` builds its triage packet over the whole
window with no pre-filter (unlike `triage_loop`, which filters on holdings/watchlist/
hard-event types). Measured against the live free tier: a 3-day window is ~560 entries
≈ 32k tokens of prompt per brew, a 14-day window ~1500 entries ≈ 86k — overwhelmingly
names you do not hold. The first smoke should prove the PIPELINE (news → filings → brew →
delivery), not spend the plan budget drowning it in earnings dates.

Turn it back on once the brew packet is bounded; the calendar's fetch path is already
proven by `probe_credentials.py`, so nothing is left untested by disabling it here.

## 4. Pre-flight (no market day needed)

Three checks, in order. Each answers a different question, and skipping to the last one is
how a bring-up burns an evening.

**(a) Are the secrets stored under the names the app reads?**

```powershell
python tools/verify_keys.py
```

Five `OK` lines and exit 0. This is not the same as `keyring set` having succeeded:
`keyring set` accepts ANY name, so a hyphen-for-underscore slip stores an entry that looks
perfectly healthy in Credential Manager and is INVISIBLE to the cockpit. This tool imports
the names from the code, so it cannot disagree with what the app looks up.

If it reports `WRONG ... stored as 'alpaca-key'`, run `--migrate` (or `--migrate --force`
if the canonical name already holds a wrong value). To (re-)enter one by hand, use
`python tools/verify_keys.py --set alpaca_key` rather than `keyring set` — it refuses an
unknown name, strips a stray trailing newline, and echoes the stored length so a hidden
paste is confirmable.

**(b) Do those secrets actually WORK?**

```powershell
python tools/probe_credentials.py --post
```

One real call per credential, driving the app's own code paths. `--post` publishes one
message to your brief channel — the ONLY check that proves the bot can *send* there, since
channel permission overwrites are invisible to every read-only probe. Run it once, then
delete the message.

Storage and validity are different questions. (a) proves the first, (b) proves the second,
and only (b) can tell you a key was regenerated after you copied it.

**(c) Does the whole graph compose, and which feeds are live?**

```powershell
cd app
python -m pytest -q
ruff check .
python -c "import main, json; s,a,_=main.compose(db_url='sqlite:///smoke-check.db', parquet_root='data/raw'); print(json.dumps(a.state.ingest.status(), indent=2, default=str))"
```

Read the log lines it prints. Every unprovisioned feed announces itself:

```
NOT PROVISIONED: sec:edgar is OFF and will stay silent. Silence from it is NOT a
quiet day. To enable: set COCKPIT_BREW_DB_PATH (+ COCKPIT_BREW_RUNS_DIR)
```

If a feed you *expect* is on that list, fix it now. A feed that is off produces zero
events forever, which looks exactly like a market with no news.

Delete `smoke-check.db` afterwards; the real run creates its own.

## 5. Run the market day

Start it **before 08:30 ET** (the premarket brew's cron time) on a weekday:

```powershell
cd app
python main.py
```

What starts, in order: the loopback API (127.0.0.1:8787), the cron jobs, and the Alpaca
news reader on a daemon thread.

Cadences: Finnhub news every minute (weekdays), EDGAR every 5 minutes (all week — filings
land at weekends), the calendar at 06:00 and 17:00 ET, the premarket brew at **08:30 ET**,
the intraday triage loop every 15 minutes while the market is open, EOD review at 16:15,
auto-disarm at 16:00.

### 5a. Confirm events are landing

Within a couple of minutes:

```powershell
# Reads the key straight out of keyring into a variable -- never on screen, never in
# scrollback, and nothing to look up or remember.
$h = @{ "X-API-Key" = (keyring get ai-investment-cockpit cockpit_api_key).Trim() }
Invoke-RestMethod -Uri http://127.0.0.1:8787/status -Headers $h | ConvertTo-Json -Depth 6
```

Look at `ingest.market_events` (should be climbing) and `ingest.runner.feeds`. Each feed
reports `persisted`, `duplicates`, `errors`, and — where the feed publishes one — its own
progress signal and freshness verdict. A steady stream of `duplicates` with no `persisted`
on the calendar feed is normal and expected: a calendar re-publishes the same forward
window every poll.

### 5b. Confirm the brew

At 08:35 ET, **look at `#morning-brew` in Discord.** That is the acceptance criterion.

You should see a message headed `**Morning brief — YYYY-MM-DD**` with the regime, risk
flags, watch items and a source-ref count.

To trigger it by hand instead of waiting (or to re-run after a fix — note it will report
`already_briefed` once one exists for the day):

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8787/brief/run -Headers $h | ConvertTo-Json -Depth 6
```

The response tells you what happened. `"status": "ok"` with `"delivered": true` is the
pass. Anything else, see §6.

The counts alongside it account for **every** event in the window — that is the Inv 7
property, and they should always add up:

| Field | Meaning |
|---|---|
| `window_events` | everything ingested in the 18h window |
| `prefiltered` | bulk calendar rows for symbols you neither hold nor watch — deliberately not read |
| `dispatched` | what actually went to the triage model (capped at `MAX_PACKET_EVENTS`) |
| `deferred_count` | not analysed this run, and owed re-coverage next run |
| `deferred_sample` | the first 20 deferred ids; the complete set is in the audited `EVENT_TRIAGE_BATCH` payload |
| `over_cap` | present only when relevance alone did not get under the cap |

A large `prefiltered` is normal: the earnings-calendar feed publishes a forward window for
the whole market, so a typical day is several hundred calendar rows against a few dozen news
items. `"status": "no_relevant_events"` means the window held nothing but that reference
data — a **complete** run that asked no model, not a failure.

`prefiltered` and `deferred_count` mean different things and the distinction matters when
reading a run: prefiltered rows were deliberately not analysed and are owed nothing, while
deferred events will be re-covered next loop. If `deferred_count` stays high across
consecutive runs, the window is arriving faster than the cap can clear it.

### 5c. Exercise the kill switch

From a **separate** terminal — the point is that it works without the orchestrator's event
loop being responsive:

```powershell
cd app
python -m orchestrator.halt --set --reason "rw01 smoke test"
# ...confirm the sentinel file exists at COCKPIT_HALT_PATH, then:
python -m orchestrator.halt --clear
```

In `read_only` there is nothing to stop, so this is a mechanism check: the flag is written
and cleared at the path both processes resolve. There is also `POST /killswitch` (in-band,
audited, drops auto modes) — the out-of-band CLI is the one that survives a wedged loop.

Stop the cockpit with Ctrl-C. The ingest threads are daemons and exit with the process.

## 6. When it does not work

| Symptom | Cause | Fix |
|---|---|---|
| Every API call returns `401` | `cockpit_api_key` unset, or the wrong header | `keyring set ai-investment-cockpit cockpit_api_key`; header is `X-API-Key` |
| Brew says `"status": "no_events"` | nothing was ingested in the 18h window | check `/status` → `ingest.runner.feeds`; a feed absent from that map was never provisioned |
| Brew is `ok` but `"delivered": false` | no bot token, or `#brief` unmapped | set `discord_bot_token` + `COCKPIT_BRIEF_CHANNEL_ID`; the log names which one |
| Brief delivered, but the bot cannot post | channel permissions | the bot needs View Channel + Send Messages on `#morning-brew` |
| Brew says `degraded_watch_only` | a Claude usage limit or timeout mid-run | expected fail-closed behaviour (Inv 7); events are deferred, not lost — re-run later |
| Brew says `quarantined` | the model returned output that failed schema validation | expected fail-closed behaviour (Inv 3); the job is quarantined, nothing was delivered |
| A feed shows `errors` climbing | vendor 4xx/5xx, or a bad key | `last_error` in `/status` carries the exception; one sick feed never stops the others |
| EDGAR feed present but `feed_status.trustworthy: false` | the brew has not run, or ran degraded | that is the point of the field — **zero EDGAR events does not mean no filings** |
| A feed shows a `progress_signal` | the poller is wedged or clamped, not quiet | read the message; it says which and what to do |

The recurring theme: **silence is never good news on its own.** Every feed distinguishes
"nothing happened" from "I could not tell you what happened", and `/status` is where you
read the difference.

## 7. Optional: close the SEC-11 witness loop

If you created an `#audit-log` channel and set `COCKPIT_AUDIT_LOG_CHANNEL_ID`, the
orchestrator posts the verified audit head there every 30 minutes. That server-stamped
history is the genuine out-of-domain witness — the git-tracked head file alone is a
same-trust-domain mirror and does **not** satisfy the prerequisite.

This matters only when you eventually want an auto mode. Without it, `latest()` is `None`
and every auto/live consult fails closed, which is the correct posture for RW-01. Do not
read "the anchor job is running" as "SEC-11 is closed" — see
[[security-hardening-backlog]] SEC-11 for what closure requires.

## 8. Done

Record in your notes: the date, which feeds were live, the event count at 08:35, and
whether the brief landed. That is RW-01's acceptance evidence.

**Do not** take a successful smoke as licence to move up the ladder. Live capital is
gated on the W-series ([[production-work-packages]]), starting with SEC-11 and the paper
soak. RW-01 proved the system *runs*; it proved nothing about whether it should trade.

Next: **RW-02** puts the Discord adapter behind an `OperatorSurface` interface, then
**RW-03/RW-04** (notifications + data spine) are where the babysitting-reduction payoff
actually lands ([[refactor-plan]] §6).
