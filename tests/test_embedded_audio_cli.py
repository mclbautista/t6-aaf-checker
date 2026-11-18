"""Embedded audio behavior, observed only through worker NDJSON."""

import hashlib
import shutil

import pytest

from synthetic_aaf import Timeline, edit_export
from test_check_cli import check


def truncate_essence(path, source_name, remove_bytes):
    with edit_export(path) as aaf:
        source = next(mob for mob in aaf.content.sourcemobs() if mob.name == source_name)
        essence = next(item for item in aaf.content.essencedata if item.mob_id == source.mob_id)
        stream = essence.open("rw")
        stream.truncate(stream.dir.byte_size - remove_bytes)


def test_patched_truncated_essence_fails_with_clip_and_export_fix(tmp_path):
    clean = Timeline().export(tmp_path / "clean.aaf")
    truncated = tmp_path / "truncated.aaf"
    shutil.copyfile(clean, truncated)
    truncate_essence(truncated, "clip_3.wav", 3)
    before = hashlib.sha256(truncated.read_bytes()).hexdigest()

    events = check(truncated)

    assert events[-1]["verdict"] == "FAIL"
    assert [event for event in events if event["type"] == "finding"] == [{
        "schema_version": 1,
        "type": "finding",
        "aaf_path": str(truncated),
        "rule_id": "embedded_audio_unreadable",
        "rule_name": "Embedded audio unreadable",
        "severity": "FAIL",
        "track": {"slot_id": 5, "name": "Audio 3"},
        "record_in_timecode": "00:00:00:00",
        "clip_name": "Clip 3",
        "source_file": "clip_3.wav",
        "message": "Embedded audio cannot be read in full.",
        "fix_instruction": "Export with linked media.",
    }]
    assert hashlib.sha256(truncated.read_bytes()).hexdigest() == before


def test_clean_embedded_baseline_reads_every_stream_to_its_end(tmp_path):
    path = Timeline().with_clip(2, chain_depth=2).export(tmp_path / "complete.aaf")
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    assert not [event for event in events if event["type"] == "finding"]
    progress = [event for event in events if event["type"] == "progress"]
    # Each baseline WAV: 528,528 mono 24-bit sample frames plus 44 header bytes.
    assert progress[-1]["bytes_read"] == 4_756_884
    for done, expected in ((1, 1_585_628), (2, 3_171_256), (3, 4_756_884)):
        assert next(event for event in progress if event["clips_done"] == done)["bytes_read"] == expected
    assert progress[-1]["clips_done"] == progress[-1]["clips_total"] == 3
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_large_embedded_stream_reports_bounded_chunks_during_the_clip(tmp_path):
    timeline = Timeline().with_clip(1, duration=1200)
    path = Timeline(clips=(timeline.clips[0],)).export(tmp_path / "large.aaf")

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    progress = [event for event in events if event["type"] == "progress"]
    # 1,440 sequence frames at 2,002 samples/frame, three bytes/sample + header.
    assert progress[-1]["bytes_read"] == 8_648_684
    increments = [later["bytes_read"] - earlier["bytes_read"]
                  for earlier, later in zip(progress, progress[1:])]
    # Progress accounts for each bounded read, including reads before clip EOF.
    assert max(increments) <= 1_048_576
    assert sum(event["clips_done"] == 0 and event["bytes_read"] > 0
               for event in progress) >= 8
    assert progress[-1]["clips_done"] == progress[-1]["clips_total"] == 1


def test_progress_counts_only_actual_essence_bytes_and_keeps_rising_after_failure(tmp_path):
    path = Timeline().with_clip(2, media_mode="linked").export(tmp_path / "mixed.aaf")
    truncate_essence(path, "clip_1.wav", 3)

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    progress = [event for event in events if event["type"] == "progress"]
    counts = [event["bytes_read"] for event in progress]
    assert counts[0] == 0
    assert counts == sorted(counts)
    assert counts[-1] == 3_171_253
    assert all(event["aaf_path"] == str(path) for event in progress)
    assert [event["clips_done"] for event in progress] == sorted(
        event["clips_done"] for event in progress
    )
    # Reading a clip advances bytes before clips_done advances, even on failure.
    for done in (0, 2):
        during = [event["bytes_read"] for event in progress if event["clips_done"] == done]
        assert len(set(during)) > 1
    assert progress[-1]["clips_done"] == progress[-1]["clips_total"] == 3


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_odd_sized_audio_without_riff_pad_byte_is_readable(tmp_path, mode):
    from synthetic_aaf import Timeline

    path = Timeline().with_clip(2, odd_unpadded=True).export(
        tmp_path / "odd.aaf", mode=mode
    )

    events = check(path)

    assert [e for e in events if e["type"] == "finding"] == []
    assert events[-1]["verdict"] == "PASS"
