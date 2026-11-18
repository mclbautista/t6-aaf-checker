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
no quality-control rules. Completed runs show a native findings Table with rule
counts, severity filters and a selected-row detail pane. Compare UI is a later
ticket.

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

## Findings and report acceptance

From the repository root, generate synthetic inputs in a temporary folder:

```sh
export AAF_GATE_FINDINGS_DIR="$(mktemp -d /tmp/aaf-gate-findings.XXXXXX)"
.venv/bin/python - <<'PY'
import os
from pathlib import Path
import sys
sys.path.insert(0, "tests")
from synthetic_aaf import Timeline, edit_export, patch_header
from sparse_aaf import place_essence_at_boundary

folder = Path(os.environ["AAF_GATE_FINDINGS_DIR"])
mixed = Timeline().with_clip(1, sample_value=0, tape="", scene="", take="", bext=False)
mixed.export(folder / "mixed" / "mixed.aaf")
mixed.export(folder / "readonly" / "readonly.aaf")
large = mixed.export(folder / "large" / "large.aaf")
with edit_export(large) as aaf:
    composition = next(aaf.content.compositionmobs())
    sequence = composition.slot_at(3).segment
    original = sequence.components[0]
    for _ in range(533):
        sequence.components.append(original.copy())
    composition.slot_at(1).segment.length = 24 * 534
    composition.slot_at(2).segment.length = 24 * 534
container = Timeline().export(folder / "container" / "container.aaf")
place_essence_at_boundary(container)
patch_header(container, major_version=4)
(folder / "readonly").chmod(0o555)
print(folder)
PY
```

The container fixture is sparse: its apparent size exceeds 2 GiB, but its
allocated space is small. Keep it on a filesystem that supports sparse files.

1. Open `mixed/mixed.aaf` in the built app. Expect FAIL and three rule groups:
   Digital silence (1), Missing field-recorder metadata (1), No broadcast-WAV
   chunk (1), in that order. Select a row and check track, record TC, clip,
   source, full message and fix instruction. File-level findings show “File
   level” and dashes for absent location fields.
2. Toggle FAIL, WARN and INFO independently, including all off and all on.
   Counts stay correct, hidden rows return in their original order, and no
   progress indicator or new reports appear. Click **Reveal reports**: Finder
   must select both the PDF and JSON for this run.
3. Open `readonly/readonly.aaf`. Expect the same FAIL and findings, plus
   **Report could not be saved** with the worker's permission-denied reason,
   and no Reveal reports button. No report appears beside the input. After
   this check, restore permissions with `chmod 755 "$AAF_GATE_FINDINGS_DIR/readonly"`.
4. Open `large/large.aaf`. Expect 1,602 findings, with 534 per rule. Scroll
   rapidly to the end and back, select rows, resize the window and toggle
   filters. The native Table should remain responsive. There are only three
   audio streams; the fixture repeats the first timeline clip, rather than
   duplicating its essence.
5. Open `container/container.aaf`. Select Container corrupt. Its message
   includes **2 clips likely to import empty**. Expand **Likely to import
   empty (2)** in the detail pane and check Clip 2 and Clip 3, with their
   track, timecode and source names.
6. Select a previous run from the completed list. Its findings and Reveal
   reports action must still refer to that run, without launching a check.

Swift Testing covers presentation grouping, stable row order and identities,
severity-set filtering, 1,601 findings, likely-empty counts (including 271),
retained queued results, exact report file URLs and unsaved-report messages for
all verdicts. Finder selection and actual Table scrolling require the manual
checks above; a core performance test does not measure UI scrolling.

## Compare acceptance

The **Compare** tab is separate from **Check**. Each well accepts one AAF by
drop or Choose. Compare is enabled only for two different local AAF paths;
the role selections are locked while it runs. Dock and File > Open inputs
continue to queue checks, even while the Compare tab is selected.

Generate these small pairs using the existing synthetic builder:

```sh
export AAF_GATE_COMPARE_DIR="$(mktemp -d /tmp/aaf-gate-compare.XXXXXX)"
.venv/bin/python - <<'PY'
import os
from pathlib import Path
import sys
sys.path.insert(0, "tests")
from synthetic_aaf import Clip, Timeline

folder = Path(os.environ["AAF_GATE_COMPARE_DIR"])
timeline = Timeline(tuple(
    Clip(f"Clip {i}", f"clip_{i}.wav", i + 2, duration=2, head=2, tail=2)
    for i in range(1, 4)
))
timeline.export(folder / "flagged.aaf")
timeline.export(folder / "identical.aaf", mode="linked")
timeline.with_clip(2, record_start=1).with_clip(3, record_start=1).export(
    folder / "inserted-frame.aaf", mode="linked",
)
Timeline(timeline.clips[:2]).export(folder / "dropped.aaf", mode="linked")
Timeline(tuple(
    Clip(f"Other {i}", f"other_{i}.wav", i + 2, duration=2, head=2, tail=2)
    for i in range(1, 4)
)).export(folder / "unrelated.aaf", mode="linked")
timeline.with_clip(1, sample_points=((5000, -2**14),)).export(
    folder / "changed-audio.aaf", mode="linked",
)
moved = timeline
for number in range(1, 4):
    moved = moved.with_clip(number, record_start=10)
moved.export(folder / "whole-reel.aaf", mode="linked")
print(folder)
PY
open "build/AAF Gate.app"
open "$AAF_GATE_COMPARE_DIR"
```

1. Select Compare. With both wells empty, then only Flagged AAF filled,
   Compare stays disabled. Selecting the same file in both wells also leaves
   it disabled. Choose `flagged.aaf` for Flagged AAF and `identical.aaf` for
   Corrected AAF. Repeat using drops into the respective wells.
2. Run Compare. Expect PASS, Flagged AAF: PASS, Corrected AAF: PASS,
   **Regressions (0)**, and resolved / persisting / new counts of 0 / 3 / 0.
   Expand Persisting findings to inspect both sides. The corrected findings
   table contains the three short-handle warnings and supports the same
   filters and row details as Check.
3. Click Reveal reports. Finder must select the PDF and JSON whose names
   include `identical_AAFGate_Compare_PASS`, beside `identical.aaf`. Each
   rerun must reveal its own report pair.
4. Keep the flagged well and choose `inserted-frame.aaf` for corrected. Expect
   FAIL, two **Clip moved by 1 frame.** entries, both record positions and
   **Delta (frames): 1**. A reel-length change also blocks. There are no dropped
   or added clips. Regressions precede changes that do not block and the summary.
5. Choose `dropped.aaf` for corrected. Expect FAIL and **Dropped**, Clip 3,
   its flagged position, an absent corrected position, and its short-handle
   warning directly beneath the dropped clip. That warning is not resolved.
6. Choose `unrelated.aaf`. Expect ERROR with the reason that fewer than half
   the clips pair. Both individual AAF verdicts remain PASS for reference.
7. Choose `changed-audio.aaf`. Expect PASS with zero regressions and one
   **Audio content changed** note under **Changes that do not block**.
8. Choose `whole-reel.aaf`. Expect FAIL with **Whole reel moved by 10 frames.**
   first. Its disclosure shows the paired positions; individual sync shifts
   still appear as regressions beneath it.
9. Start a compare on a larger synthetic pair. Progress names the AAF being
   read, displays clips and bytes, and both wells remain locked. Cancel stops
   the worker and shows Cancelled without report links. A subsequent compare
   starts with fresh findings and diff items. Drop two AAFs on the Dock icon:
   the Check tab must show two queued checks and the Compare wells stay unchanged.

Swift Testing covers diff-item payloads (including dropped-clip findings,
classification, whole-reel positions, exact subframes and audio notes), compare
terminal fields, explicit subprocess argument roles, selection guards,
corrected-only findings, report URLs, live progress, cancellation/retry and logs.
The worker CLI suite covers synthetic pairing, regressions and compare report
contents. Native well drops, window layout and Finder selection require the
manual checks above.
