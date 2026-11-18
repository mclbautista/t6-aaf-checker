# Build the macOS worker

Run on an Apple Silicon Mac with the Command Line Tools and a native arm64
Python 3.12 virtual environment at `.venv`. Full Xcode is unnecessary. Swift 6.3
is required for the later app build; this worker build uses Python and the
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
required for its arm64 Mach-O files; the app packaging ticket signs the final
app bundle later.

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
