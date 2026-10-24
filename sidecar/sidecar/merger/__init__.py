"""Point-in-time merger dataset and an auditable completion model."""

from sidecar.merger.artifact import fit_calibration
from sidecar.merger.dataset import build_observations
from sidecar.merger.edgar import parse_submissions

__all__ = ["build_observations", "fit_calibration", "parse_submissions"]
