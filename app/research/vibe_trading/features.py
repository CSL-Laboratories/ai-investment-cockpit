"""Project sidecar JSON into normalized, token-bounded market events.

Sidecar objects are untrusted. Extra keys are dropped, categories are
recomputed when the IC series stats are present, and a contradictory
alive/reversed label is not forwarded. The text that can reach a decision
packet is escaped by ``to_market_event`` and trimmed to the context cap
*before* it is persisted. Raw payloads stay on the event for the parquet
raw store (Invariant 8) and are not copied into the headline.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from ingest.dedup import Dedup
from ingest.normalize import escape_untrusted, to_market_event
from workflows.context_limit import estimate_tokens
from workflows.packets import wrap_untrusted

SOURCE = "vibe-trading"
EVENT_ALPHA = "signal:alpha"
EVENT_RUN_CARD = "signal:run_card"
EVENT_13F = "signal:13f"
EVENT_ETF = "signal:etf"
EVENT_PROBABILITY = "signal:event_probability"

# Same slice ``workflows.packets.build_packet`` applies. Estimating the wrapped
# text at this width is what the decision prompt will actually contain.
_PACKET_ITEM_CHARS = 1200
# Longer than a real parquet raw_ref and a real timezone timestamp, so the
# trim underestimates nothing once the row is wrapped for a decision packet.
_ESTIMATE_REF = "parquet://0000-00-00/events-" + ("f" * 32) + "#000"
_ESTIMATE_TS = "0000-00-00T00:00:00.000000+00:00"
_HARD_EVENT_TYPES = frozenset({"halt", "filing"})
_FACTOR_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_QUARTER = re.compile(r"\d{4}Q[1-4]")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_AS_OF = re.compile(r"[0-9TZ:.\-+]{10,40}")

_RESEARCH_SOURCES = frozenset({"yfinance", "yahoo", "stooq", "local"})
_FORBIDDEN_IMPORT_ROOTS = frozenset({
    "brokers", "risk", "importlib", "subprocess", "ctypes", "multiprocessing",
    "keyring", "os", "socket", "ssl", "http", "urllib", "httpx", "requests",
    "aiohttp", "pwd", "grp", "dbus", "jeepney", "winreg", "win32api", "win32cred",
})
_FORBIDDEN_ORDER_NAMES = frozenset({
    "place_order",
    "submit_order",
    "place_equity_order",
    "place_crypto_order",
    "OrderGateway",
    "exec",
    "eval",
    "compile",
    "getattr",
    "globals",
    "locals",
    "vars",
    "import_module",
    "__import__",
})
_MAX_SCANNED_ENGINE_FILES = 32
_CONFIG_ORDER_KEYS = frozenset({
    "broker", "account", "api_key", "token", "secret", "password",
    "side", "qty", "quantity", "order_type", "limit_price",
})


@dataclass(frozen=True)
class FeatureLine:
    priority: tuple
    text: str
    event_type: str
    symbols: tuple[str, ...]
    raw: dict
    tool: str


def categorise(row: dict) -> tuple[str, str] | None:
    """Return ``(category, source)`` using the published bench_runner thresholds.

    alive: ic_mean > 0.02 and ic_positive_ratio >= 0.55 and |t| > 2
    reversed: ic_mean < -0.02 and |t| > 2
    dead: everything else we can recompute

    A sidecar label is kept only when the series stats needed to recompute are
    absent and the label does not contradict the sign of IC. Otherwise the
    category is ``unverified``. Bad numeric rows return ``None`` and are skipped.
    """
    ic_mean = _finite(row.get("ic_mean"))
    if ic_mean is None:
        return None
    ic_std = _finite(row.get("ic_std"))
    pos = _finite(row.get("ic_positive_ratio"))
    count = row.get("ic_count")
    if count is None:
        count_ok = False
    elif type(count) is not int or not 1 <= count <= 1_000_000:
        return None
    else:
        count_ok = True
    stats_present = ic_std is not None and pos is not None and count_ok
    if stats_present:
        # |t| > 2  <=>  ic_mean^2 * n > 4 * ic_std^2. Exact rational arithmetic
        # avoids both underflowing denominators (subnormal ic_std) and
        # overflowing products (huge finite stats).
        t_big = ic_std > 0 and (
            Fraction(ic_mean) ** 2 * count > 4 * Fraction(ic_std) ** 2
        )
        if ic_mean > 0.02 and pos >= 0.55 and t_big:
            return "alive", "recomputed"
        if ic_mean < -0.02 and t_big:
            return "reversed", "recomputed"
        return "dead", "recomputed"
    label = row.get("category")
    if label not in {"alive", "reversed", "dead"}:
        return "unverified", "unverified"
    if ic_mean > 0 and label == "reversed":
        return "unverified", "unverified"
    if ic_mean < 0 and label == "alive":
        return "unverified", "unverified"
    return label, "sidecar"


def _finite(value) -> float | None:
    if type(value) is bool or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _ok(payload: dict) -> bool:
    """Accept both alpha_bench ``status=ok`` and mirrored-tool ``ok: true`` envelopes."""
    if type(payload) is not dict:
        return False
    if payload.get("ok") is True:
        return True
    return payload.get("status") == "ok"


def _data(payload: dict) -> dict:
    data = payload.get("data")
    return data if type(data) is dict else {}


def _identity_value(payload: dict, key: str) -> str | None:
    data = _data(payload)
    value = data.get(key) if type(data.get(key)) is str else payload.get(key)
    if type(value) is not str:
        return None
    stripped = value.strip()
    return stripped or None


def _identity_matches(payload: dict, key: str, requested: str, *, fold: bool = False) -> bool:
    got = _identity_value(payload, key)
    if got is None or type(requested) is not str:
        return False
    want = requested.strip()
    if not want:
        return False
    if fold:
        return got.casefold() == want.casefold()
    return got == want


def _identity_echo(payload: dict, key: str, requested: str) -> str:
    """``match`` / ``mismatch`` / ``absent`` for one echoed identity field.

    ``alpha_bench``'s upstream envelope omits ``zoo``, ``universe``, and ``period``.
    A missing field is request-bound, not a stale-cache mismatch. A field that is
    present and disagrees is still a mismatch.
    """
    got = _identity_value(payload, key)
    if got is None:
        return "absent"
    if type(requested) is not str or not requested.strip() or got != requested.strip():
        return "mismatch"
    return "match"


def _with_source(raw: dict, payload: dict, row: dict | None = None) -> dict:
    """Keep the exact sidecar object beside the normalized projection (Invariant 8)."""
    image = {**raw, "source_payload": payload}
    if row is not None:
        image["source_row"] = row
    return image


def _aware_dt(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _factor_rows(payload: dict) -> list[dict]:
    for key in ("rows", "top", "top5_by_ir"):
        rows = payload.get(key)
        if type(rows) is list:
            return [row for row in rows if type(row) is dict]
    return []


def _counts(accepted: list[dict], payload: dict) -> tuple[int, int, int, int, str]:
    """Tally the returned top slice only. Envelope alive/reversed/dead are untrusted."""
    tallies = {"alive": 0, "reversed": 0, "dead": 0}
    for row in accepted:
        category = row.get("category")
        if category in tallies:
            tallies[category] += 1
    tested = payload.get("n_alphas_tested")
    tested_n = tested if type(tested) is int and tested >= 0 else len(accepted)
    return tallies["alive"], tallies["reversed"], tallies["dead"], tested_n, "returned_top"


def project_alpha_bench(payload: dict, *, zoo: str, universe: str, period: str,
                        symbols: tuple[str, ...] = ()) -> list[FeatureLine] | None:
    """One summary line plus one line per accepted factor. Bad rows are skipped.

    Factor scores are universe-wide, not stock-specific. ``symbols`` is ignored so
    triage cannot treat a bench as evidence for the operator watchlist.
    ``None`` means the envelope is malformed or an echoed identity disagrees with
    the request. ``[]`` is a valid empty result. Upstream ``run_alpha_bench`` does
    not echo zoo/universe/period; those labels stay request-bound unless a field
    or a per-row ``zoo`` is present and matches.
    """
    del symbols
    if not _ok(payload):
        return None
    echoes = {
        "zoo": _identity_echo(payload, "zoo", zoo),
        "universe": _identity_echo(payload, "universe", universe),
        "period": _identity_echo(payload, "period", period),
    }
    if "mismatch" in echoes.values():
        return None
    raw_rows = _factor_rows(payload)
    accepted: list[dict] = []
    zoo_conflict = False
    row_zoo_confirmed = False
    for row in raw_rows:
        row_zoo = row.get("zoo")
        if type(row_zoo) is str and row_zoo.strip():
            if row_zoo.strip() != zoo:
                zoo_conflict = True
                continue
            row_zoo_confirmed = True
        factor_id = row.get("id")
        ic_mean = _finite(row.get("ic_mean"))
        if type(factor_id) is not str or _FACTOR_ID.fullmatch(factor_id) is None or ic_mean is None:
            continue
        ir = _finite(row.get("ir"))
        projected = {
            "id": factor_id,
            "ic_mean": ic_mean,
            "ir": ir,
            "ic_std": row.get("ic_std"),
            "ic_positive_ratio": row.get("ic_positive_ratio"),
            "ic_count": row.get("ic_count") if type(row.get("ic_count")) is int else None,
            "category": row.get("category"),
        }
        labelled = categorise(projected)
        if labelled is None:
            continue
        category, source = labelled
        accepted.append({
            **projected, "category": category, "category_source": source, "_row": row,
        })
    if echoes["zoo"] == "absent" and row_zoo_confirmed:
        echoes["zoo"] = "row"
    if not accepted:
        return None if zoo_conflict and not row_zoo_confirmed else []
    alive, reversed_, dead, tested, counts_source = _counts(accepted, payload)
    bias = "true" if universe == "sp500" else "unverified"
    label = {"match": "echo", "absent": "request", "row": "row"}
    summary = (
        f"alpha_bench zoo={zoo} universe={universe} period={period} "
        f"alive={alive} reversed={reversed_} dead={dead} tested={tested} "
        f"counts_scope={counts_source} survivorship_bias={bias} "
        f"identity_zoo={label[echoes['zoo']]} "
        f"identity_universe={label[echoes['universe']]} "
        f"identity_period={label[echoes['period']]}"
    )
    lines = [FeatureLine(
        priority=(1, 0, ""),
        text=summary,
        event_type=EVENT_ALPHA,
        symbols=(),
        raw={"kind": "alpha_bench_summary", "payload": payload,
             "counts_scope": counts_source, "survivorship_bias": bias},
        tool="alpha_bench",
    )]
    rank = {"alive": 2, "reversed": 3, "dead": 4, "unverified": 5}
    for row in accepted:
        ir_text = "" if row["ir"] is None else f"{row['ir']:.4f}"
        text = (
            f"alpha_factor id={row['id']} zoo={zoo} universe={universe} period={period} "
            f"ic_mean={row['ic_mean']:.6f} ir={ir_text} "
            f"category={row['category']} category_source={row['category_source']}"
        )
        lines.append(FeatureLine(
            priority=(rank[row["category"]], -abs(row["ic_mean"]), row["id"]),
            text=text,
            event_type=EVENT_ALPHA,
            symbols=(),
            raw=_with_source({"kind": "alpha_factor", "factor": {
                "id": row["id"], "ic_mean": row["ic_mean"], "ir": row["ir"],
                "category": row["category"], "category_source": row["category_source"],
            }}, payload, row["_row"]),
            tool="alpha_bench",
        ))
    return lines


def project_holdings(payload: dict, *, ticker: str) -> list[FeatureLine] | None:
    if not _ok(payload):
        return None
    if not _identity_matches(payload, "ticker", ticker, fold=True):
        return None
    data = _data(payload)
    quarter = data.get("quarter") if type(data.get("quarter")) is str else payload.get("quarter")
    if type(quarter) is not str or _QUARTER.fullmatch(quarter) is None:
        quarter = ""
    holders = data.get("holders")
    if type(holders) is not list:
        holders = payload.get("holders") if type(payload.get("holders")) is list else []
    lines: list[FeatureLine] = []
    for index, row in enumerate(holders[:20]):
        if type(row) is not dict:
            continue
        manager = row.get("manager")
        shares = _finite(row.get("shares"))
        value = _finite(row.get("value_usd"))
        if type(manager) is not str or not manager.strip() or len(manager) > 80:
            continue
        if shares is None and value is None:
            continue
        manager = " ".join(manager.split())
        held = row.get("held")
        held_text = "" if type(held) is not bool else f" held={str(held).lower()}"
        share_text = "" if shares is None else f" shares={shares:.4g}"
        value_text = "" if value is None else f" value_usd={value:.4g}"
        text = (
            f"13f ticker={ticker} quarter={quarter} manager={manager}"
            f"{share_text}{value_text}{held_text}"
        )
        lines.append(FeatureLine(
            priority=(6, index, ticker),
            text=text,
            event_type=EVENT_13F,
            symbols=(ticker,),
            raw=_with_source({"kind": "13f_holder", "ticker": ticker, "holder": {
                "manager": manager, "quarter": quarter, "shares": shares,
                "value_usd": value, "held": held if type(held) is bool else None,
            }}, payload, row),
            tool="get_institutional_holdings",
        ))
    return lines


def _holding_weight(row: dict) -> float | None:
    weight = _finite(row.get("weight"))
    if weight is not None and 0 <= weight <= 1:
        return weight
    pct = _finite(row.get("pct_of_net_assets"))
    if pct is None or pct < 0:
        return None
    # N-PORT pctVal is always a percent (0.5329 == 0.5329%), never a 0-1 weight.
    fraction = pct / 100.0
    if not 0 <= fraction <= 1:
        return None
    return fraction


def _holding_ticker(row: dict) -> str | None:
    for key in ("ticker", "symbol"):
        value = row.get(key)
        if type(value) is str and value.strip():
            return value.strip().upper()
    return None


def project_etf(payload: dict, *, etf: str, universe: frozenset[str]) -> list[FeatureLine] | None:
    if not _ok(payload):
        return None
    if not _identity_matches(payload, "symbol", etf, fold=True):
        return None
    data = _data(payload)
    rows = data.get("holdings")
    if type(rows) is not list:
        rows = payload.get("constituents") if type(payload.get("constituents")) is list else []
    as_of = payload.get("as_of")
    if type(as_of) is dict:
        as_of = as_of.get("index_year") or as_of.get("as_of")
    if type(as_of) is not str:
        as_of = data.get("as_of")
    if type(as_of) is not str or _AS_OF.fullmatch(as_of) is None:
        return None
    kept: list[str] = []
    symbols: list[str] = []
    outside = 0
    for row in rows[:64]:
        if type(row) is not dict:
            continue
        ticker = _holding_ticker(row)
        weight = _holding_weight(row)
        if ticker is None or weight is None:
            continue
        if ticker not in universe:
            outside += 1
            continue
        kept.append(f"{ticker}:{weight:.4f}")
        symbols.append(ticker)
    if not kept and outside == 0:
        return []
    body = ",".join(kept) if kept else "none"
    coverage = payload.get("coverage")
    coverage_text = f" coverage={coverage}" if type(coverage) is str else ""
    text = (
        f"etf={etf} as_of={as_of} constituents={body} "
        f"outside_universe={outside}{coverage_text}"
    )
    # persist_events indexes symbols[0]. Watchlist constituents lead so intraday
    # triage sees the event. The ETF itself gains no execution eligibility.
    ordered = tuple(dict.fromkeys([*symbols, etf]))
    return [FeatureLine(
        priority=(7, 0, etf),
        text=text,
        event_type=EVENT_ETF,
        symbols=ordered,
        raw=_with_source({"kind": "etf_constituents", "etf": etf, "as_of": as_of,
                          "constituents": kept, "outside_universe": outside}, payload),
        tool="etf_holdings",
    )]


def _event_probability(row: dict) -> float | None:
    probability = _finite(row.get("implied_probability"))
    if probability is not None and 0 <= probability <= 1:
        return probability
    markets = row.get("markets")
    if type(markets) is not list:
        return None
    found = None
    for market in markets:
        if type(market) is not dict:
            continue
        nested = _finite(market.get("implied_probability"))
        if nested is None or not 0 <= nested <= 1:
            continue
        if found is not None:
            return None
        found = nested
    return found


def project_contracts(payload: dict, *, query: str) -> list[FeatureLine] | None:
    if not _ok(payload):
        return None
    if not _identity_matches(payload, "query", query):
        return None
    data = _data(payload)
    rows = data.get("events")
    if type(rows) is not list:
        rows = payload.get("contracts") if type(payload.get("contracts")) is list else []
    lines: list[FeatureLine] = []
    for index, row in enumerate(rows[:8]):
        if type(row) is not dict:
            continue
        contract_id = row.get("event_id") or row.get("contract_id") or row.get("slug")
        question = row.get("title") or row.get("question")
        probability = _event_probability(row)
        as_of = row.get("as_of") or row.get("end_date") or row.get("start_date") or ""
        if (type(contract_id) is not str or _FACTOR_ID.fullmatch(contract_id) is None
                or type(question) is not str or not question.strip() or len(question) > 200):
            continue
        if type(as_of) is not str or (as_of and _AS_OF.fullmatch(as_of) is None):
            as_of = ""
        question = " ".join(question.split())
        prob_text = "" if probability is None else f" implied_probability={probability:.4f}"
        status = row.get("status")
        status_text = f" status={status}" if type(status) is str else ""
        text = (
            f"event_contract id={contract_id} as_of={as_of}{prob_text}"
            f"{status_text} question={question}"
        )
        lines.append(FeatureLine(
            priority=(8, index, contract_id),
            text=text,
            event_type=EVENT_PROBABILITY,
            symbols=(),
            raw=_with_source({"kind": "event_probability", "contract_id": contract_id,
                              "implied_probability": probability, "as_of": as_of}, payload, row),
            tool="prediction_market",
        ))
    return lines


def _trace_status_ok(status) -> bool:
    if status is None:
        return True
    return type(status) is str and status in {"ok", "completed", "success"}


def _metrics_hash(payload: dict, metrics: dict) -> str | None:
    hashes = payload.get("hashes") if type(payload.get("hashes")) is dict else {}
    direct = payload.get("metrics_sha256") or hashes.get("metrics") or hashes.get("metrics_sha256")
    if type(direct) is str:
        return direct
    artifacts = payload.get("artifacts") if type(payload.get("artifacts")) is list else []
    for artifact in artifacts:
        if type(artifact) is dict and artifact.get("path") == "artifacts/metrics.csv":
            digest = artifact.get("sha256")
            if type(digest) is str:
                return digest
    if not metrics:
        return None
    blob = json.dumps(metrics, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _run_card_window(payload: dict) -> tuple[str | None, str | None]:
    started = payload.get("started_at")
    ended = payload.get("ended_at")
    traces = payload.get("tool_traces") if type(payload.get("tool_traces")) is list else []
    for trace in traces:
        if type(trace) is not dict or trace.get("tool") != "backtest":
            continue
        if not _trace_status_ok(trace.get("status")):
            return None, None
        started = started or trace.get("started_at")
        ended = ended or trace.get("ended_at")
    generated = payload.get("generated_at")
    if type(started) is not str:
        started = generated if type(generated) is str else None
    if type(ended) is not str:
        ended = generated if type(generated) is str else started
    return started if type(started) is str else None, ended if type(ended) is str else None


def normalize_run_card(payload: dict, *, config_point_in_time: bool) -> dict | None:
    """Fold the published run-card schema into one object our feature line accepts.

    Upstream ``run_card.json`` (schema 1.0) has no ``point_in_time`` field. PIT is
    proven only by the staged config we already checked. A card that explicitly
    says ``point_in_time: false`` is still rejected.
    """
    if type(payload) is not dict:
        return None
    if payload.get("point_in_time") is False:
        return None
    if payload.get("point_in_time") is True:
        point_in_time = True
    elif config_point_in_time:
        point_in_time = True
    else:
        return None
    if str(payload.get("schema_version", "")) != "1.0":
        return None
    if payload.get("status") is not None and not _trace_status_ok(payload.get("status")):
        return None
    traces = payload.get("tool_traces") if type(payload.get("tool_traces")) is list else []
    if any(type(trace) is dict and trace.get("tool") == "backtest"
           and not _trace_status_ok(trace.get("status"))
           for trace in traces):
        return None
    reproducibility = (
        payload.get("reproducibility") if type(payload.get("reproducibility")) is dict else {}
    )
    hashes = payload.get("hashes") if type(payload.get("hashes")) is dict else {}
    config_hash = (
        payload.get("config_sha256")
        or reproducibility.get("config_hash")
        or hashes.get("config")
        or hashes.get("config_sha256")
    )
    metrics = payload.get("metrics") if type(payload.get("metrics")) is dict else {}
    metrics_hash = _metrics_hash(payload, metrics)
    started, ended = _run_card_window(payload)
    run_id = payload.get("run_id")
    if type(run_id) is not str:
        run_dir = payload.get("run_dir")
        run_id = Path(str(run_dir)).name if type(run_dir) is str else None
    card = {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": "ok",
        "point_in_time": point_in_time,
        "pit_source": "staged_config",
        "started_at": started,
        "ended_at": ended,
        "config_sha256": config_hash,
        "metrics_sha256": metrics_hash,
        "metrics": {
            "sharpe": _finite(metrics.get("sharpe")),
            "annual_return": _finite(metrics.get("annual_return")),
            "max_drawdown": _finite(metrics.get("max_drawdown")),
        },
    }
    if type(card["run_id"]) is not str or _FACTOR_ID.fullmatch(card["run_id"]) is None:
        return None
    if type(card["started_at"]) is not str or type(card["ended_at"]) is not str:
        return None
    if _AS_OF.fullmatch(card["started_at"]) is None or _AS_OF.fullmatch(card["ended_at"]) is None:
        return None
    start = _aware_dt(card["started_at"])
    end = _aware_dt(card["ended_at"])
    if start is None or end is None or end < start:
        return None
    if (type(card["config_sha256"]) is not str or _HEX64.fullmatch(card["config_sha256"]) is None
            or type(card["metrics_sha256"]) is not str
            or _HEX64.fullmatch(card["metrics_sha256"]) is None):
        return None
    if any(value is None for value in card["metrics"].values()):
        return None
    return card


def project_run_card(card: dict, *, strategy_id: str, symbols: tuple[str, ...],
                     sidecar_card: dict | None = None) -> FeatureLine:
    metrics = card["metrics"]
    # admits_order is ours. A sidecar field of the same name is not read.
    text = (
        f"run_card strategy={strategy_id} run_id={card['run_id']} pit=true "
        f"pit_source={card.get('pit_source', 'staged_config')} status=ok "
        f"sharpe={metrics['sharpe']:.4f} annual_return={metrics['annual_return']:.4f} "
        f"max_drawdown={metrics['max_drawdown']:.4f} admits_order=false"
    )
    return FeatureLine(
        priority=(0, 0, strategy_id),
        text=text,
        event_type=EVENT_RUN_CARD,
        symbols=symbols,
        raw={"kind": "run_card", "strategy_id": strategy_id, "card": card,
             "sidecar_card": sidecar_card if type(sidecar_card) is dict else {}},
        tool="backtest",
    )


def scan_signal_engine(path: Path) -> str | None:
    """Staging filter for a scanned Python file.

    This is not the order-path boundary. The adapter refuses to execute staged
    strategy code under the cockpit OS identity; trading tools are refused before
    spawn. An AST walk cannot prove the absence of an order path.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "signal engine is not readable"
    if len(text.encode("utf-8")) > 256 * 1024:
        return "signal engine is too large"
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return "signal engine is not valid Python"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _FORBIDDEN_IMPORT_ROOTS or root.startswith("trading"):
                    return "signal engine imports a forbidden module"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".")[0]
            if root in _FORBIDDEN_IMPORT_ROOTS or root.startswith("trading"):
                return "signal engine imports a forbidden module"
            for alias in node.names:
                if alias.name in _FORBIDDEN_ORDER_NAMES:
                    return "signal engine imports an order name"
        elif isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in _FORBIDDEN_ORDER_NAMES:
                return "signal engine calls an order function"
    return None


def scan_run_tree(run_dir: Path) -> str | None:
    """Scan every staged ``.py`` file, not only ``code/signal_engine.py``."""
    files = []
    engine = run_dir / "code" / "signal_engine.py"
    try:
        for path in run_dir.rglob("*.py"):
            files.append(path)
            if len(files) > _MAX_SCANNED_ENGINE_FILES:
                return "run directory has too many python files"
    except OSError:
        return "run directory is not readable"
    if engine.is_file() and engine not in files:
        files.insert(0, engine)
    if not files:
        return "signal engine is not readable"
    for path in files:
        reason = scan_signal_engine(path)
        if reason is not None:
            return reason
    return None


def load_research_config(path: Path) -> dict | None:
    """A staged paper config. Broker keys or a non-research source refuse the run."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if len(text.encode("utf-8")) > 64 * 1024:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if type(parsed) is not dict or parsed.get("point_in_time") is not True:
        return None
    if parsed.get("source") not in _RESEARCH_SOURCES:
        return None
    if any(key in parsed for key in _CONFIG_ORDER_KEYS):
        return None
    return parsed


def config_symbols(config: dict, universe: frozenset[str]) -> tuple[str, ...]:
    raw = config.get("symbols")
    if type(raw) is not list:
        return tuple(sorted(universe))
    kept = [item.strip().upper() for item in raw if type(item) is str and item.strip().upper() in universe]
    return tuple(dict.fromkeys(kept))


def read_run_card(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if len(text.encode("utf-8")) > 256 * 1024:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if type(parsed) is dict else None


def _wrapped(line: FeatureLine) -> str:
    # to_market_event escapes the headline; build_packet escapes it again.
    inner = escape_untrusted(escape_untrusted(line.text))[:_PACKET_ITEM_CHARS]
    return wrap_untrusted(inner, source=SOURCE, article_ref=_ESTIMATE_REF, ts=_ESTIMATE_TS)


def trim_lines(lines: list[FeatureLine], *, budget_tokens: int) -> tuple[list[FeatureLine], int]:
    """Drop the lowest-priority tail until the wrapped block fits. Never overflow."""
    if budget_tokens < 1:
        return [], len(lines)
    ordered = sorted(lines, key=lambda line: line.priority)
    while ordered:
        block = "\n\n".join(_wrapped(line) for line in ordered)
        if estimate_tokens(block) <= budget_tokens:
            return ordered, len(lines) - len(ordered)
        ordered.pop()
    return [], len(lines)


def to_events(lines: list[FeatureLine], *, ts, dedup: Dedup | None = None) -> list[dict]:
    """Normalized ingest dicts. Enrichment types only — never halt or filing."""
    seen = dedup if dedup is not None else Dedup()
    events: list[dict] = []
    day = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
    for line in lines:
        if line.event_type in _HARD_EVENT_TYPES or line.event_type.startswith("calendar:"):
            raise RuntimeError(f"research feature used a hard event type: {line.event_type}")
        # Each daily poll is a fresh observation. The decision window selects recent
        # arrivals, so identity and the raw image both carry the observation day.
        digest = hashlib.sha256(
            f"{line.event_type}\n{line.text}\n{day}".encode("utf-8"),
        ).hexdigest()[:20]
        article_id = f"{line.event_type}:{digest}"
        if not seen.add(f"{SOURCE}:{article_id}"):
            continue
        events.append(to_market_event(
            source=SOURCE,
            article_id=article_id,
            symbols=list(line.symbols),
            headline=line.text,
            summary="",
            ts=ts,
            event_type=line.event_type,
            raw={**line.raw, "observed_on": day},
        ))
    return events
