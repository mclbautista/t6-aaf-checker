# Build the macOS worker

Run on an Apple Silicon Mac with the Command Line Tools and a native arm64
Python 3.12 virtual environment at `.venv`. Full Xcode is unnecessary. Swift 6.3
is required for the app build; this worker build uses Python and the
Command Line Tools only.

For a fresh checkout, create the environment once:

```sh
python3.12 -m venv .venv
```

Build with one command from the repository root:

```sh
./scripts/build_worker.sh
```

The script installs the worker and its exact pinned `build` extra from
`pyproject.toml`, including PyInstaller 6.22.3. It runs `scripts/worker.spec` in
**onedir** mode with an explicit **arm64** target and checks the executable's
architecture with `lipo`. Intel, Rosetta and universal2 builds are unsupported.
Intermediate files and caches stay under `build/`; the result is:

```text
dist/aaf-qc-worker/
  aaf-qc-worker
  _internal/
```

Rebuilding replaces this generated directory. Copy the **whole directory**,
preserving executable permissions and symlinks, into
`AAF Gate.app/Contents/Helpers/`. Launch
`Contents/Helpers/aaf-qc-worker/aaf-qc-worker`. Keep `_internal` beside the
executable; copying only the executable will not work.

The directory includes Python, the pinned runtime dependencies, NumPy and its
native libraries, soundfile's bundled arm64 libsndfile dylib, pyaaf2's model
modules, ReportLab's Vera fonts and the bundled `rules.json`. Distribution
metadata is copied so `importlib.metadata.version("aaf-gate-worker")` works
inside the frozen worker; rules and fonts remain available through
`importlib.resources`. The target Mac needs no Python installation. There is
no launch-time onefile extraction.

No signing identity is needed. PyInstaller applies the ad-hoc signatures
required for its arm64 Mach-O files; the app build below signs the final bundle.

## Verify the package

Install the test extra once, then run the packaged smoke test and the full suite:

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q -m packaged
.venv/bin/python -m pytest -q
```

The `packaged` test skips when `dist/aaf-qc-worker/aaf-qc-worker` is absent, so
build first to exercise it. It creates a synthetic mixed embedded/linked AAF
in a temporary folder and copies the entire built directory into a temporary
`Helpers` folder. From outside the checkout, it launches that executable with
`PATH=/usr/bin:/bin` and Python environment overrides removed. It compares its
NDJSON events and JSON report against the Python worker, allowing fresh report
names, run date/time and user. It also reads the generated PDF, exercising the
font resources, and checks worker and rule versions in the run started event.

## Build AAF Gate

With Swift 6.3 and the Command Line Tools selected, run this script from any
directory:

```sh
./scripts/build_app.sh
```

It builds the worker first if `dist/aaf-qc-worker/aaf-qc-worker` is missing,
then runs `swift build -c release --arch arm64` for the `app/` Swift package.
An existing worker distribution is reused; rerun `scripts/build_worker.sh`
first after changing Python code, dependencies or rule configuration.

The script assembles `build/AAF Gate.app`, registers `.aaf` as `com.avid.aaf`
with Viewer role and Alternate handler rank, signs with
`codesign --force --deep --sign -`, and verifies the signature. The minimum
macOS version is 14.0; only Apple Silicon is supported. Full Xcode, a signing
identity and notarization are unnecessary for this local build.

```text
AAF Gate.app/Contents/
  Info.plist
  MacOS/AAF Gate
  Helpers/aaf-qc-worker/
    aaf-qc-worker
    _internal -> ../../Resources/aaf-qc-worker/_internal
  Resources/aaf-qc-worker/_internal/
```

The relative symlink preserves PyInstaller's onedir layout when the app moves.
Runtime files live under Resources because codesign otherwise mistakes dotted
Python metadata directories under Helpers for nested code bundles. The original
`dist/` is unchanged. Copy the **entire app bundle**, preserving symlinks, to
another Apple Silicon Mac. No Python, virtual environment, repository or Swift
toolchain is needed to run it. The app launches only the bundled executable
with `check <path>`; there is no system-Python fallback.

The shell accepts Dock open-file events, window drops and File > Open (⌘O).
Several AAFs queue sequentially, each in a new worker process. It displays the
worker's terminal PASS, FAIL or ERROR and any report-save error. Unsupported
event kinds are ignored; unsupported schema versions, malformed output and
exits without a terminal event show ERROR. Reports can be revealed in Finder.
The core library handles decoding and process I/O; the SwiftUI target contains
no quality-control rules. Detailed findings and Compare UI are later tickets.

## Verify the app

This CLT installation does not expose XCTest or Swift Testing through SwiftPM's
default search paths. The test target uses the exact `swift-testing` 6.3.2
package, with its dependency revisions recorded in `app/Package.resolved`.
Its interop runtime comes from the CLT developer library directory. The default
is `/Library/Developer/CommandLineTools`; `DEVELOPER_DIR` can override it.
These test dependencies are not linked into the app.

```sh
cd app
swift build -c release --arch arm64
swift test --disable-xctest
cd ..
.venv/bin/python -m pytest -q
open "build/AAF Gate.app" --args
```

In a restricted agent environment, pass `--disable-sandbox` and a writable
`--cache-path` to SwiftPM, and set `CLANG_MODULE_CACHE_PATH` to a writable
directory; the build script already does this. Swift Testing covers every
known event kind, all three terminal verdicts, unknown event kinds, and unknown
schema versions (including on unknown events). The packaged pytest smoke test
runs the build script from outside the checkout, verifies the bundle metadata,
arm64 executable and deep signature, then exercises synthetic PASS, FAIL and
ERROR checks through the bundled worker with Python environment overrides
removed and `PATH=/usr/bin:/bin`. It skips if the worker has not been built.

For the reviewer’s manual input checks, create synthetic files in a temporary
folder using the same pyaaf2 fixture builder as the CLI tests:

```sh
export AAF_GATE_SMOKE_DIR="$(mktemp -d)"
.venv/bin/python - <<'PY'
import os
from pathlib import Path
import sys
sys.path.insert(0, "tests")
from synthetic_aaf import Timeline

folder = Path(os.environ["AAF_GATE_SMOKE_DIR"])
Timeline().export(folder / "pass" / "synthetic-pass.aaf")
Timeline().with_clip(1, sample_value=0).export(folder / "fail" / "synthetic-fail.aaf")
(folder / "synthetic-error.aaf").write_bytes(b"synthetic invalid AAF")
print(folder)
PY
open "$AAF_GATE_SMOKE_DIR"
```

1. Open the built app, then drag each synthetic AAF onto its Dock icon. Also
   quit the app and drop a file onto its retained Dock icon to check cold open.
2. Drop the files on its window and repeat through File > Open. Expect PASS,
   FAIL and ERROR for the respective files. Confirm the report pair appears
   beside each input and Reveal reports in Finder selects it.
3. Drop multiple files together and confirm one process runs at a time and the
   completed list retains each verdict. Two files are two checks.
4. Copy the whole bundle to an Apple Silicon Mac without Python installed and
   repeat. The restricted-PATH smoke test verifies independence from Python
   tooling on this build host; a second-machine check remains manual.
