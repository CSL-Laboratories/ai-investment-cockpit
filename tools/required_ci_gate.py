"""Required aggregate decision, deliberately rejecting unknown/skipped work."""
import json
import os


def settled(result, expected):
    """A skipped debounce is valid only when the event intentionally bypasses it."""
    return expected in ("true", "false") and (
        result == "success" or (result == "skipped" and expected == "false"))


def gate(filter_result, required, results):
    if filter_result != "success" or required not in ("true", "false") or not results:
        return False
    expected = "success" if required == "true" else "skipped"
    return all(result == expected for result in results)


if __name__ == "__main__":
    passed = gate(os.environ["FILTER_RESULT"], os.environ["REQUIRED"],
                  json.loads(os.environ["RESULTS"]))
    raise SystemExit(0 if passed else "Required CI aggregate failed closed")
