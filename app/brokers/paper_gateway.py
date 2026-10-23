"""Paper order gateway for profile handoff.

Paper admission is this concrete type, not a caller-set ``execution_scope``
string on ``OrderGateway``. When ``OrderGateway.submit`` accepts
``claim_fence`` (the CSL-218 gateway split, #252), this class forwards that
callback and does not call it. The parent then owns re-entrancy, the
admission-lock budget, the post-fence feed/risk/approval refresh, intent
coverage for ``BaseException``, and the ``AlpacaPaperBroker.submit_order``
captured before the fence.

Until that parameter exists, a supplied fence runs once before the parent
submit and again inside the broker call. The second call must return a
transport permit. A missing fence refuses at stage ``claim_fence`` either way.
``_precheck`` stays pinned here so an instance replacement cannot skip it.
"""
from __future__ import annotations

import inspect

from brokers.alpaca_paper import AlpacaPaperBroker
from brokers.gateway import GateOutcome, OrderGateway


def _parent_accepts_claim_fence() -> bool:
    return "claim_fence" in inspect.signature(OrderGateway.submit).parameters


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

    def __init__(self, broker, **kwargs):
        if not isinstance(broker, AlpacaPaperBroker):
            raise ValueError("paper gateway requires the admitted paper adapter")
        if kwargs.get("execution_scope", "paper") != "paper":
            raise ValueError("paper gateway cannot select another scope")
        if kwargs.get("require_analysis_generation", True) is not True:
            raise ValueError("paper gateway requires analysis generation checks")
        kwargs["execution_scope"] = "paper"
        kwargs["require_analysis_generation"] = True
        super().__init__(broker, **kwargs)

    def submit(self, order, **kwargs):
        if self._execution_scope != "paper" or not isinstance(self._broker, AlpacaPaperBroker):
            return GateOutcome(
                False, "transport_scope", "paper gateway pin does not match its type"
            )
        fence = kwargs.get("claim_fence")
        if not callable(fence):
            return GateOutcome(
                False,
                "claim_fence",
                "paper profile submission requires a claim fence",
            )
        # #252 calls the fence itself, after the intent is open. A pre-call here
        # would hide re-entrancy, the admission lock, and BaseException coverage.
        if _parent_accepts_claim_fence():
            return super().submit(order, **kwargs)

        broker = self._broker
        pinned_type = type(broker)
        scope = self._execution_scope
        # Captured before the fence, same as #252: a subclass override or a
        # method swapped inside the callback is not the profile transport.
        admitted_paper_submit = AlpacaPaperBroker.submit_order
        try:
            fence()
        except Exception as exc:  # noqa: BLE001 - a fence error refuses before transport
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

        class _PinnedBroker(AlpacaPaperBroker):
            def submit_order(self, canonical):
                from brokers.authority_profiles import ProfileRefused, accept_transport_permit

                # Final fence under the store lock, then the admitted class
                # function captured before the fence. OrderGateway.submit is
                # what enters this object. A class-attribute swap is refused
                # rather than called. The attribute call is the one the
                # Invariant-1 AST gate counts.
                accept_transport_permit(fence())
                if AlpacaPaperBroker.submit_order is not admitted_paper_submit:
                    raise ProfileRefused(
                        "claim_required",
                        "paper submit requires a validated profile claim",
                    )
                return AlpacaPaperBroker.submit_order(broker, canonical)

            def __getattr__(self, name):
                return getattr(broker, name)

        pinned = object.__new__(_PinnedBroker)
        self._broker = pinned
        try:
            forward = dict(kwargs)
            forward.pop("claim_fence", None)
            return super().submit(order, **forward)
        finally:
            self._broker = broker
