#!/usr/bin/env bash
# Build and stage the two sidecar binaries Tauri bundles as externalBin:
#   - gotta-sidecar      (frozen Python: foot measurements + print exports)
#   - photogrammetry-cli (Swift Object Capture / PhotogrammetrySession)
# Both are named <name>-<target-triple> so `tauri build` picks them up.
# Run this before `npm run tauri build`.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
TRIPLE="$(rustc -vV | sed -n 's/^host: //p')"
BIN="$DIR/src-tauri/bin"
mkdir -p "$BIN"

echo "==> target triple: $TRIPLE"

echo "==> Python sidecar (PyInstaller one-file)"
( cd "$DIR/sidecar" && uv run pyinstaller --onefile --name gotta-sidecar \
    --noconfirm --clean \
    --collect-all pymeshlab --collect-all scipy --collect-all shapely \
    --collect-all trimesh --collect-all fast_simplification \
    --collect-all gottascan_geometry \
    cli.py )
cp "$DIR/sidecar/dist/gotta-sidecar" "$BIN/gotta-sidecar-$TRIPLE"

echo "==> Swift reconstruction CLI"
swiftc -O -parse-as-library "$DIR/../worker/PhotogrammetryCLI.swift" \
    -o "$BIN/photogrammetry-cli-$TRIPLE"

chmod +x "$BIN/gotta-sidecar-$TRIPLE" "$BIN/photogrammetry-cli-$TRIPLE"
echo "==> staged in $BIN:"
ls -lh "$BIN"
