"""RW-01 — prove each provisioned credential is VALID, by making one real call with it.

    python tools/probe_credentials.py           # read-only checks
    python tools/probe_credentials.py --post    # ...plus post one line to the configured brief channel

``tools/verify_keys.py`` proves a secret is STORED under the name the app reads. It cannot
prove the secret WORKS — only the vendor can say that, and without this the answer arrives at
08:35 on a market morning, which is a bad time to discover a typo.

EACH PROBE DRIVES THE APP'S OWN CODE PATH, NOT A REIMPLEMENTATION. The Alpaca check runs
``main.build_alpaca_news_connect``; Finnhub runs ``main.build_finnhub_news_fetch`` and
``main.build_earnings_calendar_fetch``; the Discord post runs ``bridge.deliver`` end to end. A
probe that hand-rolls its own request proves the CREDENTIAL works and tells you nothing about
whether the wiring does — and the wiring is the part that was written this week.

NO SECRET VALUE IS EVER PRINTED, and no exception body is echoed raw: a urllib error repr
carries the request URL, which is where a key would surface if one ever moved into a query
string. Failures are reported as type + status + a hint.

--post IS OUTWARD-FACING and is therefore OPT-IN. It publishes a visible message to a real
Discord channel. It is also the ONLY check that proves the bot can actually SEND there —
channel permission overwrites are the most common Discord failure and are invisible to every
read-only probe — so run it once, then delete the message.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

import main as main_mod                                                     # noqa: E402
from bridge.deliver import CHANNEL_BRIEF, build_deliver, channel_ids_from_settings  # noqa: E402
from config.settings import get_settings                                    # noqa: E402
from core.secrets import get_secret                                         # noqa: E402
from ingest.clock_contract import utc_now                                   # noqa: E402

DISCORD_API = "https://discord.com/api/v10"
TIMEOUT = 20.0


class Result:
    def __init__(self, name: str, ok: bool, detail: str, hint: str = "") -> None:
        self.name, self.ok, self.detail, self.hint = name, ok, detail, hint

    def render(self) -> str:
        line = f"  {'PASS' if self.ok else 'FAIL'}  {self.name:<26} {self.detail}"
        return line if self.ok or not self.hint else f"{line}\n{' ' * 34}-> {self.hint}"


def _skip(name: str, secret_name: str) -> Result:
    return Result(name, False, f"{secret_name} is not provisioned",
                  f"keyring set ai-investment-cockpit {secret_name}  "
                  "(then re-run tools/verify_keys.py)")


def _http_status(exc) -> str:
    """A safe one-line rendering of a urllib failure: status + reason, never the URL."""
    code = getattr(exc, "code", None)
    if code is not None:
        return f"HTTP {code} {getattr(exc, 'reason', '')}".strip()
    return type(exc).__name__


# --------------------------------------------------------------------------- Alpaca
ALPACA_WAIT_SECONDS = 12.0


def probe_alpaca() -> Result:
    """Open the REAL news WebSocket, authenticate, subscribe — WITHOUT waiting for news.

    Drives ``main.build_alpaca_news_connect``, so this also exercises the auth-error path added
    after RW-01 bring-up: a rejected key raises ``AlpacaStreamError`` instead of ending the
    stream quietly.

    WHY A DEADLINE AND A THREAD, NOT A BARE ``next()``.
    The generator yields only NEWS frames; the auth/subscribe handshake is consumed inside it.
    So the naive probe — ``next(stream)`` — blocks until an article is published. On a weekend,
    or after hours, that is never: the first cut of this probe hung indefinitely on a perfectly
    HEALTHY connection, which is a worse failure than the one it was written to catch.

    The inference that replaces it is sound because the two outcomes have very different
    timings. Alpaca rejects bad credentials IMMEDIATELY (an ``error`` control frame, then
    close), so a raise arrives in well under a second. A healthy stream with a quiet tape
    simply blocks. Therefore: still blocked at the deadline == authenticated. Absence of a news
    article is NOT evidence about the credential, and this probe must not pretend otherwise.

    The worker is a DAEMON thread and is deliberately abandoned if still blocked — closing a
    generator from a second thread while it executes raises "generator already executing". This
    is a short-lived CLI; the socket dies with the process.
    """
    import threading

    key, secret = get_secret("alpaca_key"), get_secret("alpaca_secret")
    if key is None or secret is None:
        return _skip("alpaca news WebSocket", "alpaca_key / alpaca_secret")

    def bounded_connect(url):
        """The app's own connect seam, with a bound on the TCP/handshake wait so an unreachable
        endpoint fails fast instead of joining the quiet-tape case."""
        from websockets.sync.client import connect as ws_connect

        return ws_connect(url, open_timeout=10, close_timeout=2)

    connect = main_mod.build_alpaca_news_connect(key.reveal(), secret.reveal(),
                                                 ws_connect=bounded_connect)
    outcome: dict = {}

    def drain():
        stream = connect()
        try:
            next(stream)
            outcome["ok"] = "authenticated, subscribed, and a live news frame arrived"
        except StopIteration:
            outcome["ok"] = "authenticated and subscribed (server closed the idle stream)"
        except BaseException as exc:                            # noqa: BLE001
            outcome["err"] = exc
        finally:
            try:
                stream.close()
            except BaseException:                               # noqa: BLE001
                pass

    worker = threading.Thread(target=drain, daemon=True)
    worker.start()
    worker.join(ALPACA_WAIT_SECONDS)

    exc = outcome.get("err")
    if isinstance(exc, main_mod.AlpacaStreamError):
        return Result("alpaca news WebSocket", False, str(exc).split("(")[0].strip(),
                      "the key/secret pair was REJECTED. Regenerate on the Alpaca dashboard "
                      "(Paper Trading account) and re-run keyring set for BOTH values.")
    if exc is not None:
        return Result("alpaca news WebSocket", False, type(exc).__name__,
                      "could not reach stream.data.alpaca.markets - check connectivity, then "
                      "that the keys came from the Paper Trading dashboard.")
    if "ok" in outcome:
        return Result("alpaca news WebSocket", True, outcome["ok"])
    return Result("alpaca news WebSocket", True,
                  f"authenticated and subscribed; no article in {ALPACA_WAIT_SECONDS:.0f}s "
                  "(quiet tape - expected outside market hours, NOT a credential problem)")


# --------------------------------------------------------------------------- Finnhub
def probe_finnhub_news() -> Result:
    key = get_secret("finnhub_key")
    if key is None:
        return _skip("finnhub news", "finnhub_key")
    try:
        rows = main_mod.build_finnhub_news_fetch(key.reveal())(0, 0.0)
    except Exception as exc:                                    # noqa: BLE001
        return Result("finnhub news", False, _http_status(exc),
                      "401/403 => the key was rejected; 429 => rate limited, retry shortly.")
    return Result("finnhub news", True, f"{len(rows)} article(s) returned")


def probe_finnhub_calendar() -> Result:
    """The endpoint whose free-tier availability was UNVERIFIABLE from any primary source
    (both secondary sources contradicted each other). This settles it empirically."""
    key = get_secret("finnhub_key")
    if key is None:
        return _skip("finnhub earnings calendar", "finnhub_key")
    try:
        rows = main_mod.build_earnings_calendar_fetch(key.reveal(), clock=utc_now)()
    except Exception as exc:                                    # noqa: BLE001
        status = _http_status(exc)
        return Result("finnhub earnings calendar", False, status,
                      "403 => /calendar/earnings is PREMIUM-GATED on your plan. Not fatal: the "
                      "other four feeds are unaffected. Tell Claude and the calendar source "
                      "gets swapped.")
    return Result("finnhub earnings calendar", True,
                  f"{len(rows)} upcoming entr(ies) - free tier covers this endpoint")


# --------------------------------------------------------------------------- Discord
def _discord_get(path: str, token: str):
    import urllib.request

    req = urllib.request.Request(
        f"{DISCORD_API}{path}",
        headers={"Authorization": f"Bot {token}",
                 "User-Agent": "cockpit-bridge (https://localhost, 1.0)"},
        method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def probe_discord_identity() -> Result:
    token = get_secret("discord_bot_token")
    if token is None:
        return _skip("discord bot token", "discord_bot_token")
    try:
        me = _discord_get("/users/@me", token.reveal())
    except Exception as exc:                                    # noqa: BLE001
        return Result("discord bot token", False, _http_status(exc),
                      "401 => the token is wrong or was regenerated (regenerating INVALIDATES "
                      "the old one). Copy it again and re-run keyring set.")
    return Result("discord bot token", True,
                  f"authenticated as {me.get('username')}#{me.get('discriminator', '0')}")


def probe_discord_channel() -> Result:
    """Can the bot SEE the configured brief channel? Visibility only -- send permission
    is a separate overwrite and only ``--post`` proves it."""
    token = get_secret("discord_bot_token")
    settings = get_settings()
    channel_id = getattr(settings, "brief_channel_id", None)
    if token is None:
        return _skip("discord #brief channel", "discord_bot_token")
    if channel_id is None:
        return Result("discord #brief channel", False, "COCKPIT_BRIEF_CHANNEL_ID is not set",
                      "without it the brief is computed and stored but DELIVERED NOWHERE.")
    try:
        channel = _discord_get(f"/channels/{channel_id}", token.reveal())
    except Exception as exc:                                    # noqa: BLE001
        return Result("discord #brief channel", False, _http_status(exc),
                      "403/404 => the bot cannot see this channel. Check it was invited to the "
                      "guild and has View Channel on it, and that the id is the CHANNEL id "
                      "(not the guild or your user id).")
    return Result("discord #brief channel", True,
                  f"visible: #{channel.get('name')} (id {channel_id})")


def probe_discord_post() -> Result:
    """Post one line through the REAL bridge.deliver path. The only check that proves the bot
    can SEND -- and the only one that publishes anything, hence opt-in."""
    token = get_secret("discord_bot_token")
    settings = get_settings()
    channel_ids = channel_ids_from_settings(settings)
    if token is None:
        return _skip("discord post (--post)", "discord_bot_token")
    if CHANNEL_BRIEF not in channel_ids:
        return Result("discord post (--post)", False, "COCKPIT_BRIEF_CHANNEL_ID is not set", "")

    posted: list = []
    import urllib.request

    def post_fn(channel_id, content: str) -> None:
        req = urllib.request.Request(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            data=json.dumps({"content": content}).encode("utf-8"),
            headers={"Authorization": f"Bot {token.reveal()}",
                     "Content-Type": "application/json",
                     "User-Agent": "cockpit-bridge (https://localhost, 1.0)"},
            method="POST")
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
        posted.append(channel_id)

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ok = build_deliver(channel_ids, post_fn)(CHANNEL_BRIEF, {
        "date": stamp, "degraded": False,
        "brief": {"market_regime": "RW-01 credential probe - delete this message.",
                  "source_refs": []},
    })
    if not ok:
        return Result("discord post (--post)", False, "bridge.deliver reported failure",
                      "the bot can SEE the channel but not SEND. Grant Send Messages on the "
                      "channel (a channel permission overwrite beats a guild-level grant).")
    channel_hint = f"channel id {channel_ids[CHANNEL_BRIEF]}"
    return Result("discord post (--post)", True,
                  f"posted through bridge.deliver to {channel_hint} - check it, then "
                  "delete the message")


# --------------------------------------------------------------------------- driver
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/probe_credentials.py",
        description="Make one real call per credential. Never prints a secret value.")
    parser.add_argument("--post", action="store_true",
                        help="ALSO post one message to the configured brief channel. Outward-facing: "
                             "publishes to a real channel. The only proof the bot can SEND.")
    args = parser.parse_args(argv)

    print("Probing provisioned credentials against the live vendors.")
    print("No secret value is printed. --post publishes a visible message.\n")

    # Fast HTTP probes FIRST so the operator sees progress immediately; the WebSocket probe is
    # last because it is the only one with a deadline to wait out (see probe_alpaca).
    probes = [probe_finnhub_news, probe_finnhub_calendar,
              probe_discord_identity, probe_discord_channel]
    if args.post:
        probes.append(probe_discord_post)
    probes.append(probe_alpaca)

    results = []
    for probe in probes:
        if probe is probe_alpaca:
            print(f"  ....  alpaca news WebSocket    connecting (up to "
                  f"{ALPACA_WAIT_SECONDS:.0f}s; a quiet tape is normal)", flush=True)
        result = probe()
        results.append(result)
        print(result.render(), flush=True)

    failed = [r for r in results if not r.ok]
    print()
    if failed:
        print(f"{len(failed)} of {len(results)} probes FAILED - fix before the live smoke "
              "(docs/runbooks/rw01-live-smoke.md).")
        return 1
    print(f"All {len(results)} probes passed.")
    if not args.post:
        print("NOT yet proven: that the bot can SEND to the brief channel (permission "
              "overwrites are invisible to a read-only probe). Run with --post to settle it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
