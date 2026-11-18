"""Fixture capabilities exercised only through the worker's CLI contract."""

import pytest

from test_check_cli import check


def test_clean_embedded_export_passes(tmp_path):
    from synthetic_aaf import Timeline

    path = Timeline().export(tmp_path / "embedded.aaf")

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    assert not [event for event in events if event["type"] == "finding"]


def test_clean_linked_export_passes(tmp_path):
    from synthetic_aaf import Timeline

    path = Timeline().export(tmp_path / "linked.aaf", mode="linked")

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    assert not [event for event in events if event["type"] == "finding"]


def test_declarative_clip_variant_and_counterpart_are_readable(tmp_path):
    from synthetic_aaf import Timeline

    timeline = Timeline()
    stereo = timeline.with_clip(3, channels=2)
    mono = stereo.with_clip(3, channels=1)

    for name, variant in (("stereo", stereo), ("mono", mono)):
        events = check(variant.export(tmp_path / f"{name}.aaf"))

        # Format checking belongs to its rule ticket. Here both must be readable.
        assert events[-1]["verdict"] in ("PASS", "FAIL")


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_same_timeline_can_be_exported_twice(tmp_path, mode):
    from synthetic_aaf import Timeline

    flagged, corrected = Timeline().export_pair(
        tmp_path, flagged_mode=mode, corrected_mode=mode
    )

    first = check(flagged)
    second = check(corrected)

    assert first[-1]["verdict"] == second[-1]["verdict"] == "PASS"
    assert not [event for event in first + second if event["type"] == "finding"]


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_truncated_audio_variant_preserves_readable_aaf_container(tmp_path, mode):
    from synthetic_aaf import Timeline

    path = Timeline().with_clip(3, truncate_bytes=3).export(
        tmp_path / "truncated.aaf", mode=mode
    )

    events = check(path)

    # The unreadable-audio rule is a later ticket; the container remains valid.
    assert events[-1]["verdict"] in ("PASS", "FAIL")


def test_header_patch_copies_clean_export_to_corrupt_counterpart(tmp_path):
    from synthetic_aaf import Timeline, patch_header

    green = Timeline().export(tmp_path / "green.aaf")
    red = patch_header(green, output=tmp_path / "red.aaf", major_version=4)

    red_events = check(red)
    green_events = check(green)

    assert red_events[-1]["verdict"] == "FAIL"
    findings = [event for event in red_events if event["type"] == "finding"]
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "container_corrupt"
    assert findings[0]["fix_instruction"] == "Export with linked media."
    assert green_events[-1]["verdict"] == "PASS"
    assert not [event for event in green_events if event["type"] == "finding"]
