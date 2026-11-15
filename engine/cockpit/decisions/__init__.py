"""Decision records and the model-risk log (lane COCKPIT-PROPOSALS, first slice).

An append-only, owner-only, hash-chained file per research case. Records data only: no
code here sizes, approves, vetoes or books anything. See ``record``.
"""

from .record import ANSWERS, GENESIS, KINDS, RECORD_SCHEMA, DecisionRecord

__all__ = ["ANSWERS", "GENESIS", "KINDS", "RECORD_SCHEMA", "DecisionRecord"]
