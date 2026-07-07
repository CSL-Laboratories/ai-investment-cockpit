"""WP0 — structured logging setup.

A value+pattern secret-scrubbing filter is required before any live use
(docs/security-hardening-backlog SEC-09); this WP0 version wires the handler and leaves
a hook for that filter.
"""
from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt="%(asctime)s %(levelname)s %(name)s :: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    return root
