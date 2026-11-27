"""Exercise the documented app build and its relocated worker contract."""

import plistlib
from pathlib import Path
import subprocess
import sys

import pytest

from synthetic_aaf import Timeline
from test_packaged_cli import BUNDLE, run_worker


ROOT = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.packaged,
    pytest.mark.skipif(sys.platform != "darwin" or not (BUNDLE / "aaf-qc-worker").is_file(),
                       reason="Requires macOS and the packaged worker"),
]


def test_signed_app_bundle_runs_bundled_worker_without_python(tmp_path):
    built = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts" / "build_app.sh")], cwd=tmp_path,
        capture_output=True, text=True, timeout=600,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    app = ROOT / "build" / "AAF Gate.app"
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    executable = app / "Contents" / "MacOS" / "AAF Gate"
    assert executable.is_file()
    assert subprocess.check_output(["/usr/bin/lipo", "-archs", str(executable)], text=True).strip() == "arm64"
    with (app / "Contents" / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    assert info["CFBundleExecutable"] == "AAF Gate"
    assert info["CFBundleIdentifier"] == "com.terminalsix.aafgate"
    assert info["CFBundleShortVersionString"] == "0.2.0"
    document_type = info["CFBundleDocumentTypes"][0]
    assert document_type["LSHandlerRank"] in ("Owner", "Alternate")
    assert "com.avid.aaf" in document_type["LSItemContentTypes"]
    declaration = info["UTImportedTypeDeclarations"][0]
    assert declaration["UTTypeTagSpecification"]["public.filename-extension"] == ["aaf"]

    worker = app / "Contents" / "Helpers" / "aaf-qc-worker" / "aaf-qc-worker"
    for name, sample, verdict in (("clean", 100, "PASS"), ("silent", 0, "FAIL")):
        path = Timeline().with_clip(1, sample_value=sample, duration=72).export(tmp_path / name / "synthetic.aaf")
        events, _ = run_worker([str(worker)], ["check", path], tmp_path)
        assert events[-1]["verdict"] == verdict
    unreadable = tmp_path / "unreadable.aaf"
    unreadable.write_bytes(b"synthetic invalid AAF")
    events, _ = run_worker([str(worker)], ["check", unreadable], tmp_path)
    assert events[-1]["verdict"] == "ERROR"
