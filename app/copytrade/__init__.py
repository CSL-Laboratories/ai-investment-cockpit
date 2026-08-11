"""RW-16 — congressional copy-trading: the deterministic core.

    models.py   the domain types (a disclosed transaction line, an intent, a skip, a plan)
    policy.py   every knob, in one reviewable frozen dataclass
    sizing.py   a disclosed dollar BUCKET -> a size for THIS account
    options.py  a disclosed CONTRACT -> an action in the underlying (never copied 1:1)
    engine.py   disclosures + policy + context -> a fully-accounted CopyPlan

EVERYTHING RE-EXPORTED HERE IS A PURE FUNCTION OR A FROZEN VALUE. No clock, no I/O, no
model, no randomness — so importing this package cannot start a poller, open a database, or
read a credential, and ``plan()`` can be replayed against a historical batch to reproduce the
decisions that were actually made.

DELIBERATELY NOT RE-EXPORTED: ``copytrade.universe`` (audited state writes) and
``workflows.copytrade_orders`` (the module that carries an intent toward the order path).
They are imported by their full path, at the call site, so that "this code can cause a state
change" is visible in the importing file rather than hidden behind a package convenience.
An order still reaches a broker ONLY via risk-pass -> mode -> approval -> ``OrderGateway``
(Invariant 1); nothing in this package goes near that path.
"""
from copytrade.engine import ALL_SKIP_REASONS, SIDE_BUY, SIDE_SELL, PlanContext, plan
from copytrade.models import (
    ASSET_EQUITY,
    ASSET_OPTION_CALL,
    ASSET_OPTION_PUT,
    ASSET_OTHER,
    CHAMBER_HOUSE,
    CHAMBER_SENATE,
    KNOWN_TX_TYPES,
    TX_EXCHANGE,
    TX_PURCHASE,
    TX_SALE,
    TX_SALE_PARTIAL,
    AmountRange,
    CopyIntent,
    CopyPlan,
    Disclosure,
    Skip,
)
from copytrade.options import (
    ACTION_BUY_UNDERLYING,
    ACTION_DROP,
    ACTION_EXIT_UNDERLYING,
    OPTION_DROP_REASONS,
    OptionTranslation,
    translate_option,
)
from copytrade.policy import (
    OPTIONS_IGNORE,
    OPTIONS_POLICIES,
    OPTIONS_UNDERLYING,
    CopyPolicy,
    PolicyError,
    normalize_filer,
    parse_follow_list,
    policy_from_settings,
)

__all__ = [
    # models
    "AmountRange", "Disclosure", "CopyIntent", "Skip", "CopyPlan",
    "TX_PURCHASE", "TX_SALE", "TX_SALE_PARTIAL", "TX_EXCHANGE", "KNOWN_TX_TYPES",
    "ASSET_EQUITY", "ASSET_OPTION_CALL", "ASSET_OPTION_PUT", "ASSET_OTHER",
    "CHAMBER_HOUSE", "CHAMBER_SENATE",
    # policy
    "CopyPolicy", "PolicyError", "policy_from_settings", "normalize_filer", "parse_follow_list",
    "OPTIONS_UNDERLYING", "OPTIONS_IGNORE", "OPTIONS_POLICIES",
    # options translation
    "OptionTranslation", "translate_option",
    "ACTION_BUY_UNDERLYING", "ACTION_EXIT_UNDERLYING", "ACTION_DROP", "OPTION_DROP_REASONS",
    # engine
    "plan", "PlanContext", "ALL_SKIP_REASONS", "SIDE_BUY", "SIDE_SELL",
]
