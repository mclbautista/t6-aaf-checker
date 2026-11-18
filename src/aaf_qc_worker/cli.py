"""Command-line NDJSON contract consumed by the app shell."""

import argparse
from datetime import datetime
from importlib.metadata import version
from importlib.resources import files
import json
from pathlib import Path
import signal

from .check import check_aaf
from .compare import compare_checks
from .report import write_check_reports, write_compare_reports


def emit(event_type, **fields):
    print(json.dumps({"schema_version": 1, "type": event_type, **fields}), flush=True)


def terminate(signum, frame):
    # Unwind temporary reports and any partially published pair on cancellation.
    raise SystemExit(128 + signum)


def run_compare(flagged, corrected, config, report_emit):
    checks = []
    verdicts = {}
    errors = []
    for role, path in (("flagged", flagged), ("corrected", corrected)):
        def role_emit(event_type, **fields):
            report_emit(event_type, aaf_role=role, **fields)
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
    return compare_checks(*checks, config=config, emit=report_emit)


def main():
    parser = argparse.ArgumentParser(prog="aaf-qc-worker")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Check one AAF without modifying it")
    check.add_argument("aaf", type=Path)
    compare = commands.add_parser("compare", help="Compare a flagged AAF with its correction")
    compare.add_argument("--flagged", type=Path, required=True)
    compare.add_argument("--corrected", type=Path, required=True)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, terminate)
    run_datetime = datetime.now().astimezone()
    findings = []
    compare_findings = {"flagged": [], "corrected": []}
    diff_items = []

    def compare_emit(event_type, **fields):
        if event_type == "finding":
            compare_findings[fields["aaf_role"]].append(
                {key: value for key, value in fields.items() if key != "aaf_role"},
            )
        elif event_type == "diff_item":
            diff_items.append(fields)
        emit(event_type, **fields)

    def check_emit(event_type, **fields):
        if event_type == "finding":
            findings.append(fields)
        emit(event_type, **fields)

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
            fields = {"verdict": check_aaf(args.aaf, config, check_emit).verdict}
        else:
            fields = run_compare(args.flagged, args.corrected, config, compare_emit)
    except Exception as error:
        # The process boundary turns reader and I/O failures into a single event.
        fields = dict(
            verdict="ERROR",
            reason=f"Cannot complete {args.command}: {str(error) or type(error).__name__}",
        )

    report_paths = []
    try:
        if args.command == "check":
            report_paths = write_check_reports(
                args.aaf, fields["verdict"], findings,
                app_version=version("aaf-gate-worker"), rule_version=config["rule_version"],
                reason=fields.get("reason"),
                run_datetime=run_datetime,
            )
        else:
            report_paths = write_compare_reports(
                args.flagged, args.corrected, fields, compare_findings, diff_items,
                app_version=version("aaf-gate-worker"), rule_version=config["rule_version"],
                run_datetime=run_datetime,
            )
    except Exception as error:
        fields["report_error"] = f"Report not saved: {str(error) or type(error).__name__}"
    emit("terminal", **fields, report_paths=report_paths)
    return {"PASS": 0, "FAIL": 1, "ERROR": 2}[fields["verdict"]]
