"""Paper order gateway for profile handoff.

Paper admission is this concrete type, not a caller-set ``execution_scope``
string on ``OrderGateway``. The claim-fence hook inside ``OrderGateway.submit``
is the separate CSL-218 gateway split (#252). This class sets
``_admits_paper_profile`` so that hook can pin the paper adapter. Until the
hook is present, a supplied ``claim_fence`` runs once before the parent submit
and again inside the broker call, immediately before the admitted adapter.
A fence error refuses closed.

``_precheck`` and ``broker.submit_order`` are pinned here so an instance
replacement cannot skip them. #252 pins the same calls inside
``OrderGateway.submit`` for every gateway; this class does not edit that
module. The post-admission HALT probe during ``safety_admission_lock`` stays
on #252.
"""
from __future__ import annotations

import inspect

from brokers.alpaca_paper import AlpacaPaperBroker
from brokers.gateway import GateOutcome, OrderGateway


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
                "claim_required",
                "paper submit requires a validated profile claim",
            )
        broker = self._broker
        pinned_type = type(broker)
        scope = self._execution_scope
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
                from brokers.authority_profiles import accept_transport_permit

                # Final fence under the store lock, then the admitted class
                # method. An instance ``submit_order`` replacement is not called.
                # OrderGateway.submit is what enters this object.
                accept_transport_permit(fence())
                return pinned_type.submit_order(broker, canonical)

            def __getattr__(self, name):
                return getattr(broker, name)

        pinned = object.__new__(_PinnedBroker)
        self._broker = pinned
        try:
            forward = dict(kwargs)
            if "claim_fence" in inspect.signature(OrderGateway.submit).parameters:
                return super().submit(order, **forward)
            forward.pop("claim_fence", None)
            return super().submit(order, **forward)
        finally:
            self._broker = broker
