#!/bin/bash
# Run from any directory; all generated output stays in the repository.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
unset PYTHONPATH PYTHONHOME
PYTHON="$ROOT/.venv/bin/python"
"$PYTHON" -c '
import platform, sys
if sys.platform != "darwin" or platform.machine() != "arm64" or sys.version_info[:2] != (3, 12):
    sys.exit("Build requires native arm64 macOS and a Python 3.12 .venv.")
'

export PIP_CACHE_DIR="$ROOT/build/pip-cache"
export PYINSTALLER_CONFIG_DIR="$ROOT/build/pyinstaller-cache"
"$PYTHON" -m pip install -e '.[build]'
"$PYTHON" -m PyInstaller --noconfirm --clean \
    --workpath "$ROOT/build/pyinstaller" --distpath "$ROOT/dist" \
    "$ROOT/scripts/worker.spec"

# Reject an accidental Intel/universal build before handing it to the app.
test "$(/usr/bin/lipo -archs "$ROOT/dist/aaf-qc-worker/aaf-qc-worker")" = arm64
printf 'Worker ready: %s/dist/aaf-qc-worker/\n' "$ROOT"
