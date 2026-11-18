"""Delivered handles and used audio compared through the worker CLI."""

import pytest

from synthetic_aaf import Clip, Timeline
from test_compare_cli import compare, diff_items


TIMELINE = Timeline((Clip("Clip", "clip.wav", 3, duration=2, head=22, tail=2),))


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_head_handle_shrinking_twenty_frames_is_blocking(tmp_path, mode):
    flagged = TIMELINE.export(tmp_path / "flagged.aaf", mode=mode)
    corrected = TIMELINE.with_clip(1, head=2).export(tmp_path / "corrected.aaf", mode=mode)

    events = compare(flagged, corrected)

    changes = diff_items(events, "handle_change")
    assert len(changes) == 1
    item = changes[0]
    assert item["side"] == "head"
    assert item["delta_frames"] == -20
    assert item["flagged_handle_frames"] == 22
    assert item["corrected_handle_frames"] == 2
    assert item["blocking"] is True
    assert item["harmful"] is True
    assert item["flagged_position"]["source_in_frames"] == 22
    assert item["corrected_position"]["source_in_frames"] == 2
    assert item["track"] == {"slot_id": 3, "name": "Audio 1"}
    assert item["clip_name"] == "Clip"
    assert "head" in item["message"].lower()
    assert "20 frames" in item["message"]
    assert events[-1]["verdict"] == "FAIL"
    # The changed in-point also remains a source-slip regression.
    assert events[-1]["blocking_regressions"] == 2


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_tail_handle_growing_twenty_frames_is_not_harmful(tmp_path, mode):
    flagged = TIMELINE.export(tmp_path / "flagged.aaf", mode=mode)
    corrected = TIMELINE.with_clip(1, tail=22).export(tmp_path / "corrected.aaf", mode=mode)

    events = compare(flagged, corrected)

    changes = diff_items(events, "handle_change")
    assert len(changes) == 1
    item = changes[0]
    assert item["side"] == "tail"
    assert item["delta_frames"] == 20
    assert item["flagged_handle_frames"] == 2
    assert item["corrected_handle_frames"] == 22
    assert item["blocking"] is False
    assert item["harmful"] is False
    assert "not harmful" in item["message"].lower()
    assert item["flagged_position"] == item["corrected_position"]
    assert events[-1]["verdict"] == "PASS"
    assert events[-1]["blocking_regressions"] == 0


@pytest.mark.parametrize("side", ["head", "tail"])
@pytest.mark.parametrize("delta", [-1, 1])
def test_exactly_one_frame_handle_difference_is_ignored(tmp_path, side, delta):
    timeline = Timeline((
        Clip("At tolerance", "one.wav", 3, duration=2, head=4, tail=4),
        Clip("Above tolerance", "two.wav", 4, duration=2, head=4, tail=4),
    ))
    flagged = timeline.export(tmp_path / "flagged.aaf")
    corrected = (timeline.with_clip(1, **{side: 4 + delta})
                 .with_clip(2, tail=6).export(tmp_path / "corrected.aaf", mode="linked"))

    events = compare(flagged, corrected)

    # The two-frame control proves handle comparison ran; only it is listed.
    changes = diff_items(events, "handle_change")
    assert [(item["clip_name"], item["side"], item["delta_frames"]) for item in changes] == [
        ("Above tolerance", "tail", 2),
    ]
    assert events[-1]["blocking_regressions"] == (1 if side == "head" else 0)
    assert events[-1]["verdict"] == ("FAIL" if side == "head" else "PASS")


@pytest.mark.parametrize("before_mode,after_mode", [
    ("embedded", "embedded"), ("linked", "linked"),
    ("embedded", "linked"), ("linked", "embedded"),
])
@pytest.mark.parametrize("changed_sample", [4004, 8007])
def test_only_changed_used_samples_are_info_across_media_modes(
    tmp_path, monkeypatch, before_mode, after_mode, changed_sample,
):
    from test_compare_cli import TIMELINE as short_timeline
    from test_levels_cli import configure_bundle

    # Used samples are [4004, 8008). Changes at either boundary count;
    # samples immediately outside it and WAV metadata must not affect identity.
    flagged = short_timeline.export(tmp_path / "flagged.aaf", mode=before_mode)
    corrected = (short_timeline.with_clip(1, sample_points=((changed_sample, -2**14),))
                 .with_clip(2, bext=False, ixml=(("TAPE", "Other tape"),))
                 .with_clip(3, sample_points=((4003, -2**14), (8008, -2**14)))
                 .export(tmp_path / "corrected.aaf", mode=after_mode))
    # This splits 24-bit samples and used ranges across chunk boundaries.
    configure_bundle(tmp_path, monkeypatch, audio_reader={"chunk_bytes": 4097})

    events = compare(flagged, corrected)

    changes = diff_items(events, "audio_content_changed")
    assert len(changes) == 1
    item = changes[0]
    assert item["clip_name"] == "Clip 1"
    assert item["severity"] == "INFO"
    assert item["rule_name"] == "Audio content changed"
    assert item["message"]
    assert item["blocking"] is False
    assert item["flagged_position"] == item["corrected_position"]
    assert item["track"] == {"slot_id": 3, "name": "Audio 1"}
    assert item["record_in_timecode"] == "00:00:00:00"
    assert item["source_file"] == "clip_1.wav"
    assert item["flagged_audio_sha256"] != item["corrected_audio_sha256"]
    assert len(item["flagged_audio_sha256"]) == len(item["corrected_audio_sha256"]) == 64
    assert events[-1]["paired_clips"] == 3
    assert events[-1]["blocking_regressions"] == 0
    assert events[-1]["verdict"] == "PASS"
    assert compare(flagged, corrected) == events
