"""Check reports, observed only through the worker CLI and its output files."""

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from pypdf import PdfReader
import pytest

from test_check_cli import WORKER, check
from sparse_aaf import place_essence_at_boundary
from synthetic_aaf import Clip, Timeline, edit_export, patch_header


@pytest.fixture
def fixed_clock(tmp_path, monkeypatch):
    """Freeze the subprocess's system clock, without replacing worker code."""
    startup = tmp_path / "python-startup"
    startup.mkdir()
    (startup / "sitecustomize.py").write_text(
        "import datetime\n"
        "class FixedDatetime(datetime.datetime):\n"
        "    @classmethod\n"
        "    def now(cls, tz=None):\n"
        "        return cls(2026, 11, 18, 9, 45, tzinfo=tz)\n"
        "datetime.datetime = FixedDatetime\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(startup) + os.pathsep + os.environ.get("PYTHONPATH", ""))


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def startup_code(tmp_path, monkeypatch, code):
    startup = tmp_path / "io-startup"
    startup.mkdir()
    (startup / "sitecustomize.py").write_text(code)
    monkeypatch.setenv("PYTHONPATH", str(startup) + os.pathsep + os.environ.get("PYTHONPATH", ""))


def stop_at_io_then_signal(path, termination, *, flagged=None):
    """Wait for the OS-boundary fixture to suspend the CLI during a write."""
    command = (["check", str(path)] if flagged is None else
               ["compare", "--flagged", str(flagged), "--corrected", str(path)])
    process = subprocess.Popen(
        [sys.executable, str(WORKER), *command],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            pid, status = os.waitpid(process.pid, os.WUNTRACED | os.WNOHANG)
            if pid:
                if os.WIFSTOPPED(status):
                    break
                pytest.fail("Worker exited before reaching the delayed filesystem write")
            time.sleep(0.01)
        else:
            pytest.fail("Worker did not reach the delayed filesystem write")
        process.send_signal(termination)
        if termination != signal.SIGKILL:
            process.send_signal(signal.SIGCONT)
        stdout, _ = process.communicate(timeout=15)
        events = [json.loads(line) for line in stdout.splitlines()]
        assert events[0]["type"] == "run_started"
        assert any(event["type"] == "progress" for event in events)
        assert not any(event["type"] == "terminal" for event in events)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def reports(events):
    paths = [Path(path) for path in events[-1]["report_paths"]]
    assert len(paths) == 2
    assert {path.suffix for path in paths} == {".pdf", ".json"}
    assert len({path.stem for path in paths}) == 1
    return {path.suffix: path for path in paths}


@pytest.mark.parametrize("verdict", ["PASS", "FAIL", "ERROR"])
def test_check_writes_named_pdf_and_json_and_lists_both_paths(make_aaf, verdict):
    path = make_aaf("Synthetic reel.aaf", major_version=4 if verdict == "FAIL" else 3)
    if verdict == "ERROR":
        path.write_bytes(b"Not an AAF")
    before = datetime.now()

    events = check(path)

    after = datetime.now()
    assert events[-1]["verdict"] == verdict
    saved = reports(events)
    for report in saved.values():
        assert report.parent == path.parent
        match = re.fullmatch(rf"Synthetic reel_AAFGate_{verdict}_(\d{{8}}-\d{{4}})", report.stem)
        assert match
        assert before.strftime("%Y%m%d-%H%M") <= match[1] <= after.strftime("%Y%m%d-%H%M")
    assert PdfReader(saved[".pdf"]).pages
    assert json.loads(saved[".json"].read_text())["verdict"] == verdict


def test_json_contains_provenance_counts_and_complete_event_findings(tmp_path):
    path = (Timeline().with_clip(1, sample_value=0)
            .with_clip(2, scene="", bext=False)
            .with_clip(3, scene="", bext=False).export(tmp_path / "findings.aaf"))
    place_essence_at_boundary(path)
    patch_header(path, major_version=4)
    before = datetime.now().astimezone()

    events = check(path)

    after = datetime.now().astimezone()
    saved = reports(events)
    data = json.loads(saved[".json"].read_text())
    assert data["schema_version"] == 1
    assert data["verdict"] == "FAIL"
    assert data["app_version"] == events[0]["worker_version"] == "0.1.0"
    assert data["rule_version"] == events[0]["rule_version"] == "1.0.0"
    assert data["aaf_path"] == str(path)
    with path.open("rb") as stream:
        assert data["aaf_sha256"] == hashlib.file_digest(stream, "sha256").hexdigest()
    assert before <= datetime.fromisoformat(data["run_datetime"]) <= after
    assert datetime.fromisoformat(data["run_datetime"]).strftime("%Y%m%d-%H%M") in saved[".json"].stem
    assert data["user"] == getpass.getuser()
    assert data["counts"] == {
        "by_severity": {"FAIL": 2, "WARN": 2, "INFO": 2},
        "by_rule": {"container_corrupt": 1, "digital_silence": 1,
                    "missing_field_recorder_metadata": 2, "no_broadcast_wav_chunk": 2},
    }
    assert data["findings"] == [
        {key: value for key, value in event.items() if key not in ("schema_version", "type")}
        for event in events if event["type"] == "finding"
    ]
    container = next(f for f in data["findings"] if f["rule_id"] == "container_corrupt")
    assert [clip["clip_name"] for clip in container["likely_empty_clips"]] == ["Clip 2", "Clip 3"]


def test_pdf_leads_with_verdict_and_groups_every_finding_by_severity_and_rule(tmp_path):
    path = (Timeline().with_clip(1, sample_value=0, name="Dialogue <one> & two")
            .with_clip(2, scene="", bext=False)
            .with_clip(3, scene="", bext=False).export(tmp_path / "grouped.aaf", major_version=4))

    events = check(path)

    saved = reports(events)
    data = json.loads(saved[".json"].read_text())
    text = "\n".join(page.extract_text() for page in PdfReader(saved[".pdf"]).pages)
    assert text.startswith("Verdict: FAIL\n")
    headings = ["FAIL - Container corrupt (1)", "FAIL - Digital silence (1)",
                "WARN - Missing field-recorder metadata (2)",
                "INFO - No broadcast-WAV chunk (2)"]
    offsets = [text.index(heading) for heading in headings]
    assert offsets == sorted(offsets)
    assert text.count("WARN - Missing field-recorder metadata (2)") == 1
    assert text.count("INFO - No broadcast-WAV chunk (2)") == 1
    # All provenance, summary counts and finding fields survive PDF extraction.
    compact_text = "".join(text.split())
    for key in ("app_version", "rule_version", "aaf_path", "aaf_sha256", "run_datetime", "user"):
        assert "".join(data[key].split()) in compact_text
    assert "FAIL: 2 | WARN: 2 | INFO: 2" in text
    for finding in data["findings"]:
        for key in ("rule_id", "message", "fix_instruction", "clip_name", "source_file", "record_in_timecode"):
            if finding.get(key):
                assert "".join(finding[key].split()) in compact_text
        if finding["track"]:
            assert f'{finding["track"]["name"]} (slot {finding["track"]["slot_id"]})' in text


def test_pdf_includes_likely_empty_clip_locations_and_error_reason(tmp_path):
    path = Timeline().export(tmp_path / "affected.aaf")
    place_essence_at_boundary(path)
    patch_header(path, major_version=4)

    saved = reports(check(path))

    text = "\n".join(page.extract_text() for page in PdfReader(saved[".pdf"]).pages)
    assert "Likely to import empty (2)" in text
    for clip in ("Clip 2", "Clip 3", "clip_2.wav", "clip_3.wav", "00:00:00:00"):
        assert clip in text
    path.write_bytes(b"Not an AAF")
    events = check(path)
    saved = reports(events)
    text = "\n".join(page.extract_text() for page in PdfReader(saved[".pdf"]).pages)
    assert text.startswith("Verdict: ERROR\n")
    assert "".join(events[-1]["reason"].split()) in "".join(text.split())
    assert json.loads(saved[".json"].read_text())["reason"] == events[-1]["reason"]


def test_same_minute_reruns_keep_previous_reports_and_add_numeric_suffix(make_aaf, fixed_clock):
    path = make_aaf()
    first = reports(check(path))
    original_hashes = {suffix: file_hash(report) for suffix, report in first.items()}

    second = reports(check(path))

    for suffix in (".pdf", ".json"):
        assert second[suffix].stem == first[suffix].stem + "_2"
        assert file_hash(first[suffix]) == original_hashes[suffix]
    third = reports(check(path))
    assert third[".pdf"].stem == first[".pdf"].stem + "_3"


@pytest.mark.parametrize("extension", [".pdf", ".json"])
def test_existing_single_extension_reserves_the_whole_report_pair(make_aaf, fixed_clock, extension):
    path = make_aaf()
    occupied = path.with_name("synthetic_AAFGate_PASS_20261118-0945" + extension)
    occupied.write_bytes(b"Earlier report - preserve me")

    saved = reports(check(path))

    assert occupied.read_bytes() == b"Earlier report - preserve me"
    assert saved[".pdf"].stem == "synthetic_AAFGate_PASS_20261118-0945_2"
    assert saved[".json"].stem == saved[".pdf"].stem


@pytest.mark.parametrize("verdict", ["PASS", "FAIL", "ERROR"])
def test_read_only_folder_preserves_verdict_and_explains_report_not_saved(make_aaf, verdict):
    path = make_aaf(major_version=4 if verdict == "FAIL" else 3)
    if verdict == "ERROR":
        path.write_bytes(b"Not an AAF")
    original = {entry.name: file_hash(entry) for entry in path.parent.iterdir()}
    path.parent.chmod(0o555)
    try:
        events = check(path)
    finally:
        path.parent.chmod(0o755)

    terminal = events[-1]
    assert terminal["verdict"] == verdict
    assert terminal["report_paths"] == []
    assert "report not saved" in terminal["report_error"].lower()
    assert "permission" in terminal["report_error"].lower()
    if verdict == "ERROR":
        assert "Cannot complete check" in terminal["reason"]
    assert {entry.name: file_hash(entry) for entry in path.parent.iterdir()} == original


@pytest.mark.parametrize("termination", [signal.SIGTERM, signal.SIGKILL])
def test_killed_report_build_leaves_delivery_unchanged_even_with_tmpdir_inside_it(
    tmp_path, monkeypatch, termination,
):
    path = Timeline().export(tmp_path / "delivery" / "cancelled.aaf")
    original = {entry.name: file_hash(entry) for entry in path.parent.iterdir()}
    # Suspend at the filesystem boundary when any PDF is opened for writing.
    # No worker module or report function is replaced.
    startup_code(tmp_path, monkeypatch,
        "import builtins, os, signal\n"
        "original_open = builtins.open\n"
        "def delayed_open(file, mode='r', *args, **kwargs):\n"
        "    stream = original_open(file, mode, *args, **kwargs)\n"
        "    if str(file).endswith('.pdf') and 'w' in mode:\n"
        "        os.kill(os.getpid(), signal.SIGSTOP)\n"
        "    return stream\n"
        "builtins.open = delayed_open\n"
    )
    monkeypatch.setenv("TMPDIR", str(path.parent))

    stop_at_io_then_signal(path, termination)

    assert sorted(entry.name for entry in path.parent.iterdir()) == sorted(original)
    assert {entry.name: file_hash(entry) for entry in path.parent.iterdir()} == original


def test_sigterm_during_publication_removes_the_already_published_file(tmp_path, monkeypatch):
    path = Timeline().export(tmp_path / "delivery" / "cancelled.aaf")
    original = {entry.name: file_hash(entry) for entry in path.parent.iterdir()}
    startup_code(tmp_path, monkeypatch,
        "import os, signal\n"
        "original_link = os.link\n"
        "def delayed_link(source, destination, *args, **kwargs):\n"
        "    original_link(source, destination, *args, **kwargs)\n"
        "    os.kill(os.getpid(), signal.SIGSTOP)\n"
        "os.link = delayed_link\n"
    )

    stop_at_io_then_signal(path, signal.SIGTERM)

    assert sorted(entry.name for entry in path.parent.iterdir()) == sorted(original)
    assert {entry.name: file_hash(entry) for entry in path.parent.iterdir()} == original


def test_concurrent_checks_publish_distinct_complete_pairs(make_aaf, fixed_clock):
    path = make_aaf()

    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(check, [path] * 4))

    pairs = [reports(events) for events in runs]
    assert len({pair[".pdf"] for pair in pairs}) == 4
    for pair in pairs:
        assert PdfReader(pair[".pdf"]).pages
        assert json.loads(pair[".json"].read_text())["verdict"] == "PASS"
    assert len(list(path.parent.glob("*.pdf"))) == 4
    assert len(list(path.parent.glob("*.json"))) == 4


def test_failed_second_publication_rolls_back_the_pair(tmp_path, monkeypatch):
    path = Timeline().export(tmp_path / "delivery" / "write-failure.aaf")
    original = {entry.name: file_hash(entry) for entry in path.parent.iterdir()}
    startup_code(tmp_path, monkeypatch,
        "import errno, os\n"
        "original_link = os.link\n"
        "def full_disk(source, destination, *args, **kwargs):\n"
        "    if str(destination).endswith('.json'):\n"
        "        raise OSError(errno.ENOSPC, 'No space left on device')\n"
        "    return original_link(source, destination, *args, **kwargs)\n"
        "os.link = full_disk\n"
    )

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    assert events[-1]["report_paths"] == []
    assert "No space left" in events[-1]["report_error"]
    assert {entry.name: file_hash(entry) for entry in path.parent.iterdir()} == original


def test_1600_findings_render_in_under_ten_seconds_and_all_survive_pagination(tmp_path):
    path = Timeline((Clip("Repeated clip", "clip.wav", 3, duration=1, head=0, tail=0,
                          sample_value=0, scene="", bext=False),)).export(tmp_path / "many.aaf")
    with edit_export(path) as aaf:
        sequence = next(aaf.content.compositionmobs()).slot_at(3).segment
        original = sequence.components[0]
        for _ in range(399):
            sequence.components.append(original.copy())

    events = []
    with subprocess.Popen([sys.executable, str(WORKER), "check", str(path)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        for line in process.stdout:
            event = json.loads(line)
            events.append(event)
            if event["type"] in ("progress", "finding"):
                rendering_started = time.monotonic()
            elif event["type"] == "terminal":
                rendering_seconds = time.monotonic() - rendering_started
        process.wait(timeout=30)
        assert process.returncode == 1, process.stderr.read()

    saved = reports(events)
    assert rendering_seconds < 10, f"Report rendering took {rendering_seconds:.2f}s"
    data = json.loads(saved[".json"].read_text())
    assert len(data["findings"]) == 1600
    reader = PdfReader(saved[".pdf"])
    assert len(reader.pages) > 1
    text = "\n".join(page.extract_text() for page in reader.pages)
    for message in ("Every sample in the used range plus handles is digital zero.",
                    "Missing field-recorder metadata: scene.",
                    "Short delivered handles", "Embedded WAV has no bext chunk."):
        assert text.count(message) == 400
    for page_number, page in enumerate(reader.pages, 1):
        assert f"AAF Gate | Page {page_number}" in page.extract_text()
