#!/bin/sh
# One command for macOS and Linux:  ./run.sh        (try it first with:  ./run.sh demo)
# Creates a private Python environment in .venv the first time, then starts Grad Tracker.
set -e
cd "$(dirname "$0")"

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Grad Tracker needs Python 3.9 or newer. Install it from https://www.python.org/downloads/ and run this again."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up (about a minute)..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -e .
fi
exec .venv/bin/python -m gradtracker "$@"
