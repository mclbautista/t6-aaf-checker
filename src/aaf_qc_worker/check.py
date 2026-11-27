"""The shared read-only check, used unchanged by both command modes."""

from contextlib import closing
from dataclasses import dataclass
from fractions import Fraction
import math
import os

import aaf2
from aaf2.exceptions import AAFError

from .audio import AudioReader
from .container import corruption_message, fat_count_mismatch_message, stream_reaches_offset
from .content import EmbeddedContentHash, linked_content_hash
from .exporter import unknown_exporter_message
from .format import descriptor_properties, linked_properties, mismatch_message, wave_properties
from .handles import delivered_handles, short_handles_message
from .levels import EmbeddedPeak, linked_peak
from .metadata import (
    IXMLReader, linked_chunk_ids, linked_ixml, metadata_values, missing_metadata_message,
)
from .timeline import (
    SequenceTiming, TimelineClip, delivered_compositions, sequence_timings, sound_clips,
)


@dataclass
class CheckResult:
    verdict: str
    clips: list[TimelineClip]
    findings: list[dict]
    clip_findings: list[list[dict]]
    edit_rates: set[Fraction]
    path: str
    sequence_timings: list[SequenceTiming]
    handles: list[dict | None]
    audio_hashes: list[str | None]
    peaks: list[float | None]


def classify_silence(clips, peaks, findings, ranges, clip_findings, config):
    """Grade each all-zero clip by its audible siblings and used length.

    A sibling is another track carrying the same recording: same clip name at
    the same record position, with a peak above the Low level floor. Needs every
    clip's peak, so it runs once after the whole timeline is read.
    """
    floor = 10 ** (config["low_level"]["floor_dbfs"] / 20)
    silence, empty = config["digital_silence"], config["empty_channel"]
    for index, clip in enumerate(clips):
        if len(peaks) <= index or peaks[index] != 0:
            continue
        start, end = ranges[index]
        finding = next((item for item in findings[start:end]
                        if item["rule_id"] == "digital_silence"), None)
        if finding is None:
            continue
        if any(
            other is not clip and other.name == clip.name
            and other.record_in_timecode == clip.record_in_timecode
            and other_peak is not None and other_peak > floor
            for other, other_peak in zip(clips, peaks)
        ):
            finding.update(
                rule_id="empty_channel", rule_name=empty["name"],
                severity=empty["severity"], message=empty["message"],
                fix_instruction=empty["fix_instruction"],
            )
        elif clip.used_end - clip.used_start < Fraction(silence["short_seconds"]):
            finding["severity"] = silence["short_severity"]
        findings[start:end] = sorted(findings[start:end], key=lambda item: item["rule_id"])
        clip_findings[index] = findings[start:end]


def clip_location(clip):
    return {
        "track": {"slot_id": clip.slot_id, "name": clip.track_name},
        "record_in_timecode": clip.record_in_timecode,
        "clip_name": clip.name,
        "source_file": clip.source_file,
    }


def check_aaf(path, config, emit):
    bytes_read = 0
    with path.open("rb") as stream:
        header = stream.read(512)
        file_size = os.fstat(stream.fileno()).st_size
    rule = config["container_corrupt"]
    message = corruption_message(header, file_size, rule)
    findings = []
    clips = []
    clip_findings = []
    handles = []
    audio_hashes = []
    peaks = []
    ranges = []
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
        rule = config["fat_count_mismatch"]
        with path.open("rb") as stream:
            fat_message = fat_count_mismatch_message(stream, header, file_size, rule)
        if fat_message:
            findings.append(dict(
                aaf_path=str(path), rule_id="fat_count_mismatch",
                rule_name=rule["name"], severity=rule["severity"],
                track=None, record_in_timecode=None, clip_name=None, source_file=None,
                message=fat_message, fix_instruction=rule["fix_instruction"],
            ))
        with aaf2.open(str(path), "r") as aaf:
            rule = config["unknown_exporter"]
            exporter_message = unknown_exporter_message(aaf.header, rule)
            if exporter_message:
                findings.append(dict(
                    aaf_path=str(path), rule_id="unknown_exporter",
                    rule_name=rule["name"], severity=rule["severity"],
                    track=None, record_in_timecode=None, clip_name=None, source_file=None,
                    message=exporter_message, fix_instruction=rule["fix_instruction"],
                ))
            essence = {item.mob_id: item for item in aaf.content.essencedata}
            edit_rates = {
                Fraction(slot.edit_rate)
                for composition in delivered_compositions(aaf)
                for slot in composition.slots if slot.media_kind in ("Sound", "Timecode")
            }
            clips = sound_clips(aaf, path, config)
            timings = sequence_timings(aaf)
            if not clips:
                rule = config["no_audio_clips"]
                findings.append(dict(
                    aaf_path=str(path), rule_id="no_audio_clips",
                    rule_name=rule["name"], severity=rule["severity"],
                    track=None, record_in_timecode=None, clip_name=None, source_file=None,
                    message=rule["message"], fix_instruction=rule["fix_instruction"],
                ))
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
                audio_hash = None
                ixml = {}
                chunk_ids = set()
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
                            content = EmbeddedContentHash(clip.used_start, clip.used_end)
                            xml_reader = IXMLReader()
                            for block in reader:
                                scan.consume(block, reader.format)
                                content.consume(block, reader.format)
                                if block.chunk_id == b"iXML":
                                    xml_reader.feed(block.data)
                                bytes_read += len(block.data)
                                emit(
                                    "progress", aaf_path=str(path), clips_done=done - 1,
                                    clips_total=len(clips), bytes_read=bytes_read,
                                )
                            peak = scan.peak
                            audio_hash = content.hexdigest()
                            ixml = xml_reader.finish()
                            chunk_ids = set(reader.chunk_ids)
                            if (
                                reader.format and reader.format.encoding in (1, 3)
                                and reader.format.block_align > 0 and reader.format.sample_rate > 0
                            ):
                                media_duration = Fraction(
                                    reader.audio_bytes,
                                    reader.format.block_align * reader.format.sample_rate,
                                )
                            if b"bext" not in reader.chunk_ids:
                                rule_id = "no_broadcast_wav_chunk"
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
                    audio_hash = linked_content_hash(
                        clip.linked_path, config["audio_reader"]["chunk_bytes"],
                        clip.used_start, clip.used_end,
                    )
                audio_hashes.append(audio_hash)
                peaks.append(peak)
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
                available_handles = delivered_handles(clip, media_duration)
                handles.append(available_handles)
                handle_message = short_handles_message(available_handles, config)
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
                    chunk_ids = linked_chunk_ids(clip.linked_path)
                # Unknown chunks (unreadable or absent media) never demote the warning.
                recorded = not chunk_ids or bool(chunk_ids & {b"bext", b"iXML"})
                metadata_message = missing_metadata_message(
                    metadata_values(clip.metadata_sources, ixml, rule["field_map"]), rule,
                )
                if metadata_message:
                    findings.append(dict(
                        aaf_path=str(path), rule_id="missing_field_recorder_metadata",
                        rule_name=rule["name"],
                        severity=rule["severity"] if recorded else rule["unrecorded_severity"],
                        **clip_location(clip), message=metadata_message,
                        fix_instruction=rule["fix_instruction"],
                    ))
                findings[first_finding:] = sorted(
                    findings[first_finding:], key=lambda finding: finding["rule_id"]
                )
                ranges.append((first_finding, len(findings)))
                clip_findings.append(findings[first_finding:])
                emit(
                    "progress", aaf_path=str(path), clips_done=done,
                    clips_total=len(clips), bytes_read=bytes_read,
                )
        classify_silence(clips, peaks, findings, ranges, clip_findings, config)
    finally:
        # Preserve known findings even if pyaaf2 cannot open the container.
        for finding in findings:
            emit("finding", **finding)
    verdict = "FAIL" if any(finding["severity"] == "FAIL" for finding in findings) else "PASS"
    return CheckResult(verdict, clips, findings, clip_findings, edit_rates, str(path), timings,
                       handles, audio_hashes, peaks)

