"""Equity and drawdown charts as SVG. No plotting dependency."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path


COLORS = {
    "cockpit": "#1f4b99",
    "sizer": "#0b6e4f",
    "combined": "#8a4b08",
    "tca": "#6b2d5b",
    "SPY": "#6e6e6e",
}


def _series(values: list[float], width: float, height: float, lo: float, hi: float, left: float, top: float) -> str:
    span = hi - lo
    if span <= 0:
        span = 1.0
    last = max(len(values) - 1, 1)
    points = []
    for index, value in enumerate(values):
        x = left + width * index / last
        y = top + height * (1.0 - (value - lo) / span)
        points.append(f"{x:.2f},{y:.2f}")
    return " ".join(points)


def _frame(title: str, lines: list[tuple[str, str, list[float]]], y_label: str) -> str:
    width, height = 920, 420
    left, top, plot_w, plot_h = 64, 36, 760, 320
    flat = [value for _name, _color, series in lines for value in series]
    lo, hi = min(flat), max(flat)
    pad = (hi - lo) * 0.08 or 1.0
    lo -= pad
    hi += pad
    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fbfbfb"/>',
        f'<text x="{left}" y="22" font-family="sans-serif" font-size="16">{title}</text>',
        f'<text x="16" y="{top + 12}" font-family="sans-serif" font-size="11" fill="#444">{y_label}</text>',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fff" stroke="#ddd"/>',
    ]
    for name, color, series in lines:
        body.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="1.4" points="{_series(series, plot_w, plot_h, lo, hi, left, top)}"/>'
        )
    legend_x = left
    for name, color, _series_values in lines:
        body.append(f'<rect x="{legend_x}" y="390" width="12" height="12" fill="{color}"/>')
        body.append(
            f'<text x="{legend_x + 16}" y="401" font-family="sans-serif" font-size="12">{name}</text>'
        )
        legend_x += 140
    body.append("</svg>")
    return "\n".join(body)


def write_charts(curves: dict[str, list[Decimal]], directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    names = [name for name in ("cockpit", "sizer", "combined", "tca", "SPY") if name in curves]
    levels = {name: [float(value) for _day, value in curves[name]] for name in names}
    drawdowns = {}
    for name, series in levels.items():
        peak = series[0]
        path = []
        for value in series:
            peak = max(peak, value)
            path.append(0.0 if peak <= 0 else (peak - value) / peak)
        drawdowns[name] = path
    equity = _frame(
        "Simulated equity",
        [(name, COLORS[name], levels[name]) for name in names],
        "dollars",
    )
    underwater = _frame(
        "Drawdown",
        [(name, COLORS[name], drawdowns[name]) for name in names],
        "fraction",
    )
    equity_path = directory / "equity.svg"
    drawdown_path = directory / "drawdown.svg"
    equity_path.write_text(equity, encoding="utf-8")
    drawdown_path.write_text(underwater, encoding="utf-8")
    return equity_path, drawdown_path
