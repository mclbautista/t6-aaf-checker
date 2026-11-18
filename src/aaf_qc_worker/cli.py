"""Command-line NDJSON contract consumed by the app shell."""

import argparse
from importlib.metadata import version
from importlib.resources import files
import json
from pathlib import Path

from .check import check_aaf
from .compare import compare_checks


def emit(event_type, **fields):
    print(json.dumps({"schema_version": 1, "type": event_type, **fields}), flush=True)


def run_compare(flagged, corrected, config):
    checks = []
    verdicts = {}
    errors = []
    for role, path in (("flagged", flagged), ("corrected", corrected)):
        def role_emit(event_type, **fields):
            emit(event_type, aaf_role=role, **fields)
        try:
            result = check_aaf(path, config, role_emit)
        except Exception as error:
            verdicts[f"{role}_verdict"] = "ERROR"
            errors.append(f"Cannot check {role} AAF: {str(error) or type(error).__name__}")
        else:
            verdicts[f"{role}_verdict"] = result.verdict
            checks.append(result)
    if errors:
        return dict(verdicts, verdict="ERROR", reason="; ".join(errors))
    return compare_checks(*checks, config=config, emit=emit)


def main():
    parser = argparse.ArgumentParser(prog="aaf-qc-worker")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Check one AAF without modifying it")
    check.add_argument("aaf", type=Path)
    compare = commands.add_parser("compare", help="Compare a flagged AAF with its correction")
    compare.add_argument("--flagged", type=Path, required=True)
    compare.add_argument("--corrected", type=Path, required=True)
    args = parser.parse_args()

    try:
        config = json.loads(files("aaf_qc_worker").joinpath("rules.json").read_text())
        paths = ({"aaf_path": str(args.aaf)} if args.command == "check" else {
            "flagged_path": str(args.flagged), "corrected_path": str(args.corrected),
        })
        emit(
            "run_started", mode=args.command, **paths,
            worker_version=version("aaf-gate-worker"), rule_version=config["rule_version"],
        )
        if args.command == "check":
            fields = {"verdict": check_aaf(args.aaf, config, emit).verdict}
        else:
            fields = run_compare(args.flagged, args.corrected, config)
    except Exception as error:
        # The process boundary turns reader and I/O failures into a single event.
        emit(
            "terminal", verdict="ERROR",
            reason=f"Cannot complete {args.command}: {str(error) or type(error).__name__}",
            report_paths=[],
        )
        return 2

    emit("terminal", **fields, report_paths=[])
    return {"PASS": 0, "FAIL": 1, "ERROR": 2}[fields["verdict"]]
