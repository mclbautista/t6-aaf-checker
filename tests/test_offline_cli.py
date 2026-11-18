"""Clip rules are observed only through the worker subprocess contract."""

from pathlib import Path

import pytest

from synthetic_aaf import Timeline, edit_export, operation_group
from test_check_cli import check


def findings(events):
    return [event for event in events if event["type"] == "finding"]


def test_deleted_linked_file_reports_one_offline_clip(tmp_path):
    path = Timeline().with_clip(2, record_start=48).export(
        tmp_path / "linked.aaf", mode="linked"
    )
    (tmp_path / "linked_media" / "clip_2.wav").unlink()

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    assert findings(events) == [{
        "schema_version": 1,
        "type": "finding",
        "aaf_path": str(path),
        "rule_id": "offline_clip",
        "rule_name": "Offline clip",
        "severity": "FAIL",
        "track": {"slot_id": 4, "name": "Audio 2"},
        "record_in_timecode": "00:00:02:00",
        "clip_name": "Clip 2",
        "source_file": "clip_2.wav",
        "message": "Clip has no embedded essence or linked media file that exists.",
        "fix_instruction": "Include the missing media in the delivery folder and re-export the AAF.",
    }]


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_online_baselines_report_clip_progress(tmp_path, mode):
    path = Timeline().export(tmp_path / "online.aaf", mode=mode)

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    assert findings(events) == []
    progress = [event for event in events if event["type"] == "progress"]
    assert [(event["clips_done"], event["clips_total"]) for event in progress] == [
        (0, 3), (1, 3), (2, 3), (3, 3),
    ]
    assert all(event["aaf_path"] == str(path) for event in progress)
    assert all(event["bytes_read"] >= 512 for event in progress)


@pytest.mark.parametrize("levels_above", [0, 1, 2])
def test_stale_absolute_locators_find_delivered_files_up_to_two_levels_above(
    tmp_path, levels_above,
):
    folder = tmp_path / "delivery" / "package" / "aafs"
    path = Timeline().with_clip(2, media_name="clip two.wav").export(
        folder / "linked.aaf", mode="linked"
    )
    destination = folder
    for _ in range(levels_above):
        destination = destination.parent
    for media in (folder / "linked_media").iterdir():
        media.rename(destination / media.name)

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    assert findings(events) == []


def test_mixed_media_walks_variable_depth_and_ignores_upstream_stale_locators(tmp_path):
    timeline = (
        Timeline().with_clip(1, chain_depth=3)
        .with_clip(2, chain_depth=1, media_mode="linked")
        .with_clip(3, chain_depth=2, media_mode="linked")
    )
    path = timeline.export(tmp_path / "mixed.aaf")

    online = check(path)
    assert online[-1]["verdict"] == "PASS"
    assert findings(online) == []

    (tmp_path / "mixed_media" / "clip_3.wav").unlink()
    offline = check(path)
    assert offline[-1]["verdict"] == "FAIL"
    assert [(item["clip_name"], item["source_file"]) for item in findings(offline)] == [
        ("Clip 3", "clip_3.wav"),
    ]


def test_findings_use_track_slot_order_instead_of_mob_storage_order(tmp_path):
    timeline = Timeline()
    timeline = Timeline(clips=tuple(reversed(timeline.clips)))
    path = timeline.export(tmp_path / "unordered.aaf", mode="linked")
    for media in (tmp_path / "unordered_media").iterdir():
        media.unlink()

    first = check(path)
    second = check(path)

    assert first[-1]["verdict"] == "FAIL"
    assert [item["track"]["slot_id"] for item in findings(first)] == [3, 4, 5]
    assert all(item["rule_id"] == "offline_clip" for item in findings(first))
    assert second == first


@pytest.mark.parametrize("wrappers", [
    ("gain", "pan", None), ("pan", None, "gain"), (None, "gain", "pan"),
])
def test_gain_and_pan_wrapped_slots_report_offline_positions_on_any_track(tmp_path, wrappers):
    timeline = Timeline().with_clip(1, record_start=24).with_clip(
        2, record_start=48
    ).with_clip(3, record_start=72)
    path = timeline.export(tmp_path / "wrapped.aaf", mode="linked")
    with edit_export(path) as aaf:
        composition = next(aaf.content.compositionmobs())
        composition.slot_at(1).segment.start = 86400
        for slot_id, wrapper in zip((3, 4, 5), wrappers):
            if wrapper:
                slot = composition.slot_at(slot_id)
                group = operation_group(aaf, length=96, operation=wrapper)
                group.segments.append(slot.segment.copy())
                slot.segment = group

    assert check(path)[-1]["verdict"] == "PASS"
    expected = []
    for number, wrapper in enumerate(wrappers, 1):
        if wrapper:
            (tmp_path / "wrapped_media" / f"clip_{number}.wav").unlink()
            expected.append((
                {"slot_id": number + 2, "name": f"Audio {number}"},
                {1: "01:00:01:00", 2: "01:00:02:00", 3: "01:00:03:00"}[number],
            ))

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    assert [(item["track"], item["record_in_timecode"]) for item in findings(events)] == expected
    assert all(item["rule_id"] == "offline_clip" for item in findings(events))
    assert [event for event in events if event["type"] == "progress"][-1]["clips_total"] == 3


def test_repeated_physical_track_numbers_keep_slot_id_and_name(tmp_path):
    path = Timeline().with_clip(1, record_start=48).export(
        tmp_path / "tracks.aaf", mode="linked"
    )
    with edit_export(path) as aaf:
        composition = next(aaf.content.compositionmobs())
        for slot_id, name in ((3, "Dialogue"), (4, "Effects")):
            slot = composition.slot_at(slot_id)
            slot["PhysicalTrackNumber"].value = 1
            slot.name = name
    for number in (1, 2):
        (tmp_path / "tracks_media" / f"clip_{number}.wav").unlink()

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    assert [(item["track"], item["record_in_timecode"], item["clip_name"])
            for item in findings(events)] == [
        ({"slot_id": 3, "name": "Dialogue"}, "00:00:02:00", "Clip 1"),
        ({"slot_id": 4, "name": "Effects"}, "00:00:00:00", "Clip 2"),
    ]
    assert all(item["rule_id"] == "offline_clip" for item in findings(events))


def test_deleting_shared_linked_file_reports_each_clip_occurrence(tmp_path):
    path = Timeline().with_clip(2, record_start=48).export(
        tmp_path / "shared.aaf", mode="linked"
    )
    with edit_export(path) as aaf:
        composition = next(aaf.content.compositionmobs())
        first_master = composition.slot_at(3).segment.components[0].mob
        second_master = composition.slot_at(4).segment.components[1].mob
        # Distinct timeline clips and MasterMobs resolve to the same file SourceMob.
        second_master.slot_at(1).segment.components[0].mob = (
            first_master.slot_at(1).segment.components[0].mob
        )

    online = check(path)
    assert online[-1]["verdict"] == "PASS"
    assert findings(online) == []
    (tmp_path / "shared_media" / "clip_1.wav").unlink()

    offline = check(path)

    assert offline[-1]["verdict"] == "FAIL"
    assert [(item["track"], item["record_in_timecode"], item["clip_name"], item["source_file"])
            for item in findings(offline)] == [
        ({"slot_id": 3, "name": "Audio 1"}, "00:00:00:00", "Clip 1", "clip_1.wav"),
        ({"slot_id": 4, "name": "Audio 2"}, "00:00:02:00", "Clip 2", "clip_1.wav"),
    ]
    assert all(item["rule_id"] == "offline_clip" for item in findings(offline))
    progress = [event for event in offline if event["type"] == "progress"]
    assert (progress[-1]["clips_done"], progress[-1]["clips_total"]) == (3, 3)


@pytest.mark.parametrize("wrapper", [None, "gain", "pan"])
def test_timeline_counts_occurrences_and_subtracts_transition_overlaps(tmp_path, wrapper):
    path = Timeline().export(tmp_path / "overlap.aaf", mode="linked")
    (tmp_path / "overlap_media" / "clip_1.wav").unlink()
    with edit_export(path) as aaf:
        composition = next(aaf.content.compositionmobs())
        slot = composition.slot_at(3)
        sequence = slot.segment
        master = sequence.components[0].mob
        transition = aaf.create.Transition(media_kind="sound", length=4)
        transition.cutpoint = 2
        transition["OperationGroup"].value = operation_group(
            aaf, length=4, operation="dissolve"
        )
        sequence.components.append(transition)
        sequence.components.append(master.create_source_clip(1, start=144, length=12))
        second_transition = aaf.create.Transition(media_kind="sound", length=2)
        second_transition.cutpoint = 1
        second_transition["OperationGroup"].value = operation_group(
            aaf, length=2, operation="dissolve"
        )
        sequence.components.append(second_transition)
        sequence.components.append(master.create_source_clip(1, start=156, length=8))
        # The next clip starts at the previous clip's exclusive out point.
        sequence.components.append(master.create_source_clip(1, start=164, length=6))
        if wrapper:
            group = operation_group(aaf, length=44, operation=wrapper)
            group.segments.append(sequence.copy())
            slot.segment = group

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    assert [(item["track"]["slot_id"], item["record_in_timecode"], item["clip_name"])
            for item in findings(events)] == [
        (3, "00:00:00:00", "Clip 1"), (3, "00:00:00:20", "Clip 1"),
        (3, "00:00:01:06", "Clip 1"), (3, "00:00:01:14", "Clip 1"),
    ]
    progress = [event for event in events if event["type"] == "progress"]
    assert [(item["clips_done"], item["clips_total"]) for item in progress] == [
        (0, 6), (1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6),
    ]


@pytest.mark.parametrize(
    "edit_rate,fps,drop,start,record_start,label",
    [
        ("25", 25, False, 90000, 1526, "01:01:01:01"),
        ("30000/1001", 30, True, 0, 1799, "00:00:59;29"),
        ("30000/1001", 30, True, 0, 1800, "00:01:00;02"),
        ("30000/1001", 30, True, 0, 17982, "00:10:00;00"),
        ("30000/1001", 30, True, 107892, 0, "01:00:00;00"),
        ("60000/1001", 60, True, 0, 3600, "00:01:00;04"),
        ("30000/1001", 30, False, 108000, 1800, "01:01:00:00"),
    ],
)
def test_record_timecode_uses_slot_start_nominal_rate_and_drop_flag(
    tmp_path, edit_rate, fps, drop, start, record_start, label,
):
    path = Timeline().with_clip(2, record_start=record_start).export(
        tmp_path / "timecode.aaf", mode="linked"
    )
    (tmp_path / "timecode_media" / "clip_2.wav").unlink()
    with edit_export(path) as aaf:
        composition = next(aaf.content.compositionmobs())
        for slot in composition.slots:
            slot.edit_rate = edit_rate
        timecode = composition.slot_at(1).segment
        timecode.start, timecode.fps, timecode.drop = start, fps, drop

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    assert len(findings(events)) == 1
    assert findings(events)[0]["record_in_timecode"] == label


def test_record_position_converts_sound_edit_units_at_exact_rational_rate(tmp_path):
    path = Timeline().with_clip(2, record_start=48).export(
        tmp_path / "sample_units.aaf", mode="linked"
    )
    (tmp_path / "sample_units_media" / "clip_2.wav").unlink()
    with edit_export(path) as aaf:
        composition = next(aaf.content.compositionmobs())
        composition.slot_at(1).segment.start = 86400
        slot = composition.slot_at(4)
        slot.edit_rate = 48000
        slot.segment.components[0].length = 96096

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    assert len(findings(events)) == 1
    assert findings(events)[0]["record_in_timecode"] == "01:00:02:00"


@pytest.mark.parametrize("break_chain", ["missing_slot", "missing_mob", "cycle"])
def test_unresolved_sound_references_are_offline_clips(tmp_path, break_chain):
    path = Timeline().export(tmp_path / "unresolved.aaf")
    with edit_export(path) as aaf:
        composition = next(aaf.content.compositionmobs())
        master = composition.slot_at(4).segment.components[0].mob
        reference = master.slot_at(1).segment.components[0]
        if break_chain == "missing_slot":
            reference.slot_id = 999
        elif break_chain == "missing_mob":
            reference.mob_id = aaf.create.SourceMob("Unattached synthetic source").mob_id
        else:
            reference.mob = master

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    assert [(item["rule_id"], item["clip_name"], item["track"]["slot_id"])
            for item in findings(events)] == [("offline_clip", "Clip 2", 4)]


def test_relative_aaf_argument_searches_the_same_delivery_folders(tmp_path):
    delivery = tmp_path / "delivery"
    working_folder = delivery / "package"
    path = Timeline().export(working_folder / "aafs" / "relative.aaf", mode="linked")
    for media in (path.parent / "relative_media").iterdir():
        media.rename(delivery / media.name)

    events = check(Path("aafs/relative.aaf"), cwd=working_folder)

    assert events[-1]["verdict"] == "PASS"
    assert findings(events) == []


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_picture_and_timecode_references_are_not_audio_clips(tmp_path, mode):
    path = Timeline().export(tmp_path / "audio_scope.aaf", mode=mode)
    with edit_export(path) as aaf:
        composition = next(aaf.content.compositionmobs())
        composition.slot_at(2).segment = aaf.create.SourceClip(
            media_kind="picture", length=24
        )
        extra_timecode = composition.create_timeline_slot("24000/1001", slot_id=6)
        extra_timecode.segment = aaf.create.SourceClip(media_kind="timecode", length=24)
        # A sound slot can contain a SourceClip directly rather than a sequence.
        slot = composition.slot_at(3)
        slot.segment = slot.segment.components[0].copy()

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    assert findings(events) == []
    progress = [event for event in events if event["type"] == "progress"]
    assert progress[-1]["clips_done"] == progress[-1]["clips_total"] == 3


def test_stored_absolute_file_is_online_even_outside_delivery_search(tmp_path):
    path = Timeline().export(tmp_path / "delivery" / "package" / "aafs" / "external.aaf",
                             mode="linked")
    external = tmp_path / "external_media"
    external.mkdir()
    for media in (path.parent / "external_media").iterdir():
        media.rename(external / media.name)
    with edit_export(path) as aaf:
        for mob in aaf.content.sourcemobs():
            descriptor = mob.descriptor
            for locator in descriptor["Locator"].value:
                filename = Path(locator["URLString"].value).name
                locator["URLString"].value = (external / filename).as_uri()

    events = check(path)

    # The separate outside-delivery rule belongs to its own ticket.
    assert events[-1]["verdict"] == "PASS"
    assert findings(events) == []


def test_delivery_search_does_not_extend_to_a_third_parent(tmp_path):
    path = Timeline().export(tmp_path / "delivery" / "package" / "aafs" / "bounded.aaf",
                             mode="linked")
    for media in (path.parent / "bounded_media").iterdir():
        media.rename(tmp_path / media.name)

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    assert [item["source_file"] for item in findings(events)] == [
        "clip_1.wav", "clip_2.wav", "clip_3.wav",
    ]
