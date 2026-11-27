"""Digital silence severity by length and audible sibling (editors' request, 2026-11-27)."""

import pytest

from synthetic_aaf import Clip, Timeline
from test_check_cli import check
from test_compare_cli import compare, diff_items
from test_levels_cli import findings


def rules(events):
    return [(item["rule_id"], item["severity"], item["track"]["slot_id"])
            for item in findings(events)]


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("frames,expected", [
    (71, ("digital_silence", "WARN")),   # 2.961 s used: under three seconds
    (72, ("digital_silence", "FAIL")),   # 3.003 s used: three seconds or more
])
def test_zero_clip_without_sibling_is_warn_under_three_seconds(tmp_path, mode, frames, expected):
    path = Timeline().with_clip(2, sample_value=0, duration=frames).export(
        tmp_path / "zero.aaf", mode=mode,
    )

    events = check(path)

    assert [item[:2] for item in rules(events)] == [expected]
    assert events[-1]["verdict"] == ("FAIL" if expected[1] == "FAIL" else "PASS")


def sibling_timeline(**silent):
    return Timeline(clips=(
        Clip("Recording", "ch1.wav", 3, duration=12),
        Clip("Recording", "ch3.wav", 4, duration=12, **{"sample_value": 0, **silent}),
    ))


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_zero_channel_beside_audible_channel_is_info_empty_channel(tmp_path, mode):
    path = sibling_timeline().export(tmp_path / "sibling.aaf", mode=mode)

    events = check(path)

    assert rules(events) == [("empty_channel", "INFO", 4)]
    item = findings(events)[0]
    assert item["rule_name"] == "Empty channel"
    assert item["clip_name"] == "Recording"
    assert item["source_file"] == "ch3.wav"
    assert events[-1]["verdict"] == "PASS"


def test_long_zero_channel_beside_audible_channel_is_still_empty_channel(tmp_path):
    path = Timeline(clips=(
        Clip("Recording", "ch1.wav", 3, duration=72),
        Clip("Recording", "ch3.wav", 4, sample_value=0, duration=72),
    )).export(tmp_path / "long.aaf")

    events = check(path)

    assert rules(events) == [("empty_channel", "INFO", 4)]
    assert events[-1]["verdict"] == "PASS"


def test_sibling_below_the_low_level_floor_is_not_audible(tmp_path):
    # One 24-bit LSB is about -138 dBFS, under the -90 dBFS floor.
    path = Timeline(clips=(
        Clip("Recording", "ch1.wav", 3, sample_value=1, duration=72),
        Clip("Recording", "ch3.wav", 4, sample_value=0, duration=72),
    )).export(tmp_path / "quiet_sibling.aaf")

    events = check(path)

    assert sorted(rules(events)) == [
        ("digital_silence", "FAIL", 4), ("low_level", "INFO", 3),
    ]
    assert events[-1]["verdict"] == "FAIL"


def test_all_channels_silent_is_not_an_empty_channel(tmp_path):
    path = Timeline(clips=(
        Clip("Recording", "ch1.wav", 3, sample_value=0, duration=72),
        Clip("Recording", "ch3.wav", 4, sample_value=0, duration=72),
    )).export(tmp_path / "all_zero.aaf")

    events = check(path)

    assert sorted(rules(events)) == [
        ("digital_silence", "FAIL", 3), ("digital_silence", "FAIL", 4),
    ]


def test_audible_clip_at_another_position_is_not_a_sibling(tmp_path):
    path = Timeline(clips=(
        Clip("Recording", "ch1.wav", 3, duration=72),
        Clip("Recording", "ch3.wav", 4, sample_value=0, duration=72, record_start=48),
    )).export(tmp_path / "elsewhere.aaf")

    events = check(path)

    assert rules(events) == [("digital_silence", "FAIL", 4)]


def test_audible_clip_with_another_name_is_not_a_sibling(tmp_path):
    path = Timeline(clips=(
        Clip("Dialogue", "ch1.wav", 3, duration=72),
        Clip("Recording", "ch3.wav", 4, sample_value=0, duration=72),
    )).export(tmp_path / "other_name.aaf")

    events = check(path)

    assert rules(events) == [("digital_silence", "FAIL", 4)]


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_missing_metadata_is_info_without_field_recorder_chunks(tmp_path, mode):
    path = Timeline(clips=(Clip(
        "Stock music", "stock.wav", 3, tape="", scene="", take="", bext=False,
    ),)).export(tmp_path / "stock.aaf", mode=mode)

    events = check(path)

    by_rule = {item["rule_id"]: item["severity"] for item in findings(events)}
    assert by_rule["missing_field_recorder_metadata"] == "INFO"
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_missing_metadata_stays_warn_when_the_wav_has_an_ixml_chunk(tmp_path, mode):
    path = Timeline(clips=(Clip(
        "Recorded", "recorded.wav", 3, tape="", scene="", take="", bext=False,
        ixml=(("NOTE", "kept"),),
    ),)).export(tmp_path / "ixml.aaf", mode=mode)

    events = check(path)

    by_rule = {item["rule_id"]: item["severity"] for item in findings(events)}
    assert by_rule["missing_field_recorder_metadata"] == "WARN"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_clip_that_goes_silent_in_the_corrected_export_is_blocking(tmp_path, mode):
    flagged = sibling_timeline(sample_value=2**14).export(tmp_path / "flagged.aaf", mode=mode)
    corrected = sibling_timeline().export(tmp_path / "corrected.aaf", mode=mode)

    events = compare(flagged, corrected)

    lost = diff_items(events, "audio_went_silent")
    assert len(lost) == 1
    assert lost[0]["blocking"] is True
    assert lost[0]["clip_name"] == "Recording"
    assert lost[0]["track"] == {"slot_id": 4, "name": "Audio 2"}
    assert events[-1]["verdict"] == "FAIL"
    assert events[-1]["blocking_regressions"] == 1


def test_clip_silent_in_both_exports_is_not_a_regression(tmp_path):
    flagged = sibling_timeline().export(tmp_path / "flagged.aaf")
    corrected = sibling_timeline().export(tmp_path / "corrected.aaf")

    events = compare(flagged, corrected)

    assert diff_items(events, "audio_went_silent") == []
    assert events[-1]["verdict"] == "PASS"
