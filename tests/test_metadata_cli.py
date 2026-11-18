"""Field-recorder metadata warnings observed through the worker CLI."""

import pytest
from aaf2.essence import TapeDescriptor

from synthetic_aaf import Clip, Timeline, edit_export
from test_check_cli import check, without_report_paths
from test_levels_cli import configure_bundle, findings


@pytest.mark.parametrize("mode", ["linked", "embedded"])
def test_all_blank_fields_give_one_warning_and_keep_pass(tmp_path, mode):
    path = Timeline(clips=(Clip(
        "Metadata clip", "metadata.wav", 3, record_start=24,
        tape=" \t", scene="\n ", take="",
    ),)).export(tmp_path / "blank.aaf", mode=mode)

    events = check(path)

    items = findings(events)
    assert len(items) == 1
    item = items[0]
    assert item["rule_id"] == "missing_field_recorder_metadata"
    assert item["rule_name"] == "Missing field-recorder metadata"
    assert item["severity"] == "WARN"
    assert item["message"] == "Missing field-recorder metadata: tape, scene, take."
    assert item["aaf_path"] == str(path)
    assert item["track"] == {"slot_id": 3, "name": "Audio 1"}
    assert item["record_in_timecode"] == "00:00:01:00"
    assert item["clip_name"] == "Metadata clip"
    assert item["source_file"] == "metadata.wav"
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["linked", "embedded"])
@pytest.mark.parametrize("chain_depth", [0, 2])
def test_no_ixml_uses_tape_mob_and_master_comments(tmp_path, mode, chain_depth):
    path = Timeline(clips=(Clip(
        "Metadata clip", "metadata.wav", 3, chain_depth=chain_depth,
    ),)).export(tmp_path / "fallback.aaf", mode=mode)
    with edit_export(path) as aaf:
        master = next(aaf.content.mastermobs())
        del master["UserComments"]
        master.comments["sCeNe"] = "  Scene 2  "
        master.comments["TAKE"] = "  0  "

    events = check(path)

    assert findings(events) == []
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("scene", [" \t\n", "Scene from WAV"])
@pytest.mark.parametrize("mode", ["linked", "embedded"])
def test_ixml_supplies_metadata_and_names_only_blank_scene(tmp_path, scene, mode):
    path = Timeline(clips=(Clip(
        "Metadata clip", "metadata.wav", 3, tape="", scene="", take="",
        ixml=(("TAPE", "WAV roll"), ("SCENE", scene), ("TAKE", "3")),
    ),)).export(tmp_path / "ixml.aaf", mode=mode)

    events = check(path)

    items = findings(events)
    assert [item["message"] for item in items] == (
        ["Missing field-recorder metadata: scene."] if not scene.strip() else []
    )
    assert all(item["severity"] == "WARN" for item in items)
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["linked", "embedded"])
@pytest.mark.parametrize("source", ["ixml", "master_mob_comment"])
def test_bundled_field_map_changes_where_values_are_read(tmp_path, monkeypatch, mode, source):
    alternate = (("ROLL", "Alternate roll"), ("SHOT", "Alternate scene"), ("SLATE_TAKE", "2"))
    path = Timeline(clips=(Clip(
        "Metadata clip", "metadata.wav", 3, tape="", scene="", take="", ixml=alternate,
    ),)).export(tmp_path / "mapped.aaf", mode=mode)
    with edit_export(path) as aaf:
        master = next(aaf.content.mastermobs())
        for key, value in alternate:
            master.comments[key.lower()] = value
    assert [item["message"] for item in findings(check(path))] == [
        "Missing field-recorder metadata: tape, scene, take.",
    ]
    configure_bundle(tmp_path, monkeypatch, missing_field_recorder_metadata={
        "field_map": {
            "tape": [{"source": source, "key": "ROLL"}],
            "scene": [{"source": source, "key": "SHOT"}],
            "take": [{"source": source, "key": "SLATE_TAKE"}],
        },
    })

    events = check(path)

    assert findings(events) == []
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["linked", "embedded"])
@pytest.mark.parametrize("ixml", [None, (), (("TAPE", " \t"), ("SCENE", ""), ("TAKE", "\n")),
                                  b"<BWFXML><SCENE>partial</SCENE>"])
def test_absent_blank_or_malformed_ixml_falls_back_per_field(tmp_path, mode, ixml):
    path = Timeline(clips=(Clip(
        "Metadata clip", "metadata.wav", 3, ixml=ixml,
    ),)).export(tmp_path / "fallback.aaf", mode=mode)

    events = check(path)

    assert findings(events) == []
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["linked", "embedded"])
@pytest.mark.parametrize("with_ixml", [False, True])
def test_absent_tape_mob_and_comments_use_only_wav_metadata(tmp_path, mode, with_ixml):
    path = Timeline(clips=(Clip(
        "Metadata clip", "metadata.wav", 3,
        ixml=(("TAPE", "WAV roll"), ("SCENE", "WAV scene"), ("TAKE", "2"))
        if with_ixml else None,
    ),)).export(tmp_path / "no_tape.aaf", mode=mode)
    with edit_export(path) as aaf:
        del next(aaf.content.mastermobs())["UserComments"]
        for mob in list(aaf.content.sourcemobs()):
            if isinstance(mob.descriptor, TapeDescriptor):
                aaf.content.mobs.pop(mob.mob_id)

    events = check(path)

    assert [item["message"] for item in findings(events)] == (
        [] if with_ixml else ["Missing field-recorder metadata: tape, scene, take."]
    )
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["linked", "embedded"])
def test_ixml_after_odd_data_spans_chunks_and_keeps_one_embedded_pass(tmp_path, monkeypatch, mode):
    timeline = Timeline(clips=(Clip(
        "Metadata clip", "metadata.wav", 3, tape="", scene="", take="", odd_unpadded=True,
        ixml=(("NOTE", "x" * 70001), ("TAPE", "Roll"), ("SCENE", "Scène"), ("TAKE", "0")),
    ),))
    path = timeline.export(tmp_path / "chunks.aaf", mode=mode)
    reference = timeline.export(tmp_path / "reference.aaf", mode="linked")
    expected_bytes = (reference.parent / "reference_media/metadata.wav").stat().st_size
    configure_bundle(tmp_path, monkeypatch, audio_reader={"chunk_bytes": 65536})

    events = check(path)

    assert findings(events) == []
    assert events[-1]["verdict"] == "PASS"
    progress = [event for event in events if event["type"] == "progress"]
    assert progress[-1]["bytes_read"] == (expected_bytes if mode == "embedded" else 0)
    assert all(0 <= after["bytes_read"] - before["bytes_read"] <= 65536
               for before, after in zip(progress, progress[1:]))


@pytest.mark.parametrize("mode", ["linked", "embedded"])
def test_metadata_is_per_occurrence_ordered_and_inputs_stay_read_only(tmp_path, mode):
    import hashlib

    path = Timeline(clips=(
        Clip("Later clip", "later.wav", 5, record_start=48, scene=""),
        Clip("Shared clip", "shared.wav", 3, head=60, tail=240, sample_value=149,
             tape="", scene="", take=""),
    )).export(tmp_path / "shared.aaf", mode=mode)
    with edit_export(path) as aaf:
        sequence = next(aaf.content.compositionmobs()).slot_at(3).segment
        master = sequence.components[0].mob
        sequence.components.pop(1)
        sequence.components.append(master.create_source_clip(1, start=300, length=24))
    inputs = [path, *tmp_path.glob("shared_media/*.wav")]
    before = {}
    for item in inputs:
        item.chmod(0o444)
        with item.open("rb") as stream:
            before[item] = hashlib.file_digest(stream, "sha256").hexdigest()

    events = check(path)

    assert [(item["track"]["slot_id"], item["record_in_timecode"], item["rule_id"])
            for item in findings(events)] == [
        (3, "00:00:00:00", "low_level"),
        (3, "00:00:00:00", "missing_field_recorder_metadata"),
        (3, "00:00:00:00", "short_handles"),
        (3, "00:00:01:00", "low_level"),
        (3, "00:00:01:00", "missing_field_recorder_metadata"),
        (3, "00:00:01:00", "short_handles"),
        (5, "00:00:02:00", "missing_field_recorder_metadata"),
    ]
    assert events[-1]["verdict"] == "PASS"
    assert without_report_paths(events) == without_report_paths(check(path))
    for item in inputs:
        with item.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == before[item]
