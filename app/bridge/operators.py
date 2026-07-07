"""WP6 — the single-operator allowlist (Inv 1; SEC — approval-actor authentication).

The ONE gate that decides whether a Discord user id may adjudicate a proposal. The set of
authorized operator ids is OPERATIONAL config, never an LLM-reachable path: it is read from
``Settings`` (env ``COCKPIT_OPERATOR_IDS`` — comma/space separated — or an injected config
seam), so no model output can add itself to the allowlist. A foreign/unknown Discord user id
is NOT authorized; the approval gateway (``bridge/approval.py``) and the auto-veto path
(``bridge/veto.py``) both refuse and audit any decision from a non-allowlisted actor.

``COCKPIT_OPERATOR_IDS`` is deliberately read from the environment directly (pydantic-settings
uses ``extra="ignore"`` so it is not a declared ``Settings`` field): keeping it out of the
typed settings surface means it can never be mutated by the normal config-write paths and is
only ever consulted read-only here.
"""
from __future__ import annotations

import os

_ENV_KEY = "COCKPIT_OPERATOR_IDS"


def _parse(raw) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if isinstance(raw, (set, frozenset, list, tuple)):
        items = [str(x) for x in raw]
    else:
        # comma- or whitespace-separated string
        items = [tok for tok in str(raw).replace(",", " ").split()]
    return frozenset(s.strip() for s in items if s and s.strip())


def authorized_operator_ids(settings=None) -> frozenset[str]:
    """The allowlisted Discord operator ids. Precedence: an explicit ``operator_ids`` attr on
    ``settings`` (the injected config seam, if a deployment carries one) -> the
    ``COCKPIT_OPERATOR_IDS`` env var. NEVER an LLM-produced field."""
    if settings is not None:
        seam = getattr(settings, "operator_ids", None)
        if seam:
            return _parse(seam)
    return _parse(os.environ.get(_ENV_KEY))


def is_authorized(user_id, settings=None) -> bool:
    """True iff ``user_id`` is on the operator allowlist. A ``None``/empty id, or any id not in
    the configured set (a foreign Discord user), is NOT authorized (fail closed)."""
    if user_id is None:
        return False
    uid = str(user_id).strip()
    if not uid:
        return False
    return uid in authorized_operator_ids(settings)
