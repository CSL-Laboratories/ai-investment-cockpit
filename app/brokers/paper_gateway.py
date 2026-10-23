"""Paper order gateway for profile handoff.

Paper admission is this concrete type, not a caller-set ``execution_scope``
string on ``OrderGateway``. One claim fence is bound at construction. It
reads the armed claim for this submission, confirms the claim, and consumes
the permit inside the store transaction.

``self._broker`` is not swapped per submit. Reads return the transport bound
at init. The paper client and its ``submit_order`` are the objects captured
at init, so a later replacement cannot redirect the send or the absence
proof. The captured send re-checks authority and the safety gates together.
When the parent accepts ``claim_fence`` (#252), the parent calls this same
fence and the captured client. Until then, ``submit`` runs the fence before
the parent's safety checks and then calls the parent.
"""
from __future__ import annotations

import inspect
import secrets
import threading

from brokers.alpaca_paper import AlpacaPaperBroker
from brokers.gateway import GateOutcome, OrderGateway

# #252's admission wait. A shorter renewed lease is reported on the permit.
_ADMISSION_BUDGET_S = 5.0
_ADMISSION_TIMEOUT = "admission lock acquisition exceeded the claim lease budget"
_INFLIGHT = "_gateway_inflight_orders"
_MISSING = object()
_REENTRANT = "re-entrant submit refused before the capability token is consumed"


class _AdmissionTimeout(Exception):
    """The fallback admission lock did not become free inside the budget."""


class _TimedAdmissionLock:
    """Bound a ``with lock`` that does not pass a timeout."""

    def __init__(self, inner, budget: float) -> None:
        self._inner = inner
        self._budget = budget

    def acquire(self, blocking: bool = True, timeout: float = -1):
        if timeout is None or timeout < 0:
            timeout = self._budget
        return self._inner.acquire(blocking, timeout)

    def release(self) -> None:
        self._inner.release()

    def __enter__(self):
        if not self.acquire():
            raise _AdmissionTimeout()
        return self

    def __exit__(self, *_args) -> bool:
        self.release()
        return False


def _parent_accepts_claim_fence() -> bool:
    return "claim_fence" in inspect.signature(OrderGateway.submit).parameters


def _parent_init_accepts_claim_fence() -> bool:
    return "claim_fence" in inspect.signature(OrderGateway.__init__).parameters


def _transport_matches(canonical, order_ref: str) -> bool:
    if not isinstance(canonical, dict) or type(order_ref) is not str or not order_ref:
        return False
    return canonical.get("client_order_id") == f"cockpit-{order_ref}"[:128]


def _inflight_set(st) -> set:
    inflight = getattr(st, _INFLIGHT, None)
    if inflight is None:
        inflight = set()
        setattr(st, _INFLIGHT, inflight)
    return inflight


def _order_is_inflight(st, order) -> bool:
    order_id = getattr(order, "id", None)
    if order_id is None:
        return False
    inflight = getattr(st, _INFLIGHT, None)
    return inflight is not None and order_id in inflight


_SUBMISSION_TOKEN = "_submission_token"
_SUBMISSION_NONCE = "_submission_nonce"
_STAMP_LOCK = threading.Lock()


class _StampedIntent:
    """Stamp the gateway submit intent for this submission.

    The stamp matches the approval binding and the gateway payload shape, and
    it adds a per-submission nonce the caller cannot supply. A callback that
    copies the order, approval, and risk ids does not receive the nonce. The
    closer does not read the call stack, so it does not depend on
    ``OrderGateway.submit`` living in ``gateway.py``.
    """

    def __init__(self, real) -> None:
        self._real = real
        self._local = threading.local()

    def __call__(self, op, refs=None, payload=None):
        token = getattr(self._local, "token", None)
        nonce = getattr(self._local, "nonce", None)
        order_id = getattr(self._local, "order_id", None)
        approval_id = getattr(self._local, "approval_id", None)
        risk_id = getattr(self._local, "risk_decision_id", None)
        bound = refs or {}
        body = payload or {}
        stamped = False
        if (
            token
            and nonce
            and op == "order_submit"
            and approval_id
            and risk_id
            and bound.get("order_id") == order_id
            and bound.get("approval_id") == approval_id
            and bound.get("risk_decision_id") == risk_id
            and "execution_scope" in body
            and "mode" in body
            and _SUBMISSION_TOKEN not in body
            and _SUBMISSION_NONCE not in body
        ):
            payload = dict(body)
            payload[_SUBMISSION_TOKEN] = token
            payload[_SUBMISSION_NONCE] = nonce
            stamped = True
        entry = self._real(op, refs=refs, payload=payload)
        # The timeout path closes this object, not whichever later intent copies
        # the nonce. A callback that reads the thread-local cannot replace it.
        if stamped:
            self._local.handle = entry
        return entry


def _arm_submission_token(st, token: str, order_or_id, *, approval_id=None) -> None:
    with _STAMP_LOCK:
        current = st.audit.intent
        if not isinstance(current, _StampedIntent):
            current = _StampedIntent(current)
            st.audit.intent = current
    if hasattr(order_or_id, "id"):
        order_id = order_or_id.id
        # Production births leave the row empty until the broker ack. The
        # submit argument is the approval this intent will name.
        if type(approval_id) is not str or not approval_id:
            approval_id = getattr(order_or_id, "approval_id", None)
        risk_id = getattr(order_or_id, "risk_decision_id", None)
    else:
        order_id = order_or_id
        approval_id = None
        risk_id = None
    current._local.token = token
    current._local.nonce = secrets.token_urlsafe(16)
    current._local.order_id = order_id
    current._local.approval_id = approval_id
    current._local.risk_decision_id = risk_id


def _clear_submission_token(st) -> None:
    current = getattr(getattr(st, "audit", None), "intent", None)
    if isinstance(current, _StampedIntent):
        current._local.token = None
        current._local.nonce = None
        current._local.order_id = None
        current._local.approval_id = None
        current._local.risk_decision_id = None
        current._local.handle = None


def _close_submission_intent(st, order, already_open: set, reason: str, token: str) -> None:
    """Resolve the intent this submission stamped. Leave every other intent open."""

    class _Seq:
        def __init__(self, seq: int) -> None:
            self.seq = seq

    if type(token) is not str or not token:
        return
    current = getattr(getattr(st, "audit", None), "intent", None)
    local = getattr(current, "_local", None)
    nonce = getattr(local, "nonce", None)
    if type(nonce) is not str or not nonce:
        return
    order_id = getattr(order, "id", None)
    rows = {row.seq: row for row in st.audit._rows()}
    handle = getattr(local, "handle", None)
    handle_seq = getattr(handle, "seq", None)
    if type(handle_seq) is int and handle_seq not in already_open:
        row = rows.get(handle_seq)
        payload = {} if row is None else (row.payload or {})
        if (
            payload.get("_phase") == "intent"
            and payload.get("_op") == "order_submit"
            and payload.get(_SUBMISSION_TOKEN) == token
            and payload.get(_SUBMISSION_NONCE) == nonce
        ):
            st.audit.result(
                _Seq(handle_seq),
                "order_submit",
                refs={"order_id": order_id, "aborted": "admission_lock"},
                payload={"aborted": reason[:500]},
            )
            return
    matched: list[int] = []
    for seq in list(st.audit.unresolved_intents()):
        if seq in already_open:
            continue
        row = rows.get(seq)
        if row is None:
            continue
        payload = row.payload or {}
        if payload.get("_phase") != "intent" or payload.get("_op") != "order_submit":
            continue
        if payload.get(_SUBMISSION_TOKEN) != token or payload.get(_SUBMISSION_NONCE) != nonce:
            continue
        matched.append(seq)
    if not matched:
        return
    st.audit.result(
        _Seq(max(matched)),
        "order_submit",
        refs={"order_id": order_id, "aborted": "admission_lock"},
        payload={"aborted": reason[:500]},
    )


def _noop_risk(_positions):
    """Final admission must not call back into the caller.

    The callback-bearing pass already ran. This stand-in keeps that second
    pass from re-entering the caller. ``_refuse_changed_risk_inputs`` and
    ``_refuse_risk_ceiling`` are the deterministic check over the snapshot
    taken before the callback.
    """
    return {"decision": "approved", "failed_checks": []}


def _risk_snapshot(order, positions):
    """A copy of the inputs the final risk pass compares."""
    import copy

    body = copy.deepcopy(positions) if isinstance(positions, dict) else positions
    return (
        str(getattr(order, "qty", None)),
        str(getattr(order, "limit_price", None)),
        str(getattr(order, "notional", None)),
        body,
    )


def _refuse_changed_risk_inputs(gateway) -> None:
    """Refuse when the callback edited the order or the positions it was shown."""
    from brokers.authority_profiles import ProfileRefused

    fence = object.__getattribute__(gateway, "_profile_fence")
    snapshot = getattr(fence._local, "risk_snapshot", None)
    if snapshot is None:
        return
    safety = getattr(fence._local, "safety", None)
    order = getattr(fence._local, "order", None)
    positions = safety.get("positions") if isinstance(safety, dict) else None
    if _risk_snapshot(order, positions) != snapshot:
        raise ProfileRefused("risk", "risk inputs changed during admission")


def _refuse_risk_ceiling(gateway) -> None:
    """The persisted risk decision's ceiling, not the callback's verdict."""
    from decimal import Decimal

    from brokers.authority_profiles import ProfileRefused
    from schemas.models.risk_decision import RiskDecision

    fence = object.__getattribute__(gateway, "_profile_fence")
    safety = getattr(fence._local, "safety", None)
    order = getattr(fence._local, "order", None)
    if not isinstance(safety, dict) or order is None:
        raise ProfileRefused("risk", "persisted risk decision is not approved")
    risk = safety.get("risk_decision")
    risk_id = getattr(risk, "id", None)
    st = object.__getattribute__(gateway, "_st")
    with st.session_factory() as session:
        row = session.get(RiskDecision, risk_id)
        if row is not None:
            session.expunge(row)
    if row is None or row.decision != "approved":
        raise ProfileRefused("risk", "persisted risk decision is not approved")
    ceiling = row.max_allowed_notional
    if ceiling is None:
        raise ProfileRefused("risk", "persisted risk decision is not approved")
    exposure = _tuple_exposure_notional(order, gateway)
    if not isinstance(exposure, Decimal) or not exposure.is_finite() or exposure > ceiling:
        raise ProfileRefused("risk", "order exposure exceeds the persisted risk ceiling")


def _safety_failure(gateway, *, risk_recheck=None):
    """Re-run the gateway gates on the state clock. ``None`` means they passed."""
    from brokers.authority_profiles import ProfileRefused
    from brokers.order_tuple import order_tuple_hash

    fence = object.__getattribute__(gateway, "_profile_fence")
    safety = getattr(fence._local, "safety", None)
    order = getattr(fence._local, "order", None)
    if safety is None or order is None:
        raise ProfileRefused(
            "claim_required", "paper submit requires a validated profile claim"
        )
    st = object.__getattribute__(gateway, "_st")
    resolved_hash = order_tuple_hash(gateway._resolve_tuple(order))
    recheck = safety["risk_recheck"] if risk_recheck is None else risk_recheck
    return OrderGateway._precheck(
        gateway,
        st,
        order,
        approval=safety["approval"],
        risk_decision=safety["risk_decision"],
        ladder=safety["ladder"],
        breaker_engine=safety["breaker_engine"],
        risk_recheck=recheck,
        positions=safety["positions"],
        producing_runtime=safety["producing_runtime"],
        analysis_vendor=safety.get("analysis_vendor"),
        analysis_generation=safety.get("analysis_generation"),
        resolved_hash=resolved_hash,
        clock=st.clock,
    )


def _guarded_submit(gateway, captured):
    """The send both the fallback and #252's captured adapter actually call."""

    def submit_order(canonical):
        from brokers.authority_profiles import ProfileRefused

        fence = object.__getattribute__(gateway, "_profile_fence")
        binding = getattr(fence._local, "binding", None)
        if binding is None:
            raise ProfileRefused(
                "claim_required", "paper submit requires a validated profile claim"
            )
        store, client_id, generation, order_ref = binding
        if not _transport_matches(canonical, order_ref):
            raise ProfileRefused(
                "client_order_conflict", "permit is bound to a different order"
            )
        authority = (
            client_id,
            generation,
            order_ref,
        )
        from brokers.authority_profiles import _state_quote

        market, quote_now = _state_quote(gateway)
        authority_kwargs = {
            "order": getattr(fence._local, "order", None),
            "producing_runtime": getattr(fence._local, "producing_runtime", None),
            "market": market,
            "now_quote": quote_now,
        }
        store._recheck_transport_authority(*authority, **authority_kwargs)
        # The parent precheck already ran the callback. If that call edited the
        # inputs and returned the old approval, do not send.
        _refuse_changed_risk_inputs(gateway)
        failure = _safety_failure(gateway)
        if failure is not None:
            stage, _event, reason = failure
            raise ProfileRefused(stage, reason)
        _refuse_changed_risk_inputs(gateway)
        # The risk callback above can revoke, halt, or stale the feed after the
        # checks that already ran. This pass has no callback. The ceiling is
        # the persisted risk decision, not the callback's verdict.
        seen = store._recheck_transport_authority(*authority, **authority_kwargs)
        failure = _safety_failure(gateway, risk_recheck=_noop_risk)
        if failure is not None:
            stage, _event, reason = failure
            raise ProfileRefused(stage, reason)
        _refuse_risk_ceiling(gateway)
        if (
            isinstance(canonical, dict)
            and str(canonical.get("order_type") or "").strip().lower() == "market"
        ):
            from brokers.authority_profiles import (
                _bind_sent_protective_limit,
                _collar_market_submission,
                _reserved_notional,
            )

            market, quote_now = _state_quote(gateway)
            order = authority_kwargs["order"]
            canonical = _collar_market_submission(
                order,
                canonical,
                market=market,
                now=quote_now,
                reserved=_reserved_notional(store, client_id),
            )
            _bind_sent_protective_limit(
                store, gateway, client_id, order, canonical.get("limit_price"),
            )
        from brokers.authority_profiles import (
            _approval_attempt_blocked,
            _bind_sent_submission,
        )

        safety = getattr(fence._local, "safety", None)
        approval = safety.get("approval") if isinstance(safety, dict) else None
        approval_id = getattr(approval, "id", None)
        if _approval_attempt_blocked(gateway, approval_id, except_client_id=client_id):
            raise ProfileRefused("approval", "approval is bound to an open attempt")
        attempt_id = secrets.token_urlsafe(16)
        store._arm_inflight(client_id, attempt_id)
        try:
            _bind_sent_submission(
                gateway,
                client_id,
                authority_kwargs["order"],
                approval_id,
                attempt_id=attempt_id,
            )
            store._require_authority_generation(seen)
            return captured(canonical)
        finally:
            store._clear_inflight(client_id)

    return submit_order


class _AdmittedPaperClient:
    """Client captured at init. ``submit_order`` stays the guarded send."""

    def __init__(self, real, submit, order_info=None) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_submit", submit)
        object.__setattr__(self, "_order_info", order_info)

    def __getattribute__(self, name):
        if name == "submit_order":
            return object.__getattribute__(self, "_submit")
        if name == "order_info":
            info = object.__getattribute__(self, "_order_info")
            if info is not None:
                return info
        if name == "_real_client":
            return object.__getattribute__(self, "_real")
        if name in {"_real", "_submit", "_order_info"}:
            return object.__getattribute__(self, name)
        return getattr(object.__getattribute__(self, "_real"), name)

    def __setattr__(self, name, value) -> None:
        if name in {"submit_order", "_request", "_headers", "_endpoint", "_client"}:
            return
        setattr(object.__getattribute__(self, "_real"), name, value)


class _ProxyCopyError(Exception):
    """The caller's explicit proxy cannot be copied onto a fresh transport."""


def _explicit_proxy(http):
    """The caller's ``proxy=`` URL, or None when no explicit proxy is set.

    Env proxies are per-scheme mounts and are not copied. An ``all://`` proxy
    with credentials, or one whose URL cannot be read, refuses: the send must
    not fall through to a direct connection or the caller's handler.
    """
    mounts = getattr(http, "_mounts", None)
    if not isinstance(mounts, dict):
        return None
    found = None
    for pattern, transport in mounts.items():
        if getattr(pattern, "pattern", None) != "all://":
            continue
        pool = None if transport is None else getattr(transport, "_pool", None)
        proxy_url = None if pool is None else getattr(pool, "_proxy_url", None)
        headers = () if pool is None else (getattr(pool, "_proxy_headers", ()) or ())
        if proxy_url is None or headers:
            raise _ProxyCopyError("explicit proxy cannot be copied")
        scheme = proxy_url.scheme.decode("ascii") if isinstance(proxy_url.scheme, bytes) else proxy_url.scheme
        host = proxy_url.host.decode("ascii") if isinstance(proxy_url.host, bytes) else proxy_url.host
        port = proxy_url.port
        if scheme not in {"http", "https"} or type(host) is not str or not host or type(port) is not int:
            raise _ProxyCopyError("explicit proxy cannot be copied")
        rendered = f"{scheme}://{host}:{port}"
        if found is not None and found != rendered:
            raise _ProxyCopyError("explicit proxy cannot be copied")
        found = rendered
    return found


def _owned_transport(http):
    """A fresh pool. The caller's transport and its handler are never reused.

    An explicit proxy is applied to that new pool. Env proxies are not.
    """
    import httpx

    try:
        proxy = _explicit_proxy(http)
        if proxy is None:
            return httpx.HTTPTransport()
        return httpx.HTTPTransport(proxy=proxy)
    except Exception:
        return None


def _owned_http_session(http):
    """A private HTTP session for this gateway.

    The caller's ``httpx.Client`` stays shared with them. This session gets
    its own connection pool. The caller's handler and event hooks are not
    copied. An explicit ``proxy=`` is copied onto the new pool; env proxies
    are not. A proxy that cannot be copied, including one with credentials,
    refuses instead of dialing directly. If the pool cannot be built, the
    caller is not a fallback. The secret is not copied into the profile store.
    """
    import httpx

    if not isinstance(http, httpx.Client):
        return None, None
    pinned_timeout = http.timeout
    owned_transport = _owned_transport(http)
    if owned_transport is None:
        return None, None
    owned = httpx.Client(
        timeout=pinned_timeout,
        follow_redirects=False,
        trust_env=False,
        event_hooks={},
        transport=owned_transport,
    )

    def request(method, url, *, headers=None, json=None, params=None, **_ignored):
        outgoing = httpx.Request(method, url, headers=headers, json=json, params=params)
        outgoing.extensions = dict(outgoing.extensions, timeout=pinned_timeout.as_dict())
        response = owned_transport.handle_request(outgoing)
        response.read()
        return response

    class _Session:
        def request(self, method, url, **kwargs):
            return request(method, url, **kwargs)

    return _Session(), owned


def _capture_send(client):
    """Pin the request function, credential snapshot, and paper endpoint.

    A later assignment on the live client cannot redirect this send. The
    secret stays in the captured headers and is not written to the profile store.
    An HTTP client gets a private session built here, at gateway init, so a
    request hook added on the caller's session cannot see that secret.
    """
    request = getattr(client, "_request", None)
    headers = getattr(client, "_headers", None)
    if not callable(request) or not isinstance(headers, dict):
        send = getattr(client, "submit_order")
        info = getattr(client, "order_info", None)

        def frozen(canonical, _send=send):
            return _send(canonical)

        if not callable(info):
            return frozen, None, None

        def frozen_info(*args, _info=info, **kwargs):
            return _info(*args, **kwargs)

        return frozen, frozen_info, None

    from brokers.alpaca_paper import (
        DATA_BASE_URL,
        PAPER_BASE_URL,
        _decimal_text,
        _normalize_order_body,
    )

    raw = request.__func__
    snap_headers = {
        key: value
        for key, value in headers.items()
        if type(key) is str and type(value) is str
    }
    import httpx

    from brokers.authority_profiles import ProfileRefused

    http = getattr(client, "_client", None)
    session, owned = _owned_http_session(http)
    # A real HTTP client with no owned pool must not fall back to that client.
    # A test double with no HTTP client keeps the pinned request function.
    if isinstance(http, httpx.Client) and (session is None or owned is None):
        def unavailable(_canonical):
            raise ProfileRefused(
                "transport", "owned paper transport could not be built"
            )

        def unavailable_info(*_args, **_kwargs):
            raise ProfileRefused(
                "transport", "owned paper transport could not be built"
            )

        return unavailable, unavailable_info, None

    class _Pin:
        pass

    pin = _Pin()
    pin._headers = dict(snap_headers)
    pin._client = session

    def call(method, base_url, path, *, json=None, params=None):
        if isinstance(base_url, str) and "data.alpaca.markets" in base_url:
            base = DATA_BASE_URL
        else:
            base = PAPER_BASE_URL
        return raw(pin, method, base, path, json=json, params=params)

    def submit(canonical):
        payload = {
            "symbol": canonical["symbol"],
            "qty": _decimal_text(canonical["qty"]),
            "side": canonical["side"],
            "type": canonical["order_type"],
            "time_in_force": canonical["tif"],
        }
        if canonical.get("limit_price") is not None:
            payload["limit_price"] = _decimal_text(canonical["limit_price"])
        if canonical.get("client_order_id"):
            payload["client_order_id"] = str(canonical["client_order_id"])[:128]
        body = call("POST", PAPER_BASE_URL, "/v2/orders", json=payload)
        return _normalize_order_body(body)

    def order_info(*, broker_order_id=None, client_order_id=None):
        from brokers.alpaca_paper import AlpacaPaperError, _clean_order_lookup_id

        if (broker_order_id is None) == (client_order_id is None):
            raise AlpacaPaperError("exactly one paper-order lookup id is required")
        if broker_order_id is not None:
            lookup = _clean_order_lookup_id(broker_order_id, field="broker_order_id")
            body = call("GET", PAPER_BASE_URL, f"/v2/orders/{lookup}")
            if not isinstance(body, dict) or str(body.get("id") or "") != lookup:
                raise AlpacaPaperError("Alpaca paper order response did not match broker id")
        else:
            lookup = _clean_order_lookup_id(client_order_id, field="client_order_id")
            body = call(
                "GET",
                PAPER_BASE_URL,
                "/v2/orders:by_client_order_id",
                params={"client_order_id": lookup},
            )
            if not isinstance(body, dict) or str(body.get("client_order_id") or "") != lookup:
                raise AlpacaPaperError(
                    "Alpaca paper order response did not match client id"
                )
        return _normalize_order_body(body)

    return submit, order_info, owned


class BoundClaimFence:
    """The one fence a paper gateway binds at construction.

    ``arm`` records the claim on this submission's thread. A later arm on
    another thread cannot change the generation this call confirms.
    """

    def __init__(self, gateway: PaperOrderGateway) -> None:
        self._gateway = gateway
        self._local = threading.local()
        self.store = None
        self.client_order_id = None
        self.generation = None
        self._overlay = None

    def arm(self, store, client_order_id: str, generation: int) -> None:
        self._require_state_clock(store)
        self._local.armed = (store, client_order_id, generation)
        self.store = store
        self.client_order_id = client_order_id
        self.generation = generation
        self._overlay = None

    def bind_order(self, order) -> None:
        """Remember this thread's order. A nested submit must not change it."""
        from brokers.authority_profiles import ProfileRefused

        ref = getattr(order, "id", None)
        if ref is None:
            raise ProfileRefused("order_required", "paper submit requires an order id")
        self._local.order_ref = str(ref)
        self._local.order = order

    def overlay(self, fn) -> None:
        """Run ``fn`` instead of the armed claim. The callable identity stays."""
        if not callable(fn):
            raise TypeError("claim fence overlay must be callable")
        self._overlay = fn

    def _order_ref(self) -> str | None:
        return getattr(self._local, "order_ref", None)

    def _require_state_clock(self, store) -> None:
        """The post-fence refresh reads ``st.clock``. The store must use that clock."""
        from brokers.authority_profiles import ProfileRefused

        state = getattr(self._gateway, "_st", None)
        state_clock = None if state is None else getattr(state, "clock", None)
        store_clock = getattr(store, "_clock", None)
        if state_clock is None or store_clock is not state_clock:
            raise ProfileRefused("clock", "store clock is not the state clock")

    def __call__(self):
        from brokers.authority_profiles import ProfileRefused, accept_transport_permit

        overlay = self._overlay
        armed = getattr(self._local, "armed", None)
        order_ref = self._order_ref()
        if overlay is not None:
            value = overlay()
        else:
            if armed is None:
                raise ProfileRefused(
                    "claim_required", "paper submit requires a validated profile claim"
                )
            store, client_order_id, generation = armed
            if order_ref is None:
                raise ProfileRefused("order_required", "paper submit requires an order id")
            self._require_state_clock(store)
            permit = store._confirm_claim_before_broker_call(client_order_id, generation)
            value = _with_short_remainder(store, client_order_id, permit)
        if value is None:
            return None
        if order_ref is None:
            raise ProfileRefused("order_required", "paper submit requires an order id")
        client = object.__getattribute__(self._gateway, "_admitted_client")
        from brokers.authority_profiles import _state_quote

        market, quote_now = _state_quote(self._gateway)
        self._local.binding = accept_transport_permit(
            value,
            order_ref=order_ref,
            client=client,
            order=getattr(self._local, "order", None),
            producing_runtime=getattr(self._local, "producing_runtime", None),
            market=market,
            now_quote=quote_now,
        )
        return value


def _with_short_remainder(store, client_order_id: str, permit):
    """Attach ``remaining_s`` when the renewed lease is inside the admission budget."""
    from brokers.authority_profiles import CLAIM_LEASE, TransportPermit, _parse_claimed_at

    if type(permit) is not TransportPermit or permit.remaining_s is not None:
        return permit
    with store._txn() as conn:
        row = conn.execute(
            "SELECT claimed_at FROM reservations WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
        now = store._authoritative_now
    if row is None or not row["claimed_at"]:
        return permit
    remaining = (_parse_claimed_at(row["claimed_at"]) + CLAIM_LEASE - now).total_seconds()
    if remaining >= _ADMISSION_BUDGET_S:
        return permit
    return TransportPermit(permit.store, permit.nonce, remaining_s=float(remaining))


class _BoundPaperTransport(AlpacaPaperBroker):
    """The broker object installed once. Submit never replaces ``gateway._broker``."""

    def __init__(self, real, admitted, client) -> None:
        super().__init__(client=client)
        self._real = real
        self._admitted = admitted
        self._client_pin = client
        self._submit_pin = _BoundPaperTransport.submit_order.__get__(self)

    def __getattribute__(self, name):
        if name == "_client":
            try:
                return object.__getattribute__(self, "_client_pin")
            except AttributeError:
                return super().__getattribute__(name)
        if name == "submit_order":
            try:
                return object.__getattribute__(self, "_submit_pin")
            except AttributeError:
                return super().__getattribute__(name)
        return super().__getattribute__(name)

    def submit_order(self, canonical):
        from brokers.authority_profiles import ProfileRefused

        admitted = object.__getattribute__(self, "_admitted")
        if AlpacaPaperBroker.submit_order is not admitted:
            raise ProfileRefused(
                "claim_required", "paper submit requires a validated profile claim"
            )
        return AlpacaPaperBroker.submit_order(self, canonical)


def _chain_notionals(st) -> dict:
    """MAX verified transition notional per order. Missing chain data is absent."""
    from sqlalchemy import select

    from orchestrator.state import EVENT_ORDER_TRANSITION
    from schemas.models.audit_entry import AuditEntry

    found: dict = {}
    if st is None or getattr(st, "audit", None) is None:
        return found
    with st.session_factory() as session:
        entries = session.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == EVENT_ORDER_TRANSITION)
            .order_by(AuditEntry.seq)
        ).scalars().all()
    for entry in entries:
        if not st.audit.verify_entry(entry):
            continue
        row_id = (entry.refs or {}).get("row_id")
        if not row_id or "notional" not in (entry.payload or {}):
            continue
        from brokers.gateway import _dec

        amount = _dec((entry.payload or {}).get("notional"))
        if not amount.is_finite():
            continue
        previous = found.get(row_id)
        found[row_id] = amount if previous is None else max(previous, amount)
    return found


def _frozen_market_exposure(st, order):
    """The exposure signed when a market order was sent, or Infinity."""
    from decimal import Decimal

    order_ref = "" if getattr(order, "id", None) is None else str(order.id)
    audit = None if st is None else getattr(st, "audit", None)
    if audit is None or not order_ref:
        return Decimal("Infinity")
    found = None
    for row in audit._rows():
        if not getattr(audit, "verify_entry", lambda _row: False)(row):
            continue
        if getattr(row, "event_type", None) not in {
            "paper_protective_limit", "paper_submission",
        }:
            continue
        refs = row.refs or {}
        if str(refs.get("order_id") or "") != order_ref:
            continue
        text = (row.payload or {}).get("exposure")
        if type(text) is not str or not text:
            continue
        from brokers.gateway import _dec

        amount = _dec(text)
        if amount.is_finite() and amount > 0:
            found = amount if found is None else max(found, amount)
    return Decimal("Infinity") if found is None else found


def _sized_exposure(st, order, chain_notional, *, current=False, gateway=None):
    """qty x limit, or the exposure frozen at send for a market order.

    The signed chain notional is a floor. A detached ``order.notional`` of $1
    must not shrink a sibling whose limit says $30. The order being submitted
    has not frozen a market size yet, so its own size is the fresh touch.
    """
    from decimal import Decimal

    from brokers.gateway import _dec

    sizes = []
    if chain_notional is not None and chain_notional.is_finite() and chain_notional > 0:
        sizes.append(chain_notional)
    limit = getattr(order, "limit_price", None)
    qty = getattr(order, "qty", None)
    if limit is not None and qty is not None:
        product = _dec(qty) * _dec(limit)
        if not product.is_finite() or product <= 0:
            return Decimal("Infinity")
        sizes.append(product)
    elif current:
        sizes.append(_tuple_exposure_notional(order, gateway))
    else:
        sizes.append(_frozen_market_exposure(st, order))
    if not sizes:
        return Decimal("Infinity")
    return max(sizes)


def _paper_pending_ok(self, st, order):
    """Parent tamper checks, then qty x limit (or frozen market size) for every open order.

    ``OrderGateway._pending_ok`` adds each sibling's chain notional. A birth
    signed at $1 understates a $30 limit. This sum replaces that total when
    the parent would have allowed the order. A parent refusal is kept.
    """
    from decimal import Decimal

    from sqlalchemy import select

    from schemas.models.order import Order

    parent_ok, parent_total, cap = OrderGateway._pending_ok(self, st, order)
    if not parent_ok:
        return parent_ok, parent_total, cap
    chain = _chain_notionals(st)
    open_states = {"approved", "submitted", "partially_filled"}
    with st.session_factory() as session:
        rows = list(session.execute(select(Order)).scalars().all())
        for row in rows:
            session.expunge(row)
    total = Decimal("0")
    for row in rows:
        if row.id == order.id:
            continue
        if row.state not in open_states:
            continue
        sized = _sized_exposure(st, row, chain.get(row.id))
        if not sized.is_finite():
            return False, sized, cap
        total += sized
    current = _sized_exposure(
        st, order, chain.get(order.id), current=True, gateway=self,
    )
    if not current.is_finite():
        return False, current, cap
    total += current
    return total <= cap, total, cap


def _tuple_exposure_notional(order, gateway=None):
    """Exposure is qty times limit price, or qty times a buffered fresh touch.

    ``OrderGateway._order_notional`` prefers a detached ``order.notional``.
    A birth row signed at $1 would then pass a cap the order exceeds. A
    market order has no limit price; without a fresh quote the cap fails
    closed instead of reading ``order.notional``.
    """
    from decimal import Decimal

    from brokers.authority_profiles import _tuple_notional
    from brokers.gateway import _dec

    limit = getattr(order, "limit_price", None)
    qty = getattr(order, "qty", None)
    if limit is not None and qty is not None:
        product = _dec(qty) * _dec(limit)
        if product.is_finite() and product > 0:
            return product
    if gateway is None:
        return Decimal("Infinity")
    try:
        st = object.__getattribute__(gateway, "_st")
        market = getattr(st, "market_data", None)
        now = st.clock.now()
        return _tuple_notional(order, market=market, now=now)
    except Exception:  # noqa: BLE001 - a missing quote must not fall through to order.notional
        return Decimal("Infinity")


class PaperExecutionGateway(OrderGateway):
    """The paper composition gateway ``build_paper_execution`` constructs.

    Pending exposure uses qty times limit price for every open order. The
    HTTP pool is owned here, the same way ``PaperOrderGateway`` owns it, so
    the production builder does not send on the caller's transport.
    """

    def __getattribute__(self, name):
        if name == "_order_notional":
            def _notional(order, _gateway=self):
                return _tuple_exposure_notional(order, _gateway)

            return _notional
        if name == "_pending_ok":
            return _paper_pending_ok.__get__(self, type(self))
        if name == "_broker":
            try:
                transport = object.__getattribute__(self, "_transport")
            except AttributeError:
                transport = None
            if transport is not None:
                return transport
        return super().__getattribute__(name)

    def __init__(self, broker, **kwargs):
        if not isinstance(broker, AlpacaPaperBroker):
            raise ValueError("paper gateway requires the admitted paper adapter")
        self._admitted_broker = broker
        self._admitted_client = broker._client
        startup_key = getattr(self._admitted_client, "_cockpit_startup_mac_key", None)
        if type(startup_key) is not str or len(startup_key) < 32:
            try:
                setattr(
                    self._admitted_client,
                    "_cockpit_startup_mac_key",
                    secrets.token_urlsafe(32),
                )
            except Exception:
                pass
        self._admitted_paper_submit = AlpacaPaperBroker.submit_order
        self._captured_submit, self._frozen_order_info, self._owned_http = _capture_send(
            self._admitted_client
        )
        self._paper_submit_open = False
        super().__init__(broker, **kwargs)
        client = _AdmittedPaperClient(
            self._admitted_client,
            self._captured_submit,
            order_info=self._frozen_order_info,
        )
        self._transport = _BoundPaperTransport(
            broker, self._admitted_paper_submit, client,
        )

    def __del__(self) -> None:
        owned = getattr(self, "_owned_http", None)
        if owned is not None:
            try:
                owned.close()
            except Exception:
                pass


class PaperOrderGateway(PaperExecutionGateway):
    """Paper transport by concrete type, not by a caller-set scope string.

    Construction forces the paper scope, fresh analysis and the admitted paper
    adapter. A plain ``OrderGateway(..., execution_scope="paper")`` is a
    different type and is not paper authority for a profile handoff.
    """

    _admits_paper_profile = True

    def __getattribute__(self, name):
        # Instance assignment of ``_precheck`` must not skip risk, mode, or approval.
        if name == "_precheck":
            return OrderGateway._precheck.__get__(self, PaperOrderGateway)
        # Transport is the object bound at init. Assignment does not redirect it.
        if name == "_broker":
            try:
                transport = object.__getattribute__(self, "_transport")
            except AttributeError:
                transport = None
            if transport is not None:
                return transport
        return super().__getattribute__(name)

    def __init__(self, broker, **kwargs):
        if not isinstance(broker, AlpacaPaperBroker):
            raise ValueError("paper gateway requires the admitted paper adapter")
        if kwargs.get("execution_scope", "paper") != "paper":
            raise ValueError("paper gateway cannot select another scope")
        if kwargs.get("require_analysis_generation", True) is not True:
            raise ValueError("paper gateway requires analysis generation checks")
        kwargs["execution_scope"] = "paper"
        kwargs["require_analysis_generation"] = True
        kwargs.pop("claim_fence", None)
        self._profile_fence = BoundClaimFence(self)
        if _parent_init_accepts_claim_fence():
            kwargs["claim_fence"] = self._profile_fence
        super().__init__(broker, **kwargs)
        # The parent already owns the pool. This wrapper is the guarded send.
        client = _AdmittedPaperClient(
            self._admitted_client,
            _guarded_submit(self, self._captured_submit),
            order_info=self._frozen_order_info,
        )
        self._transport = _BoundPaperTransport(broker, self._admitted_paper_submit, client)

    def arm_profile_claim(self, store, client_order_id: str, generation: int) -> None:
        """Arm the bound fence with the claim ``submit`` will confirm."""
        self._profile_fence.arm(store, client_order_id, generation)

    def arm_profile_fence(self, fn) -> None:
        """Install extra fence work on the bound callable. Identity does not change."""
        self._profile_fence.overlay(fn)

    def _reentrant(self) -> bool:
        return bool(self._paper_submit_open or getattr(self, "_submit_active", False))

    def _bind_submission(self, order, kwargs) -> None:
        self._profile_fence.bind_order(order)
        local = self._profile_fence._local
        local.producing_runtime = kwargs.get("producing_runtime")
        local.safety = kwargs

    def submit(self, order, **kwargs):
        if self._reentrant() or _order_is_inflight(self._st, order):
            return GateOutcome(False, "reentrant_submit", _REENTRANT)
        if self._execution_scope != "paper" or not isinstance(self._broker, AlpacaPaperBroker):
            return GateOutcome(
                False, "transport_scope", "paper gateway pin does not match its type"
            )
        self._bind_submission(order, kwargs)
        self._profile_fence._local.risk_snapshot = _risk_snapshot(
            order, kwargs.get("positions"),
        )
        supplied = kwargs.get("claim_fence", _MISSING)
        if _parent_accepts_claim_fence():
            # The parent calls the fence bound at init, after its re-entry guard.
            # A different object is its refusal. Omitting the argument uses that fence.
            if supplied is self._profile_fence:
                kwargs.pop("claim_fence", None)
            return super().submit(order, **kwargs)
        if supplied is not _MISSING and supplied is not self._profile_fence:
            return GateOutcome(
                False,
                "claim_fence",
                "per-call claim fence is not the fence bound at setup",
            )
        kwargs.pop("claim_fence", None)
        inflight = _inflight_set(self._st)
        order_id = getattr(order, "id", None)
        inflight.add(order_id)
        try:
            return self._submit_without_parent_fence(order, **kwargs)
        finally:
            inflight.discard(order_id)

    def _submit_without_parent_fence(self, order, **kwargs):
        """Parent ``submit`` has no ``claim_fence``. Fence first, then its checks.

        The parent's safety checks run after this fence. A HALT, watch-only
        mode, stale feed, or approval expiry that arrives during the fence is
        still visible, and the admission lock cannot wait past the budget.
        """
        scope = self._execution_scope
        self._paper_submit_open = True
        try:
            self._profile_fence._local.binding = None
            try:
                admitted_value = self._profile_fence()
            except Exception as exc:  # noqa: BLE001 - a fence error refuses before transport
                return GateOutcome(
                    False,
                    "claim_fence",
                    f"claim fence refused the broker call: {exc}",
                )
            if admitted_value is None:
                return GateOutcome(
                    False, "claim_fence", "claim fence returned no admission"
                )
            if self._execution_scope != "paper" or self._execution_scope != scope:
                return GateOutcome(
                    False, "transport_scope", "paper gateway pin does not match its type"
                )
            # The parent precheck uses the caller clock. When that clock is not
            # the state clock, refuse here so a frozen caller cannot hide expiry.
            caller_clock = kwargs.get("clock")
            if caller_clock is not None and caller_clock is not self._st.clock:
                failure = _safety_failure(self)
                if failure is not None:
                    stage, _event, reason = failure
                    return GateOutcome(False, stage, reason)
            st = self._st
            state_lock = st.lock
            with state_lock:
                original_lock = st.safety_admission_lock
                while isinstance(original_lock, _TimedAdmissionLock):
                    original_lock = original_lock._inner
                wrapper = _TimedAdmissionLock(original_lock, _ADMISSION_BUDGET_S)
                st.safety_admission_lock = wrapper
            open_before = set(st.audit.unresolved_intents())
            token = secrets.token_urlsafe(16)
            approval = kwargs.get("approval")
            _arm_submission_token(
                st, token, order, approval_id=getattr(approval, "id", None),
            )
            try:
                try:
                    return super().submit(order, **kwargs)
                except _AdmissionTimeout:
                    _close_submission_intent(
                        st, order, open_before, _ADMISSION_TIMEOUT, token
                    )
                    return GateOutcome(False, "admission_lock", _ADMISSION_TIMEOUT)
            finally:
                _clear_submission_token(st)
                with state_lock:
                    if st.safety_admission_lock is wrapper:
                        st.safety_admission_lock = original_lock
        finally:
            self._paper_submit_open = False
