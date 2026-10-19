"""Write proxy IV history for the options runner.

```bash
python -m sidecar.options.iv_backfill --out artifacts/iv_history.jsonl
```

The file is append-only. A date that already has a row for that underlying is
left as it is, including a row that came from the live chain.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sidecar.options.iv_proxy import backfill_proxy_history
from sidecar.options.runner import DEFAULT_IV, OptionsRunnerError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill proxy IV history from CBOE, then FRED.")
    parser.add_argument("--out", type=Path, default=DEFAULT_IV)
    args = parser.parse_args(argv)
    try:
        result = backfill_proxy_history(args.out)
    except OptionsRunnerError as exc:
        print(str(exc))
        return 1
    print(
        f"appended {result['appended']} proxy rows to {args.out}; "
        f"left {result['skipped_existing']} existing rows in place"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
