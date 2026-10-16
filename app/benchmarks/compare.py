"""Compare hot-path samples from two checkouts of this repo.

The pull-request job runs this against the default-branch tree and the PR tree
in one runner. Each side is measured several times, the run order is swapped
on the second pass, and the decision uses medians. A case is a regression only
when ``median slowdown - sample spread`` is still above the threshold. A wide
spread is inconclusive: runner noise, not a flag. Regressions print a GitHub
warning and a pull-request comment; the process still exits 0.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from benchmarks.cases import CASE_NAMES

COMMENT_MARKER = "<!-- csl-perf-bench -->"
MIN_SAMPLE_S = 0.005
HARNESS = Path(__file__).resolve().parent / "harness.py"


@dataclass(frozen=True)
class CaseResult:
    name: str
    status: str
    baseline_median_s: float | None
    candidate_median_s: float | None
    slowdown: float | None
    noise: float | None
    baseline_n: int
    candidate_n: int
    note: str
    baseline_samples: tuple[float, ...] = ()
    candidate_samples: tuple[float, ...] = ()


@dataclass(frozen=True)
class Report:
    threshold: float
    rounds: int
    orderings: int
    elapsed_s: float
    cases: tuple[CaseResult, ...]

    @property
    def regressions(self) -> tuple[str, ...]:
        return tuple(case.name for case in self.cases if case.status == "regression")

    @property
    def inconclusive(self) -> tuple[str, ...]:
        return tuple(case.name for case in self.cases if case.status == "inconclusive")


    @property
    def unverified(self) -> tuple[str, ...]:
        """Cases that were not measured to a verdict: inconclusive or missing."""
        return tuple(case.name for case in self.cases if case.status in ("inconclusive", "missing"))


def median(samples: list[float]) -> float:
    return statistics.median(samples)


def _exceeds(value: float, threshold: float) -> bool:
    return value - threshold > 1e-9


def rel_iqr(samples: list[float]) -> float:
    """(q75 - q25) / median. Quartile indexes are nearest-rank on the sorted list."""
    ordered = sorted(samples)
    n = len(ordered)
    if n == 0:
        return float("inf")
    mid = median(ordered)
    if mid <= 0:
        return float("inf")

    def at(fraction: float) -> float:
        return ordered[int(round(fraction * (n - 1)))]

    return (at(0.75) - at(0.25)) / mid


def _judge(name: str, base: list[float], cand: list[float], threshold: float) -> CaseResult:
    base_n, cand_n = len(base), len(cand)
    base_t = tuple(base)
    cand_t = tuple(cand)
    if base_n == 0 and cand_n == 0:
        return CaseResult(name, "missing", None, None, None, None, 0, 0, "no samples", base_t, cand_t)
    if base_n == 0:
        med_c = median(cand) if cand_n else None
        return CaseResult(name, "added", None, med_c, None, None, 0, cand_n, "no baseline samples", base_t, cand_t)
    if cand_n == 0:
        return CaseResult(
            name, "missing", median(base), None, None, None, base_n, 0,
            "case absent on the pull request", base_t, cand_t,
        )
    med_b = median(base)
    med_c = median(cand)
    if med_b <= 0:
        return CaseResult(
            name, "inconclusive", med_b, med_c, None, None, base_n, cand_n,
            "baseline median is zero", base_t, cand_t,
        )
    slowdown = med_c / med_b - 1
    if base_n < 4 or cand_n < 4:
        status = "inconclusive" if _exceeds(slowdown, threshold) else "ok"
        return CaseResult(
            name, status, med_b, med_c, slowdown, None, base_n, cand_n,
            "fewer than 4 samples", base_t, cand_t,
        )
    noise = max(rel_iqr(base), rel_iqr(cand))
    if med_b < MIN_SAMPLE_S or med_c < MIN_SAMPLE_S:
        status = "inconclusive" if _exceeds(slowdown, threshold) else "ok"
        note = "sample shorter than 5ms" if status == "inconclusive" else ""
        return CaseResult(
            name, status, med_b, med_c, slowdown, noise, base_n, cand_n, note, base_t, cand_t,
        )
    # Spread is subtracted so a 10% median gap that is mostly jitter stays
    # inconclusive, while a larger gap still flags. The epsilon keeps a binary
    # float that lands on the threshold from counting as over it.
    if _exceeds(slowdown - noise, threshold):
        return CaseResult(name, "regression", med_b, med_c, slowdown, noise, base_n, cand_n, "", base_t, cand_t)
    if _exceeds(slowdown, threshold):
        return CaseResult(
            name, "inconclusive", med_b, med_c, slowdown, noise, base_n, cand_n,
            "spread overlaps the threshold", base_t, cand_t,
        )
    return CaseResult(name, "ok", med_b, med_c, slowdown, noise, base_n, cand_n, "", base_t, cand_t)


def _names(base: dict[str, list[float]], cand: dict[str, list[float]]) -> list[str]:
    present = set(base) | set(cand)
    ordered = [name for name in CASE_NAMES if name in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def compare_samples(
    baseline: dict[str, list[float]],
    candidate: dict[str, list[float]],
    *,
    threshold: float,
    rounds: int,
    orderings: int,
    elapsed_s: float,
) -> Report:
    cases = tuple(
        _judge(name, list(baseline.get(name, [])), list(candidate.get(name, [])), threshold)
        for name in _names(baseline, candidate)
    )
    return Report(threshold, rounds, orderings, elapsed_s, cases)


def child_env(repo: Path, *, scale: float) -> dict[str, str]:
    app = repo.resolve() / "app"
    env = os.environ.copy()
    env.pop("COCKPIT_BENCH_SCALE", None)
    env["PYTHONPATH"] = str(app)
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if scale != 1:
        # A scaled smoke test must not inherit the Actions guard into the child.
        env.pop("GITHUB_ACTIONS", None)
        env["COCKPIT_BENCH_SCALE"] = str(scale)
    return env


def _repo_app(repo: Path) -> Path:
    app = repo.resolve() / "app"
    if not (app / "risk" / "engine.py").is_file():
        raise SystemExit(f"{repo} is not a cockpit checkout (missing app/risk/engine.py)")
    return app


def measure(repo: Path, rounds: int, *, scale: float) -> dict[str, list[float]]:
    _repo_app(repo)
    proc = subprocess.run(
        [sys.executable, str(HARNESS), "--rounds", str(rounds)],
        cwd=str(repo.resolve()),
        env=child_env(repo, scale=scale),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"harness failed for {repo} (exit {proc.returncode})\n{detail}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"harness for {repo} did not print JSON\n{proc.stdout[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"harness for {repo} returned {type(payload).__name__}")
    return {str(key): [float(value) for value in values] for key, values in payload.items()}


def collect(
    baseline: Path,
    candidate: Path,
    *,
    rounds: int,
    orderings: int,
    scale: float,
) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    base_samples: dict[str, list[float]] = {}
    cand_samples: dict[str, list[float]] = {}
    for index in range(orderings):
        order = (
            (("baseline", baseline, base_samples), ("candidate", candidate, cand_samples))
            if index % 2 == 0
            else (("candidate", candidate, cand_samples), ("baseline", baseline, base_samples))
        )
        for _label, repo, dest in order:
            measured = measure(repo, rounds, scale=scale)
            for name, samples in measured.items():
                dest.setdefault(name, []).extend(samples)
    return base_samples, cand_samples


def _ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 1000:.1f} ms"


def _pct(value: float | None, *, signed: bool = True) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1%}" if signed else f"{value:.1%}"


def render_table(report: Report) -> str:
    lines = [
        "| Case | Base median | Head median | Change | Spread | Result |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for case in report.cases:
        result = case.status if not case.note else f"{case.status} ({case.note})"
        lines.append(
            f"| `{case.name}` | {_ms(case.baseline_median_s)} | {_ms(case.candidate_median_s)} "
            f"| {_pct(case.slowdown)} | {_pct(case.noise, signed=False)} | {result} |"
        )
    return "\n".join(lines)


def render_summary(report: Report) -> str:
    samples = report.rounds * report.orderings
    footer = (
        f"\nSamples per side: {samples} ({report.rounds} rounds x {report.orderings} orderings, "
        "order swapped on the second pass). "
        f"Comparison time: {report.elapsed_s:.1f}s.\n\n"
        "Both trees ran in this job. A case is flagged when median slowdown minus the "
        f"larger relative interquartile spread still exceeds {report.threshold:.0%}. "
        "A wide spread is inconclusive and is not flagged. "
        "Warnings do not fail the required `linux-gates` check.\n"
    )
    return render_table(report) + footer


def render_comment(report: Report) -> str:
    names = ", ".join(f"`{name}`" for name in report.regressions)
    return (
        f"{COMMENT_MARKER}\n"
        "## Performance regression flagged\n\n"
        f"Median runtime for {names} is far enough above the default-branch tree to clear "
        f"the {report.threshold:.0%} threshold after sample spread. Both trees ran in this "
        "job, on this runner. The check does not fail the build.\n\n"
        f"{render_summary(report)}"
    )


def render_unverified(report: Report) -> str:
    """Notice for a run that did not confirm a regression and did not clear.

    Posted only when the pull request has no regression warning. The body names
    each inconclusive or missing case and does not claim an all-clear.
    """
    if not report.unverified:
        raise ValueError("an unverified run needs an inconclusive or missing case")
    bullets = []
    statuses = set()
    for case in report.cases:
        if case.status == "missing":
            statuses.add("missing")
            bullets.append(f"- `{case.name}`: missing on the pull request")
        elif case.status == "inconclusive":
            statuses.add("inconclusive")
            detail = case.note or "not confirmed"
            bullets.append(f"- `{case.name}`: inconclusive ({detail})")
    title = (
        "## Performance case missing"
        if statuses == {"missing"}
        else "## Performance check inconclusive"
    )
    return (
        f"{COMMENT_MARKER}\n"
        f"{title}\n\n"
        "No regression was confirmed. This run is not an all-clear.\n\n"
        + "\n".join(bullets)
        + "\n\n"
        + render_summary(report)
    )


def render_clear(report: Report) -> str:
    if report.unverified:
        raise ValueError("an inconclusive or missing case is not a cleared performance check")
    return (
        f"{COMMENT_MARKER}\n"
        "## Performance check cleared\n\n"
        f"No hot-path case is more than {report.threshold:.0%} slower than the "
        "default-branch tree on this run, after sample spread.\n\n"
        f"{render_summary(report)}"
    )


def warning_lines(report: Report) -> list[str]:
    lines = []
    for case in report.cases:
        if case.status == "regression" and case.slowdown is not None:
            lines.append(
                f"::warning::performance regression: {case.name} median {case.slowdown:+.1%} "
                f"vs base (threshold {report.threshold:.0%}); job stays green"
            )
        elif case.status == "inconclusive" and case.slowdown is not None:
            lines.append(
                f"::warning::performance inconclusive: {case.name} median {case.slowdown:+.1%} "
                "vs base but sample spread is too wide to flag"
            )
        elif case.status == "missing":
            lines.append(
                f"::warning::performance case {case.name} is missing on the pull request"
            )
    return lines


def write_outputs(
    report: Report,
    *,
    comment: Path | None,
    clear_comment: Path | None,
    summary: Path | None,
    report_path: Path | None,
    leave: Path | None = None,
) -> None:
    text = render_summary(report)
    if summary is not None:
        summary.write_text(text, encoding="utf-8")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write("## Performance\n\n")
            handle.write(text)
            handle.write("\n")
    if report_path is not None:
        body = asdict(report)
        body["regressions"] = list(report.regressions)
        report_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    # One comment action per run. A regression comment wins. Otherwise an
    # inconclusive or missing case writes a notice that is not an all-clear.
    # select_comment_update posts that notice unless a regression warning is
    # already on the pull request.
    action = "regression" if report.regressions else "leave" if report.unverified else "clear"

    def _drop(path: Path | None) -> None:
        if path is not None and path.exists():
            path.unlink()

    if action == "regression":
        if comment is not None:
            comment.write_text(render_comment(report), encoding="utf-8")
        _drop(clear_comment)
        _drop(leave)
    elif action == "leave":
        _drop(comment)
        _drop(clear_comment)
        if leave is not None:
            leave.write_text(render_unverified(report), encoding="utf-8")
    else:
        _drop(comment)
        _drop(leave)
        if clear_comment is not None:
            clear_comment.write_text(render_clear(report), encoding="utf-8")


REGRESSION_HEADING = "## Performance regression flagged"


def select_comment_update(
    existing: str | None,
    regression_path: Path,
    unverified_path: Path,
    clear_path: Path,
) -> Path | None:
    """Choose the comment body to post, or None to leave the existing comment.

    A regression body always wins. An inconclusive or missing notice replaces a
    stale all-clear and is posted when no comment exists, but it does not
    replace an earlier regression warning. A clear body updates an existing
    comment only.
    """
    if regression_path.is_file() and regression_path.stat().st_size > 0:
        return regression_path
    if unverified_path.is_file() and unverified_path.stat().st_size > 0:
        if existing and REGRESSION_HEADING in existing:
            return None
        return unverified_path
    if existing and clear_path.is_file() and clear_path.stat().st_size > 0:
        return clear_path
    return None


def compare_trees(
    baseline: Path,
    candidate: Path,
    *,
    threshold: float,
    rounds: int,
    orderings: int,
    scale: float,
) -> Report:
    if not 0 < threshold < 1:
        raise SystemExit("threshold must be a fraction such as 0.10, not a percent")
    if rounds < 1 or orderings < 1:
        raise SystemExit("rounds and orderings must be >= 1")
    if os.environ.get("GITHUB_ACTIONS") == "true" and scale != 1:
        raise SystemExit("refusing --scale on GitHub Actions")
    started = time.perf_counter()
    base, cand = collect(baseline, candidate, rounds=rounds, orderings=orderings, scale=scale)
    elapsed = time.perf_counter() - started
    return compare_samples(
        base, cand, threshold=threshold, rounds=rounds, orderings=orderings, elapsed_s=elapsed,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare hot-path benchmarks for two checkouts.")
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.10)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--orderings", type=int, default=2)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--comment", type=Path)
    parser.add_argument("--clear-comment", type=Path)
    parser.add_argument("--leave", type=Path)
    args = parser.parse_args(argv)
    try:
        report = compare_trees(
            args.baseline_root,
            args.candidate_root,
            threshold=args.threshold,
            rounds=args.rounds,
            orderings=args.orderings,
            scale=args.scale,
        )
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"performance comparison failed: {exc}", file=sys.stderr)
        return 1
    write_outputs(
        report,
        comment=args.comment,
        clear_comment=args.clear_comment,
        summary=args.summary,
        report_path=args.report,
        leave=args.leave,
    )
    print(render_summary(report))
    for line in warning_lines(report):
        print(line)
    if report.regressions:
        print("performance regression flagged (non-blocking): " + ", ".join(report.regressions))
    else:
        print("no performance regression flagged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
