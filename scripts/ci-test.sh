#!/usr/bin/env bash
# Run the test suite (Linux CI and local). Invoke from repository root:
#   ./scripts/ci-test.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

run_python() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "$@"
  elif command -v python >/dev/null 2>&1; then
    python "$@"
  else
    echo "Python not found." >&2
    exit 1
  fi
}

run_python -m pip install --upgrade pip
run_python -m pip install -r requirements.txt -r requirements-dev.txt
run_python -m pytest tests/ -q
