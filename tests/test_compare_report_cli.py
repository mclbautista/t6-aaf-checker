"""Compare reports observed at the CLI and report-file seam."""

from datetime import datetime
import getpass
import json
import re
import signal

from pypdf import PdfReader
import pytest

from synthetic_aaf import Timeline, edit_export, patch_header
from test_compare_cli import TIMELINE, compare, diff_items
from test_report_cli import (
    file_hash, fixed_clock, reports, startup_code, stop_at_io_then_signal,
)


def folder_contents(folder):
    return {str(path.relative_to(folder)): file_hash(path) if path.is_file() else None
            for path in folder.rglob("*")}


def export_pair(tmp_path):
    flagged = TIMELINE.export(tmp_path / "flagged" / "original.aaf")
    corrected = TIMELINE.export(tmp_path / "corrected" / "Corrected reel.aaf", mode="linked")
    return flagged, corrected


@pytest.mark.parametrize("verdict", ["PASS", "FAIL", "ERROR"])
def test_compare_writes_only_one_pair_beside_corrected_and_preserves_inputs(tmp_path, verdict):
    flagged, corrected = export_pair(tmp_path)
    if verdict == "FAIL":
        corrected = TIMELINE.with_clip(1, sample_value=0).export(corrected, mode="linked")
    elif verdict == "ERROR":
        flagged.write_bytes(b"Not an AAF")
    before = {folder: folder_contents(folder) for folder in (flagged.parent, corrected.parent)}

    saved = reports(compare(flagged, corrected))

    for path in saved.values():
        assert path.parent == corrected.parent
        assert re.fullmatch(rf"Corrected reel_AAFGate_Compare_{verdict}_\d{{8}}-\d{{4}}", path.stem)
    assert PdfReader(saved[".pdf"]).pages
    assert json.loads(saved[".json"].read_text())["verdict"] == verdict
    assert folder_contents(flagged.parent) == before[flagged.parent]
    after = folder_contents(corrected.parent)
    for path in saved.values():
        del after[path.name]
    assert after == before[corrected.parent]


def test_compare_same_minute_reruns_preserve_reports_and_suffix_both_extensions(tmp_path, fixed_clock):
    flagged, corrected = export_pair(tmp_path)
    first = reports(compare(flagged, corrected))
    original = {extension: file_hash(path) for extension, path in first.items()}

    second = reports(compare(flagged, corrected))

    for extension in (".pdf", ".json"):
        assert second[extension].stem == first[extension].stem + "_2"
        assert file_hash(first[extension]) == original[extension]
    third = reports(compare(flagged, corrected))
    assert third[".pdf"].stem == first[".pdf"].stem + "_3"
    assert third[".json"].stem == third[".pdf"].stem


@pytest.mark.parametrize("termination", [signal.SIGTERM, signal.SIGKILL])
@pytest.mark.parametrize("temporary_role", ["flagged", "corrected"])
def test_killed_compare_report_build_leaves_both_folders_unchanged(
    tmp_path, monkeypatch, termination, temporary_role,
):
    flagged, corrected = export_pair(tmp_path)
    before = {folder: folder_contents(folder) for folder in (flagged.parent, corrected.parent)}
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
    monkeypatch.setenv("TMPDIR", str(flagged.parent if temporary_role == "flagged" else corrected.parent))

    stop_at_io_then_signal(corrected, termination, flagged=flagged)

    assert {folder: folder_contents(folder) for folder in before} == before


def report_fields(event):
    return {key: value for key, value in event.items()
            if key not in ("schema_version", "type", "aaf_role")}


def changed_pair(tmp_path):
    flagged = (TIMELINE.with_clip(1, scene="").with_clip(2, sample_value=0)
               .export(tmp_path / "flagged" / "original.aaf"))
    patch_header(flagged, major_version=4)
    corrected = (TIMELINE.with_clip(1, record_start=4).with_clip(2, head=4)
                 .with_clip(3, duration=3, track_slot=8, tail=4, scene="")
                 .export(tmp_path / "corrected" / "Corrected reel.aaf", mode="linked"))
    return flagged, corrected


def test_compare_json_has_both_headers_summary_all_regressions_and_full_corrected_findings(tmp_path):
    flagged, corrected = changed_pair(tmp_path)
    before = datetime.now().astimezone()

    events = compare(flagged, corrected)

    after = datetime.now().astimezone()
    data = json.loads(reports(events)[".json"].read_text())
    assert data["schema_version"] == 1
    assert data["verdict"] == "FAIL"
    for role, path, verdict, counts in (
        ("flagged", flagged, "FAIL", {"FAIL": 2, "WARN": 4, "INFO": 0}),
        ("corrected", corrected, "PASS", {"FAIL": 0, "WARN": 4, "INFO": 0}),
    ):
        header = data[role]
        assert header["verdict"] == verdict
        assert header["aaf_path"] == str(path)
        assert header["aaf_sha256"] == file_hash(path)
        assert header["app_version"] == events[0]["worker_version"]
        assert header["rule_version"] == events[0]["rule_version"]
        assert before <= datetime.fromisoformat(header["run_datetime"]) <= after
        assert header["user"] == getpass.getuser()
        assert header["counts"]["by_severity"] == counts
        assert header["counts"]["by_rule"]["short_handles"] == 3
    assert data["flagged"]["run_datetime"] == data["corrected"]["run_datetime"]
    assert data["diff_summary"] == {
        "resolved": 3, "persisting": 3, "new": 1,
        "paired_clips": 3, "blocking_regressions": 5,
        "by_regression_kind": {
            "added": 0, "dropped": 0, "sync_shift": 1, "source_slip": 1,
            "length_change": 1, "track_change": 1, "handle_change": 2,
            "whole_reel_offset": 0, "start_timecode_change": 0,
            "reel_length_change": 1, "audio_content_changed": 2,
        },
    }
    changes = [item for item in diff_items(events)
               if item["kind"] not in ("resolved", "persisting", "new")]
    assert len(data["regressions"]) == 9
    assert data["regressions"] == [
        dict(report_fields(item), delta_frames=item.get("delta_frames")) for item in changes
    ]
    shift = next(item for item in data["regressions"] if item["kind"] == "sync_shift")
    assert shift["flagged_position"]["record_in_frames"] == 0
    assert shift["corrected_position"]["record_in_frames"] == 4
    assert shift["delta_frames"] == 4
    assert data["findings"] == [report_fields(event) for event in events
                                if event["type"] == "finding" and event["aaf_role"] == "corrected"]
    order = list(data)
    assert order.index("flagged") < order.index("corrected") < order.index("diff_summary")
    assert order.index("diff_summary") < order.index("regressions") < order.index("findings")


@pytest.mark.parametrize("kind", ["added", "dropped"])
def test_compare_json_keeps_unpaired_clip_position_and_attached_findings(tmp_path, kind):
    short = Timeline(TIMELINE.clips[:2])
    before, after = (short, TIMELINE) if kind == "added" else (TIMELINE, short)
    flagged = before.export(tmp_path / "flagged" / "original.aaf")
    corrected = after.export(tmp_path / "corrected" / "corrected.aaf", mode="linked")

    events = compare(flagged, corrected)

    data = json.loads(reports(events)[".json"].read_text())
    item, = data["regressions"]
    assert item["kind"] == kind
    present, absent = ("corrected", "flagged") if kind == "added" else ("flagged", "corrected")
    assert item[f"{present}_position"] == {
        "track": {"slot_id": 5, "name": "Audio 3"},
        "record_in_timecode": "00:00:00:00", "record_in_frames": 0,
        "source_in_frames": 2, "length_frames": 2,
    }
    assert item[f"{absent}_position"] is None
    assert item["delta_frames"] is None
    assert item["findings"][0]["rule_id"] == "short_handles"
    assert data["diff_summary"]["by_regression_kind"][kind] == 1
    assert data["diff_summary"]["resolved"] == data["diff_summary"]["new"] == 0


def pdf_text(path):
    return "\n".join(page.extract_text() for page in PdfReader(path).pages)


def test_compare_pdf_leads_with_regressions_then_both_headers_summary_and_findings(tmp_path):
    flagged, corrected = changed_pair(tmp_path)

    saved = reports(compare(flagged, corrected))

    data = json.loads(saved[".json"].read_text())
    text = pdf_text(saved[".pdf"])
    # 9 diff items: 5 block PASS, 4 (content notes, longer handles) do not.
    assert text.startswith("Verdict: FAIL\nRegressions (5)\n")
    assert (text.index("Regressions (5)") < text.index("Changes that do not block (4)")
            < text.index("Flagged AAF") < text.index("Corrected AAF"))
    assert text.index("Corrected AAF") < text.index("Diff summary") < text.index("Corrected AAF findings")
    regression_text, finding_text = text.split("Corrected AAF findings", 1)
    compact = "".join(text.split())
    for role in ("flagged", "corrected"):
        for key in ("verdict", "app_version", "rule_version", "aaf_path", "aaf_sha256",
                    "run_datetime", "user"):
            assert "".join(data[role][key].split()) in compact
        for rule_id, count in data[role]["counts"]["by_rule"].items():
            assert f"{rule_id}: {count}" in text
    assert "Resolved: 3 | Persisting: 3 | New: 1" in text
    for kind, count in data["diff_summary"]["by_regression_kind"].items():
        assert f"{kind}: {count}" in text
    for item in data["regressions"]:
        assert "".join(item["message"].split()) in "".join(regression_text.split())
        if item["delta_frames"] is not None:
            assert f'Delta: {item["delta_frames"]} frames' in regression_text
        for role in ("flagged", "corrected"):
            assert f"{role.capitalize()} position:" in regression_text
            position = item[f"{role}_position"]
            assert position["record_in_timecode"] in regression_text
            if position["track"]:
                track = position["track"]
                assert f'{track["name"]} (slot {track["slot_id"]})' in regression_text
    for finding in data["findings"]:
        for key in ("rule_id", "rule_name", "message", "clip_name", "source_file",
                    "record_in_timecode", "fix_instruction"):
            if finding.get(key):
                assert "".join(finding[key].split()) in "".join(finding_text.split())
    assert "not harmful" in regression_text


def test_whole_reel_offset_is_first_regression_and_keeps_all_positions_in_both_formats(tmp_path):
    flagged = TIMELINE.export(tmp_path / "flagged" / "original.aaf")
    moved = TIMELINE
    for number in range(1, 4):
        moved = moved.with_clip(number, record_start=10)
    corrected = moved.export(tmp_path / "corrected" / "corrected.aaf", mode="linked")
    with edit_export(corrected) as aaf:
        next(aaf.content.compositionmobs()).slot_at(1).segment.start = 240

    events = compare(flagged, corrected)

    saved = reports(events)
    data = json.loads(saved[".json"].read_text())
    whole = data["regressions"][0]
    assert whole["kind"] == "whole_reel_offset"
    assert whole["delta_frames"] == 10
    assert [position["record_in_frames"] for position in whole["flagged_positions"]] == [0, 0, 0]
    assert [position["record_in_frames"] for position in whole["corrected_positions"]] == [250, 250, 250]
    assert data["diff_summary"]["by_regression_kind"]["whole_reel_offset"] == 1
    assert data["diff_summary"]["by_regression_kind"]["start_timecode_change"] == 1
    text = pdf_text(saved[".pdf"])
    assert text.startswith("Verdict: FAIL\nRegressions (6)\nWhole reel moved by 10 frames.\n")
    assert text.count("Whole reel moved by 10 frames.") == 1
    assert text.index("Whole reel moved") < text.index("Sequence start timecode changed")
    assert "00:00:10:10" in text


def test_compare_pdf_lists_non_blocking_changes_apart_from_regressions(tmp_path):
    flagged = TIMELINE.export(tmp_path / "flagged" / "original.aaf")
    corrected = TIMELINE.with_clip(1, sample_points=((5000, -2**14),)).export(
        tmp_path / "corrected" / "Corrected reel.aaf", mode="linked",
    )

    saved = reports(compare(flagged, corrected))

    data = json.loads(saved[".json"].read_text())
    assert data["verdict"] == "PASS"
    assert data["diff_summary"]["by_regression_kind"]["audio_content_changed"] == 1
    text = pdf_text(saved[".pdf"])
    # A PASS report must not head an INFO note as a regression.
    assert text.startswith("Verdict: PASS\nRegressions (0)\nNone.\n")
    assert text.index("Regressions (0)") < text.index("Changes that do not block (1)")
    assert text.index("Changes that do not block (1)") < text.index("Audio content changed")
