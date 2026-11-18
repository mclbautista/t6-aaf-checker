"""File-level exporter and CFB notes observed through the worker CLI."""

from datetime import datetime
import hashlib
import struct

import pytest

from synthetic_aaf import Timeline, edit_export
from test_check_cli import check
from test_levels_cli import findings


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_non_premiere_exporter_warns_once_and_keeps_pass(tmp_path, mode):
    path = Timeline().export(tmp_path / "other_exporter.aaf", mode=mode)
    with edit_export(path) as aaf:
        identification = aaf.header["IdentificationList"].value[0]
        identification["ProductName"].value = "Synthetic Editor"
        identification["CompanyName"].value = "Synthetic Company"

    events = check(path)

    items = findings(events)
    assert len(items) == 1
    item = items[0]
    assert item["rule_id"] == "unknown_exporter"
    assert item["rule_name"] == "Unknown exporter"
    assert item["severity"] == "WARN"
    assert item["aaf_path"] == str(path)
    assert item["track"] is None
    assert item["record_in_timecode"] is None
    assert item["clip_name"] is None
    assert item["source_file"] is None
    assert "Synthetic Editor" in item["message"]
    assert "Synthetic Company" in item["message"]
    assert item["fix_instruction"] is None
    assert events[-1]["verdict"] == "PASS"
    assert events[-2]["type"] == "finding"
    assert [event for event in events if event["type"] == "progress"][-1]["clips_done"] == 3


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_premiere_baseline_has_no_exporter_warning(tmp_path, mode):
    path = Timeline().export(tmp_path / "premiere.aaf", mode=mode)

    events = check(path)

    assert findings(events) == []
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("company,product,accepted", [
    ("Adobe", "Premiere Pro", True),
    ("ADOBE INC.", "premiere pro 26.2.2", True),
    ("Other Company", "ADOBE PREMIERE PRO", True),
    ("Other Company", "Premiere Pro", False),
    ("Adobe", "Other Editor", False),
])
def test_premiere_identity_matches_product_and_adobe_case_insensitively(
    tmp_path, company, product, accepted,
):
    path = Timeline().export(tmp_path / "identity.aaf")
    with edit_export(path) as aaf:
        identification = aaf.header["IdentificationList"].value[0]
        identification["CompanyName"].value = company
        identification["ProductName"].value = product
        identification["ProductVersionString"].value = "26.2.2"
        identification["Platform"].value = "AAFSDK (MacOS X)"

    events = check(path)

    assert [item["rule_id"] for item in findings(events)] == (
        [] if accepted else ["unknown_exporter"]
    )
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("sector_size", [512, 4096])
@pytest.mark.parametrize("difference", [-1, 1])
def test_fat_count_disagreement_is_one_file_note_and_keeps_pass(
    tmp_path, sector_size, difference,
):
    path = Timeline().export(tmp_path / "fat_mismatch.aaf", sector_size=sector_size)
    with path.open("r+b") as stream:
        stream.seek(44)
        actual, = struct.unpack("<I", stream.read(4))
        declared = actual + difference
        stream.seek(44)
        stream.write(struct.pack("<I", declared))

    events = check(path)

    items = findings(events)
    assert len(items) == 1
    item = items[0]
    assert item["rule_id"] == "fat_count_mismatch"
    assert item["rule_name"] == "FAT count mismatch"
    assert item["severity"] == "INFO"
    assert item["aaf_path"] == str(path)
    assert item["track"] is None
    assert item["record_in_timecode"] is None
    assert item["clip_name"] is None
    assert item["source_file"] is None
    assert item["message"] == (
        f"CFB header declares {declared} FAT sectors; DIFAT lists {actual}."
    )
    assert item["fix_instruction"] is None
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("sector_size", [512, 4096])
def test_consistent_fat_header_has_no_note(tmp_path, sector_size):
    path = Timeline().export(tmp_path / "consistent.aaf", sector_size=sector_size)

    events = check(path)

    assert findings(events) == []
    assert events[-1]["verdict"] == "PASS"


def add_difat_chain(path, *, difference=0, move_fat=True):
    """Add nonadjacent DIFAT sectors, optionally moving two FAT references there."""
    with path.open("r+b") as stream:
        header = stream.read(512)
        sector_size = 1 << struct.unpack_from("<H", header, 30)[0]
        declared, = struct.unpack_from("<I", header, 44)
        entries = list(struct.unpack_from("<109I", header, 76))
        occupied = [index for index, sid in enumerate(entries) if sid < 0xFFFFFFFA]
        moved = [entries[index] for index in occupied[-2:]] if move_fat else [0xFFFFFFFF] * 2
        assert len(moved) == 2
        if move_fat:
            for index in occupied[-2:]:
                entries[index] = 0xFFFFFFFF
        stream.seek(76)
        stream.write(struct.pack("<109I", *entries))
        stream.seek(0, 2)
        first = stream.tell() // sector_size - 1
        for index, fat_sid in enumerate(moved):
            sid = first + index * 2
            next_sid = first + 2 if index == 0 else 0xFFFFFFFE
            words = [fat_sid] + [0xFFFFFFFF] * (sector_size // 4 - 2) + [next_sid]
            stream.seek((sid + 1) * sector_size)
            stream.write(struct.pack(f"<{len(words)}I", *words))
        stream.seek(68)
        stream.write(struct.pack("<II", first, 2))
        stream.seek(44)
        stream.write(struct.pack("<I", declared + difference))
    return declared


@pytest.mark.parametrize("sector_size", [512, 4096])
@pytest.mark.parametrize("difference", [0, 1])
def test_fat_count_includes_difat_chain_and_excludes_chain_pointers(
    tmp_path, sector_size, difference,
):
    path = Timeline().export(tmp_path / "difat.aaf", sector_size=sector_size)
    actual = add_difat_chain(path, difference=difference)

    events = check(path)

    assert [item["message"] for item in findings(events)] == (
        [f"CFB header declares {actual + 1} FAT sectors; DIFAT lists {actual}."]
        if difference else []
    )
    assert all(item["severity"] == "INFO" for item in findings(events))
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("difference", [0, 1])
def test_free_sector_chain_terminator_does_not_change_fat_count(tmp_path, difference):
    path = Timeline().export(tmp_path / "free_terminator.aaf")
    actual = add_difat_chain(path, difference=difference)
    with path.open("r+b") as stream:
        stream.seek(0, 2)
        stream.seek(stream.tell() - 4)
        stream.write(struct.pack("<I", 0xFFFFFFFF))

    events = check(path)

    assert events[-1]["verdict"] == "PASS"
    assert [item["message"] for item in findings(events)] == (
        [f"CFB header declares {actual + 1} FAT sectors; DIFAT lists {actual}."]
        if difference else []
    )


@pytest.mark.parametrize("premiere_position", [None, 0, 1])
def test_exporter_uses_latest_date_but_accepts_premiere_anywhere(tmp_path, premiere_position):
    path = Timeline().export(tmp_path / "history.aaf")
    with edit_export(path) as aaf:
        recent = aaf.header["IdentificationList"].value[0]
        recent["ProductName"].value = "Recent Editor"
        recent["CompanyName"].value = "Recent Company"
        recent["Date"].value = datetime(2025, 5, 1)
        older = recent.copy()
        older["ProductName"].value = "Older Editor"
        older["CompanyName"].value = "Older Company"
        older["Date"].value = datetime(2024, 5, 1)
        history = [recent, older]
        if premiere_position is not None:
            history[premiere_position]["ProductName"].value = "Premiere Pro"
            history[premiere_position]["CompanyName"].value = "Adobe"
        aaf.header["IdentificationList"].value = history

    events = check(path)

    items = findings(events)
    if premiere_position is None:
        assert len(items) == 1
        assert items[0]["rule_id"] == "unknown_exporter"
        assert "Recent Editor" in items[0]["message"]
        assert "Recent Company" in items[0]["message"]
        assert "Older" not in items[0]["message"]
    else:
        assert items == []
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("missing", ["history", "product"])
def test_unidentified_exporter_warns_without_stopping_checks(tmp_path, missing):
    path = Timeline().export(tmp_path / "unidentified.aaf")
    with edit_export(path) as aaf:
        if missing == "history":
            aaf.header["IdentificationList"].value = []
        else:
            aaf.header["IdentificationList"].value[0]["ProductName"].value = ""

    events = check(path)

    assert [(item["rule_id"], item["severity"]) for item in findings(events)] == [
        ("unknown_exporter", "WARN"),
    ]
    assert "Unknown product" in findings(events)[0]["message"]
    assert events[-1]["verdict"] == "PASS"


@pytest.mark.parametrize("mode", ["embedded", "linked"])
def test_file_findings_precede_clip_findings_and_checks_remain_read_only(tmp_path, mode):
    path = (Timeline().with_clip(1, sample_value=0).with_clip(3, scene="")
            .export(tmp_path / "ordered.aaf", mode=mode, major_version=4))
    with edit_export(path) as aaf:
        aaf.header["IdentificationList"].value[0]["ProductName"].value = "Other Editor"
    # edit_export normalizes the CFB version, so patch it after the AAF edits.
    with path.open("r+b") as stream:
        stream.seek(26)
        stream.write(struct.pack("<H", 4))
        stream.seek(44)
        count, = struct.unpack("<I", stream.read(4))
        stream.seek(44)
        stream.write(struct.pack("<I", count + 1))
    inputs = [path, *tmp_path.glob("ordered_media/*.wav")]
    before = {}
    for item in inputs:
        item.chmod(0o444)
        with item.open("rb") as stream:
            before[item] = hashlib.file_digest(stream, "sha256").hexdigest()

    events = check(path)

    assert [item["rule_id"] for item in findings(events)] == [
        "container_corrupt", "fat_count_mismatch", "unknown_exporter",
        "digital_silence", "missing_field_recorder_metadata",
    ]
    assert all(item["track"] is None and item["record_in_timecode"] is None
               for item in findings(events)[:3])
    assert [item["track"]["slot_id"] for item in findings(events)[3:]] == [3, 5]
    assert events[-1]["verdict"] == "FAIL"
    assert events == check(path)
    for item in inputs:
        with item.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == before[item]


@pytest.mark.parametrize("damage", ["cycle", "truncated", "past_eof", "early_end"])
def test_unreadable_difat_emits_info_and_continues_checking(tmp_path, damage):
    path = Timeline().export(tmp_path / "damaged_difat.aaf")
    # Retain all required FAT references in the header so an optional DIFAT
    # anomaly cannot remove the information needed to read the audio.
    add_difat_chain(path, move_fat=False)
    with path.open("r+b") as stream:
        stream.seek(68)
        first, = struct.unpack("<I", stream.read(4))
        if damage == "cycle":
            stream.seek((first + 2) * 512 - 4)
            stream.write(struct.pack("<I", first))
        elif damage == "truncated":
            stream.seek(0, 2)
            stream.truncate(stream.tell() - 1)
        elif damage == "past_eof":
            stream.seek(68)
            stream.write(struct.pack("<I", 0xFFFF0000))
        else:
            stream.seek((first + 2) * 512 - 4)
            stream.write(struct.pack("<I", 0xFFFFFFFE))

    events = check(path)

    items = findings(events)
    assert [(item["rule_id"], item["severity"]) for item in items] == [
        ("fat_count_mismatch", "INFO"),
    ]
    assert "DIFAT could not be fully read" in items[0]["message"]
    assert items[0]["track"] is None
    assert items[0]["record_in_timecode"] is None
    if damage in ("cycle", "early_end"):
        assert events[-1]["verdict"] == "PASS"
        assert [event for event in events if event["type"] == "progress"][-1]["clips_done"] == 3
    else:
        # pyaaf2 itself cannot open a chain whose sector bytes are absent.
        # Preserve the INFO, then report the actual AAF-reader failure.
        assert events[-1]["verdict"] == "ERROR"
        assert "DIFAT could not be fully read" not in events[-1]["reason"]


@pytest.mark.parametrize("sector_size", [512, 4096])
@pytest.mark.parametrize("difference", [0, 1])
def test_difat_continuing_past_declared_length_is_info_and_keeps_pass(
    tmp_path, sector_size, difference,
):
    path = Timeline().export(tmp_path / "continuing_difat.aaf", sector_size=sector_size)
    actual = add_difat_chain(path, difference=difference)
    with path.open("r+b") as stream:
        stream.seek(68)
        first, = struct.unpack("<I", stream.read(4))
        # Put both necessary FAT references in the first sector, leaving an
        # extra sector beyond the declared chain length that pyaaf2 ignores.
        stream.seek((first + 3) * sector_size)
        second_fat = stream.read(4)
        stream.seek((first + 1) * sector_size + 4)
        stream.write(second_fat)
        # This extra FAT ID must not contribute to the count: its sector is
        # past the declared chain length and is not visited by pyaaf2.
        stream.seek(72)
        stream.write(struct.pack("<I", 1))

    events = check(path)

    items = findings(events)
    assert [(item["rule_id"], item["severity"]) for item in items] == [
        ("fat_count_mismatch", "INFO"),
    ]
    assert items[0]["message"] == (
        f"CFB header declares {actual + difference} FAT sectors; DIFAT lists {actual}. "
        "DIFAT chain continues past its declared length."
    )
    assert items[0]["track"] is None
    assert items[0]["record_in_timecode"] is None
    assert events[-1]["verdict"] == "PASS"
    assert [event for event in events if event["type"] == "progress"][-1]["clips_done"] == 3
