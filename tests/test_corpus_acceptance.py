"""Local acceptance run against the real five-reel corpus (#25).

The corpus is production media. It never enters the repository: its folder
comes from AAF_GATE_CORPUS and the test is skipped when that is unset. The
folder holds ``Fail/*R<n>_*.aaf`` (flagged exports) and ``Pass/R<n>/*.aaf``
(accepted exports, with their media folder beside them).

Optional: AAF_GATE_CORPUS_MANIFEST names a ``sha256sum``-style file, kept
outside the repository, listing the expected hash of every corpus file. When
given, the corpus is verified against it before any result is trusted.

The test drives only the worker's command-line seam, removes the reports it
wrote, and asserts that every corpus file is byte-identical afterwards.
Run it on its own: ``pytest -m corpus -s tests/test_corpus_acceptance.py``.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

CORPUS = os.environ.get("AAF_GATE_CORPUS")
MANIFEST = os.environ.get("AAF_GATE_CORPUS_MANIFEST")
WORKER = Path(sys.executable).with_name("aaf-qc-worker")
FIVE_MINUTES = 300

# Spec #1, Testing Decisions; ticket #25.
LIKELY_EMPTY = {1: 482, 2: 248, 3: 178, 4: 271}

# Open spec-vs-corpus conflict, written up on #1 for the owner: R2 and R4
# deliver clips whose WAVs are entirely digital zero, in both exports, so
# Digital silence FAILs the accepted exports and their compares. The rule is
# not loosened; these cases must keep failing until the owner decides.
SILENT_CONFLICT = {2, 4}


def conflict(reel):
    return pytest.param(reel, marks=pytest.mark.xfail(
        strict=True, reason="all-zero delivered clips; open owner decision on #1",
    )) if reel in SILENT_CONFLICT else reel

pytestmark = [
    pytest.mark.corpus,
    pytest.mark.skipif(not CORPUS, reason="AAF_GATE_CORPUS is not set"),
]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 << 20):
            digest.update(block)
    return digest.hexdigest()


def corpus_files(root):
    return sorted(path for path in root.rglob("*")
                  if path.is_file() and "_AAFGate_" not in path.name
                  and path.name != ".DS_Store")


def snapshot(root):
    return {str(path.relative_to(root)): sha256(path) for path in corpus_files(root)}


def one(paths, what):
    paths = sorted(paths)
    assert len(paths) == 1, f"expected one {what}, found {len(paths)}"
    return paths[0]


def run(*args):
    started = time.monotonic()
    process = subprocess.run(
        [sys.executable, str(WORKER), *map(str, args)],
        capture_output=True, text=True, timeout=1800,
    )
    elapsed = time.monotonic() - started
    events = [json.loads(line) for line in process.stdout.splitlines()]
    assert events and events[-1]["type"] == "terminal", process.stderr[-2000:]
    return events, elapsed


def findings(events, role=None):
    return [event for event in events if event["type"] == "finding"
            and (role is None or event.get("aaf_role") == role)]


def rules(events, role=None, severity=None):
    return sorted({item["rule_id"] for item in findings(events, role)
                   if severity is None or item["severity"] == severity})


@pytest.fixture(scope="module")
def corpus():
    root = Path(CORPUS)
    assert root.is_dir(), "AAF_GATE_CORPUS is not a folder"
    before = snapshot(root)
    if MANIFEST:
        expected = {}
        for line in Path(MANIFEST).read_text().splitlines():
            digest, name = line.split(None, 1)
            expected[name.strip().lstrip("*")] = digest
        assert before == expected, "corpus differs from its manifest; results are not trusted"
    reels = {}
    for reel in range(1, 6):
        reels[reel] = (
            one((root / "Fail").glob(f"*R{reel}_*.aaf"), f"flagged R{reel}"),
            one((root / "Pass" / f"R{reel}").glob("*.aaf"), f"accepted R{reel}"),
        )
    results = {"checks": {}, "compares": {}, "times": {}}
    yield root, reels, results
    for report in root.rglob("*_AAFGate_*"):
        report.unlink()
    assert snapshot(root) == before, "a corpus file changed during the run"
    print("\nCorpus acceptance wall times (s):",
          json.dumps({key: round(value, 1) for key, value in results["times"].items()}))


@pytest.mark.parametrize("reel", [conflict(reel) for reel in range(1, 6)])
def test_flagged_export_check(corpus, reel):
    _, reels, results = corpus
    events, elapsed = run("check", reels[reel][0])
    results["times"][f"check flagged R{reel}"] = elapsed
    verdict = events[-1]["verdict"]
    container = [item for item in findings(events) if item["rule_id"] == "container_corrupt"]
    if reel in LIKELY_EMPTY:
        assert verdict == "FAIL"
        assert rules(events, severity="FAIL") == ["container_corrupt"]
        assert len(container) == 1
        # The spec counts embedded streams; the finding lists clips, and a
        # stream used by two clips lists both. One stream = one media file.
        streams = {item["source_file"] for item in container[0]["likely_empty_clips"]}
        assert len(streams) == LIKELY_EMPTY[reel]
        results["checks"][f"R{reel} likely-empty clips"] = len(container[0]["likely_empty_clips"])
        results["checks"][f"R{reel} likely-empty files"] = len(streams)
    else:
        assert verdict == "PASS"
        assert container == []
    assert elapsed < FIVE_MINUTES, f"R{reel} flagged took {elapsed:.0f} s"


@pytest.mark.parametrize("reel", [conflict(reel) for reel in range(1, 6)])
def test_accepted_export_check(corpus, reel):
    _, reels, results = corpus
    events, elapsed = run("check", reels[reel][1])
    results["times"][f"check accepted R{reel}"] = elapsed
    assert events[-1]["verdict"] == "PASS", rules(events, severity="FAIL")
    assert rules(events, severity="FAIL") == []


@pytest.mark.parametrize("reel", [conflict(reel) for reel in range(1, 6)])
def test_pair_compare(corpus, reel):
    _, reels, results = corpus
    flagged, corrected = reels[reel]
    events, elapsed = run("compare", "--flagged", flagged, "--corrected", corrected)
    results["times"][f"compare R{reel}"] = elapsed
    terminal = events[-1]
    assert terminal["blocking_regressions"] == 0
    assert terminal["verdict"] == "PASS"
    resolved = [item for item in events if item["type"] == "diff_item"
                and item["kind"] == "resolved"]
    container_resolved = [item for item in resolved if item.get("rule_id") == "container_corrupt"]
    assert len(container_resolved) == (1 if reel in LIKELY_EMPTY else 0)
