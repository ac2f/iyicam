#!/usr/bin/env bash
# Builds iyiCAM for Linux or macOS: a folder (dist/iyiCAM) and a zip.   Usage: packaging/build_unix.sh [--no-test]
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
"$PY" -c "import struct, sys; sys.exit(0 if struct.calcsize('P') == 8 and sys.version_info >= (3, 11) else 1)" || {
  echo "This needs a 64-bit Python 3.11 or newer (set PYTHON=/path/to/python3.12)."; exit 1; }
[ -x .venv-build/bin/python ] || "$PY" -m venv .venv-build
# shellcheck disable=SC1091
source .venv-build/bin/activate
python -m pip install --upgrade pip
python -m pip install ".[gui]" pyinstaller
python packaging/build.py "$@"
