"""Paper order gateway for profile handoff.

Paper admission is this concrete type, not a caller-set ``execution_scope``
string on ``OrderGateway``. The claim-fence hook inside ``OrderGateway.submit``
is the separate CSL-218 gateway split. This class sets
``_admits_paper_profile`` so that hook can pin the paper adapter. Until the
hook is present, a supplied ``claim_fence`` runs once before the parent submit
and a fence error refuses closed. This pull request depends on that split
landing on main first for the in-lock fence.
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
        if "claim_fence" in inspect.signature(OrderGateway.submit).parameters:
            return super().submit(order, **kwargs)
        fence = kwargs.pop("claim_fence", None)
        if fence is not None:
            try:
                fence()
            except Exception as exc:  # noqa: BLE001 - a fence error refuses before transport
                return GateOutcome(
                    False,
                    "claim_fence",
                    f"claim fence refused the broker call: {exc}",
                )
        return super().submit(order, **kwargs)
