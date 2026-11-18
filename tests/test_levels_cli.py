"""Silence and peak rules observed through the worker's CLI contract."""

import pytest

from synthetic_aaf import Timeline
from test_check_cli import check


def findings(events):
    return [event for event in events if event["type"] == "finding"]





@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_all_zero_clip_fails_digital_silence_only(tmp_path, mode):
    path = Timeline().with_clip(2, sample_value=0).export(
        tmp_path / "zero.aaf", mode=mode,
    )

    events = check(path)

    assert events[-1]["verdict"] == "FAIL"
    assert [(item["rule_id"], item["severity"]) for item in findings(events)] == [
        ("digital_silence", "FAIL"),
    ]
    item = findings(events)[0]
    assert item["rule_name"] == "Digital silence"
    assert item["clip_name"] == "Clip 2"
    assert item["source_file"] == "clip_2.wav"
    assert item["track"] == {"slot_id": 4, "name": "Audio 2"}
    assert item["record_in_timecode"] == "00:00:00:00"
    assert item["message"]
    assert item["fix_instruction"]


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("sample_value,expected", [
    (149, [("low_level", "INFO")]),  # approximately -95 dBFS in 24-bit PCM
    (-149, [("low_level", "INFO")]),
    (298, []),  # approximately -89 dBFS
])
def test_quiet_nonzero_audio_is_information_and_stays_pass(
    tmp_path, mode, sample_value, expected,
):
    path = Timeline().with_clip(2, sample_value=sample_value).export(
        tmp_path / "quiet.aaf", mode=mode,
    )

    events = check(path)

    assert [(item["rule_id"], item["severity"]) for item in findings(events)] == expected
    assert events[-1]["verdict"] == "PASS"
    if expected:
        item = findings(events)[0]
        assert item["rule_name"] == "Low level"
        assert item["clip_name"] == "Clip 2"
        assert "-95" in item["message"]
        assert "-90" in item["message"]








def configure_bundle(tmp_path, monkeypatch, **changes):
    import json
    from pathlib import Path
    import shutil

    bundle = tmp_path / "bundle"
    package = bundle / "aaf_qc_worker"
    shutil.copytree(Path(__file__).resolve().parents[1] / "src/aaf_qc_worker", package)
    config_path = package / "rules.json"
    config = json.loads(config_path.read_text())
    for key, value in changes.items():
        if isinstance(value, dict):
            config[key].update(value)
        else:
            config[key] = value
    config_path.write_text(json.dumps(config))
    monkeypatch.setenv("PYTHONPATH", str(bundle))


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_bundled_floor_changes_low_level_outcome(tmp_path, monkeypatch, mode):
    path = Timeline().with_clip(2, sample_value=149).export(
        tmp_path / "floor.aaf", mode=mode,
    )
    assert [item["rule_id"] for item in findings(check(path))] == ["low_level"]
    configure_bundle(tmp_path, monkeypatch, low_level={"floor_dbfs": -96})

    events = check(path)

    assert findings(events) == []
    assert events[-1]["verdict"] == "PASS"





@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("sample_value,expected", [
    (2**22, ["low_level"]),
    (2**22 + 1, []),
    (-(2**23), []),
])
def test_peak_floor_is_inclusive_and_full_scale_is_signed_bit_depth(
    tmp_path, monkeypatch, mode, sample_value, expected,
):
    path = Timeline().with_clip(2, sample_value=sample_value).export(
        tmp_path / "boundary.aaf", mode=mode,
    )
    # Exactly half full scale: the sample at the floor is included, the next
    # integer sample is above it. Other clips are below this configured floor.
    configure_bundle(tmp_path, monkeypatch, low_level={"floor_dbfs": -6.020599913279624})

    events = check(path)

    assert [item["rule_id"] for item in findings(events) if item["clip_name"] == "Clip 2"] == expected
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("head,tail,pulse", [(0, 0, 0), (0, 0, 48047), (1, 1, 52051)])
def test_handles_are_clipped_to_media_extent(tmp_path, mode, head, tail, pulse):
    path = Timeline().with_clip(
        2, head=head, tail=tail, sample_value=0, sample_points=((pulse, 2**14),),
    ).export(tmp_path / "short_media.aaf", mode=mode)

    events = check(path)

    assert [(item["rule_id"], item["severity"]) for item in findings(events)] == [
        ("short_handles", "WARN"),
    ]
    assert events[-1]["verdict"] == "PASS"


# "Used range plus handles" is the whole delivered media file (owner decision,
# 2026-11-18): the embedded stream or the linked WAV, from first to last sample.
WHOLE_FILE = 1_009_008  # (240 + 24 + 240) frames x 2,002 samples/frame


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("pulse", [0, 240239, 480480, 768768, WHOLE_FILE - 1])
def test_audio_anywhere_in_the_delivered_file_is_not_silent(tmp_path, mode, pulse):
    path = Timeline().with_clip(
        2, head=240, tail=240, sample_value=0, sample_points=((pulse, 2**14),),
    ).export(tmp_path / "anywhere.aaf", mode=mode)

    events = check(path)

    assert findings(events) == []
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_whole_file_silence_fails_even_with_long_handles(tmp_path, mode):
    path = Timeline().with_clip(2, head=240, tail=240, sample_value=0).export(
        tmp_path / "silent.aaf", mode=mode,
    )

    events = check(path)

    assert [item["rule_id"] for item in findings(events)] == ["digital_silence"]
    assert events[-1]["verdict"] == "FAIL"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("pulse", [0, WHOLE_FILE - 1])
def test_loud_audio_far_outside_the_used_range_lifts_the_peak(tmp_path, mode, pulse):
    path = Timeline().with_clip(
        2, head=240, tail=240, sample_value=149, sample_points=((pulse, 2**22),),
    ).export(tmp_path / "loud_edge.aaf", mode=mode)

    events = check(path)

    assert findings(events) == []
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_quiet_whole_file_is_low_level(tmp_path, mode):
    path = Timeline().with_clip(2, head=240, tail=240, sample_value=149).export(
        tmp_path / "quiet.aaf", mode=mode,
    )

    events = check(path)

    assert [item["rule_id"] for item in findings(events)] == ["low_level"]
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_handle_length_setting_does_not_change_the_scan(tmp_path, monkeypatch, mode):
    path = Timeline().with_clip(
        2, sample_value=0, sample_points=((0, 2**14),),
    ).export(tmp_path / "handles.aaf", mode=mode)
    configure_bundle(tmp_path, monkeypatch, handle_frames=0)

    events = check(path)

    assert [item["rule_id"] for item in findings(events)] == []
    assert events[-1]["verdict"] == "PASS"

