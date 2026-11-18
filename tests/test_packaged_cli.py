"""The shipped worker must behave like the Python CLI after relocation."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from pypdf import PdfReader
import pytest

from synthetic_aaf import Timeline
from test_check_cli import WORKER, without_report_paths


BUNDLE = Path(__file__).resolve().parents[1] / "dist" / "aaf-qc-worker"
pytestmark = [
    pytest.mark.packaged,
    pytest.mark.skipif(not (BUNDLE / "aaf-qc-worker").is_file(),
                       reason="Build the worker with scripts/build_worker.sh first"),
]


def run_worker(command, arguments, cwd):
    # Use an absolute executable, with no venv or Python import overrides.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("PYTHON") and key != "VIRTUAL_ENV"}
    env["PATH"] = "/usr/bin:/bin"
    process = subprocess.run(
        [*command, *map(str, arguments)], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=60,
    )
    events = [json.loads(line) for line in process.stdout.splitlines()]
    assert events, process.stderr
    assert all(event["schema_version"] == 1 for event in events)
    assert events[0]["type"] == "run_started"
    assert events[0]["worker_version"] == "0.1.0"
    assert events[0]["rule_version"] == "1.0.0"
    assert sum(event["type"] == "terminal" for event in events) == 1
    assert events[-1]["type"] == "terminal"
    assert process.returncode == {"PASS": 0, "FAIL": 1, "ERROR": 2}[events[-1]["verdict"]]
    assert len(events[-1]["report_paths"]) == 2, events[-1]
    saved = {Path(path).suffix: Path(path) for path in events[-1]["report_paths"]}
    report = json.loads(saved[".json"].read_text())
    pdf_text = "\n".join(page.extract_text() for page in PdfReader(saved[".pdf"]).pages)
    assert f'Verdict: {events[-1]["verdict"]}' in pdf_text
    assert "App version: 0.1.0" in pdf_text
    assert "Rule version: 1.0.0" in pdf_text
    return events, report


def stable_report(value):
    if isinstance(value, dict):
        return {key: stable_report(item) for key, item in value.items()
                if key not in ("run_datetime", "user")}
    if isinstance(value, list):
        return [stable_report(item) for item in value]
    return value


def test_relocated_packaged_check_without_python_matches_source(tmp_path):
    # Exercise embedded essence, linked libsndfile/numpy scanning, rule findings,
    # and PDF font resources together through the public command.
    timeline = (Timeline().with_clip(1, sample_value=0)
                .with_clip(2, media_mode="embedded", scene="", bext=False))
    path = timeline.export(tmp_path / "delivery" / "mixed.aaf", mode="linked")
    arguments = ["check", path]
    expected_events, expected_report = run_worker([sys.executable, str(WORKER)], arguments, tmp_path)
    assert expected_events[-1]["verdict"] == "FAIL"

    relocated = tmp_path / "Helpers" / BUNDLE.name
    shutil.copytree(BUNDLE, relocated, symlinks=True)
    events, report = run_worker([str(relocated / "aaf-qc-worker")], arguments, tmp_path)

    assert all(Path(saved).parent == path.parent for saved in events[-1]["report_paths"])
    assert without_report_paths(events) == without_report_paths(expected_events)
    assert stable_report(report) == stable_report(expected_report)
