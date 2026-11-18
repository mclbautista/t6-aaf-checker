"""Command-line NDJSON contract consumed by the app shell."""

import argparse
from contextlib import closing
from importlib.metadata import version
from importlib.resources import files
import json
import os
from pathlib import Path

import aaf2
from aaf2.exceptions import AAFError

from .audio import AudioReader
from .container import corruption_message
from .timeline import sound_clips


def emit(event_type, **fields):
    print(json.dumps({"schema_version": 1, "type": event_type, **fields}), flush=True)


def check_aaf(path, config):
    bytes_read = 0
    with path.open("rb") as stream:
        header = stream.read(512)
        file_size = os.fstat(stream.fileno()).st_size
    rule = config["container_corrupt"]
    message = corruption_message(header, file_size, rule)
    if message:
        emit(
            "progress", aaf_path=str(path), clips_done=0, clips_total=0,
            bytes_read=bytes_read,
        )
        emit(
            "finding",
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
        )
    else:
        with aaf2.open(str(path), "r") as aaf:
            essence = {item.mob_id: item for item in aaf.content.essencedata}
            clips = sound_clips(aaf, path, config)
            emit(
                "progress", aaf_path=str(path), clips_done=0, clips_total=len(clips),
                bytes_read=bytes_read,
            )
            for done, clip in enumerate(clips, 1):
                rule_id = None
                if clip.embedded:
                    try:
                        with closing(essence[clip.essence_id].open("r")) as stream:
                            for block in AudioReader(stream, config["audio_reader"]["chunk_bytes"]):
                                bytes_read += len(block.data)
                                emit(
                                    "progress", aaf_path=str(path), clips_done=done - 1,
                                    clips_total=len(clips), bytes_read=bytes_read,
                                )
                    except (AAFError, OSError, ValueError, EOFError, IndexError, KeyError, AssertionError):
                        # A readable AAF with damaged essence is a clip FAIL.
                        rule_id = "embedded_audio_unreadable"
                elif clip.linked_path is None:
                    rule_id = "offline_clip"
                elif clip.outside_delivery:
                    rule_id = "media_outside_delivery_folder"
                if rule_id:
                    rule = config[rule_id]
                    emit(
                        "finding",
                        aaf_path=str(path),
                        rule_id=rule_id,
                        rule_name=rule["name"],
                        severity=rule["severity"],
                        track={"slot_id": clip.slot_id, "name": clip.track_name},
                        record_in_timecode=clip.record_in_timecode,
                        clip_name=clip.name,
                        source_file=clip.source_file,
                        message=rule["message"],
                        fix_instruction=rule["fix_instruction"],
                    )
                    message = rule["message"]
                emit(
                    "progress", aaf_path=str(path), clips_done=done,
                    clips_total=len(clips), bytes_read=bytes_read,
                )
    return "FAIL" if message else "PASS"


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
