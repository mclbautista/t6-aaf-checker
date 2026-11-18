"""Command-line NDJSON contract consumed by the app shell."""

import argparse
from contextlib import closing
from fractions import Fraction
from importlib.metadata import version
from importlib.resources import files
import json
import math
import os
from pathlib import Path

import aaf2
from aaf2.exceptions import AAFError

from .audio import AudioReader
from .container import corruption_message, stream_reaches_offset
from .format import descriptor_properties, linked_properties, mismatch_message, wave_properties
from .handles import short_handles_message
from .levels import EmbeddedPeak, linked_peak
from .metadata import IXMLReader, linked_ixml, metadata_values, missing_metadata_message
from .timeline import sound_clips


def emit(event_type, **fields):
    print(json.dumps({"schema_version": 1, "type": event_type, **fields}), flush=True)


def clip_location(clip):
    return {
        "track": {"slot_id": clip.slot_id, "name": clip.track_name},
        "record_in_timecode": clip.record_in_timecode,
        "clip_name": clip.name,
        "source_file": clip.source_file,
    }


def check_aaf(path, config):
    bytes_read = 0
    with path.open("rb") as stream:
        header = stream.read(512)
        file_size = os.fstat(stream.fileno()).st_size
    rule = config["container_corrupt"]
    message = corruption_message(header, file_size, rule)
    findings = []
    likely_empty_clips = []
    if message:
        findings.append(dict(
            aaf_path=str(path),
            rule_id="container_corrupt",
            rule_name=rule["name"],
            severity=rule["severity"],
            track=None,
            record_in_timecode=None,
            clip_name=None,
            source_file=None,
            message=message,
            fix_instruction=rule["fix_instruction"],
            likely_empty_clips=likely_empty_clips,
        ))
    try:
        with aaf2.open(str(path), "r") as aaf:
            essence = {item.mob_id: item for item in aaf.content.essencedata}
            clips = sound_clips(aaf, path, config)
            emit(
                "progress", aaf_path=str(path), clips_done=0, clips_total=len(clips),
                bytes_read=bytes_read,
            )
            for done, clip in enumerate(clips, 1):
                first_finding = len(findings)
                rule_id = None
                media_format = None
                media_duration = None
                peak = None
                ixml = {}
                if clip.embedded:
                    reader = None
                    try:
                        with closing(essence[clip.essence_id].open("r")) as stream:
                            if message and stream_reaches_offset(
                                stream, config["container_corrupt"]["likely_empty_offset_bytes"]
                            ):
                                likely_empty_clips.append(clip_location(clip))
                            reader = AudioReader(stream, config["audio_reader"]["chunk_bytes"])
                            scan = EmbeddedPeak(clip.scan_start, clip.scan_end)
                            xml_reader = IXMLReader()
                            for block in reader:
                                scan.consume(block, reader.format)
                                if block.chunk_id == b"iXML":
                                    xml_reader.feed(block.data)
                                bytes_read += len(block.data)
                                emit(
                                    "progress", aaf_path=str(path), clips_done=done - 1,
                                    clips_total=len(clips), bytes_read=bytes_read,
                                )
                            peak = scan.peak
                            ixml = xml_reader.finish()
                            if (
                                reader.format and reader.format.encoding in (1, 3)
                                and reader.format.block_align > 0 and reader.format.sample_rate > 0
                            ):
                                media_duration = Fraction(
                                    reader.audio_bytes,
                                    reader.format.block_align * reader.format.sample_rate,
                                )
                    except (AAFError, OSError, ValueError, EOFError, IndexError, KeyError, AssertionError):
                        # A readable AAF with damaged essence is a clip FAIL.
                        rule_id = "embedded_audio_unreadable"
                    if reader is not None and reader.format is not None:
                        media_format = wave_properties(reader.format)
                elif clip.linked_path is None:
                    rule_id = "offline_clip"
                elif clip.outside_delivery:
                    rule_id = "media_outside_delivery_folder"
                if rule_id:
                    rule = config[rule_id]
                    findings.append(dict(
                        aaf_path=str(path),
                        rule_id=rule_id,
                        rule_name=rule["name"],
                        severity=rule["severity"],
                        **clip_location(clip),
                        message=rule["message"],
                        fix_instruction=rule["fix_instruction"],
                    ))
                rule = config["format_mismatch"]
                format_errors = []
                descriptor_format = None
                try:
                    if clip.descriptor:
                        descriptor_format = descriptor_properties(clip.descriptor)
                except ValueError as error:
                    format_errors.append(f"AAF descriptor format cannot be read: {error}")
                if not clip.embedded and clip.linked_path:
                    try:
                        media_format = linked_properties(clip.linked_path)
                        media_duration = Fraction(
                            media_format["frames"], media_format["sample_rate"],
                        )
                    except ValueError as error:
                        format_errors.append(str(error))
                format_message = " ".join(filter(None, [
                    *format_errors, mismatch_message(descriptor_format, media_format, rule),
                ]))
                if format_message:
                    findings.append(dict(
                        aaf_path=str(path), rule_id="format_mismatch",
                        rule_name=rule["name"], severity=rule["severity"],
                        **clip_location(clip), message=format_message,
                        fix_instruction=rule["fix_instruction"],
                    ))
                if not clip.embedded and clip.linked_path and not format_errors:
                    peak = linked_peak(
                        clip.linked_path, config["audio_reader"]["chunk_bytes"],
                        clip.scan_start, clip.scan_end,
                    )
                level_rule = None
                if peak == 0:
                    level_rule = "digital_silence"
                elif peak is not None and peak <= 10 ** (config["low_level"]["floor_dbfs"] / 20):
                    level_rule = "low_level"
                if level_rule:
                    rule = config[level_rule]
                    findings.append(dict(
                        aaf_path=str(path), rule_id=level_rule,
                        rule_name=rule["name"], severity=rule["severity"],
                        **clip_location(clip), message=rule["message"].format(
                            peak_dbfs=20 * math.log10(peak) if peak else None,
                            floor_dbfs=config["low_level"]["floor_dbfs"],
                        ),
                        fix_instruction=rule["fix_instruction"],
                    ))
                handle_message = short_handles_message(clip, media_duration, config)
                if handle_message:
                    rule = config["short_handles"]
                    findings.append(dict(
                        aaf_path=str(path), rule_id="short_handles",
                        rule_name=rule["name"], severity=rule["severity"],
                        **clip_location(clip), message=handle_message,
                        fix_instruction=rule["fix_instruction"],
                    ))
                rule = config["missing_field_recorder_metadata"]
                if clip.linked_path:
                    ixml = linked_ixml(
                        clip.linked_path, config["audio_reader"]["chunk_bytes"],
                    )
                metadata_message = missing_metadata_message(
                    metadata_values(clip.metadata_sources, ixml, rule["field_map"]), rule,
                )
                if metadata_message:
                    findings.append(dict(
                        aaf_path=str(path), rule_id="missing_field_recorder_metadata",
                        rule_name=rule["name"], severity=rule["severity"],
                        **clip_location(clip), message=metadata_message,
                        fix_instruction=rule["fix_instruction"],
                    ))
                findings[first_finding:] = sorted(
                    findings[first_finding:], key=lambda finding: finding["rule_id"]
                )
                emit(
                    "progress", aaf_path=str(path), clips_done=done,
                    clips_total=len(clips), bytes_read=bytes_read,
                )
    finally:
        # Preserve known findings even if pyaaf2 cannot open the container.
        for finding in findings:
            emit("finding", **finding)
    return "FAIL" if any(finding["severity"] == "FAIL" for finding in findings) else "PASS"


def main():
    parser = argparse.ArgumentParser(prog="aaf-qc-worker")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Check one AAF without modifying it")
    check.add_argument("aaf", type=Path)
    args = parser.parse_args()

    try:
        config = json.loads(files("aaf_qc_worker").joinpath("rules.json").read_text())
        emit(
            "run_started",
            mode="check",
            aaf_path=str(args.aaf),
            worker_version=version("aaf-gate-worker"),
            rule_version=config["rule_version"],
        )
        verdict = check_aaf(args.aaf, config)
    except Exception as error:
        # The process boundary turns reader and I/O failures into a single event.
        emit(
            "terminal",
            verdict="ERROR",
            reason=f"Cannot complete check: {str(error) or type(error).__name__}",
            report_paths=[],
        )
        return 2

    emit("terminal", verdict=verdict, report_paths=[])
    return 1 if verdict == "FAIL" else 0
