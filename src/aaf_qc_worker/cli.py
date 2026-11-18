"""Command-line NDJSON contract consumed by the app shell."""

import argparse
from importlib.metadata import version
from importlib.resources import files
import json
import os
from pathlib import Path

import aaf2

from .container import corruption_message
from .timeline import sound_clips


def emit(event_type, **fields):
    print(json.dumps({"schema_version": 1, "type": event_type, **fields}), flush=True)


def check_aaf(path, config):
    with path.open("rb") as stream:
        header = stream.read(512)
        file_size = os.fstat(stream.fileno()).st_size
    rule = config["container_corrupt"]
    message = corruption_message(header, file_size, rule)
    if message:
        emit(
            "progress", aaf_path=str(path), clips_done=0, clips_total=0,
            bytes_read=len(header),
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
            clips = sound_clips(aaf, path, config)
            emit(
                "progress", aaf_path=str(path), clips_done=0, clips_total=len(clips),
                bytes_read=len(header),
            )
            for done, clip in enumerate(clips, 1):
                if not clip.embedded and clip.linked_path is None:
                    rule = config["offline_clip"]
                    emit(
                        "finding",
                        aaf_path=str(path),
                        rule_id="offline_clip",
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
                    clips_total=len(clips), bytes_read=len(header),
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
