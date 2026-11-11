"""The Cockpit paper panel, served read-only by the app (item 5; #318, CSL-736).

Lucas, Nov 11, 12:52am PST: "Lift the #318 hold and have new app/ code wire in #318 and #319".
His Nov 10 8:49pm PST unfreeze covers app/ "only for the paper panel and owner-only key
integration"; this module is the paper-panel half and touches no other app/ code.

Paper only: PRODUCT.md 5.2 / CP0-v2 (pin d84cdd26a4de2366f3c5d34c4aec5215f822a494, CSLR-D-012).
The panel data comes from ``engine/cockpit/gui/panels.py`` (#318) and is checked here before
anything is served:

* exactly one panel is exported, ``cockpit.paper_own_trading``; its venue is the paper book's
  O7 id, its use is ``paper:own_trading``, it is outside every venue switch and its title starts
  with the R1 string ``PAPER (SIMULATED) — NOT REAL MONEY`` (PRODUCT.md 5.4);
* every read it names is an ``operator.*.inspect`` read; anything else (an order, cancel,
  intervention or Stop verb) refuses the whole panel;
* any other shape refuses the whole panel (``PanelRefused``); nothing is served half-checked.

The router has GET routes only. It has no order, cancel, venue or switch parameter, so the
panel never offers a path to an order, and never to a venue other than the paper book. Each
read result is passed through Cockpit's ``redact_panel`` with every venue switch off, so rows
labelled with another venue (Kalshi, Coinbase, Gemini) are dropped and O7 status, refusal,
stale and error states stay visible (PRODUCT.md 3.5). The caller injects ``read`` (the O7
read client) and the app's auth dependency (required); mounting the router is the caller's step.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any

from core.engine_path import import_engine

__all__ = ["PAPER_PANEL_ID", "PanelRefused", "build_paper_panel_router", "mount_paper_panel",
           "paper_panel", "render_paper_panel"]

PAPER_PANEL_ID = "cockpit.paper_own_trading"
PAPER_VENUE_USE = "paper:own_trading"
PANEL_KEYS = frozenset({"id", "title", "venue", "venue_use", "reads", "withhold_venue_data"})

ReadFn = Callable[[str], object]


class PanelRefused(RuntimeError):
    """The engine's panel data is not the one read-only paper panel; nothing is served."""

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


def _panels() -> Any:
    return import_engine("cockpit.gui.panels")


def _is_inspect_read(name: object) -> bool:
    if type(name) is not str:
        return False
    parts = name.split(".")
    return (len(parts) == 3 and parts[0] == "operator" and parts[2] == "inspect"
            and parts[1].isidentifier())


def paper_panel() -> Mapping[str, Any]:
    """The one exported Cockpit panel, checked paper-only and read-only, or ``PanelRefused``."""
    gp = _panels()
    exported = gp.registry().get("panels")
    if type(exported) is not list or len(exported) != 1 or type(exported[0]) is not dict:
        raise PanelRefused("panel_set_not_one_paper_panel")
    panel = exported[0]
    if set(panel) != PANEL_KEYS:
        raise PanelRefused("panel_keys_changed")
    venue = panel["venue"]
    if type(panel["id"]) is not str or panel["id"] != PAPER_PANEL_ID:
        raise PanelRefused("panel_id_not_paper")
    if type(venue) is not str or venue not in gp.OWN_TRADING_VENUES or venue not in gp.PAPER_BOOK:
        raise PanelRefused("panel_venue_not_paper_book")
    if type(panel["venue_use"]) is not str or panel["venue_use"] != PAPER_VENUE_USE:
        raise PanelRefused("panel_use_not_paper_own_trading")
    if panel["withhold_venue_data"] is not False or gp.panel_switch(panel) is not None:
        raise PanelRefused("panel_behind_a_switch")
    if type(panel["title"]) is not str or not panel["title"].startswith(gp.R1_PAPER):
        raise PanelRefused("panel_title_missing_r1")
    reads = panel["reads"]
    if type(reads) is not list or not reads or not all(_is_inspect_read(r) for r in reads):
        raise PanelRefused("panel_read_not_inspect")
    if len(set(reads)) != len(reads):
        raise PanelRefused("panel_read_repeated")
    return MappingProxyType({**panel, "reads": tuple(reads)})


def render_paper_panel(read: ReadFn) -> dict[str, Any]:
    """Run the checked panel's named reads through ``read`` and return Cockpit's redacted view.

    Each result is normalised to plain JSON first (tuples become lists; anything that is not
    JSON, such as a model object, is ``read_failed``), so the venue filter sees every row. A read
    that raises is shown as ``read_failed`` with the exception type, never hidden and never
    retried here. Venue switches are all off (``DEFAULT_SWITCH_STATES``); the paper panel is not
    behind one, and nothing in a read result can switch one on.
    """
    return _render(read, paper_panel())


def _failed(reason: str) -> dict[str, Any]:
    return {"status_code": "read_failed", "errors": [{"code": "read_failed", "message": reason}]}


def _render(read: ReadFn, panel: Mapping[str, Any]) -> dict[str, Any]:
    gp = _panels()
    results: dict[str, object] = {}
    for command in panel["reads"]:
        try:
            raw = read(command)
        except Exception as exc:  # shown, not hidden (PRODUCT.md 3.5)
            results[command] = _failed(type(exc).__name__)
            continue
        try:
            results[command] = json.loads(json.dumps(raw, allow_nan=False))
        except (TypeError, ValueError):
            results[command] = _failed("not_json")
    view = gp.redact_panel(panel, results, gp.DEFAULT_SWITCH_STATES)
    return {"id": panel["id"], "title": panel["title"], "venue": panel["venue"],
            "venue_use": panel["venue_use"], "paper_only": True, "reads": view}


def build_paper_panel_router(read: ReadFn, *, dependencies: Sequence[Any]) -> Any:
    """A FastAPI router with two GET routes: ``/panels`` and ``/panels/{panel_id}``.

    The panel is checked once here, so a bad engine panel refuses the mount rather than a
    request. Any other panel id is 404. ``dependencies`` is the app's auth (for example
    ``[Depends(ApiKeyAuth(...))]``); an empty one refuses the mount, so the operator reads
    are never served without auth.
    """
    from fastapi import APIRouter, HTTPException

    panel = paper_panel()
    if not callable(read):
        raise PanelRefused("read_not_callable")
    if isinstance(dependencies, (str, bytes)) or not list(dependencies):
        raise PanelRefused("no_auth_dependency")
    router = APIRouter(dependencies=list(dependencies))
    summary = {"id": panel["id"], "title": panel["title"], "venue": panel["venue"],
               "venue_use": panel["venue_use"], "paper_only": True}

    @router.get("/panels")
    def list_panels() -> dict:
        return {"panels": [dict(summary)]}

    @router.get("/panels/{panel_id}")
    def get_panel(panel_id: str) -> dict:
        if panel_id != PAPER_PANEL_ID:
            raise HTTPException(status_code=404, detail="unknown panel")
        return _render(read, panel)

    return router


def mount_paper_panel(app: Any, read: ReadFn, *, dependencies: Sequence[Any]) -> None:
    """Add the panel's two GET routes to ``app`` as plain routes, with the same checks as
    ``build_paper_panel_router``.

    ``app.include_router`` is not used because newer FastAPI keeps an included router as one
    entry without a ``path``, and existing app code and tests walk ``app.routes`` by ``path``.
    """
    router = build_paper_panel_router(read, dependencies=dependencies)
    for route in router.routes:
        app.add_api_route(route.path, route.endpoint, methods=["GET"], dependencies=list(dependencies))
