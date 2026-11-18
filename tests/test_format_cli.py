"""Format mismatch behavior through the worker's command-line contract."""

import hashlib
import json
from pathlib import Path
import shutil
import struct

import numpy as np
import pytest
import soundfile

from synthetic_aaf import Timeline, edit_export, embedded_essence_by_name
from test_check_cli import check


def format_findings(events):
    return [event for event in events
            if event["type"] == "finding" and event["rule_id"] == "format_mismatch"]


def change_descriptor_rate(path, mode, descriptor_kind, rate):
    with edit_export(path) as aaf:
        mob = (embedded_essence_by_name(aaf)["clip_2.wav"].mob if mode == "embedded"
               else next(mob for mob in aaf.content.sourcemobs() if mob.name == "clip_2.wav"))
        if descriptor_kind == "wave":
            summary = bytearray(mob.descriptor["Summary"].value)
            struct.pack_into("<I", summary, 24, rate)
            mob.descriptor["Summary"].value = summary
        else:
            mob.descriptor["AudioSamplingRate"].value = rate


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("descriptor_kind", ["wave", "pcm"])
@pytest.mark.parametrize("changes,property_name,actual,expected", [
    pytest.param({"sample_rate": 44100}, "sample rate", "44100", "48000", id="44.1-kHz"),
    pytest.param({"bits": 16}, "bit depth", "16", "24", id="16-bit"),
    pytest.param({"channels": 2}, "channels", "2", "1", id="stereo"),
])
def test_wrong_format_fails_on_affected_clip(
    tmp_path, mode, descriptor_kind, changes, property_name, actual, expected,
):
    path = Timeline().with_clip(
        2, **changes, descriptor_kind=descriptor_kind,
    ).export(tmp_path / "rate.aaf", mode=mode)

    events = check(path)

    findings = format_findings(events)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["rule_name"] == "Format mismatch"
    assert finding["severity"] == "FAIL"
    assert finding["track"] == {"slot_id": 4, "name": "Audio 2"}
    assert finding["clip_name"] == "Clip 2"
    assert finding["source_file"] == "clip_2.wav"
    assert finding["record_in_timecode"] == "00:00:00:00"
    assert property_name in finding["message"].lower()
    assert actual in finding["message"]
    assert expected in finding["message"]
    assert "descriptor" in finding["message"].lower()
    assert "media" in finding["message"].lower()
    assert finding["fix_instruction"]
    assert events[-1]["verdict"] == "FAIL"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("descriptor_kind", ["wave", "pcm"])
def test_matching_baseline_preserves_inputs_and_has_no_format_findings(
    tmp_path, mode, descriptor_kind,
):
    timeline = Timeline()
    for number in range(1, 4):
        timeline = timeline.with_clip(number, descriptor_kind=descriptor_kind)
    path = timeline.export(tmp_path / "baseline.aaf", mode=mode)
    inputs = [path, *tmp_path.glob("baseline_media/*.wav")]
    before = {}
    for item in inputs:
        item.chmod(0o444)
        with item.open("rb") as stream:
            before[item] = hashlib.file_digest(stream, "sha256").hexdigest()

    events = check(path)

    assert format_findings(events) == []
    assert events[-1]["verdict"] == "PASS"
    progress = [event for event in events if event["type"] == "progress"]
    assert progress[-1]["bytes_read"] == (4_756_884 if mode == "embedded" else 0)
    for item in inputs:
        with item.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == before[item]


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("field,value,property_name", [
    ("sample_rate", 44100, "sample rate"),
    ("bits", 16, "bit depth"),
    ("channels", 2, "channels"),
    ("encoding", "FLOAT", "encoding"),
    ("container", "AIFF", "container"),
])
def test_expected_format_comes_from_bundled_config(
    tmp_path, monkeypatch, mode, field, value, property_name,
):
    path = Timeline().export(tmp_path / "config.aaf", mode=mode)
    assert format_findings(check(path)) == []
    # Run an isolated worker bundle so changing configuration never mutates
    # the installed worker or adds a per-run override to the CLI.
    bundle = tmp_path / "bundle"
    package = bundle / "aaf_qc_worker"
    shutil.copytree(Path(__file__).resolve().parents[1] / "src/aaf_qc_worker", package)
    config_path = package / "rules.json"
    config = json.loads(config_path.read_text())
    config["format_mismatch"]["expected"][field] = value
    config_path.write_text(json.dumps(config))
    monkeypatch.setenv("PYTHONPATH", str(bundle))

    events = check(path)

    findings = format_findings(events)
    assert [finding["clip_name"] for finding in findings] == ["Clip 1", "Clip 2", "Clip 3"]
    assert all(property_name in finding["message"].lower() for finding in findings)
    assert all(f"expected {value}" in finding["message"] for finding in findings)
    assert events[-1]["verdict"] == "FAIL"


def test_linked_non_wav_fails_even_when_pcm_properties_match(tmp_path):
    path = Timeline().export(tmp_path / "non_wav.aaf", mode="linked")
    media = tmp_path / "non_wav_media" / "clip_2.wav"
    soundfile.write(media, np.full(480, 0.25), 48000, format="AIFF", subtype="PCM_24")

    events = check(path)

    findings = format_findings(events)
    assert len(findings) == 1
    assert findings[0]["clip_name"] == "Clip 2"
    assert "container" in findings[0]["message"].lower()
    assert "AIFF" in findings[0]["message"]
    assert "WAV" in findings[0]["message"]
    assert events[-1]["verdict"] == "FAIL"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_non_pcm_wav_fails_once_and_names_encoding(tmp_path, mode):
    path = Timeline().with_clip(2, bits=32, wav_subtype="FLOAT").export(
        tmp_path / "float.aaf", mode=mode,
    )

    events = check(path)

    findings = format_findings(events)
    assert len(findings) == 1
    assert findings[0]["clip_name"] == "Clip 2"
    assert findings[0]["severity"] == "FAIL"
    assert "encoding" in findings[0]["message"].lower()
    assert "PCM" in findings[0]["message"]
    assert "descriptor" in findings[0]["message"].lower()
    assert "media" in findings[0]["message"].lower()
    assert events[-1]["verdict"] == "FAIL"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
@pytest.mark.parametrize("descriptor_kind", ["wave", "pcm"])
@pytest.mark.parametrize("descriptor_rate,media_rate", [(48000, 44100), (44100, 48000)])
def test_descriptor_media_disagreement_is_explicit(
    tmp_path, mode, descriptor_kind, descriptor_rate, media_rate,
):
    path = Timeline().with_clip(
        2, sample_rate=media_rate, descriptor_kind=descriptor_kind, chain_depth=2,
    ).export(tmp_path / "disagreement.aaf", mode=mode)
    change_descriptor_rate(path, mode, descriptor_kind, descriptor_rate)

    events = check(path)

    findings = format_findings(events)
    assert len(findings) == 1
    assert findings[0]["clip_name"] == "Clip 2"
    assert "disagree" in findings[0]["message"].lower()
    assert "sample rate" in findings[0]["message"].lower()
    assert f"descriptor {descriptor_rate}" in findings[0]["message"].lower()
    assert f"media header {media_rate}" in findings[0]["message"].lower()
    assert events[-1]["verdict"] == "FAIL"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_extensible_pcm_wave_is_a_matching_format(tmp_path, mode):
    path = Timeline().with_clip(2, wav_container="WAVEX").export(
        tmp_path / "extensible.aaf", mode=mode,
    )

    events = check(path)

    assert format_findings(events) == []
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("source", ["descriptor", "media"])
def test_unreadable_format_is_a_clip_failure_and_later_clips_are_checked(tmp_path, source):
    path = Timeline().with_clip(3, channels=2).export(tmp_path / "bad_header.aaf", mode="linked")
    if source == "descriptor":
        with edit_export(path) as aaf:
            mob = next(mob for mob in aaf.content.sourcemobs() if mob.name == "clip_2.wav")
            mob.descriptor["Summary"].value = bytearray(b"not a WAV header")
    else:
        (tmp_path / "bad_header_media/clip_2.wav").write_bytes(b"not an audio file")

    events = check(path)

    findings = format_findings(events)
    assert [finding["clip_name"] for finding in findings] == ["Clip 2", "Clip 3"]
    assert source in findings[0]["message"].lower()
    assert "cannot" in findings[0]["message"].lower()
    assert events[-1]["verdict"] == "FAIL"


def test_format_header_is_checked_even_when_later_audio_is_truncated(tmp_path):
    path = (Timeline()
            .with_clip(1, track_slot=7, channels=2, record_start=48)
            .with_clip(2, track_slot=6, sample_rate=44100, truncate_bytes=3)
            .with_clip(3, track_slot=3, bits=16)
            .export(tmp_path / "multiple.aaf"))
    change_descriptor_rate(path, "embedded", "wave", 48000)

    events = check(path)

    findings = [event for event in events if event["type"] == "finding"]
    assert [(finding["track"]["slot_id"], finding["rule_id"]) for finding in findings] == [
        (3, "format_mismatch"),
        (6, "embedded_audio_unreadable"), (6, "format_mismatch"),
        (7, "format_mismatch"),
    ]
    assert "disagree" in findings[2]["message"].lower()
    assert events == check(path)
