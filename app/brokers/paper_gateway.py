"""Paper order gateway for profile handoff.

Paper admission is this concrete type, not a caller-set ``execution_scope``
string on ``OrderGateway``. One claim fence is bound at construction. It
reads the armed claim generation when the gateway calls it, confirms the
claim, and consumes the permit inside the store transaction.

``self._broker`` is not swapped per submit. Reads return the transport bound
at init, and that object's ``submit_order`` runs inside the gateway lock.
When the parent accepts ``claim_fence`` (#252), the parent calls this same
fence. Until then, ``submit`` runs the fence before the parent's safety
checks and then calls the parent.
"""
from __future__ import annotations

import inspect
import threading

from brokers.alpaca_paper import AlpacaPaperBroker
from brokers.gateway import GateOutcome, OrderGateway

# #252's admission wait. A shorter renewed lease is reported on the permit.
_ADMISSION_BUDGET_S = 5.0
_MISSING = object()
_REENTRANT = "re-entrant submit refused before the capability token is consumed"


def _parent_accepts_claim_fence() -> bool:
    return "claim_fence" in inspect.signature(OrderGateway.submit).parameters


def _parent_init_accepts_claim_fence() -> bool:
    return "claim_fence" in inspect.signature(OrderGateway.__init__).parameters


def _transport_matches(canonical, order_ref: str) -> bool:
    if not isinstance(canonical, dict) or type(order_ref) is not str or not order_ref:
        return False
    return canonical.get("client_order_id") == f"cockpit-{order_ref}"[:128]


class BoundClaimFence:
    """The one fence a paper gateway binds at construction.

    Handoff arms a store, client id, and generation on this object. ``submit``
    does not build a new function. The generation and the order id are read
    here, when the gateway calls the fence.
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

        order_ref = self._order_ref()
        if self._overlay is not None:
            value = self._overlay()
        else:
            if self.store is None or self.client_order_id is None or self.generation is None:
                raise ProfileRefused(
                    "claim_required", "paper submit requires a validated profile claim"
                )
            if order_ref is None:
                raise ProfileRefused("order_required", "paper submit requires an order id")
            self._require_state_clock(self.store)
            # Read at call time. A new lambda is not built per handoff.
            generation = self.generation
            permit = self.store._confirm_claim_before_broker_call(
                self.client_order_id, generation
            )
            value = _with_short_remainder(self.store, self.client_order_id, permit)
        if value is None:
            return None
        if order_ref is None:
            raise ProfileRefused("order_required", "paper submit requires an order id")
        client = object.__getattribute__(self._gateway, "_admitted_client")
        self._local.binding = accept_transport_permit(
            value, order_ref=order_ref, client=client
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

    def __init__(self, real, admitted) -> None:
        super().__init__(client=real._client)
        self._real = real
        self._admitted = admitted
        self._client_pin = real._client

    def __getattribute__(self, name):
        # A substituted client cannot prove an order absent or carry the submit.
        if name == "_client":
            try:
                return object.__getattribute__(self, "_client_pin")
            except AttributeError:
                return super().__getattribute__(name)
        return super().__getattribute__(name)

    def submit_order(self, canonical):
        from brokers.authority_profiles import ProfileRefused

        admitted = object.__getattribute__(self, "_admitted")
        real = object.__getattribute__(self, "_real")
        if AlpacaPaperBroker.submit_order is not admitted:
            raise ProfileRefused(
                "claim_required", "paper submit requires a validated profile claim"
            )
        gateway = object.__getattribute__(self, "_gateway")
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
        store._recheck_transport_authority(client_id, generation, order_ref)
        return AlpacaPaperBroker.submit_order(real, canonical)


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
        # Same capture #252 takes in ``OrderGateway.__init__``. A later
        # replacement of the class attribute is not this gateway's transport.
        self._admitted_paper_submit = AlpacaPaperBroker.submit_order
        self._paper_submit_open = False
        if _parent_init_accepts_claim_fence():
            kwargs["claim_fence"] = self._profile_fence
        super().__init__(broker, **kwargs)
        transport = _BoundPaperTransport(broker, self._admitted_paper_submit)
        transport._gateway = self
        self._transport = transport

    def arm_profile_claim(self, store, client_order_id: str, generation: int) -> None:
        """Arm the bound fence with the claim ``submit`` will confirm."""
        self._profile_fence.arm(store, client_order_id, generation)

    def arm_profile_fence(self, fn) -> None:
        """Install extra fence work on the bound callable. Identity does not change."""
        self._profile_fence.overlay(fn)

    def _reentrant(self) -> bool:
        return bool(self._paper_submit_open or getattr(self, "_submit_active", False))

    def submit(self, order, **kwargs):
        if self._reentrant():
            return GateOutcome(False, "reentrant_submit", _REENTRANT)
        if self._execution_scope != "paper" or not isinstance(self._broker, AlpacaPaperBroker):
            return GateOutcome(
                False, "transport_scope", "paper gateway pin does not match its type"
            )
        self._profile_fence.bind_order(order)
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
        return self._submit_without_parent_fence(order, **kwargs)

    def _submit_without_parent_fence(self, order, **kwargs):
        """Parent ``submit`` has no ``claim_fence``. Fence first, then its checks.

        The parent's safety checks run after this fence, so a HALT or a stale
        feed that arrives during the fence is still visible. Transport stays
        the object bound at init.
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
            return super().submit(order, **kwargs)
        finally:
            self._paper_submit_open = False
