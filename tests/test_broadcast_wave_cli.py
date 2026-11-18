"""Broadcast-WAV information observed through the worker CLI."""

import struct

import pytest

from synthetic_aaf import Clip, Timeline, edit_export
from test_check_cli import check, without_report_paths
from test_levels_cli import findings


def test_missing_bext_is_one_info_for_embedded_clip_only_and_keeps_pass(tmp_path):
    path = Timeline(clips=(
        Clip("Embedded clip", "embedded.wav", 4, record_start=48, bext=False),
        Clip("Linked clip", "linked.wav", 3, media_mode="linked", bext=False),
    )).export(tmp_path / "mixed.aaf")
    path.chmod(0o444)

    events = check(path)

    assert findings(events) == [{
        "schema_version": 1,
        "type": "finding",
        "aaf_path": str(path),
        "rule_id": "no_broadcast_wav_chunk",
        "rule_name": "No broadcast-WAV chunk",
        "severity": "INFO",
        "track": {"slot_id": 4, "name": "Audio 2"},
        "record_in_timecode": "00:00:02:00",
        "clip_name": "Embedded clip",
        "source_file": "embedded.wav",
        "message": "Embedded WAV has no bext chunk.",
        "fix_instruction": None,
    }]
    assert events[-1]["verdict"] == "PASS"
    # Only the embedded WAV is read, once, through the existing progress stream.
    assert [event for event in events if event["type"] == "progress"][-1][
        "bytes_read"
    ] == 1_585_628


@pytest.mark.parametrize("position", ["before_data", "after_data"])
def test_embedded_wav_with_bext_has_no_finding(tmp_path, position):
    path = Timeline(clips=(Clip(
        "Broadcast clip", "broadcast.wav", 3, bext=position == "before_data",
    ),)).export(tmp_path / "broadcast.aaf")
    if position == "after_data":
        with edit_export(path) as aaf:
            stream = next(iter(aaf.content.essencedata)).open("rw")
            size = stream.dir.byte_size
            stream.seek(size)
            stream.write(struct.pack("<4sI", b"bext", 602) + bytes(602))
            stream.seek(4)
            stream.write(struct.pack("<I", size + 610 - 8))

    events = check(path)

    assert findings(events) == []
    assert events[-1]["verdict"] == "PASS"
    assert [event for event in events if event["type"] == "progress"][-1][
        "bytes_read"
    ] == 1_586_238


@pytest.mark.parametrize("bext", [False, True])
def test_linked_wavs_never_get_broadcast_chunk_finding(tmp_path, bext):
    path = Timeline(clips=(Clip(
        "Linked clip", "linked.wav", 3, bext=bext,
    ),)).export(tmp_path / "linked.aaf", mode="linked")

    events = check(path)

    assert findings(events) == []
    assert events[-1]["verdict"] == "PASS"
    assert [event for event in events if event["type"] == "progress"][-1][
        "bytes_read"
    ] == 0


def test_shared_essence_gives_one_info_per_occurrence_in_finding_order(tmp_path):
    path = Timeline(clips=(
        Clip("Later track", "later.wav", 6, bext=False),
        Clip("Shared clip", "shared.wav", 3, bext=False, sample_value=149, scene="",
             ixml=(("NOTE", "bext is text here, not a chunk"),)),
    )).export(tmp_path / "shared.aaf")
    with edit_export(path) as aaf:
        sequence = next(aaf.content.compositionmobs()).slot_at(3).segment
        sequence.components.append(sequence.components[0].copy())

    events = check(path)

    assert [(item["track"]["slot_id"], item["record_in_timecode"], item["rule_id"])
            for item in findings(events)] == [
        (3, "00:00:00:00", "low_level"),
        (3, "00:00:00:00", "missing_field_recorder_metadata"),
        (3, "00:00:00:00", "no_broadcast_wav_chunk"),
        (3, "00:00:01:00", "low_level"),
        (3, "00:00:01:00", "missing_field_recorder_metadata"),
        (3, "00:00:01:00", "no_broadcast_wav_chunk"),
        (6, "00:00:00:00", "no_broadcast_wav_chunk"),
    ]
    assert events[-1]["verdict"] == "PASS"
    assert without_report_paths(events) == without_report_paths(check(path))
