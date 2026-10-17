"""Jev (TypeSafe) structured-decision client for paper/read-only proposals.

Jev is not an OpenAI-compatible chat API. A call is ``POST
https://api.typesafe.ai/v1/systemone`` with a state plus named questions, and
the response is typed answers (choice / score / yes-no) with probabilities.
There is no free-text rationale, so thesis text is a template over those
answers. The bearer token is the keyring secret ``jev_api_key`` (Invariant 4).
It is never read from the environment, logged, or written into ``.env``.

Live and auto-trading modes never reach this module. Oversized packets, low
confidence, and ordinary transport errors are returned as fallback reasons so
the caller can use Claude. HTTP 429, and HTTP 503 only when ``Retry-After`` is
present, are vendor limits: the attempt is marked so the caller can open
watch-only for Jev and still fall back. A transient 500 and every other 4xx
or 5xx fall back without that cooldown.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal

import httpx
import jsonschema

from agent_runner.result import agent_dispatched, mark_agent_dispatched
from config.models import (
    DEFAULT_JEV_MODEL,
    JEV_MODEL_PINS,
    JEV_CAP_MARGIN,
    JEV_REQUEST_HARD_LIMIT,
    JEV_STATE_HARD_LIMIT,
)
from core.ids import new_id
from core.secrets import get_secret
from ingest.normalize import escape_untrusted
from workflows.context_limit import estimate_tokens, fit_packet
from workflows.cost_log import quote_cost
from workflows.packets import Packet, PacketItem, wrap_untrusted

JEV_URL = "https://api.typesafe.ai/v1/systemone"
_REF = re.compile(r"^[A-Za-z0-9:/#._\-]{1,128}$")
_SYMBOL = re.compile(r"^[A-Z0-9.\-]{1,12}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")

_SIDES = frozenset({"buy", "sell"})
_ASSETS = frozenset({"equity", "option"})
_ORDERS = frozenset({"market", "limit", "stop", "stop_limit"})
_HORIZONS = frozenset({"intraday", "short", "long"})


@dataclass(frozen=True)
class JevAttempt:
    accepted: bool
    reason: str | None
    proposal: dict | None
    model_pinned: str
    response_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    input_token_source: str | None
    estimated_input: int
    usd: str | None
    rate_tier: str | None
    usd_scope: str | None
    billed: bool
    raw: str
    sent_refs: tuple[str, ...]
    dropped: int
    vendor_limited: bool = False
    request_hash: str = ""
    dispatched: bool = False

    def audit_payload(self) -> dict:
        """Fixed tokens only. No state text, no answer prose, no credential."""
        body = {
            "fallback": self.reason,
            "model_pinned": self.model_pinned,
            "billed": self.billed,
            "usd": self.usd,
            "rate_tier": self.rate_tier,
            "usd_scope": self.usd_scope,
            "input_token_source": self.input_token_source,
        }
        if self.input_tokens is not None:
            body["input_tokens"] = self.input_tokens
            body["output_tokens"] = self.output_tokens
        if self.response_model and self.response_model != self.model_pinned:
            body["response_model"] = self.response_model
        return body


JEV_SECRET_NAME = "jev_api_key"
# Same role-marker class ingest uses before a feed may enter a prompt (SEC-02).
_ROLE_MARKERS = re.compile(
    r"(?i)\b(?:system|assistant|human|developer|user|tool|function)\s*:"
    r"|</?untrusted-content|<\|im_(?:start|end)\|>|\[/?INST\]|###\s*instruction",
)
_WRAPPED = re.compile(
    r"^<untrusted-content\b[^>]*>\n(.*)\n</untrusted-content>$",
    re.DOTALL,
)


def jev_api_key() -> str | None:
    """Bearer token from the OS credential store. Missing means unconfigured."""
    secret = get_secret(JEV_SECRET_NAME)
    if secret is None:
        return None
    value = secret.reveal()
    if not value.strip():
        return None
    return value


def jev_weight(value) -> Decimal | None:
    """Triage score as a finite number. Booleans and missing values do not qualify."""
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    if not number.is_finite():
        return None
    return number


def _cap(settings, name: str, default: int, hard: int) -> int:
    raw = getattr(settings, name, default)
    try:
        cap = int(raw)
    except (TypeError, ValueError):
        return default
    if 1 <= cap <= hard - JEV_CAP_MARGIN:
        return cap
    return default


def _model_pin(settings) -> str | None:
    raw = str(getattr(settings, "jev_model", DEFAULT_JEV_MODEL) or "").strip()
    if raw in JEV_MODEL_PINS:
        return raw
    return None


def _safe_model_id(value) -> str | None:
    if isinstance(value, str) and _MODEL_ID.fullmatch(value):
        return value
    return None


def decision_questions(symbol: str) -> dict:
    """Operator-authored questions. Feed text stays in ``state``, not here."""
    return {
        "side": {
            "type": "choice",
            "instructions": f"For {symbol}, should a paper proposal be a buy or a sell?",
            "criteria": {
                "buy": "Open or add a long position",
                "sell": "Reduce or close a long position",
            },
        },
        "asset_type": {
            "type": "choice",
            "instructions": f"Is the {symbol} proposal an equity or an option?",
            "criteria": {
                "equity": "Common stock or ETF share",
                "option": "Listed option contract",
            },
        },
        "order_type": {
            "type": "choice",
            "instructions": f"Which order type fits the {symbol} proposal?",
            "criteria": {
                "market": "Market order",
                "limit": "Limit order",
                "stop": "Stop order",
                "stop_limit": "Stop-limit order",
            },
        },
        "time_horizon": {
            "type": "choice",
            "instructions": f"What holding horizon fits the {symbol} proposal?",
            "criteria": {
                "intraday": "Exit the same session",
                "short": "Days to a few weeks",
                "long": "Longer than a few weeks",
            },
        },
    }


def _framed_pair(state: str, name: str, question: dict) -> int:
    """Tokens in the JSON of state plus one named question, including keys."""
    fragment = {"state": state, name: question}
    return estimate_tokens(json.dumps(fragment, ensure_ascii=False))


def _prepare_packet(packet: Packet) -> Packet | None:
    """Neutralize feed text before it can be sent as Jev state.

    A packet that is already a single untrusted-content block is kept when its
    inner text has no live role marker. Anything else is escaped and wrapped.
    A live marker inside an existing wrapper is refused rather than sent.
    """
    prepared: list[PacketItem] = []
    for item in packet.items:
        text = item.text or ""
        wrapped = _WRAPPED.fullmatch(text)
        if wrapped:
            inner = wrapped.group(1)
            if _ROLE_MARKERS.search(inner) or "<" in inner or ">" in inner:
                return None
            prepared.append(item)
            continue
        inner = escape_untrusted(text)
        block = wrap_untrusted(inner, source="packet", article_ref=item.raw_ref)
        prepared.append(PacketItem(item.event_id, item.raw_ref, item.symbol, block))
    return Packet(items=tuple(prepared))


def _fit_state(packet: Packet, questions: dict, model: str, *,
               state_cap: int, request_cap: int) -> Packet | None:
    """Tail-trim until each framed state+question pair and the whole request fit."""
    overhead = max(
        _framed_pair("", name, question) for name, question in questions.items()
    )
    state_budget = state_cap - overhead
    if state_budget < 1:
        return None
    fitted = fit_packet(packet, reserved_tokens=0, cap=state_budget)
    items = list(fitted.packet.items)
    while items:
        state = "\n\n".join(item.text for item in items)
        if any(
            _framed_pair(state, name, question) > state_cap
            for name, question in questions.items()
        ):
            items.pop()
            continue
        body = {"state": state, "model": model, "questions": questions}
        encoded = json.dumps(body, ensure_ascii=False)
        if estimate_tokens(encoded) <= request_cap:
            return Packet(items=tuple(items))
        items.pop()
    return None


def blocked_jev_attempt(settings, reason: str) -> JevAttempt:
    """A fallback that never calls HTTP. Used when Jev is already watch-only."""
    model = _model_pin(settings) or DEFAULT_JEV_MODEL
    return _fallback(reason, model)


def _fallback(reason: str, model: str, *, estimated: int = 0, dropped: int = 0,
              usage: tuple[int, int] | None = None, response_model: str | None = None,
              raw: str = "", vendor_limited: bool = False, dispatched: bool = False) -> JevAttempt:
    usd = rate = scope = source = None
    billed = False
    inp = out = None
    if usage is not None:
        inp, out = usage
        quote = quote_cost(
            model, input_tokens=inp, output_tokens=out, input_token_source="vendor",
        )
        usd, rate, scope, source = quote.usd, quote.rate_tier, quote.usd_scope, "vendor"
        billed = True
    return JevAttempt(
        accepted=False, reason=reason, proposal=None, model_pinned=model,
        response_model=response_model, input_tokens=inp, output_tokens=out,
        input_token_source=source, estimated_input=estimated, usd=usd,
        rate_tier=rate, usd_scope=scope, billed=billed, raw=raw, sent_refs=(),
        dropped=dropped, vendor_limited=vendor_limited, dispatched=dispatched,
    )


def _read_choice(answers: dict, key: str, allowed: frozenset[str]) -> tuple[str, Decimal] | None:
    raw = answers.get(key)
    if not isinstance(raw, dict) or raw.get("type") != "choice":
        return None
    choice = raw.get("choice")
    if not isinstance(choice, str) or choice not in allowed:
        return None
    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    try:
        number = Decimal(str(confidence))
    except (ArithmeticError, ValueError):
        return None
    if not number.is_finite() or number < 0 or number > 1:
        return None
    return choice, number


def _fmt(number: Decimal) -> str:
    return format(number.quantize(Decimal("0.01")), "f")


def _proposal(symbol: str, model: str, side, asset, order, horizon, refs: list[str]) -> dict:
    side_v, side_p = side
    asset_v, asset_p = asset
    order_v, order_p = order
    horizon_v, horizon_p = horizon
    return {
        "proposal_id": new_id("prop"),
        "symbol": symbol,
        "side": side_v,
        "asset_type": asset_v,
        "order_type": order_v,
        "quantity": None,
        "notional": None,
        "time_horizon": horizon_v,
        "thesis": (
            f"Typed Jev decision for {symbol}: side {side_v} (confidence {_fmt(side_p)}), "
            f"asset {asset_v} (confidence {_fmt(asset_p)}), "
            f"order {order_v} (confidence {_fmt(order_p)}), "
            f"horizon {horizon_v} (confidence {_fmt(horizon_p)})."
        ),
        "invalidation": (
            f"This template is wrong if {symbol} should not be a {side_v} "
            f"{asset_v} {order_v} held on a {horizon_v} horizon."
        ),
        "sizing_basis": (
            "Jev does not set size. Quantity and notional are unset pending a human."
        ),
        "source_refs": refs,
        "generated_by": {"runtime": "jev", "model": model, "session_id": None},
    }


def _usage_pair(payload: dict) -> tuple[int, int] | None:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    incoming = usage.get("input_tokens")
    outgoing = usage.get("output_tokens")
    if type(incoming) is int and type(outgoing) is int and incoming >= 0 and outgoing >= 0:
        return incoming, outgoing
    return None


def _post(body: dict, *, api_key: str, client) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    return client.post(JEV_URL, headers=headers, json=body, timeout=30.0)


def _opens_jev_cooldown(response: httpx.Response) -> bool:
    """429 always. 503 only with a non-empty Retry-After. Nothing else.

    A single 500 must not stick Jev in watch-only. Other 4xx stay ordinary
    fallbacks. The caller opens the Jev window only; it does not freeze triage.
    """
    if response.status_code == 429:
        return True
    if response.status_code != 503:
        return False
    retry_after = response.headers.get("Retry-After")
    return isinstance(retry_after, str) and bool(retry_after.strip())


def evaluate_decision(settings, packet: Packet, *, symbol: str | None, schema: dict,
                      client=None, secret_values=None) -> JevAttempt:
    """Ask Jev for one proposal, or return a fallback reason without raising.

    Ordinary HTTP failures, low confidence, and schema-invalid answers do not
    open watch-only. HTTP 429, and HTTP 503 with Retry-After, are marked
    ``vendor_limited`` so the caller can.
    """
    model = _model_pin(settings) or DEFAULT_JEV_MODEL
    ticker = str(symbol or "").strip().upper()
    if _model_pin(settings) is None:
        return _fallback("jev_error", model)
    if not _SYMBOL.fullmatch(ticker):
        return _fallback("jev_invalid", model)
    original_count = len(packet.items)
    prepared = _prepare_packet(packet)
    if prepared is None:
        return _fallback("jev_error", model)
    packet = prepared
    questions = decision_questions(ticker)
    state_cap = _cap(settings, "jev_state_token_cap", 30_000, JEV_STATE_HARD_LIMIT)
    request_cap = _cap(settings, "jev_request_token_cap", 60_000, JEV_REQUEST_HARD_LIMIT)
    fitted = _fit_state(
        packet, questions, model, state_cap=state_cap, request_cap=request_cap,
    )
    dropped = 0 if fitted is None else original_count - len(fitted.items)
    if fitted is None or not fitted.items:
        return _fallback("jev_over_cap", model, dropped=dropped)
    state = fitted.prompt_block()
    body = {"state": state, "model": model, "questions": questions}
    encoded = json.dumps(body, ensure_ascii=False)
    estimated = estimate_tokens(encoded)
    key = jev_api_key()
    if key is None:
        return _fallback("jev_unconfigured", model, estimated=estimated, dropped=dropped)
    secrets = [item for item in (secret_values or []) if item]
    secrets.append(key)
    # Scan the state itself: JSON escaping can hide a value that would still be sent.
    if any(secret in state or secret in encoded for secret in secrets):
        return _fallback("jev_error", model, estimated=estimated, dropped=dropped)

    def _sent(reason: str, **kwargs) -> JevAttempt:
        return _fallback(
            reason, model, estimated=estimated, dropped=dropped, dispatched=True, **kwargs,
        )

    owns_client = client is None
    http = client
    if owns_client:
        http = httpx.Client(timeout=30.0)

    def _mark_on_send(_request) -> None:
        # httpx runs request hooks after the closed-client check and before the
        # transport. A closed client raises first and must not keep the unit.
        mark_agent_dispatched()

    hooks = None
    try:
        candidate = http.event_hooks["request"]
    except (AttributeError, KeyError, TypeError):
        candidate = None
    if isinstance(candidate, list):
        candidate.append(_mark_on_send)
        hooks = candidate
    try:
        try:
            response = _post(body, api_key=key, client=http)
        except Exception:
            # A closed client raises before the request hook. A failure after
            # that hook has started the send and keeps the unit.
            if not agent_dispatched():
                return _fallback(
                    "jev_error", model, estimated=estimated, dropped=dropped,
                )
            return _sent("jev_error")
        if hooks is None:
            # A hook-less double has no send boundary. post() returned, so the
            # call completed before close() can discard this attempt.
            mark_agent_dispatched()
        if _opens_jev_cooldown(response):
            return _sent("jev_vendor_limit", vendor_limited=True)
        if response.status_code != 200:
            return _sent("jev_error")
        try:
            payload = response.json()
        except ValueError:
            return _sent("jev_error")
    finally:
        if hooks is not None:
            try:
                hooks.remove(_mark_on_send)
            except ValueError:
                pass
        if owns_client and http is not None:
            http.close()
    if not isinstance(payload, dict):
        return _sent("jev_invalid")
    usage = _usage_pair(payload)
    returned = _safe_model_id(payload.get("model"))
    if returned != model:
        return _sent("jev_invalid", usage=usage, response_model=returned)
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return _sent("jev_invalid", usage=usage, response_model=returned)
    parsed = (
        _read_choice(answers, "side", _SIDES),
        _read_choice(answers, "asset_type", _ASSETS),
        _read_choice(answers, "order_type", _ORDERS),
        _read_choice(answers, "time_horizon", _HORIZONS),
    )
    if any(item is None for item in parsed):
        return _sent("jev_invalid", usage=usage, response_model=returned)
    floor = Decimal(str(getattr(settings, "jev_min_confidence", "0.6")))
    if any(item[1] < floor for item in parsed):
        return _sent("jev_low_confidence", usage=usage, response_model=returned)
    refs = sorted(ref for ref in fitted.dispatched_refs if _REF.fullmatch(ref))
    proposal = _proposal(ticker, model, *parsed, refs)
    try:
        jsonschema.validate(proposal, schema)
    except jsonschema.ValidationError:
        return _sent("jev_invalid", usage=usage, response_model=returned)
    if usage is not None:
        quote = quote_cost(
            model, input_tokens=usage[0], output_tokens=usage[1],
            input_token_source="vendor",
        )
        source = "vendor"
        inp, out = usage
    else:
        quote = quote_cost(
            model, input_tokens=estimated, output_tokens=None,
            input_token_source="estimate",
        )
        source = "estimate"
        inp = out = None
    raw = json.dumps(
        {"model": returned or model, "answers": answers, "usage": payload.get("usage")},
        ensure_ascii=False,
    )
    return JevAttempt(
        accepted=True, reason=None, proposal=proposal, model_pinned=model,
        response_model=returned, input_tokens=inp, output_tokens=out,
        input_token_source=source, estimated_input=estimated, usd=quote.usd,
        rate_tier=quote.rate_tier, usd_scope=quote.usd_scope, billed=True,
        raw=raw, sent_refs=tuple(refs), dropped=dropped, dispatched=True,
        request_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )
