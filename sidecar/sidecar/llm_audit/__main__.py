"""Write the showdown weekly example."""

from pathlib import Path

from sidecar.llm_audit.adapters import write_weekly_example


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    examples = root / "examples"
    write_weekly_example(
        examples / "showdown_weekly.md",
        examples / "showdown_weekly.json",
        examples / "showdown_weekly.jsonl",
    )


if __name__ == "__main__":
    main()
