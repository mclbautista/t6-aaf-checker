"""Container findings and affected clips, observed at the worker CLI seam."""

from sparse_aaf import place_essence_at_boundary
from synthetic_aaf import Timeline, edit_export, patch_header
from test_check_cli import check


def test_container_failure_still_checks_every_clip(tmp_path):
    path = (
        Timeline().with_clip(1, truncate_bytes=3)
        .with_clip(2, media_mode="linked")
        .with_clip(3, media_mode="linked")
        .export(tmp_path / "corrupt.aaf", major_version=4)
    )
    (tmp_path / "corrupt_media" / "clip_2.wav").unlink()

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    assert [event["rule_id"] for event in events if event["type"] == "finding"] == [
        "container_corrupt", "embedded_audio_unreadable", "offline_clip",
    ]
    progress = [event for event in events if event["type"] == "progress"]
    assert progress[-1]["clips_done"] == progress[-1]["clips_total"] == 3
    assert progress[-1]["bytes_read"] == 1_585_625


def test_container_lists_exactly_clips_with_any_sector_at_or_above_boundary(tmp_path):
    path = (Timeline().with_clip(2, record_start=48, chain_depth=2)
            .with_clip(3, record_start=72).export(tmp_path / "boundary.aaf"))
    place_essence_at_boundary(path)
    patch_header(path, major_version=4)
    path.chmod(0o444)
    before = path.stat()

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    findings = [event for event in events if event["type"] == "finding"]
    assert [item["rule_id"] for item in findings] == ["container_corrupt"]
    assert findings[0]["likely_empty_clips"] == [
        {"track": {"slot_id": 4, "name": "Audio 2"},
         "record_in_timecode": "00:00:02:00", "clip_name": "Clip 2",
         "source_file": "clip_2.wav"},
        {"track": {"slot_id": 5, "name": "Audio 3"},
         "record_in_timecode": "00:00:03:00", "clip_name": "Clip 3",
         "source_file": "clip_3.wav"},
    ]
    progress = [event for event in events if event["type"] == "progress"]
    assert progress[-1]["clips_done"] == progress[-1]["clips_total"] == 3
    assert progress[-1]["bytes_read"] == 4_756_884
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert path.stat().st_blocks * 512 < 32 * 1024**2


def test_shared_high_stream_lists_both_timeline_occurrences_in_record_order(tmp_path):
    path = Timeline().export(tmp_path / "shared.aaf")
    with edit_export(path) as aaf:
        composition = next(aaf.content.compositionmobs())
        slot = composition.slot_at(4)
        original = slot.segment.components[0]
        # Append another occurrence of the same MasterMob, after one second.
        slot.segment.components.append(original.copy())
    place_essence_at_boundary(path)
    patch_header(path, major_version=4)

    first = check(path)
    second = check(path)

    assert first[-1]["verdict"] == "FAIL"
    finding = next(event for event in first if event["type"] == "finding")
    assert [(clip["track"], clip["record_in_timecode"], clip["clip_name"])
            for clip in finding["likely_empty_clips"]] == [
        ({"slot_id": 4, "name": "Audio 2"}, "00:00:00:00", "Clip 2"),
        ({"slot_id": 4, "name": "Audio 2"}, "00:00:01:00", "Clip 2"),
        ({"slot_id": 5, "name": "Audio 3"}, "00:00:00:00", "Clip 3"),
    ]
    assert second == first


def test_valid_large_container_has_no_likely_empty_list_even_with_high_audio(tmp_path):
    path = Timeline().export(tmp_path / "valid.aaf", sector_size=4096)
    place_essence_at_boundary(path)

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    assert not [event for event in events if event["type"] == "finding"]
    assert all("likely_empty_clips" not in event for event in events)
    progress = [event for event in events if event["type"] == "progress"]
    assert progress[-1]["clips_done"] == progress[-1]["clips_total"] == 3
    assert progress[-1]["bytes_read"] == 4_756_884
