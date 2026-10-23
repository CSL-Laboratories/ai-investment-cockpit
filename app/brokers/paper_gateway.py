"""Paper order gateway for profile handoff.

Paper admission is this concrete type, not a caller-set ``execution_scope``
string on ``OrderGateway``. One claim fence is bound at construction. It
reads the armed claim generation when the gateway calls it and returns the
``TransportPermit`` from ``_confirm_claim_before_broker_call``. A different
callable passed to ``submit`` is refused.

When ``OrderGateway`` accepts ``claim_fence`` (the CSL-218 gateway split,
#252), that parent calls this fence, caps admission by ``remaining_s``, and
submits through ``AlpacaPaperBroker.submit_order`` captured at init. This
class does not wrap the broker in that case. Until the parent accepts the
parameter, ``submit`` runs the same fence and pins the same captured adapter.
"""
from __future__ import annotations

import inspect

from brokers.alpaca_paper import AlpacaPaperBroker
from brokers.gateway import GateOutcome, OrderGateway

# #252's admission wait. A shorter renewed lease is reported on the permit.
_ADMISSION_BUDGET_S = 5.0
_MISSING = object()


def _parent_accepts_claim_fence() -> bool:
    return "claim_fence" in inspect.signature(OrderGateway.submit).parameters


def _parent_init_accepts_claim_fence() -> bool:
    return "claim_fence" in inspect.signature(OrderGateway.__init__).parameters


class BoundClaimFence:
    """The one fence a paper gateway binds at construction.

    Handoff arms a store, client id, and generation on this object. ``submit``
    does not build a new function. The generation is read here, when the
    gateway calls the fence, so a later ``arm_profile_claim`` is visible.
    """

    def __init__(self, gateway: PaperOrderGateway) -> None:
        self._gateway = gateway
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

    def overlay(self, fn) -> None:
        """Run ``fn`` instead of the armed claim. The callable identity stays."""
        if not callable(fn):
            raise TypeError("claim fence overlay must be callable")
        self._overlay = fn

    def _require_state_clock(self, store) -> None:
        """The post-fence refresh reads ``st.clock``. The store must use that clock."""
        from brokers.authority_profiles import ProfileRefused

        state = getattr(self._gateway, "_st", None)
        state_clock = None if state is None else getattr(state, "clock", None)
        store_clock = getattr(store, "_clock", None)
        if state_clock is None or store_clock is not state_clock:
            raise ProfileRefused("clock", "store clock is not the state clock")

    def __call__(self):
        if self._overlay is not None:
            return self._overlay()
        from brokers.authority_profiles import ProfileRefused

        if self.store is None or self.client_order_id is None or self.generation is None:
            raise ProfileRefused(
                "claim_required", "paper submit requires a validated profile claim"
            )
        self._require_state_clock(self.store)
        # Read at call time. A new lambda is not built per handoff.
        generation = self.generation
        permit = self.store._confirm_claim_before_broker_call(
            self.client_order_id, generation
        )
        return _with_short_remainder(self.store, self.client_order_id, permit)


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
        return super().__getattribute__(name)

    def __setattr__(self, name, value):
        # While the fallback pin is installed, a callback cannot replace it.
        # The parent then calls ``self._broker.submit_order``.
        if (
            name == "_broker"
            and getattr(self, "_hold_broker", False)
            and value is not getattr(self, "_held_broker", None)
        ):
            return
        super().__setattr__(name, value)

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
        # Same capture #252 takes in ``OrderGateway.__init__``. A later
        # replacement of the class attribute is not this gateway's transport.
        self._admitted_paper_submit = AlpacaPaperBroker.submit_order
        if _parent_init_accepts_claim_fence():
            kwargs["claim_fence"] = self._profile_fence
        super().__init__(broker, **kwargs)

    def arm_profile_claim(self, store, client_order_id: str, generation: int) -> None:
        """Arm the bound fence with the claim ``submit`` will confirm."""
        self._profile_fence.arm(store, client_order_id, generation)

    def arm_profile_fence(self, fn) -> None:
        """Install extra fence work on the bound callable. Identity does not change."""
        self._profile_fence.overlay(fn)

    def submit(self, order, **kwargs):
        if self._execution_scope != "paper" or not isinstance(self._broker, AlpacaPaperBroker):
            return GateOutcome(
                False, "transport_scope", "paper gateway pin does not match its type"
            )
        supplied = kwargs.get("claim_fence", _MISSING)
        if _parent_accepts_claim_fence():
            # The parent calls the fence bound at init. A different object is
            # its refusal. Omitting the argument uses that same fence.
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
        """Parent ``submit`` has no ``claim_fence``. Run the bound fence here.

        The permit is checked before the parent submit. The broker object is
        pinned so the call is the adapter captured at init, immediately after
        the fence runs again. ``#252`` does that inside ``OrderGateway.submit``
        and does not enter this path.
        """
        from brokers.authority_profiles import ProfileRefused, accept_transport_permit

        broker = self._broker
        pinned_type = type(broker)
        scope = self._execution_scope
        admitted = self._admitted_paper_submit
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
        try:
            accept_transport_permit(admitted_value)
        except Exception as exc:  # noqa: BLE001 - a forged permit never reaches the parent
            return GateOutcome(
                False,
                "claim_fence",
                f"claim fence refused the broker call: {exc}",
            )
        if (
            self._broker is not broker
            or type(self._broker) is not pinned_type
            or self._execution_scope != scope
        ):
            self._broker = broker
            self._execution_scope = scope
            return GateOutcome(
                False, "transport_scope", "paper gateway pin does not match its type"
            )

        fence = self._profile_fence

        class _PinnedBroker(AlpacaPaperBroker):
            def submit_order(self, canonical):
                # The bound fence confirms. This only consumes its permit and
                # calls the adapter captured at init.
                accept_transport_permit(fence())
                if AlpacaPaperBroker.submit_order is not admitted:
                    raise ProfileRefused(
                        "claim_required",
                        "paper submit requires a validated profile claim",
                    )
                return AlpacaPaperBroker.submit_order(broker, canonical)

            def __getattr__(self, name):
                return getattr(broker, name)

        pinned = object.__new__(_PinnedBroker)
        self._held_broker = pinned
        self._hold_broker = True
        self._broker = pinned
        try:
            return super().submit(order, **kwargs)
        finally:
            self._hold_broker = False
            self._broker = broker
