"""Delivered handle warnings observed only through the worker CLI."""

import pytest

from synthetic_aaf import Clip, Timeline, edit_export
from test_check_cli import check
from test_levels_cli import configure_bundle, findings


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("head", [120, 40])
@pytest.mark.parametrize("sample_rate_slots", [True, False])
def test_sample_rate_mob_slots_keep_timeline_start_in_sequence_frames(
    tmp_path, mode, head, sample_rate_slots,
):
    path = Timeline(clips=(Clip(
        "Handle clip", "handles.wav", 3, head=head, sample_rate_slots=sample_rate_slots,
    ),)).export(tmp_path / "sample_slots.aaf", mode=mode)

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    items = findings(events)
    assert [item["rule_id"] for item in items] == (["short_handles"] if head == 40 else [])
    if items:
        assert items[0]["severity"] == "WARN"
        assert "head: 40 frames" in items[0]["message"]
        assert "tail" not in items[0]["message"]


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("head,tail,threshold,short_sides", [
    (60, 120, 120, {"head": 60}),
    (120, 60, 120, {"tail": 60}),
    (60, 45, 120, {"head": 60, "tail": 45}),
    (120, 120, 120, {}),
    (60, 60, 60, {}),
    (120, 120, 121, {"head": 120, "tail": 120}),
])
def test_short_handles_warn_once_at_bundled_threshold_and_keep_pass(
    tmp_path, monkeypatch, mode, head, tail, threshold, short_sides,
):
    path = Timeline(clips=(Clip(
        "Handle clip", "handles.wav", 3, head=head, tail=tail,
    ),)).export(tmp_path / "handles.aaf", mode=mode)
    if threshold != 120:
        original = findings(check(path))
        assert [item["rule_id"] for item in original] == (
            ["short_handles"] if head == 60 else []
        )
        configure_bundle(tmp_path, monkeypatch, handle_frames=threshold)

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    items = findings(events)
    assert [(item["rule_id"], item["severity"]) for item in items] == (
        [("short_handles", "WARN")] if short_sides else []
    )
    if short_sides:
        item = items[0]
        assert item["rule_name"] == "Short handles"
        assert item["aaf_path"] == str(path)
        assert item["track"] == {"slot_id": 3, "name": "Audio 1"}
        assert item["record_in_timecode"] == "00:00:00:00"
        assert item["clip_name"] == "Handle clip"
        assert item["source_file"] == "handles.wav"
        for side, count in short_sides.items():
            assert f"{side}: {count} frames" in item["message"].lower()
        for side in {"head", "tail"} - short_sides.keys():
            assert side not in item["message"].lower()


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_handles_use_delivered_extent_and_accumulate_chain_offsets(tmp_path, mode):
    path = Timeline(clips=(Clip(
        "Handle clip", "handles.wav", 3, head=60, tail=60, chain_depth=2,
    ),)).export(tmp_path / "offset.aaf", mode=mode)
    with edit_export(path) as aaf:
        master = next(aaf.content.mastermobs())
        reference = master.slot_at(1).segment.components[0]
        for _ in range(2):
            reference.start = 15 * 2002
            reference = reference.slot.segment.components[0]
        # Neither the claimed file length nor the original physical source
        # extends the delivered WAV. The used range is [90, 114) of 144 frames.
        reference.mob.descriptor["Length"].value = 48_000_000
        reference.slot.segment.start = 9_000_000
        reference.slot.segment.length = 48_000_000

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    items = findings(events)
    assert [item["rule_id"] for item in items] == ["short_handles"]
    assert "head: 90 frames" in items[0]["message"]
    assert "tail: 30 frames" in items[0]["message"]


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("offset,side", [(-1, "head"), (1, "tail")])
def test_one_sample_below_threshold_is_short_at_exact_sequence_rate(tmp_path, mode, offset, side):
    path = Timeline(clips=(Clip(
        "Handle clip", "handles.wav", 3,
    ),)).export(tmp_path / "fractional.aaf", mode=mode)
    with edit_export(path) as aaf:
        master = next(aaf.content.mastermobs())
        reference = master.slot_at(1).segment.components[0]
        reference.start = offset

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    items = findings(events)
    assert [item["rule_id"] for item in items] == ["short_handles"]
    assert f"{side}: 119 frames" in items[0]["message"]
    assert ("tail" if side == "head" else "head") not in items[0]["message"]


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_shared_media_handles_are_per_occurrence_and_findings_stay_ordered(tmp_path, mode):
    import hashlib

    path = Timeline(clips=(
        Clip("Later clip", "later.wav", 5, head=60, tail=60, record_start=48),
        Clip("Shared clip", "shared.wav", 3, head=60, tail=240, sample_value=149),
    )).export(tmp_path / "shared.aaf", mode=mode)
    with edit_export(path) as aaf:
        composition = next(aaf.content.compositionmobs())
        sequence = composition.slot_at(3).segment
        master = sequence.components[0].mob
        # Replace trailing filler with a second use of this delivered file.
        sequence.components.pop(1)
        sequence.components.append(master.create_source_clip(1, start=300, length=24))
    inputs = [path, *tmp_path.glob("shared_media/*.wav")]
    before = {}
    for item in inputs:
        item.chmod(0o444)
        with item.open("rb") as stream:
            before[item] = hashlib.file_digest(stream, "sha256").hexdigest()

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    assert [(item["track"]["slot_id"], item["record_in_timecode"], item["rule_id"])
            for item in findings(events)] == [
        (3, "00:00:00:00", "low_level"),
        (3, "00:00:00:00", "short_handles"),
        (3, "00:00:01:00", "low_level"),
        (3, "00:00:01:00", "short_handles"),
        (5, "00:00:02:00", "short_handles"),
    ]
    warnings = [item for item in findings(events) if item["rule_id"] == "short_handles"]
    assert "head: 60 frames" in warnings[0]["message"]
    assert "tail" not in warnings[0]["message"]
    assert "tail: 0 frames" in warnings[1]["message"]
    assert "head" not in warnings[1]["message"]
    assert events == check(path)
    for item in inputs:
        with item.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == before[item]
