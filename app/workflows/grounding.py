"""WP8 — SEC-14 fail-closed grounding (contract Owner A 'grounding.py').

Implements docs/security-hardening-backlog.md SEC-14 and docs/prompt-injection-defense.md §7:
the LLM grounding verifier is ADVISORY and co-injectable — it can flag but NEVER clear.
Clearing requires DETERMINISTIC corroboration: (a) every cited ref is a member of the
dispatched packet (defeats citation-laundering) and resolvable from the verified raw store,
and (b) the proposal's symbol AND every thesis token that is a ticker-claim (an ALL-CAPS
ticker-shaped token in the original thesis, or a known ``symbol_universe`` ticker) appear as
a full member of the cited items' STRUCTURED ``symbols`` set — never matched against free-text
prose (so a capitalized English word equal to a ticker cannot corroborate). A lowercase,
off-universe ticker-shaped word in the thesis is treated as ordinary prose, not a claim; the
TRADED symbol is always corroborated with no case hole. The verifier model reads a deterministically
EXTRACTED slice (entities/numbers/dates/escaped headline), never the raw prose the proposer
saw. Absent / errored / invalid verification == flagged (fail closed). ``injection_predicate``
is the deterministic SEC-01 seam passed to ``BreakerEngine.check_order_path`` /
``AutoVetoCoordinator.resolve`` (also the SEC-21 same-ticker novelty breaker trigger).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from agent_runner.result import OK
from agent_runner.runner import run_agent
from agent_runner.spec import AgentSpec
from config.models import runtime_for
from core.ids import new_id
from ingest.normalize import escape_untrusted
from workflows.constants import EVENT_GROUNDING
from workflows.prompts import load_prompt
from workflows.schema_loader import load_schema

_TICKERISH = re.compile(r"^[A-Z]{2,5}$")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


@dataclass(frozen=True)
class Corroboration:
    cited_ok: bool
    entities_ok: bool
    reasons: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.cited_ok and self.entities_ok


def _cited_symbols(items: list[dict]) -> set[str]:
    """Union of the STRUCTURED ``symbols`` field across the resolved cited items, uppercased.
    Corroboration is decided against these tagged symbols ONLY — never the free-text
    headline/summary prose. (Round-2 bypass: a 'BUY IT' proposal cleared off an MSFT article
    that merely contained the English word 'it'; capitalized prose words equal to real
    tickers — IT/ALL/AI/ON/FOR/NEW/GO — must never count as corroboration.)"""
    symbols: set[str] = set()
    for item in items:
        for sym in item.get("symbols") or []:
            symbols.add(str(sym).upper())
    return symbols


def corroborate(proposal: dict, dispatched_refs: frozenset[str],
                raw_resolver: Callable[[str], dict], *,
                symbol_universe: frozenset[str] = frozenset()) -> Corroboration:
    """Deterministic, non-LLM corroboration (SEC-14). ``cited_ok``: source_refs non-empty,
    every ref in the dispatched packet, every ref resolvable from the verified raw store.
    ``entities_ok``: the proposal symbol and every ticker-like thesis claim appear in the
    cited items' STRUCTURED ``symbols`` field (an actual tagged symbol of a cited raw item) —
    NOT anywhere in the free-text headline/summary prose. A capitalized English word equal to
    a real ticker ('IT'/'ALL'/'AI'/'ON') in a cited article's prose must not corroborate, and
    a lowercase thesis naming a known ticker from ``symbol_universe`` still requires the ticker
    to be a tagged symbol of a cited item (fail closed)."""
    reasons: list[str] = []
    refs = list(proposal.get("source_refs") or [])
    cited_ok = True
    if not refs:
        cited_ok = False
        reasons.append("no source_refs cited")
    resolved: list[dict] = []
    for ref in refs:
        if ref not in dispatched_refs:
            cited_ok = False
            reasons.append(f"cited ref not in dispatched packet: {ref}")
            continue
        try:
            resolved.append(raw_resolver(ref))
        except Exception as exc:   # any resolution failure fails closed
            cited_ok = False
            reasons.append(f"cited ref unresolvable: {ref} ({type(exc).__name__})")

    entities_ok = True
    cited_symbols = _cited_symbols(resolved)   # tagged symbols of cited items ONLY, not prose
    symbol = str(proposal.get("symbol", "")).upper()
    if not symbol or symbol not in cited_symbols:
        entities_ok = False
        reasons.append(f"proposal symbol {symbol or '<missing>'} absent from cited symbols")
    # Case-insensitive thesis token extraction: an ALL-CAPS ticker-shaped token is treated
    # as a ticker-claim, as is any token whose upper form is a known ticker (symbol_universe).
    thesis = str(proposal.get("thesis", ""))
    universe = {s.upper() for s in symbol_universe}
    claims: set[str] = set()
    for raw in re.findall(r"\b[A-Za-z][A-Za-z0-9.\-]{0,11}\b", thesis):
        upper = raw.upper()
        if not _TICKERISH.match(upper):
            continue
        if raw.isupper() or upper in universe:   # ALL-CAPS in original OR a known ticker
            claims.add(upper)
    for token in claims:
        if token not in cited_symbols:
            entities_ok = False
            reasons.append(f"thesis entity {token} absent from cited symbols")
    return Corroboration(cited_ok=cited_ok, entities_ok=entities_ok, reasons=tuple(reasons))


def extract_slice(raw_items: list[dict]) -> str:
    """Deterministic entity/number slice of the cited raw items — what the verifier model
    is fed INSTEAD of the raw prose the proposer saw (SEC-14 'deterministically-extracted
    slice'). Symbols, numbers, dates and the ESCAPED headline only."""
    lines = []
    for item in raw_items:
        headline = escape_untrusted(str(item.get("headline", "")))[:200]
        combined = f"{item.get('headline', '')} {item.get('summary', '')}"
        numbers = ",".join(_NUMBER.findall(str(combined))[:16])
        symbols = ",".join(item.get("symbols") or [])
        lines.append(
            f'<untrusted-content source="{item.get("source", "")}" '
            f'article_ref="{item.get("raw_ref", "")}" ts="{item.get("ts", "")}">\n'
            f"symbols={symbols} numbers={numbers} event_type={item.get('event_type', '')}\n"
            f"headline={headline}\n"
            f"</untrusted-content>"
        )
    return "\n\n".join(lines)


def run_grounding_check(state, proposal: dict, raw_items: list[dict], *,
                        transport=None) -> dict | None:
    """One tool-starved triage-tier verifier call over the extracted slice. Returns the
    schema-validated GroundingCheck dict, or None on ANY failure (limit, timeout,
    quarantine, exception) — the caller treats None as flagged (fail closed)."""
    try:
        template = load_prompt("grounding_check")
        minimized = {k: proposal.get(k) for k in
                     ("proposal_id", "symbol", "side", "thesis", "invalidation", "source_refs")}
        prompt = template.render(
            proposal_id=str(proposal.get("proposal_id", "")),
            proposal_json=json.dumps(minimized, sort_keys=True, default=str),
            evidence_slice=extract_slice(raw_items),
        )
        alias = "triage"
        spec = AgentSpec(
            job_id=new_id("job"),
            runtime=runtime_for(alias),
            model_alias=alias,
            prompt=prompt,
            schema=load_schema("GroundingCheck"),
            decision_stage=True,
            allowed_tools=[],
        )
        result = run_agent(spec, transport=transport, usage=state.usage)
    except Exception:
        return None
    if result.status != OK or not isinstance(result.validated, dict):
        return None
    return result.validated


@dataclass(frozen=True)
class GroundingOutcome:
    flagged: bool
    check: dict | None
    corroboration: Corroboration
    reasons: tuple[str, ...]


def evaluate_grounding(state, proposal, dispatched_refs, raw_resolver, *,
                       transport=None,
                       symbol_universe: frozenset[str] = frozenset()) -> GroundingOutcome:
    """FAIL-CLOSED grounding evaluation (SEC-14). Flagged when the verifier check is
    absent/errored/invalid, when it reports grounded=false / injection_suspected /
    unsupported_claims, or when deterministic corroboration fails. A model
    ``grounded=true`` NEVER clears anything by itself. Audits EVENT_GROUNDING."""
    corr = corroborate(proposal, dispatched_refs, raw_resolver,
                       symbol_universe=symbol_universe)
    raw_items: list[dict] = []
    for ref in proposal.get("source_refs") or []:
        if ref in dispatched_refs:
            try:
                raw_items.append(raw_resolver(ref))
            except Exception:
                pass   # already reflected in corroboration reasons
    check = run_grounding_check(state, proposal, raw_items, transport=transport)

    reasons: list[str] = list(corr.reasons)
    flagged = False
    if check is None:
        flagged = True
        reasons.append("grounding check absent/errored/invalid (fail closed)")
    else:
        if check.get("grounded") is not True:
            flagged = True
            reasons.append("verifier reported grounded=false")
        if check.get("injection_suspected"):
            flagged = True
            reasons.append("verifier suspected injection")
        if check.get("unsupported_claims"):
            flagged = True
            reasons.append("verifier listed unsupported claims")
    if not corr.ok:
        flagged = True
        reasons.append("deterministic corroboration failed")

    state.audit.append(
        EVENT_GROUNDING, actor="grounding",
        refs={"proposal_id": str(proposal.get("proposal_id", ""))},
        payload={
            "proposal_id": str(proposal.get("proposal_id", "")),
            "flagged": flagged,
            "grounded": (check or {}).get("grounded"),
            "injection_suspected": (check or {}).get("injection_suspected"),
            "corroborated": corr.ok,
            "reasons": reasons,
        },
    )
    return GroundingOutcome(flagged=flagged, check=check, corroboration=corr,
                            reasons=tuple(reasons))


def injection_predicate(context: dict) -> bool:
    """Deterministic SEC-01 seam for BreakerEngine.check_order_path /
    AutoVetoCoordinator.resolve. Trips on a grounding-flagged proposal in an auto path or
    a same-ticker escalation burst (SEC-21 novelty breaker: count >= 4)."""
    if not context:
        return False
    if bool(context.get("grounding_flagged")):
        return True
    burst = context.get("same_ticker_burst")
    if isinstance(burst, dict):
        try:
            if int(burst.get("count", 0)) >= 4:
                return True
        except (TypeError, ValueError):
            return True   # malformed burst context fails closed
    return False
