"""Marker files for Premiere Pro and Avid Media Composer, observed through the worker CLI."""

import csv
from pathlib import Path
import xml.etree.ElementTree as ElementTree

import pytest

from synthetic_aaf import Clip, Timeline, patch_header
from test_check_cli import check

LONG = 72  # frames: three seconds or more, so an all-zero clip is a FAIL


def markers(events):
    paths = [Path(path) for path in events[-1].get("marker_paths", [])]
    return {path.suffix: path for path in paths}


def failing_path(tmp_path, **changes):
    changes = {"sample_value": 0, "duration": LONG, **changes}
    return Timeline().with_clip(2, **changes).export(tmp_path / "reel.aaf")


def test_fail_findings_write_premiere_xml_premiere_csv_and_avid_text(tmp_path):
    events = check(failing_path(tmp_path))

    files = markers(events)
    assert set(files) == {".xml", ".csv", ".txt"}
    assert all(path.is_file() and path.parent == tmp_path for path in files.values())
    assert files[".xml"].stem.endswith("_PremiereXML")
    assert files[".csv"].stem.endswith("_PremiereCSV")
    assert files[".txt"].stem.endswith("_Avid")
    # Report files are unchanged: still exactly the PDF and JSON pair.
    assert len(events[-1]["report_paths"]) == 2


def test_avid_text_has_one_tab_delimited_line_per_fail_clip_finding(tmp_path):
    files = markers(check(failing_path(tmp_path, record_start=48)))

    lines = files[".txt"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    fields = lines[0].split("\t")
    assert fields[0] == "AAF Gate"
    assert fields[1] == "00:00:02:00"
    assert fields[2] == "TC1"
    assert fields[3] == "Red"
    assert "Digital silence" in fields[4] and "Clip 2" in fields[4] and "clip_2.wav" in fields[4]
    assert fields[5].strip() == "1"


def test_premiere_csv_uses_the_markers_panel_columns(tmp_path):
    files = markers(check(failing_path(tmp_path, record_start=48)))

    rows = list(csv.reader(files[".csv"].open(encoding="utf-8", newline=""), delimiter="\t"))
    assert rows[0] == ["Marker Name", "Description", "In", "Out", "Duration", "Marker Type"]
    assert len(rows) == 2
    name, description, start, end, duration, kind = rows[1]
    assert name == "Digital silence"
    assert "Clip 2" in description and "clip_2.wav" in description
    assert start == "00:00:02:00"
    assert end == "00:00:02:00"
    assert duration == "00:00:00:00"
    assert kind == "Comment"


def test_premiere_xml_places_markers_in_frames_from_the_sequence_start(tmp_path):
    files = markers(check(failing_path(tmp_path, record_start=48)))

    root = ElementTree.parse(files[".xml"]).getroot()
    assert root.tag == "xmeml" and root.get("version") == "4"
    sequence = root.find("sequence")
    assert sequence.find("rate/timebase").text == "24"
    assert sequence.find("rate/ntsc").text == "TRUE"
    found = sequence.findall("marker")
    assert len(found) == 1
    assert found[0].find("name").text == "Digital silence"
    assert "Clip 2" in found[0].find("comment").text
    assert found[0].find("in").text == "48"
    assert found[0].find("out").text == "-1"


def test_one_marker_per_fail_finding_in_timecode_order(tmp_path):
    path = Timeline().with_clip(1, sample_value=0, duration=LONG, record_start=96) \
        .with_clip(2, sample_value=0, duration=LONG, record_start=24) \
        .export(tmp_path / "two.aaf")

    files = markers(check(path))

    starts = [line.split("\t")[1] for line in files[".txt"].read_text().splitlines()]
    assert starts == ["00:00:01:00", "00:00:04:00"]
    assert [m.find("in").text for m in ElementTree.parse(files[".xml"]).getroot()
            .find("sequence").findall("marker")] == ["24", "96"]


def test_text_in_names_is_kept_to_one_line_per_marker(tmp_path):
    path = Timeline().with_clip(
        2, sample_value=0, duration=LONG, name="Tab\there\nand newline",
    ).export(tmp_path / "odd.aaf")

    files = markers(check(path))

    lines = files[".txt"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and len(lines[0].split("\t")) == 6


def test_pass_and_warn_only_reels_write_no_marker_files(tmp_path):
    warn = Timeline().with_clip(2, sample_value=0, duration=12).export(tmp_path / "warn.aaf")

    events = check(warn)

    assert events[-1]["verdict"] == "PASS"
    assert "marker_paths" not in events[-1]
    assert not list(tmp_path.glob("*Markers*")) and not list(tmp_path.glob("*.xml"))


def test_file_level_fail_has_no_timecode_so_no_markers(tmp_path):
    clean = Timeline().export(tmp_path / "clean.aaf")
    corrupt = patch_header(clean, output=tmp_path / "corrupt.aaf", major_version=4)

    events = check(corrupt)

    assert events[-1]["verdict"] == "FAIL"
    assert "marker_paths" not in events[-1]


def test_rerun_never_overwrites_marker_files(tmp_path):
    path = failing_path(tmp_path)

    first = markers(check(path))
    second = markers(check(path))

    assert set(first.values()).isdisjoint(second.values()) or all(
        first[suffix] != second[suffix] for suffix in first
    )
    assert all(path.is_file() for path in [*first.values(), *second.values()])


def test_channels_of_one_clip_share_a_single_marker(tmp_path):
    path = Timeline(clips=(
        Clip("Recording", "ch1.wav", 3, sample_value=0, duration=LONG),
        Clip("Recording", "ch2.wav", 4, sample_value=0, duration=LONG),
    )).export(tmp_path / "channels.aaf")

    events = check(path)

    files = markers(events)
    lines = files[".txt"].read_text().splitlines()
    assert len(lines) == 1
    assert "ch1.wav" in lines[0] and "ch2.wav" in lines[0]
    assert len(ElementTree.parse(files[".xml"]).getroot().findall("sequence/marker")) == 1
    assert len(list(csv.reader(files[".csv"].open(), delimiter="\t"))) == 2
