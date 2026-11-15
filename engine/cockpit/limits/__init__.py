"""The signed caps and allowlist source (lane COCKPIT-PROPOSALS, first slice).

Reads and checks one installer-signed file per scope. It sizes, gates and books nothing
and is wired to nothing; see ``source``.
"""

from .source import (HIGH_WATER_SCHEMA, LIMITS_SCHEMA, SIGNING_PREFIX, FileHighWater, Limits,
                     LimitsSource, MemoryHighWater, Verifier, create_high_water, signing_input)

__all__ = ["HIGH_WATER_SCHEMA", "LIMITS_SCHEMA", "SIGNING_PREFIX", "FileHighWater", "Limits",
           "LimitsSource", "MemoryHighWater", "Verifier", "create_high_water", "signing_input"]
