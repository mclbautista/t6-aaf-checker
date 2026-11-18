"""Silence and peak rules observed through the worker's CLI contract."""

import pytest

from synthetic_aaf import Timeline
from test_check_cli import check


def findings(events):
    return [event for event in events if event["type"] == "finding"]


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("pulse,value,expected_rule", [
    (240239, 2**14, "digital_silence"),  # one sample before the head handle
    (240240, 2**14, None),  # first included sample
    (768767, 2**14, None),  # last included sample
    (768768, 2**14, "digital_silence"),  # exclusive end of the tail handle
    (480480, 149, "low_level"),  # quiet pulse at the used in-point
])
def test_sample_rate_mob_slots_scan_the_correct_samples(
    tmp_path, mode, pulse, value, expected_rule,
):
    from synthetic_aaf import Clip

    path = Timeline(clips=(Clip(
        "Window clip", "window.wav", 3, head=240, tail=240,
        sample_value=0, sample_points=((pulse, value),),
    ),)).export(tmp_path / "sample_slots.aaf", mode=mode)

    events = check(path)

    # 240 sequence frames = 480,480 samples. With 120-frame handles,
    # the exact scan is [240,240, 768,768) at 48 kHz.
    levels = [item for item in findings(events)
              if item["rule_id"] in ("digital_silence", "low_level")]
    assert [item["rule_id"] for item in levels] == ([expected_rule] if expected_rule else [])
    assert findings(events) == levels
    assert events[-1]["verdict"] == ("FAIL" if expected_rule == "digital_silence" else "PASS")


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


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("pulse,expected", [
    (0, "FAIL"),
    (240239, "FAIL"),  # one sample before the head handle
    (768768, "FAIL"),  # exclusive end of the tail handle
    (240240, "PASS"),  # first head-handle sample
    (768767, "PASS"),  # last tail-handle sample
    (480480, "PASS"),  # used range starts here
    (349525, "PASS"),  # a 24-bit sample straddling the default read boundary
])
def test_only_used_range_and_up_to_120_frame_handles_count(tmp_path, mode, pulse, expected):
    path = Timeline().with_clip(
        2, head=240, tail=240, sample_value=0, sample_points=((pulse, 2**14),),
    ).export(tmp_path / "window.aaf", mode=mode)

    events = check(path)

    assert events[-1]["verdict"] == expected
    assert [item["rule_id"] for item in findings(events)] == (
        ["digital_silence"] if expected == "FAIL" else []
    )


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("chain_depth", [0, 2])
@pytest.mark.parametrize("pulse,expected", [(240240, "FAIL"), (948947, "PASS")])
def test_master_to_file_offset_uses_each_slots_rate_and_ignores_physical_start(
    tmp_path, mode, chain_depth, pulse, expected,
):
    from synthetic_aaf import edit_export

    path = Timeline().with_clip(
        2, head=240, tail=240, chain_depth=chain_depth,
        sample_value=0, sample_points=((pulse, 2**14),),
    ).export(tmp_path / "offset.aaf", mode=mode)
    with edit_export(path) as aaf:
        master = next(mob for mob in aaf.content.mastermobs() if mob.name == "Clip 2")
        reference = master.slot_at(1).segment.components[0]
        for _ in range(chain_depth):
            reference.start = 15 * 2002
            reference = reference.slot.segment.components[0]
        # Total offset = 90 sequence frames; the last hop uses sample units.
        reference.start = (90 - 15 * chain_depth) * 2002
        # This is the upstream physical time, beyond the delivered file extent.
        reference.slot.segment.start = 9_000_000

    events = check(path)

    assert events[-1]["verdict"] == expected
    assert [item["rule_id"] for item in findings(events)] == (
        ["digital_silence"] if expected == "FAIL" else []
    )


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
def test_bundled_handle_length_changes_scan_window(tmp_path, monkeypatch, mode):
    path = Timeline().with_clip(
        2, sample_value=0, sample_points=((240239, 2**14),),
    ).export(tmp_path / "handles.aaf", mode=mode)
    assert findings(check(path)) == []
    configure_bundle(tmp_path, monkeypatch, handle_frames=0)

    events = check(path)

    assert [item["rule_id"] for item in findings(events)] == ["digital_silence"]
    assert events[-1]["verdict"] == "FAIL"


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


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_peak_ignores_loud_audio_outside_handles(tmp_path, mode):
    path = Timeline().with_clip(
        2, head=240, tail=240, sample_value=149,
        sample_points=((240239, -(2**23)), (768768, 2**22)),
    ).export(tmp_path / "quiet_window.aaf", mode=mode)

    events = check(path)

    assert [item["rule_id"] for item in findings(events)] == ["low_level"]
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("pulse,expected", [(1601, "FAIL"), (1602, "PASS"), (424424, "FAIL")])
def test_fractional_sample_boundaries_use_exact_slot_rate(tmp_path, mode, pulse, expected):
    from synthetic_aaf import edit_export

    path = Timeline().with_clip(
        2, head=121, sample_value=0, sample_points=((pulse, 2**14),),
    ).export(tmp_path / "fractional.aaf", mode=mode)
    with edit_export(path) as aaf:
        composition = next(aaf.content.compositionmobs())
        composition.slot_at(4).edit_rate = "30000/1001"
    # 48 kHz at 30000/1001 gives 1601.6 samples/edit unit. The scan is
    # [1601.6, 424424), so sample 1601 is excluded and sample 1602 is included.

    events = check(path)

    assert events[-1]["verdict"] == expected
    assert [item["rule_id"] for item in findings(events)] == (
        ["digital_silence"] if expected == "FAIL" else []
    )


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_shared_file_occurrences_keep_distinct_windows_and_deterministic_order(tmp_path, mode):
    from synthetic_aaf import edit_export

    path = (Timeline().with_clip(
        1, head=240, tail=480, sample_value=0, sample_points=((240240, 149),),
    ).with_clip(3, sample_value=149).export(tmp_path / "shared_windows.aaf", mode=mode))
    with edit_export(path) as aaf:
        composition = next(aaf.content.compositionmobs())
        sequence = composition.slot_at(3).segment
        master = sequence.components[0].mob
        sequence.components.append(master.create_source_clip(1, start=600, length=24))

    events = check(path)

    assert [(item["track"]["slot_id"], item["record_in_timecode"], item["rule_id"])
            for item in findings(events)] == [
        (3, "00:00:00:00", "low_level"),
        (3, "00:00:01:00", "digital_silence"),
        (5, "00:00:00:00", "low_level"),
    ]
    assert events[-1]["verdict"] == "FAIL"
    assert events == check(path)
    progress = [event for event in events if event["type"] == "progress"]
    # One full embedded pass per occurrence, including two uses of the file.
    assert progress[-1]["bytes_read"] == (12_110_712 if mode == "embedded" else 0)
