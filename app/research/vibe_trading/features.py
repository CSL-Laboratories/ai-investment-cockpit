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
from datetime import datetime
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
_FORBIDDEN_IMPORT_ROOTS = frozenset({"brokers", "risk", "importlib"})
_FORBIDDEN_ORDER_NAMES = frozenset({
    "place_order",
    "submit_order",
    "place_equity_order",
    "place_crypto_order",
    "OrderGateway",
    "exec",
    "eval",
    "import_module",
    "__import__",
})
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


def categorise(row: dict) -> tuple[str, str]:
    """Return ``(category, source)`` using the published bench_runner thresholds.

    alive: ic_mean > 0.02 and ic_positive_ratio >= 0.55 and |t| > 2
    reversed: ic_mean < -0.02 and |t| > 2
    dead: everything else we can recompute

    A sidecar label is kept only when the series stats needed to recompute are
    absent and the label does not contradict the sign of IC. Otherwise the
    category is ``unverified``.
    """
    ic_mean = row["ic_mean"]
    ic_std = row.get("ic_std")
    pos = row.get("ic_positive_ratio")
    count = row.get("ic_count")
    stats_present = (
        type(ic_std) is not bool and type(pos) is not bool and type(count) is int
        and isinstance(ic_std, (int, float)) and isinstance(pos, (int, float))
        and math.isfinite(float(ic_std)) and math.isfinite(float(pos)) and count > 0
    )
    if stats_present:
        std = float(ic_std)
        t_stat = 0.0 if std <= 0 else ic_mean / (std / math.sqrt(count))
        if ic_mean > 0.02 and float(pos) >= 0.55 and abs(t_stat) > 2:
            return "alive", "recomputed"
        if ic_mean < -0.02 and abs(t_stat) > 2:
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
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _factor_rows(payload: dict) -> list[dict]:
    for key in ("rows", "top", "top5_by_ir"):
        rows = payload.get(key)
        if type(rows) is list:
            return [row for row in rows if type(row) is dict]
    return []


def _counts(payload: dict, rows: list[dict]) -> tuple[int, int, int, int | None, str]:
    keys = ("alive", "reversed", "dead")
    if all(type(payload.get(key)) is int and payload[key] >= 0 for key in keys):
        tested = payload.get("n_alphas_tested")
        tested_n = tested if type(tested) is int and tested >= 0 else None
        return payload["alive"], payload["reversed"], payload["dead"], tested_n, "envelope"
    tallies = {"alive": 0, "reversed": 0, "dead": 0}
    for row in rows:
        category, _source = categorise(row) if "ic_mean" in row else ("unverified", "unverified")
        if category in tallies:
            tallies[category] += 1
    tested = payload.get("n_alphas_tested")
    tested_n = tested if type(tested) is int and tested >= 0 else len(rows)
    return tallies["alive"], tallies["reversed"], tallies["dead"], tested_n, "rows"


def project_alpha_bench(payload: dict, *, zoo: str, universe: str, period: str,
                        symbols: tuple[str, ...]) -> list[FeatureLine]:
    """One summary line plus one line per accepted factor. Bad rows are skipped."""
    if type(payload) is not dict or payload.get("status") != "ok":
        return []
    raw_rows = _factor_rows(payload)
    accepted: list[dict] = []
    for row in raw_rows:
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
        category, source = categorise(projected)
        accepted.append({**projected, "category": category, "category_source": source})
    if not accepted and not all(
        type(payload.get(key)) is int for key in ("alive", "reversed", "dead")
    ):
        return []
    alive, reversed_, dead, tested, counts_source = _counts(payload, accepted)
    summary = (
        f"alpha_bench zoo={zoo} universe={universe} period={period} "
        f"alive={alive} reversed={reversed_} dead={dead} tested={tested if tested is not None else ''} "
        f"counts_source={counts_source}"
    )
    lines = [FeatureLine(
        priority=(1, 0, ""),
        text=summary,
        event_type=EVENT_ALPHA,
        symbols=symbols,
        raw={"kind": "alpha_bench_summary", "payload": payload},
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
            symbols=symbols,
            raw={"kind": "alpha_factor", "factor": {
                "id": row["id"], "ic_mean": row["ic_mean"], "ir": row["ir"],
                "category": row["category"], "category_source": row["category_source"],
            }},
            tool="alpha_bench",
        ))
    return lines


def project_holdings(payload: dict, *, ticker: str) -> list[FeatureLine]:
    if type(payload) is not dict or payload.get("status") != "ok":
        return []
    diffs = payload.get("diffs")
    if type(diffs) is not list:
        return []
    lines: list[FeatureLine] = []
    for index, row in enumerate(diffs[:20]):
        if type(row) is not dict:
            continue
        manager = row.get("manager")
        quarter = row.get("quarter")
        delta = _finite(row.get("delta_shares"))
        if (type(manager) is not str or not manager.strip() or len(manager) > 80
                or type(quarter) is not str or _QUARTER.fullmatch(quarter) is None
                or delta is None):
            continue
        manager = " ".join(manager.split())
        text = f"13f ticker={ticker} quarter={quarter} manager={manager} delta_shares={delta:.4g}"
        lines.append(FeatureLine(
            priority=(6, index, ticker),
            text=text,
            event_type=EVENT_13F,
            symbols=(ticker,),
            raw={"kind": "13f_diff", "ticker": ticker, "diff": {
                "manager": manager, "quarter": quarter, "delta_shares": delta,
            }},
            tool="get_institutional_holdings",
        ))
    return lines


def project_etf(payload: dict, *, etf: str, universe: frozenset[str]) -> list[FeatureLine]:
    if type(payload) is not dict or payload.get("status") != "ok":
        return []
    rows = payload.get("constituents")
    as_of = payload.get("as_of")
    if type(rows) is not list or type(as_of) is not str or _AS_OF.fullmatch(as_of) is None:
        return []
    kept: list[str] = []
    symbols: list[str] = []
    outside = 0
    for row in rows[:64]:
        if type(row) is not dict:
            continue
        symbol = row.get("symbol")
        weight = _finite(row.get("weight"))
        if type(symbol) is not str or weight is None or not 0 <= weight <= 1:
            continue
        ticker = symbol.strip().upper()
        if ticker not in universe:
            outside += 1
            continue
        kept.append(f"{ticker}:{weight:.4f}")
        symbols.append(ticker)
    if not kept and outside == 0:
        return []
    body = ",".join(kept) if kept else "none"
    text = f"etf={etf} as_of={as_of} constituents={body} outside_universe={outside}"
    ordered = tuple(dict.fromkeys([etf, *symbols]))
    return [FeatureLine(
        priority=(7, 0, etf),
        text=text,
        event_type=EVENT_ETF,
        symbols=ordered,
        raw={"kind": "etf_constituents", "etf": etf, "as_of": as_of,
             "constituents": kept, "outside_universe": outside},
        tool="etf_holdings",
    )]


def project_contracts(payload: dict) -> list[FeatureLine]:
    if type(payload) is not dict or payload.get("status") != "ok":
        return []
    rows = payload.get("contracts")
    if type(rows) is not list:
        return []
    lines: list[FeatureLine] = []
    for index, row in enumerate(rows[:8]):
        if type(row) is not dict:
            continue
        contract_id = row.get("contract_id")
        question = row.get("question")
        probability = _finite(row.get("implied_probability"))
        as_of = row.get("as_of")
        if (type(contract_id) is not str or _FACTOR_ID.fullmatch(contract_id) is None
                or type(question) is not str or not question.strip() or len(question) > 200
                or probability is None or not 0 <= probability <= 1
                or type(as_of) is not str or _AS_OF.fullmatch(as_of) is None):
            continue
        question = " ".join(question.split())
        text = (
            f"event_contract id={contract_id} as_of={as_of} "
            f"implied_probability={probability:.4f} question={question}"
        )
        lines.append(FeatureLine(
            priority=(8, index, contract_id),
            text=text,
            event_type=EVENT_PROBABILITY,
            symbols=(),
            raw={"kind": "event_probability", "contract_id": contract_id,
                 "implied_probability": probability, "as_of": as_of},
            tool="prediction_market",
        ))
    return lines


def normalize_run_card(payload: dict, *, config_point_in_time: bool) -> dict | None:
    """Fold the published run-card shapes into one object our schema accepts.

    ``point_in_time`` is taken from the card when the card states it. A card
    that says false is rejected. A card that omits the flag inherits it only
    from the staged config we already checked, never from a default of true
    invented here without that check.
    """
    if type(payload) is not dict:
        return None
    if payload.get("point_in_time") is False:
        return None
    point_in_time = payload.get("point_in_time")
    if point_in_time is None:
        point_in_time = True if config_point_in_time else None
    if point_in_time is not True:
        return None
    trace = payload.get("trace") if type(payload.get("trace")) is dict else {}
    started = payload.get("started_at") or trace.get("started_at") or trace.get("start")
    ended = payload.get("ended_at") or trace.get("ended_at") or trace.get("end")
    status = payload.get("status") or trace.get("status")
    if status in {"completed", "success"}:
        status = "ok"
    hashes = payload.get("hashes") if type(payload.get("hashes")) is dict else {}
    config_hash = payload.get("config_sha256") or hashes.get("config") or hashes.get("config_sha256")
    metrics_hash = payload.get("metrics_sha256") or hashes.get("metrics") or hashes.get("metrics_sha256")
    metrics = payload.get("metrics") if type(payload.get("metrics")) is dict else payload
    card = {
        "schema_version": str(payload.get("schema_version", "")),
        "run_id": payload.get("run_id"),
        "status": status,
        "point_in_time": True,
        "started_at": started,
        "ended_at": ended,
        "config_sha256": config_hash,
        "metrics_sha256": metrics_hash,
        "metrics": {
            "sharpe": _finite(metrics.get("sharpe") if type(metrics) is dict else None),
            "annual_return": _finite(metrics.get("annual_return") if type(metrics) is dict else None),
            "max_drawdown": _finite(metrics.get("max_drawdown") if type(metrics) is dict else None),
        },
    }
    if card["schema_version"] != "1.0":
        return None
    if type(card["run_id"]) is not str or _FACTOR_ID.fullmatch(card["run_id"]) is None:
        return None
    if card["status"] != "ok":
        return None
    if type(card["started_at"]) is not str or type(card["ended_at"]) is not str:
        return None
    if _AS_OF.fullmatch(card["started_at"]) is None or _AS_OF.fullmatch(card["ended_at"]) is None:
        return None
    try:
        start = datetime.fromisoformat(card["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(card["ended_at"].replace("Z", "+00:00"))
    except ValueError:
        return None
    if end < start:
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
        f"run_card strategy={strategy_id} run_id={card['run_id']} pit=true status=ok "
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
    """Return a refusal reason when the staged engine can see an order path."""
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
    inner = escape_untrusted(line.text)[:_PACKET_ITEM_CHARS]
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
    for line in lines:
        if line.event_type in _HARD_EVENT_TYPES or line.event_type.startswith("calendar:"):
            raise RuntimeError(f"research feature used a hard event type: {line.event_type}")
        digest = hashlib.sha256(f"{line.event_type}\n{line.text}".encode("utf-8")).hexdigest()[:20]
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
            raw=line.raw,
        ))
    return events
