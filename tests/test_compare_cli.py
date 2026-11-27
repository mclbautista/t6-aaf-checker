"""Compare behavior at the worker's subprocess/NDJSON seam."""

import json
import subprocess
import sys

import pytest

from synthetic_aaf import Clip, Timeline
from test_check_cli import WORKER, check, without_report_paths


# Keep comparison exports small while preserving exact rational media offsets.
TIMELINE = Timeline(tuple(
    Clip(f"Clip {i}", f"clip_{i}.wav", i + 2, duration=2, head=2, tail=2)
    for i in range(1, 4)
))


def compare(flagged, corrected):
    process = subprocess.run(
        [sys.executable, str(WORKER), "compare", "--flagged", str(flagged),
         "--corrected", str(corrected)],
        capture_output=True, text=True, timeout=30,
    )
    events = [json.loads(line) for line in process.stdout.splitlines()]
    assert events, process.stderr
    assert all(event["schema_version"] == 1 for event in events)
    assert events[0]["type"] == "run_started"
    assert sum(event["type"] == "terminal" for event in events) == 1
    assert events[-1]["type"] == "terminal"
    assert process.returncode == {"PASS": 0, "FAIL": 1, "ERROR": 2}[events[-1]["verdict"]]
    return events


def diff_items(events, kind=None):
    return [event for event in events if event["type"] == "diff_item"
            and (kind is None or event["kind"] == kind)]


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_identical_timeline_with_regenerated_ids_passes(tmp_path, mode):
    flagged, corrected = TIMELINE.export_pair(
        tmp_path, flagged_mode=mode, corrected_mode=mode,
    )

    events = compare(flagged, corrected)

    assert events[0] == {
        "schema_version": 1, "type": "run_started", "mode": "compare",
        "flagged_path": str(flagged), "corrected_path": str(corrected),
        "worker_version": "0.2.0", "rule_version": "1.1.0",
    }
    assert events[-1]["verdict"] == "PASS"
    assert events[-1]["flagged_verdict"] == events[-1]["corrected_verdict"] == "PASS"
    assert events[-1]["blocking_regressions"] == 0
    assert events[-1]["paired_clips"] == 3
    assert not diff_items(events, "dropped")
    assert not diff_items(events, "added")
    assert {event["aaf_role"] for event in events if event["type"] == "progress"} == {
        "flagged", "corrected",
    }
    for role, path in (("flagged", flagged), ("corrected", corrected)):
        findings = [event for event in events if event["type"] == "finding"
                    and event["aaf_role"] == role]
        assert [{k: v for k, v in event.items() if k != "aaf_role"}
                for event in findings] == [event for event in check(path)
                                           if event["type"] == "finding"]


def test_embedded_to_linked_has_no_regressions_and_persisting_findings(tmp_path):
    flagged, corrected = TIMELINE.export_pair(tmp_path)

    events = compare(flagged, corrected)

    assert events[-1]["verdict"] == "PASS"
    assert events[-1]["blocking_regressions"] == 0
    assert [(item["kind"], item["rule_id"]) for item in diff_items(events)] == [
        ("persisting", "short_handles"),
        ("persisting", "short_handles"),
        ("persisting", "short_handles"),
    ]
    assert [item["corrected_finding"]["track"]["slot_id"]
            for item in diff_items(events)] == [3, 4, 5]
    assert all(item["flagged_finding"]["aaf_path"] == str(flagged)
               and item["corrected_finding"]["aaf_path"] == str(corrected)
               for item in diff_items(events))


def test_container_corruption_is_resolved_and_flagged_failure_does_not_block(tmp_path):
    from synthetic_aaf import patch_header

    flagged, corrected = TIMELINE.export_pair(tmp_path)
    patch_header(flagged, major_version=4)

    events = compare(flagged, corrected)

    assert events[-1]["verdict"] == "PASS"
    assert events[-1]["flagged_verdict"] == "FAIL"
    assert events[-1]["corrected_verdict"] == "PASS"
    resolved = diff_items(events, "resolved")
    assert [item["rule_id"] for item in resolved] == ["container_corrupt"]
    assert resolved[0]["flagged_finding"]["severity"] == "FAIL"
    assert resolved[0]["corrected_finding"] is None
    assert events[-1]["blocking_regressions"] == 0


@pytest.mark.parametrize("kind", ["dropped", "added"])
def test_unpaired_clip_is_one_blocking_regression(tmp_path, kind):
    shorter = Timeline(TIMELINE.clips[:2])
    before, after = (TIMELINE, shorter) if kind == "dropped" else (shorter, TIMELINE)
    flagged = before.export(tmp_path / "flagged.aaf")
    corrected = after.export(tmp_path / "corrected.aaf", mode="linked")

    events = compare(flagged, corrected)

    assert events[-1]["verdict"] == "FAIL"
    assert events[-1]["corrected_verdict"] == "PASS"
    assert events[-1]["blocking_regressions"] == 1
    assert events[-1]["paired_clips"] == 2
    regressions = [item for item in diff_items(events) if item["blocking"]]
    assert len(regressions) == 1
    item = regressions[0]
    assert item["kind"] == kind
    assert item["clip_name"] == "Clip 3"
    assert item["track"]["slot_id"] == 5
    assert item["aaf_role"] == ("flagged" if kind == "dropped" else "corrected")
    assert [finding["rule_id"] for finding in item["findings"]] == ["short_handles"]
    assert not diff_items(events, "resolved")


def test_corrected_failure_is_new_and_equals_standalone_check(tmp_path):
    # Three seconds or longer, so the silence is a FAIL. The clip was audible in the
    # flagged export, so it is also a blocking "audio went silent" regression.
    flagged = TIMELINE.with_clip(2, duration=72).export(tmp_path / "flagged.aaf")
    corrected = TIMELINE.with_clip(2, duration=72, sample_value=0).export(
        tmp_path / "corrected.aaf",
    )

    events = compare(flagged, corrected)

    assert events[-1]["verdict"] == events[-1]["corrected_verdict"] == "FAIL"
    assert events[-1]["flagged_verdict"] == "PASS"
    assert events[-1]["blocking_regressions"] == 1
    assert [item["track"]["slot_id"] for item in diff_items(events, "audio_went_silent")] == [4]
    new = diff_items(events, "new")
    assert [item["rule_id"] for item in new] == ["digital_silence"]
    assert new[0]["flagged_finding"] is None
    assert new[0]["corrected_finding"]["clip_name"] == "Clip 2"
    corrected_findings = [event for event in events if event["type"] == "finding"
                          and event["aaf_role"] == "corrected"]
    assert [{k: v for k, v in event.items() if k != "aaf_role"}
            for event in corrected_findings] == [event for event in check(corrected)
                                                 if event["type"] == "finding"]


@pytest.mark.parametrize("problem", ["unrelated", "less_than_half", "different_rate"])
def test_same_reel_guard_errors(tmp_path, problem):
    from synthetic_aaf import edit_export

    flagged = TIMELINE.export(tmp_path / "flagged.aaf")
    other = TIMELINE
    if problem == "unrelated":
        other = Timeline(tuple(Clip(f"Other {i}", f"other_{i}.wav", i + 2,
                                    duration=2, head=2, tail=2) for i in range(1, 4)))
    elif problem == "less_than_half":
        other = Timeline(TIMELINE.clips[:1] + (
            Clip("Other 2", "other_2.wav", 4, duration=2, head=2, tail=2),
            Clip("Other 3", "other_3.wav", 5, duration=2, head=2, tail=2),
        ))
    corrected = other.export(tmp_path / "corrected.aaf")
    if problem == "different_rate":
        with edit_export(corrected) as aaf:
            for composition in aaf.content.compositionmobs():
                for slot in composition.slots:
                    slot.edit_rate = 25

    events = compare(flagged, corrected)

    assert events[-1]["verdict"] == "ERROR"
    assert events[-1]["reason"].strip()
    assert events[-1]["flagged_verdict"] == events[-1]["corrected_verdict"] == "PASS"
    assert not diff_items(events)
    if problem == "different_rate":
        assert "edit rate" in events[-1]["reason"].lower()


def test_duplicate_uses_pair_structurally_using_any_chain_locator(tmp_path):
    from synthetic_aaf import edit_export

    flagged = TIMELINE.with_clip(1, chain_depth=2).export(tmp_path / "flagged.aaf")
    # A length difference must not defeat locator pairing or hide the regression.
    corrected = TIMELINE.with_clip(1, duration=3).export(
        tmp_path / "corrected.aaf", mode="linked",
    )
    for path in (flagged, corrected):
        with edit_export(path) as aaf:
            composition = next(aaf.content.compositionmobs())
            sequence = composition.slot_at(3).segment
            sequence.components.append(sequence.components[0].copy())
            if path == flagged:
                from synthetic_aaf import embedded_essence_by_name
                essence = embedded_essence_by_name(aaf)["clip_1.wav"]
                imported = essence.mob.slot_at(1).segment.mob
                decoy = aaf.create.NetworkLocator()
                decoy["URLString"].value = "file:///absent-synthetic/stale.wav"
                imported.descriptor["Locator"].insert(0, decoy)

    events = compare(flagged, corrected)

    assert events[-1]["paired_clips"] == 4
    assert events[-1]["blocking_regressions"] == 4
    assert events[-1]["verdict"] == "FAIL"
    uses = [item for item in diff_items(events, "persisting")
            if item["corrected_finding"]["track"]["slot_id"] == 3]
    assert [(item["flagged_finding"]["record_in_timecode"],
             item["corrected_finding"]["record_in_timecode"]) for item in uses] == [
        ("00:00:00:00", "00:00:00:00"), ("00:00:00:02", "00:00:00:03"),
    ]
    assert uses[0]["flagged_finding"]["source_file"] == "stale.wav"
    assert uses[0]["corrected_finding"]["source_file"] == "clip_1.wav"


def test_stable_media_mob_ids_pair_before_names_and_locators(tmp_path):
    import shutil
    from synthetic_aaf import edit_export

    flagged = TIMELINE.export(tmp_path / "flagged.aaf")
    corrected = tmp_path / "corrected.aaf"
    shutil.copyfile(flagged, corrected)
    with edit_export(corrected) as aaf:
        for master in aaf.content.mastermobs():
            master.name = f"Renamed {master.name}"

    events = compare(flagged, corrected)

    assert events[-1]["verdict"] == "PASS"
    assert events[-1]["paired_clips"] == 3
    assert events[-1]["blocking_regressions"] == 0
    assert [item["corrected_finding"]["clip_name"] for item in diff_items(events)] == [
        "Renamed Clip 1", "Renamed Clip 2", "Renamed Clip 3",
    ]


@pytest.mark.parametrize("role", ["flagged", "corrected"])
def test_unreadable_aaf_still_checks_the_other_and_reports_both_verdicts(tmp_path, role):
    flagged, corrected = TIMELINE.export_pair(tmp_path)
    broken = flagged if role == "flagged" else corrected
    broken.write_bytes(b"Not an AAF")

    events = compare(flagged, corrected)

    assert events[-1]["verdict"] == "ERROR"
    assert events[-1][f"{role}_verdict"] == "ERROR"
    other_role = "corrected" if role == "flagged" else "flagged"
    assert events[-1][f"{other_role}_verdict"] == "PASS"
    assert role in events[-1]["reason"]
    assert any(event["type"] == "progress" and event["aaf_role"] == other_role
               and event["clips_done"] == 3 for event in events)
    assert not diff_items(events)


def test_repeated_compares_are_identical_in_corrected_position_order(tmp_path):
    flagged = TIMELINE.export(tmp_path / "flagged.aaf")
    corrected = (TIMELINE.with_clip(1, name="Added", media_name="added.wav")
                 .with_clip(2, sample_value=0).export(tmp_path / "corrected.aaf"))

    first = compare(flagged, corrected)
    second = compare(flagged, corrected)

    assert without_report_paths(first) == without_report_paths(second)
    items = diff_items(first)
    assert [item["kind"] for item in items] == [
        "added", "audio_content_changed", "audio_went_silent", "new", "persisting",
        "persisting", "dropped",
    ]
    assert [(item.get("corrected_finding") or item)["track"]["slot_id"]
            for item in items] == [3, 4, 4, 4, 4, 5, 3]


@pytest.mark.parametrize("extra_side", ["flagged", "corrected"])
def test_exactly_half_paired_is_fail_for_unpaired_clip_not_guard_error(tmp_path, extra_side):
    one, two = Timeline(TIMELINE.clips[:1]), Timeline(TIMELINE.clips[:2])
    before, after = (two, one) if extra_side == "flagged" else (one, two)
    events = compare(before.export(tmp_path / "flagged.aaf"),
                     after.export(tmp_path / "corrected.aaf"))

    assert events[-1]["verdict"] == "FAIL"
    assert events[-1]["paired_clips"] == 1
    assert events[-1]["blocking_regressions"] == 1


def test_dropped_clip_failure_is_nested_and_never_resolved(tmp_path):
    flagged = TIMELINE.with_clip(3, sample_value=0).export(tmp_path / "flagged.aaf")
    corrected = Timeline(TIMELINE.clips[:2]).export(tmp_path / "corrected.aaf")

    events = compare(flagged, corrected)

    assert events[-1]["verdict"] == "FAIL"
    dropped = diff_items(events, "dropped")
    assert len(dropped) == 1
    assert [finding["rule_id"] for finding in dropped[0]["findings"]] == [
        "digital_silence", "short_handles",
    ]
    assert not diff_items(events, "resolved")


def test_last_fallback_pairs_without_matching_locators_or_mob_ids(tmp_path):
    flagged = TIMELINE.export(tmp_path / "flagged.aaf")
    corrected = TIMELINE
    for i in range(1, 4):
        corrected = corrected.with_clip(i, media_name=f"renamed_{i}.wav", record_start=i)
    corrected = corrected.export(tmp_path / "corrected.aaf", mode="linked")

    events = compare(flagged, corrected)

    assert events[-1]["paired_clips"] == 3
    assert events[-1]["blocking_regressions"] == 4
    assert events[-1]["verdict"] == "FAIL"


def test_locator_key_requires_clip_name_agreement(tmp_path):
    flagged = TIMELINE.export(tmp_path / "flagged.aaf")
    corrected = TIMELINE.with_clip(1, name="Different clip").export(tmp_path / "corrected.aaf")

    events = compare(flagged, corrected)

    assert events[-1]["paired_clips"] == 2
    assert events[-1]["blocking_regressions"] == 2
    assert len(diff_items(events, "dropped")) == len(diff_items(events, "added")) == 1


def test_source_offset_is_exact_delivered_media_time_not_sample_or_record_units(tmp_path):
    flagged = TIMELINE.with_clip(1, chain_depth=2, sample_rate_slots=False).export(
        tmp_path / "flagged.aaf",
    )
    corrected = TIMELINE.export(tmp_path / "corrected.aaf", mode="linked")

    events = compare(flagged, corrected)

    assert events[-1]["verdict"] == "PASS"
    assert events[-1]["paired_clips"] == 3
    assert events[-1]["blocking_regressions"] == 0


def test_file_level_finding_persists_without_a_clip(tmp_path):
    from synthetic_aaf import edit_export

    flagged, corrected = TIMELINE.export_pair(tmp_path)
    for path in (flagged, corrected):
        with edit_export(path) as aaf:
            identification = aaf.header["IdentificationList"].value[0]
            identification["CompanyName"].value = "Synthetic vendor"
            identification["ProductName"].value = "Synthetic exporter"

    events = compare(flagged, corrected)

    assert events[-1]["verdict"] == "PASS"
    unknown = [item for item in diff_items(events, "persisting")
               if item["rule_id"] == "unknown_exporter"]
    assert len(unknown) == 1
    assert unknown[0]["flagged_finding"]["track"] is None
    assert unknown[0]["corrected_finding"]["track"] is None


def test_locator_identity_wins_over_structural_name_fallback(tmp_path):
    first = Clip("Repeated name", "first.wav", 3, duration=2, head=2, tail=2, sample_value=0)
    second = Clip("Repeated name", "second.wav", 4, duration=2, head=2, tail=2)
    flagged = Timeline((first, second)).export(tmp_path / "flagged.aaf")
    corrected = Timeline((
        Clip("Repeated name", "second.wav", 3, duration=2, head=2, tail=2),
        Clip("Repeated name", "first.wav", 4, duration=2, head=2, tail=2, sample_value=0),
    )).export(tmp_path / "corrected.aaf", mode="linked")

    events = compare(flagged, corrected)

    assert events[-1]["paired_clips"] == 2
    # Both clips share a name and record position, and one is audible, so the
    # silent one is graded as an empty channel rather than digital silence.
    silence = [item for item in diff_items(events) if item.get("rule_id") == "empty_channel"]
    assert len(silence) == 1
    assert silence[0]["kind"] == "persisting"
    assert silence[0]["flagged_finding"]["track"]["slot_id"] == 3
    assert silence[0]["corrected_finding"]["track"]["slot_id"] == 4


@pytest.mark.parametrize("arguments", [[], ["--flagged", "one.aaf"],
                                       ["--corrected", "two.aaf"], ["one.aaf", "two.aaf"]])
def test_compare_requires_both_explicitly_named_paths(arguments):
    process = subprocess.run(
        [sys.executable, str(WORKER), "compare", *arguments],
        capture_output=True, text=True, timeout=30,
    )
    assert process.returncode == 2
    assert not process.stdout
    assert "--flagged" in process.stderr or "--corrected" in process.stderr


def test_duplicate_ties_follow_structure_even_when_record_order_is_reversed(tmp_path):
    from synthetic_aaf import edit_export, operation_group

    flagged, corrected = TIMELINE.export_pair(tmp_path)
    for path, offsets in ((flagged, (10, 0)), (corrected, (0, 10))):
        with edit_export(path) as aaf:
            composition = next(aaf.content.compositionmobs())
            slot = composition.slot_at(3)
            original = slot.segment.components[0].copy()
            group = operation_group(aaf, length=12, operation="dissolve")
            for offset in offsets:
                sequence = aaf.create.Sequence(media_kind="sound")
                if offset:
                    sequence.components.append(aaf.create.Filler(media_kind="sound", length=offset))
                sequence.components.append(original.copy())
                group.segments.append(sequence)
            slot.segment = group

    events = compare(flagged, corrected)

    assert events[-1]["paired_clips"] == 4
    assert events[-1]["blocking_regressions"] == 2
    uses = [item for item in diff_items(events, "persisting")
            if item["corrected_finding"]["track"]["slot_id"] == 3]
    assert [(item["flagged_finding"]["record_in_timecode"],
             item["corrected_finding"]["record_in_timecode"]) for item in uses] == [
        ("00:00:00:10", "00:00:00:00"), ("00:00:00:00", "00:00:00:10"),
    ]


def test_early_filler_reports_later_sync_shifts_without_drops_or_adds(tmp_path):
    timeline = TIMELINE.with_clip(2, record_start=4).with_clip(3, record_start=8)
    flagged = timeline.export(tmp_path / "flagged.aaf")
    corrected = (timeline.with_clip(2, record_start=5).with_clip(3, record_start=9)
                 .export(tmp_path / "corrected.aaf", mode="linked"))

    events = compare(flagged, corrected)

    shifts = diff_items(events, "sync_shift")
    assert len(shifts) == 2
    assert events[-1]["verdict"] == "FAIL"
    assert events[-1]["paired_clips"] == 3
    assert events[-1]["blocking_regressions"] == 3
    assert diff_items(events, "reel_length_change")[0]["delta_frames"] == 1
    assert not diff_items(events, "dropped")
    assert not diff_items(events, "added")
    assert not diff_items(events, "whole_reel_offset")
    assert [(item["clip_name"], item["delta_frames"]) for item in shifts] == [
        ("Clip 2", 1), ("Clip 3", 1),
    ]
    assert all(item["blocking"] and "moved by 1 frame" in item["message"] for item in shifts)
    assert shifts[0]["flagged_position"] == {
        "track": {"slot_id": 4, "name": "Audio 2"},
        "record_in_timecode": "00:00:00:04", "record_in_frames": 4,
        "source_in_frames": 2, "length_frames": 2,
    }
    assert shifts[0]["corrected_position"] == {
        "track": {"slot_id": 4, "name": "Audio 2"},
        "record_in_timecode": "00:00:00:05", "record_in_frames": 5,
        "source_in_frames": 2, "length_frames": 2,
    }
    assert without_report_paths(compare(flagged, corrected)) == without_report_paths(events)


def test_whole_reel_offset_is_summarised_once_before_clip_diffs(tmp_path):
    flagged = TIMELINE.export(tmp_path / "flagged.aaf")
    corrected = TIMELINE
    for number in range(1, 4):
        corrected = corrected.with_clip(number, record_start=10)
    corrected = corrected.export(tmp_path / "corrected.aaf", mode="linked")

    events = compare(flagged, corrected)

    summary = diff_items(events, "whole_reel_offset")
    assert len(summary) == 1
    assert diff_items(events)[0] == summary[0]
    assert summary[0]["message"] == "Whole reel moved by 10 frames."
    assert summary[0]["delta_frames"] == 10
    assert summary[0]["blocking"] is True
    assert summary[0]["paired_clips"] == 3
    assert len(diff_items(events, "sync_shift")) == 3
    assert all(item["delta_frames"] == 10 for item in diff_items(events, "sync_shift"))
    assert events[-1]["verdict"] == "FAIL"
    assert not diff_items(events, "dropped")
    assert not diff_items(events, "added")


def test_source_slip_pairs_remaining_clip_and_reports_exact_delta(tmp_path):
    flagged = TIMELINE.export(tmp_path / "flagged.aaf")
    corrected = TIMELINE.with_clip(2, head=7).export(
        tmp_path / "corrected.aaf", mode="linked",
    )

    events = compare(flagged, corrected)

    slips = diff_items(events, "source_slip")
    assert len(slips) == 1
    assert events[-1]["verdict"] == "FAIL"
    assert events[-1]["paired_clips"] == 3
    assert events[-1]["blocking_regressions"] == 1
    assert not diff_items(events, "dropped")
    assert not diff_items(events, "added")
    assert slips[0]["delta_frames"] == 5
    assert slips[0]["flagged_position"]["source_in_frames"] == 2
    assert slips[0]["corrected_position"]["source_in_frames"] == 7
    assert slips[0]["flagged_position"]["record_in_frames"] == 0
    assert slips[0]["corrected_position"]["record_in_frames"] == 0
    assert slips[0]["flagged_position"]["track"] == {"slot_id": 4, "name": "Audio 2"}
    assert slips[0]["corrected_position"]["track"] == {"slot_id": 4, "name": "Audio 2"}
    assert "5 frames" in slips[0]["message"]


def test_three_frame_trim_is_length_change_with_both_positions(tmp_path):
    timeline = TIMELINE.with_clip(2, duration=8).with_clip(3, duration=12)
    flagged = timeline.export(tmp_path / "flagged.aaf")
    corrected = timeline.with_clip(2, duration=5).export(
        tmp_path / "corrected.aaf", mode="linked",
    )

    events = compare(flagged, corrected)

    changes = diff_items(events, "length_change")
    assert len(changes) == 1
    assert changes[0]["delta_frames"] == -3
    assert changes[0]["flagged_position"]["length_frames"] == 8
    assert changes[0]["corrected_position"]["length_frames"] == 5
    assert changes[0]["flagged_position"]["record_in_timecode"] == "00:00:00:00"
    assert changes[0]["corrected_position"]["record_in_timecode"] == "00:00:00:00"
    assert changes[0]["blocking"] is True
    assert events[-1]["verdict"] == "FAIL"
    assert events[-1]["paired_clips"] == 3
    assert events[-1]["blocking_regressions"] == 1
    assert not diff_items(events, "dropped")
    assert not diff_items(events, "added")


@pytest.mark.parametrize("change", [{"track_slot": 8}, {"track_name": "Dialogue"}])
def test_sound_track_change_retains_pair_and_carries_positions(tmp_path, change):
    from synthetic_aaf import edit_export

    flagged, corrected = TIMELINE.export_pair(tmp_path)
    with edit_export(corrected) as aaf:
        slot = next(aaf.content.compositionmobs()).slot_at(4)
        if "track_slot" in change:
            slot.slot_id = change["track_slot"]
            slot.name = "Audio 6"
        else:
            slot.name = change["track_name"]

    events = compare(flagged, corrected)

    changes = diff_items(events, "track_change")
    assert len(changes) == 1
    assert events[-1]["verdict"] == "FAIL"
    assert events[-1]["paired_clips"] == 3
    assert events[-1]["blocking_regressions"] == 1
    assert not diff_items(events, "dropped")
    assert not diff_items(events, "added")
    assert changes[0]["flagged_position"]["track"] == {"slot_id": 4, "name": "Audio 2"}
    assert changes[0]["corrected_position"]["track"] == (
        {"slot_id": 8, "name": "Audio 6"} if "track_slot" in change else
        {"slot_id": 4, "name": "Dialogue"}
    )
    assert changes[0]["delta_frames"] == 0
    assert changes[0]["flagged_position"]["record_in_frames"] == 0
    assert changes[0]["corrected_position"]["record_in_frames"] == 0


def test_changed_start_timecode_is_blocking_regression_not_error(tmp_path):
    from synthetic_aaf import edit_export

    flagged, corrected = TIMELINE.export_pair(tmp_path)
    with edit_export(corrected) as aaf:
        next(aaf.content.compositionmobs()).slot_at(1).segment.start = 240

    events = compare(flagged, corrected)

    changes = diff_items(events, "start_timecode_change")
    assert len(changes) == 1
    assert events[-1]["verdict"] == "FAIL"
    assert events[-1]["paired_clips"] == 3
    assert changes[0]["blocking"] is True
    assert changes[0]["delta_frames"] == 240
    assert changes[0]["flagged_position"]["record_in_timecode"] == "00:00:00:00"
    assert changes[0]["corrected_position"]["record_in_timecode"] == "00:00:10:00"
    assert changes[0]["flagged_position"]["record_in_frames"] == 0
    assert changes[0]["corrected_position"]["record_in_frames"] == 240
    assert not diff_items(events, "dropped")
    assert not diff_items(events, "added")


def test_changed_reel_length_is_regression_even_with_unchanged_clips(tmp_path):
    from synthetic_aaf import edit_export

    flagged, corrected = TIMELINE.export_pair(tmp_path)
    with edit_export(corrected) as aaf:
        composition = next(aaf.content.compositionmobs())
        composition.slot_at(1).segment.length += 7
        composition.slot_at(2).segment.length += 7
        for slot in composition.slots:
            if slot.media_kind == "Sound":
                slot.segment.components.append(aaf.create.Filler(media_kind="sound", length=7))

    events = compare(flagged, corrected)

    changes = diff_items(events, "reel_length_change")
    assert len(changes) == 1
    assert events[-1]["verdict"] == "FAIL"
    assert events[-1]["paired_clips"] == 3
    assert events[-1]["blocking_regressions"] == 1
    assert changes[0]["delta_frames"] == 7
    assert changes[0]["flagged_position"]["length_frames"] == 2
    assert changes[0]["corrected_position"]["length_frames"] == 9
    assert not diff_items(events, "sync_shift")


def test_simultaneous_changes_follow_corrected_track_record_and_rule_order(tmp_path):
    timeline = TIMELINE.with_clip(2, duration=8).with_clip(3, duration=12)
    flagged = timeline.export(tmp_path / "flagged.aaf")
    corrected = timeline.with_clip(2, duration=5, record_start=4, track_slot=8).export(
        tmp_path / "corrected.aaf", mode="linked",
    )

    events = compare(flagged, corrected)

    assert [(item.get("corrected_finding") or item)["track"]["slot_id"]
            for item in diff_items(events)] == [3, 5, 8, 8, 8, 8, 8]
    assert [(item["kind"], item.get("rule_id")) for item in diff_items(events)[2:]] == [
        ("audio_content_changed", None), ("length_change", None), ("persisting", "short_handles"),
        ("sync_shift", None), ("track_change", None),
    ]
    assert events[-1]["blocking_regressions"] == 3
    assert without_report_paths(compare(flagged, corrected)) == without_report_paths(events)


def test_whole_reel_summary_carries_positions_of_every_paired_clip(tmp_path):
    flagged = TIMELINE.export(tmp_path / "flagged.aaf")
    corrected = TIMELINE
    for number in range(1, 4):
        corrected = corrected.with_clip(number, record_start=10)
    corrected = corrected.export(tmp_path / "corrected.aaf", mode="linked")

    events = compare(flagged, corrected)

    summary = diff_items(events, "whole_reel_offset")[0]
    assert [position["record_in_frames"] for position in summary["flagged_positions"]] == [0, 0, 0]
    assert [position["record_in_frames"] for position in summary["corrected_positions"]] == [10, 10, 10]
    assert [position["track"]["slot_id"] for position in summary["corrected_positions"]] == [3, 4, 5]


def test_one_sample_source_slip_preserves_fractional_sequence_frames(tmp_path):
    from synthetic_aaf import edit_export

    flagged, corrected = TIMELINE.export_pair(tmp_path)
    with edit_export(corrected) as aaf:
        master = next(mob for mob in aaf.content.mastermobs() if mob.name == "Clip 2")
        master.slot_at(1).segment.components[0].start += 1

    events = compare(flagged, corrected)

    slips = diff_items(events, "source_slip")
    assert len(slips) == 1
    assert slips[0]["delta_frames"] == "1/2002"
    assert slips[0]["flagged_position"]["source_in_frames"] == 2
    assert slips[0]["corrected_position"]["source_in_frames"] == "4005/2002"
    assert events[-1]["verdict"] == "FAIL"
    assert events[-1]["paired_clips"] == 3
    assert not diff_items(events, "dropped")
    assert not diff_items(events, "added")


@pytest.mark.parametrize("change", [
    {"record_start": 1}, {"track_slot": 8},
    {"name": "Unrelated", "media_name": "unrelated.wav"},
    {"media_name": "unrelated.wav"},
])
def test_source_slip_requires_same_track_record_position_and_media_identity(tmp_path, change):
    flagged = TIMELINE.export(tmp_path / "flagged.aaf")
    corrected = TIMELINE.with_clip(2, head=7, **change).export(
        tmp_path / "corrected.aaf", mode="linked",
    )

    events = compare(flagged, corrected)

    assert not diff_items(events, "source_slip")
    assert events[-1]["paired_clips"] == 2
    assert len(diff_items(events, "dropped")) == len(diff_items(events, "added")) == 1
    assert events[-1]["verdict"] == "FAIL"


@pytest.mark.parametrize("delta,label", [(-1, "01:00:00:03"), (1, "01:00:00:05")])
def test_sync_positions_include_sequence_start_and_signed_frame_delta(tmp_path, delta, label):
    from synthetic_aaf import edit_export

    timeline = TIMELINE.with_clip(2, record_start=4).with_clip(3, duration=12)
    flagged = timeline.export(tmp_path / "flagged.aaf")
    corrected = timeline.with_clip(2, record_start=4 + delta).export(
        tmp_path / "corrected.aaf", mode="linked",
    )
    for path in (flagged, corrected):
        with edit_export(path) as aaf:
            next(aaf.content.compositionmobs()).slot_at(1).segment.start = 86400

    events = compare(flagged, corrected)

    shifts = diff_items(events, "sync_shift")
    assert len(shifts) == 1
    assert shifts[0]["delta_frames"] == delta
    assert shifts[0]["flagged_position"]["record_in_frames"] == 86404
    assert shifts[0]["corrected_position"]["record_in_frames"] == 86404 + delta
    assert shifts[0]["flagged_position"]["record_in_timecode"] == "01:00:00:04"
    assert shifts[0]["corrected_position"]["record_in_timecode"] == label
    assert events[-1]["blocking_regressions"] == 1


def test_diff_rule_order_spans_duplicate_occurrences_at_the_same_position(tmp_path):
    from synthetic_aaf import edit_export, operation_group

    flagged, corrected = TIMELINE.export_pair(tmp_path)
    for path in (flagged, corrected):
        with edit_export(path) as aaf:
            slot = next(aaf.content.compositionmobs()).slot_at(3)
            original = slot.segment.components[0].copy()
            group = operation_group(aaf, length=3, operation="dissolve")
            group.segments.append(original.copy())
            second = original.copy()
            if path == corrected:
                second.length = 3
            group.segments.append(second)
            slot.segment = group

    events = compare(flagged, corrected)

    items = [item for item in diff_items(events)
             if (item.get("corrected_finding") or item).get("track", {}).get("slot_id") == 3]
    assert [(item["kind"], item.get("rule_id")) for item in items] == [
        ("audio_content_changed", None), ("length_change", None), ("persisting", "short_handles"),
        ("persisting", "short_handles"),
    ]
