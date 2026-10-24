#!/bin/sh
# Local checks for this tree only. Not the cockpit DEC-09 matrix.
set -eu
cd "$(dirname "$0")"
python -m pytest -q
python -m ruff check .
