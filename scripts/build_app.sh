#!/bin/bash
# Build with the Command Line Tools; Xcode is not required.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
    printf 'Build requires native arm64 macOS.\n' >&2
    exit 1
fi
if [[ ! -x "$ROOT/dist/aaf-qc-worker/aaf-qc-worker" ]]; then
    "$ROOT/scripts/build_worker.sh"
fi

export CLANG_MODULE_CACHE_PATH="$ROOT/build/swift-module-cache"
SWIFT_OPTIONS=(--package-path "$ROOT/app" --disable-sandbox
    --cache-path "$ROOT/build/swiftpm-cache"
    --config-path "$ROOT/build/swiftpm-config"
    --security-path "$ROOT/build/swiftpm-security"
    -c release --arch arm64 --product AAFGate)
swift build "${SWIFT_OPTIONS[@]}"
BIN_DIR="$(swift build "${SWIFT_OPTIONS[@]}" --show-bin-path)"

APP="$ROOT/build/AAF Gate.app"
# Replace only this generated bundle, never the worker source or distribution.
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Helpers"
cp "$BIN_DIR/AAFGate" "$APP/Contents/MacOS/AAF Gate"
cp "$ROOT/app/Info.plist" "$APP/Contents/Info.plist"
/usr/bin/ditto "$ROOT/dist/aaf-qc-worker" "$APP/Contents/Helpers/aaf-qc-worker"
# codesign treats dotted metadata directories in Helpers as nested code bundles.
# Keep the onedir layout via an internal relative symlink, with its runtime and
# data sealed as resources. The original dist directory is untouched.
mkdir -p "$APP/Contents/Resources/aaf-qc-worker"
mv "$APP/Contents/Helpers/aaf-qc-worker/_internal" "$APP/Contents/Resources/aaf-qc-worker/_internal"
ln -s ../../Resources/aaf-qc-worker/_internal "$APP/Contents/Helpers/aaf-qc-worker/_internal"
test "$(/usr/bin/lipo -archs "$APP/Contents/MacOS/AAF Gate")" = arm64
test "$(/usr/bin/lipo -archs "$APP/Contents/Helpers/aaf-qc-worker/aaf-qc-worker")" = arm64
/usr/bin/plutil -lint "$APP/Contents/Info.plist"
/usr/bin/codesign --force --deep --sign - "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"
printf 'App ready: %s\n' "$APP"
