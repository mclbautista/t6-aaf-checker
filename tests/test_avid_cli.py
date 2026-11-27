"""Avid Media Composer export traits, modelled on Avid Media Composer 25.12 sound exports."""

import pytest

from synthetic_aaf import Timeline, edit_export
from test_check_cli import check
from test_levels_cli import findings


def avid_style(path, *, subclips=1):
    """Mark the sequence top level and add Avid's extra sub-clip compositions."""
    with edit_export(path) as aaf:
        identification = aaf.header["IdentificationList"].value[0]
        identification["CompanyName"].value = "Avid Technology, Inc."
        identification["ProductName"].value = "Avid Media Composer 25.12.0.58863"
        top = next(aaf.content.compositionmobs())
        top.usage = "Usage_TopLevel"
        used = {}
        for slot in top.slots:
            if slot.media_kind == "Sound":
                clip = next(c for c in slot.segment.components if hasattr(c, "mob"))
                used[slot.slot_id] = clip
        for number in range(subclips):
            sub = aaf.create.CompositionMob(f"Clip 2.new.{number + 2:02}")
            sub.usage = "Usage_SubClip"
            aaf.content.mobs.append(sub)
            source = used[4]
            slot = sub.create_empty_sequence_slot(
                top.slots[0].edit_rate, slot_id=1, media_kind="sound",
            )
            slot.segment.components.append(
                source.mob.create_source_clip(1, start=source.start, length=source.length)
            )
    return path


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_sub_clip_compositions_are_not_part_of_the_delivered_timeline(tmp_path, mode):
    path = Timeline().with_clip(2, sample_value=0).export(tmp_path / "avid.aaf", mode=mode)
    avid_style(path, subclips=3)

    events = check(path)

    silence = [item for item in findings(events) if item["rule_id"] == "digital_silence"]
    assert len(silence) == 1
    assert silence[0]["track"] == {"slot_id": 4, "name": "Audio 2"}
    progress = [event for event in events if event["type"] == "progress"][-1]
    assert progress["clips_total"] == 3


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_aaf_without_audio_clips_does_not_pass(tmp_path, mode):
    """A picture-only Avid export (grading AAF) must not earn a pass report."""
    path = Timeline().export(tmp_path / "picture_only.aaf", mode=mode)
    with edit_export(path) as aaf:
        for composition in aaf.content.compositionmobs():
            for slot in composition.slots:
                if slot.media_kind == "Sound":
                    slot.segment.components.clear()

    events = check(path)

    items = findings(events)
    assert [(item["rule_id"], item["severity"]) for item in items] == [
        ("no_audio_clips", "FAIL"),
    ]
    assert items[0]["fix_instruction"]
    assert events[-1]["verdict"] == "FAIL"


@pytest.mark.parametrize("company,product,accepted", [
    ("Avid Technology, Inc.", "Avid Media Composer 25.12.0.58863", True),
    ("AVID TECHNOLOGY, INC.", "avid media composer 21.12", True),
    ("Avid Technology, Inc.", "Pro Tools", False),
    ("Other Company", "Avid Media Composer", True),
    ("Other Company", "Media Composer", False),
])
def test_avid_media_composer_is_a_known_exporter(tmp_path, company, product, accepted):
    path = Timeline().export(tmp_path / "identity.aaf")
    with edit_export(path) as aaf:
        identification = aaf.header["IdentificationList"].value[0]
        identification["CompanyName"].value = company
        identification["ProductName"].value = product

    events = check(path)

    assert [item["rule_id"] for item in findings(events)] == (
        [] if accepted else ["unknown_exporter"]
    )
