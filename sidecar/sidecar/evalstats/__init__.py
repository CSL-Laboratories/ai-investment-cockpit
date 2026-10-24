"""Statistical evaluation: PSR, DSR, PBO, track-record length, bootstrap, drawdown."""

from sidecar.evalstats.ledger import TrialLedger
from sidecar.evalstats.report import evaluate

__all__ = ["TrialLedger", "evaluate"]
