"""Write a small deterministic Figgie report under examples/."""

from pathlib import Path

from sidecar.figgie_bench.tournament import write_example


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    write_example(root / "examples")


if __name__ == "__main__":
    main()
