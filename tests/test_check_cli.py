import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


WORKER = Path(sys.executable).with_name("aaf-qc-worker")


def check(path, *, cwd=None):
    process = subprocess.run(
        [sys.executable, str(WORKER), "check", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=cwd,
    )
    events = [json.loads(line) for line in process.stdout.splitlines()]
    assert events, f"Worker emitted no events: {process.stderr}"
    assert all(event["schema_version"] == 1 for event in events)
    assert events[0]["type"] == "run_started"
    assert sum(event["type"] == "terminal" for event in events) == 1
    assert events[-1]["type"] == "terminal"
    return events


def test_clean_aaf_streams_started_progress_and_terminal_pass(make_aaf):
    path = make_aaf()

    events = check(path)

    assert events[0] == {
        "schema_version": 1,
        "type": "run_started",
        "mode": "check",
        "aaf_path": str(path),
        "worker_version": "0.1.0",
        "rule_version": "1.0.0",
    }
    progress = [event for event in events if event["type"] == "progress"]
    assert progress
    assert all(event["aaf_path"] == str(path) for event in progress)
    assert progress[0]["clips_done"] == 0
    assert all(event["clips_total"] == 3 for event in progress)
    assert progress[-1]["clips_done"] == 3
    assert progress[-1]["bytes_read"] == 4_756_884
    assert not [event for event in events if event["type"] == "finding"]
    assert events[-1]["verdict"] == "PASS"
    assert events[-1]["report_paths"] == []


@pytest.mark.parametrize("major_version,sector_size", [(4, 512), (3, 4096)])
def test_incompatible_version_and_sector_size_fails(make_aaf, major_version, sector_size):
    path = make_aaf(major_version=major_version, sector_size=sector_size)

    events = check(path)

    findings = [event for event in events if event["type"] == "finding"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["rule_id"] == "container_corrupt"
    assert finding["rule_name"] == "Container corrupt"
    assert finding["severity"] == "FAIL"
    assert finding["aaf_path"] == str(path)
    assert finding["track"] is None
    assert finding["record_in_timecode"] is None
    assert finding["clip_name"] is None
    assert finding["source_file"] is None
    assert str(major_version) in finding["message"]
    assert str(sector_size) in finding["message"]
    assert finding["fix_instruction"] == "Export with linked media."
    assert events[-1]["verdict"] == "FAIL"


@pytest.mark.parametrize(
    "sector_size,size,verdict",
    [
        (512, 2**31, "PASS"),
        (512, 2**31 + 1, "FAIL"),
        (4096, None, "PASS"),
        (4096, 2**31 + 1, "PASS"),
    ],
)
def test_container_size_limit_applies_only_to_version_3(make_aaf, sector_size, size, verdict):
    path = make_aaf(sector_size=sector_size)
    if size is not None:
        # Sparse padding exercises the logical size without allocating 2 GiB.
        with path.open("r+b") as stream:
            stream.truncate(size)

    events = check(path)

    assert events[-1]["verdict"] == verdict
    findings = [event for event in events if event["type"] == "finding"]
    if verdict == "FAIL":
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "container_corrupt"
        assert findings[0]["severity"] == "FAIL"
        assert "2 GiB" in findings[0]["message"]
        assert findings[0]["fix_instruction"] == "Export with linked media."
    else:
        assert findings == []


@pytest.mark.parametrize(
    "problem",
    ["missing", "text", "empty", "directory", "invalid_signature", "header_only"],
)
def test_unreadable_input_ends_in_error_with_reason(make_aaf, tmp_path, problem):
    path = tmp_path / "unreadable.aaf"
    if problem == "text":
        path.write_text("This is not an AAF.")
    elif problem == "empty":
        path.touch()
    elif problem == "directory":
        path.mkdir()
    elif problem in ("invalid_signature", "header_only"):
        path = make_aaf(major_version=4 if problem == "invalid_signature" else 3)
        with path.open("r+b") as stream:
            if problem == "invalid_signature":
                stream.write(b"NOT AAF!")
            else:
                stream.truncate(512)

    events = check(path)

    assert events[-1]["verdict"] == "ERROR"
    assert events[-1]["reason"].strip()
    assert not [event for event in events if event["type"] == "finding"]


@pytest.mark.parametrize("verdict", ["PASS", "FAIL", "ERROR"])
def test_check_preserves_aaf_bytes_on_every_verdict(make_aaf, verdict):
    path = make_aaf(major_version=4 if verdict == "FAIL" else 3)
    if verdict == "ERROR":
        with path.open("r+b") as stream:
            stream.truncate(512)
    path.chmod(0o444)
    with path.open("rb") as stream:
        before = hashlib.file_digest(stream, "sha256").hexdigest()

    events = check(path)

    assert events[-1]["verdict"] == verdict
    with path.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == before


@pytest.mark.parametrize("verdict", ["PASS", "FAIL", "ERROR"])
def test_repeated_checks_have_identical_events(make_aaf, verdict):
    path = make_aaf(major_version=4 if verdict == "FAIL" else 3)
    if verdict == "ERROR":
        with path.open("r+b") as stream:
            stream.truncate(512)

    first = check(path)
    second = check(path)

    assert first[-1]["verdict"] == verdict
    assert second == first


@pytest.mark.parametrize("major_version", [3, 4])
def test_invalid_sector_shift_is_a_container_failure(make_aaf, major_version):
    path = make_aaf(major_version=major_version)
    with path.open("r+b") as stream:
        stream.seek(30)
        stream.write(b"\xff\xff")

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    findings = [event for event in events if event["type"] == "finding"]
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "container_corrupt"
    assert findings[0]["fix_instruction"] == "Export with linked media."
