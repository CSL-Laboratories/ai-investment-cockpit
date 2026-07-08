"""WP9 / G9 — SECRETS-IN-LOGS GREP = 0 (the RUNTIME analog of the static secrets-in-repo gate).

docs/build-scaffold-ultracode.md WP9 + docs/ultracode-build-playbook.md §3 (G9 gate). The
static invariant gate (tests/test_invariants.test_invariant_4_no_plaintext_secrets_in_app)
greps the *repo* for secret literals; this file is its runtime twin: it composes the full
cockpit, drives a representative slice that would PLAUSIBLY emit secrets/PII, and asserts
ZERO secret material reached the log stream, the delivered content, or the audit chain.

The slice (task-specified):
  1. a morning brew over the SEC-09 bait fixture (FAKE_ACCOUNT + $-amounts + foreign
     ticker+size ride inside ``market_day_events``);
  2. an UNAUTHORIZED / foreign approval attempt (a non-allowlisted actor -> Rejection);
  3. a proposal that gets scrubbed/quarantined (``poisoned_proposal_body``);
  4. a killswitch.

CAUSALITY (WP9 non-negotiable — every guarantee test proves it is not vacuously green): the
app source today logs almost nothing (only ``core.logging`` touches the ``logging`` module),
so a bare "no secret in logs" assertion could pass while proving nothing. Two controls make
the machinery provably causal:
  * ``test_g9_control_scanner_detects_planted_secrets`` — emits AKIA / discord-token /
    ``api_key="..."`` shapes AND asserts (a) ``capture_logs`` actually captured them (the
    capture mechanism is live) and (b) ``SECRET_PATTERNS`` flags each (the scanner is live);
  * the delivered-content check pairs with a positive control asserting the SOURCE brief
    carried a ``$``-amount, so a clean delivered payload proves the SEC-09 scrubber REMOVED
    it (not that it was never there).

If a real path ever emits secret material, that is a REAL finding — the assertions are not
loosened to pass (project rule). Assertions go through PUBLIC entry points, the delivered
seam, and HMAC-verified persisted rows only (mirrors tests/workflows/test_g8_acceptance).
Hermetic: tmp SQLite, autouse in-memory keyring, FrozenClock, isolated COCKPIT_HALT_PATH.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from decimal import Decimal

from sqlalchemy import select

from brokers.order_tuple import canonical_order_tuple
from bridge.approval import Rejection, record_decision
from core.ids import new_id
from orchestrator.killswitch import halt_is_set, killswitch
from schemas.models.audit_entry import AuditEntry
from schemas.models.risk_decision import RiskDecision
from workflows.packets import build_packet
from workflows.proposals import draft_proposal

from tests.system.harness import (
    HOLDINGS,
    WATCHLIST,
    FakeTransport,
    RoutedTransport,
    SECRET_PATTERNS,
    brief_body,
    capture_logs,
    envelope,
    grounding_body,
    market_day_events,
    persist_day,
    scored_item,
    triage_body,
)
from tests.workflows.fixtures_market_day import (
    FAKE_ACCOUNT,
    poisoned_proposal_body,
)

# A dollar amount anywhere in a blob (the SEC-09 scrubber redacts these to "[amount]").
_DOLLAR = re.compile(r"\$\s?\d")


def _secret_hits(text: str) -> list[str]:
    """Every SECRET_PATTERNS regex (from the invariant gate, re-exported by the harness) that
    matches ``text``. Empty == the runtime-log grep is clean (G9 == 0)."""
    return [p.pattern for p in SECRET_PATTERNS if p.search(text)]


def _key_encodings(key: bytes) -> list[str]:
    """The plausible textual encodings a 32-byte HMAC key could leak as (hex/base64/raw/repr).
    A secret must appear in NONE of the log text nor any persisted audit payload."""
    encs = [
        key.hex(),
        key.hex().upper(),
        base64.b64encode(key).decode("ascii"),
        base64.b64encode(key).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(key).decode("ascii"),
        repr(key),
    ]
    try:
        encs.append(key.decode("latin-1"))
    except Exception:  # noqa: BLE001 - a non-decodable key simply contributes no raw form
        pass
    return [e for e in encs if e]


# =======================================================================================
# G9 — the runtime secrets-in-logs gate over a representative full-system slice
# =======================================================================================
def test_g9_no_secrets_or_pii_in_logs(system, tmp_path):
    now = system.clock.now()
    key_encs = _key_encodings(system.hmac_key)

    # ---- persist the SEC-09 bait day (FAKE_ACCOUNT + $-amounts + foreign ticker+size) -----
    events = market_day_events(now)
    _, rows = persist_day(system, events)
    assert len(rows) == len(events), "every fixture item must persist exactly once"

    dispatched = {rows["a-aapl-1"].raw_ref, rows["a-msft-1"].raw_ref, rows["f-filing-1"].raw_ref}
    classification = {"a-aapl-1": ("material", 72), "a-msft-1": ("material", 68),
                      "f-filing-1": ("urgent", 91)}
    scored = [scored_item(row.raw_ref, row.symbol, *classification.get(aid, ("ignore", 5)))
              for aid, row in rows.items()]

    # POSITIVE CONTROL for the delivered-scrub guarantee: the SOURCE brief carries a $-amount
    # (brief_body's default watch_items), so a clean delivered payload proves the scrubber
    # REMOVED it rather than it never being there.
    brief = brief_body(sorted(dispatched), now=now)
    assert _DOLLAR.search(json.dumps(brief, default=str)), \
        "control: the source brief must carry a $-amount so the scrub is observable"

    with capture_logs(level=logging.DEBUG) as get_log:
        # (1) morning brew over the bait day -> BriefRecord + delivered brief.
        brew_transport = FakeTransport([envelope(triage_body(scored, now=now)), envelope(brief)])
        brew_res = system.run_brew(brew_transport)
        assert brew_res.get("status") not in ("no_events", "degraded_watch_only")

        # (2) UNAUTHORIZED / foreign approval attempt, routing PII (FAKE_ACCOUNT) through the
        #     interaction id — must be refused, no ApprovalRecord, no token.
        clk = system.clock
        rd = RiskDecision(
            id=new_id("rd"), proposal_id="p-bait", decision="approved", checks={},
            failed_checks=[], warnings=[], max_allowed_notional=Decimal("5000"),
            human_required=False, evaluated_at=clk.now(), created_at=clk.now())
        resolved = canonical_order_tuple(symbol="AAPL", side="buy", qty="10",
                                         order_type="limit", limit_price="150", tif="day")
        rej = record_decision(
            system.st, proposal_id="p-bait", risk_decision=rd, resolved_order=resolved,
            actor_user_id="intruder-90210", user_decision="approve", surface="discord",
            interaction_id=f"intr-{FAKE_ACCOUNT}", token_registry=system.token_registry,
            clock=clk)
        assert isinstance(rej, Rejection) and rej.code == "unauthorized", \
            "a foreign actor must be refused (no ApprovalRecord, no token)"

        # (3) a poisoned proposal (fake account + foreign ticker+size + $-amounts) that the
        #     SEC-09 scrubber must QUARANTINE — the bait must never egress a rendered embed.
        packet = build_packet([rows["a-aapl-1"], rows["a-bait-1"]])
        draft_transport = RoutedTransport(
            haiku=[envelope(grounding_body("prop-bait-1", [rows["a-aapl-1"].raw_ref]))],
            opus=[envelope(poisoned_proposal_body("prop-bait-1", "AAPL",
                                                  [rows["a-aapl-1"].raw_ref]))])
        outcome = draft_proposal(
            system.st, clk, event=rows["a-aapl-1"], packet=packet, transport=draft_transport,
            parquet_root=system.parquet_root, holdings_symbols=HOLDINGS, watchlist=WATCHLIST)
        assert outcome.status == "quarantined" and outcome.embed is None, \
            "the poisoned proposal must be quarantined and never rendered"

        # (4) killswitch — instant containment (isolated HALT path).
        kill = killswitch(system.st, actor="op", reason="G9 secrets-in-logs drill",
                          halt_path=str(tmp_path / "g9-HALT"), clock=clk)
        assert halt_is_set(kill.halt_path), "the killswitch must set the SEC-04 HALT sentinel"

        logs = get_log()

    # ---- GUARANTEE A: the runtime log stream carries ZERO secret material -----------------
    hits = _secret_hits(logs)
    assert not hits, f"G9: SECRET_PATTERNS matched the runtime logs (real leak): {hits}"
    for enc in key_encs:
        assert enc not in logs, "G9: the HMAC audit key leaked into the log stream"
    assert FAKE_ACCOUNT not in logs, "G9: a fake account number / PII reached the log stream"
    assert _DOLLAR.search(logs) is None, "G9: an un-redacted $-amount reached the log stream"

    # ---- GUARANTEE B: the DELIVERED brief is scrubbed (non-vacuous: it was delivered) -----
    assert system.deliver.posts, "the brew delivered nothing — the scrub check would be vacuous"
    posts_blob = json.dumps(system.deliver.posts, default=str)
    assert _DOLLAR.search(posts_blob) is None, "SEC-09: a $-amount reached the delivered brief"
    assert FAKE_ACCOUNT not in posts_blob, "SEC-09: the fake account reached the delivered brief"
    assert _secret_hits(posts_blob) == [], "a secret pattern reached the delivered content"

    # ---- GUARANTEE C: the HMAC key never enters the audit chain (scan ALL rows) -----------
    assert system.st.audit.verify_chain().ok, "the full slice must leave the chain verified"
    with system.st.session_factory() as s:
        entries = s.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
    assert entries, "the slice produced no audit entries — the payload scan would be vacuous"
    audit_blob = "\n".join(
        json.dumps({"payload": e.payload, "refs": e.refs}, default=str) for e in entries)
    for enc in key_encs:
        assert enc not in audit_blob, \
            "SEC-10: the HMAC signing key entered a persisted AuditEntry.payload"


# =======================================================================================
# CONTROL — the capture + scanner machinery is CAUSAL (proves G9 is not vacuously green)
# =======================================================================================
def test_g9_control_scanner_detects_planted_secrets(system):
    """If a secret WERE logged, the gate MUST fail. Emit representative secret shapes through
    the real ``logging`` module under ``capture_logs`` and prove (a) the capture is live and
    (b) ``SECRET_PATTERNS`` + the key-encoding substring check both fire."""
    planted = [
        "AKIA" + "A" * 16,                                  # AWS access-key id shape
        'api_key="ABCDEFGHIJKLMNOP0123"',                   # generic key=quoted-literal shape
        "N" + "a" * 23 + "." + "bbbbbb" + "." + "c" * 27,   # discord bot token shape
    ]
    with capture_logs(level=logging.DEBUG) as get_log:
        log = logging.getLogger("tests.system.g9.control")
        for sample in planted:
            log.info("planted secret sample -> %s", sample)
        text = get_log()

    assert text.strip(), "capture_logs captured nothing — the capture mechanism is dead"
    hits = _secret_hits(text)
    assert len(hits) >= 3, f"scanner failed to flag planted secret shapes; hits={hits}"

    # the key-encoding substring check is causal: a payload carrying the hex key IS detected.
    key = system.hmac_key
    synthetic = json.dumps({"leaked": key.hex()})
    assert any(enc in synthetic for enc in _key_encodings(key)), \
        "the HMAC-key encoding scan cannot detect a planted key (scan is dead)"

    # negative half: a benign line trips nothing (no false-positive that would mask a leak).
    with capture_logs(level=logging.DEBUG) as get_clean:
        logging.getLogger("tests.system.g9.control").info("brew ok; 3 items dispatched")
        clean = get_clean()
    assert _secret_hits(clean) == [], "the scanner false-positives on benign log text"
