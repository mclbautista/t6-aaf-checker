"""Marker files for Premiere Pro (FCP 7 XML and CSV) and Avid (tab-delimited text).

Only FAIL findings on a clip carry a record timecode, so only those become
markers. Files are staged and published like the reports and never overwrite.
"""

import csv
from datetime import datetime
from fractions import Fraction
from pathlib import Path
import tempfile
from xml.etree import ElementTree

from .report import publish_reports, staging_parent

AVID_USER = "AAF Gate"
AVID_TRACK = "TC1"
AVID_COLOR = "Red"
PREMIERE_COLUMNS = ["Marker Name", "Description", "In", "Out", "Duration", "Marker Type"]


def timecode_frames(label, fps, drop):
    """Invert timeline.timecode_label: nominal HH:MM:SS:FF (or ;FF) to a frame count."""
    sign = -1 if label.startswith("-") else 1
    hours, minutes, seconds, frames = (int(part) for part in label.lstrip("-").replace(";", ":").split(":"))
    total_minutes = hours * 60 + minutes
    count = (total_minutes * 60 + seconds) * fps + frames
    if drop:
        dropped = round(Fraction(fps, 15))
        count -= dropped * (total_minutes - total_minutes // 10)
    return sign * count


def one_line(text):
    return " ".join(str(text).split())


def marker_rows(findings, timing):
    """Marker data for FAIL clip findings, in timeline order.

    Channels of one clip share a position, name and rule, so they share a marker.
    """
    fps = round(timing.edit_rate)
    drop = ";" in timing.start_label
    origin = timecode_frames(timing.start_label, fps, drop)
    groups = {}
    for finding in findings:
        label = finding.get("record_in_timecode")
        if finding["severity"] != "FAIL" or label is None or finding.get("track") is None:
            continue
        frame = timecode_frames(label, fps, drop) - origin
        key = (frame, finding.get("clip_name"), finding["rule_id"])
        group = groups.setdefault(key, dict(
            name=finding["rule_name"], message=finding["message"], timecode=label,
            frame=frame, clip=finding.get("clip_name"), rule=finding["rule_id"],
            slot=finding["track"]["slot_id"], files=[], tracks=[],
        ))
        group["slot"] = min(group["slot"], finding["track"]["slot_id"])
        if finding.get("source_file") and finding["source_file"] not in group["files"]:
            group["files"].append(finding["source_file"])
        track = finding["track"]["name"] or str(finding["track"]["slot_id"])
        if track not in group["tracks"]:
            group["tracks"].append(track)
    rows = []
    for group in groups.values():
        group["description"] = one_line(" | ".join(filter(None, (
            group["message"], f'Clip: {group["clip"]}',
            f'Files: {", ".join(group["files"])}' if group["files"] else None,
            f'Tracks: {", ".join(group["tracks"])}',
        ))))
        rows.append(group)
    return sorted(rows, key=lambda row: (row["frame"], row["slot"], row["rule"]))


def write_avid(destination, rows):
    with destination.open("w", encoding="utf-8", newline="") as stream:
        for row in rows:
            stream.write("\t".join((
                AVID_USER, row["timecode"], AVID_TRACK, AVID_COLOR,
                one_line(f'{row["name"]}: {row["description"]}'), "1",
            )) + "\n")


def write_premiere_csv(destination, rows):
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(PREMIERE_COLUMNS)
        for row in rows:
            writer.writerow([row["name"], row["description"], row["timecode"],
                             row["timecode"], "00:00:00:00", "Comment"])


def write_premiere_xml(destination, rows, timing, sequence_name):
    fps = round(timing.edit_rate)
    root = ElementTree.Element("xmeml", version="4")
    sequence = ElementTree.SubElement(root, "sequence")
    ElementTree.SubElement(sequence, "name").text = sequence_name
    rate = ElementTree.SubElement(sequence, "rate")
    ElementTree.SubElement(rate, "timebase").text = str(fps)
    ElementTree.SubElement(rate, "ntsc").text = "TRUE" if timing.edit_rate != fps else "FALSE"
    timecode = ElementTree.SubElement(sequence, "timecode")
    code_rate = ElementTree.SubElement(timecode, "rate")
    ElementTree.SubElement(code_rate, "timebase").text = str(fps)
    ElementTree.SubElement(code_rate, "ntsc").text = rate.find("ntsc").text
    ElementTree.SubElement(timecode, "string").text = timing.start_label
    ElementTree.SubElement(timecode, "displayformat").text = (
        "DF" if ";" in timing.start_label else "NDF"
    )
    for row in rows:
        marker = ElementTree.SubElement(sequence, "marker")
        ElementTree.SubElement(marker, "name").text = row["name"]
        ElementTree.SubElement(marker, "comment").text = row["description"]
        ElementTree.SubElement(marker, "in").text = str(row["frame"])
        ElementTree.SubElement(marker, "out").text = "-1"
    ElementTree.indent(root)
    ElementTree.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)


def write_markers(path, verdict, findings, timings, run_datetime):
    """Write the three marker files beside the AAF; return their paths (may be empty)."""
    if not timings:
        return []
    rows = marker_rows(findings, timings[0])
    if not rows:
        return []
    path = path.resolve()
    base = f'{path.stem}_AAFGate_{verdict}_{run_datetime.strftime("%Y%m%d-%H%M")}'
    staging = staging_parent(path.parent)
    published = []
    with tempfile.TemporaryDirectory(prefix="aaf-gate-", dir=staging) as scratch:
        scratch = Path(scratch)
        xml, table, text = scratch / "m.xml", scratch / "m.csv", scratch / "m.txt"
        write_premiere_xml(xml, rows, timings[0], path.stem)
        write_premiere_csv(table, rows)
        write_avid(text, rows)
        for source, label in ((xml, "PremiereXML"), (table, "PremiereCSV"), (text, "Avid")):
            published += publish_reports((source,), path.parent, f"{base}_{label}")
    return published
