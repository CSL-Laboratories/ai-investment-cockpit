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


def _close_submission_intent(st, order, already_open: set, reason: str) -> None:
    """Resolve this submission's own intent. Leave every other intent open."""

    class _Seq:
        def __init__(self, seq: int) -> None:
            self.seq = seq

    order_id = getattr(order, "id", None)
    if type(order_id) is not str or not order_id:
        return
    rows = {row.seq: row for row in st.audit._rows()}
    for seq in list(st.audit.unresolved_intents()):
        if seq in already_open:
            continue
        row = rows.get(seq)
        if row is None:
            continue
        payload = row.payload or {}
        if payload.get("_phase") != "intent" or payload.get("_op") != "order_submit":
            continue
        if (row.refs or {}).get("order_id") != order_id:
            continue
        st.audit.result(
            _Seq(seq),
            "order_submit",
            refs={"order_id": order_id, "aborted": "admission_lock"},
            payload={"aborted": reason[:500]},
        )
        return


def _noop_risk(_positions):
    """Final admission must not call back into the caller."""
    return {"decision": "approved", "failed_checks": []}


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
        authority_kwargs = {
            "order": getattr(fence._local, "order", None),
            "producing_runtime": getattr(fence._local, "producing_runtime", None),
        }
        store._recheck_transport_authority(*authority, **authority_kwargs)
        failure = _safety_failure(gateway)
        if failure is not None:
            stage, _event, reason = failure
            raise ProfileRefused(stage, reason)
        # The risk callback above can revoke, halt, or stale the feed after the
        # checks that already ran. This pass has no callback.
        store._recheck_transport_authority(*authority, **authority_kwargs)
        failure = _safety_failure(gateway, risk_recheck=_noop_risk)
        if failure is not None:
            stage, _event, reason = failure
            raise ProfileRefused(stage, reason)
        return captured(canonical)

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


def _capture_send(client):
    """Pin the request function, credential snapshot, and paper endpoint.

    A later assignment on the live client cannot redirect this send. The
    secret stays in the captured headers and is not written to the profile store.
    """
    request = getattr(client, "_request", None)
    headers = getattr(client, "_headers", None)
    if not callable(request) or not isinstance(headers, dict):
        send = getattr(client, "submit_order")
        info = getattr(client, "order_info", None)

        def frozen(canonical, _send=send):
            return _send(canonical)

        if not callable(info):
            return frozen, None

        def frozen_info(*args, _info=info, **kwargs):
            return _info(*args, **kwargs)

        return frozen, frozen_info

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
    http = getattr(client, "_client", None)

    class _Pin:
        pass

    pin = _Pin()
    pin._headers = dict(snap_headers)
    pin._client = http

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

    return submit, order_info


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
        self._local.binding = accept_transport_permit(
            value,
            order_ref=order_ref,
            client=client,
            order=getattr(self._local, "order", None),
            producing_runtime=getattr(self._local, "producing_runtime", None),
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


class PaperOrderGateway(OrderGateway):
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
        # Same capture #252 takes in ``OrderGateway.__init__``. A later
        # replacement of the class attribute is not this gateway's transport.
        self._admitted_paper_submit = AlpacaPaperBroker.submit_order
        self._captured_submit, frozen_info = _capture_send(self._admitted_client)
        self._paper_submit_open = False
        if _parent_init_accepts_claim_fence():
            kwargs["claim_fence"] = self._profile_fence
        super().__init__(broker, **kwargs)
        client = _AdmittedPaperClient(
            self._admitted_client,
            _guarded_submit(self, self._captured_submit),
            order_info=frozen_info,
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
            try:
                return super().submit(order, **kwargs)
            except _AdmissionTimeout:
                _close_submission_intent(st, order, open_before, _ADMISSION_TIMEOUT)
                return GateOutcome(False, "admission_lock", _ADMISSION_TIMEOUT)
            finally:
                with state_lock:
                    if st.safety_admission_lock is wrapper:
                        st.safety_admission_lock = original_lock
        finally:
            self._paper_submit_open = False
