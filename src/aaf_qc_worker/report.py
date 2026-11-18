"""Report rendering, shared by worker command modes."""

from collections import Counter
from datetime import datetime
import getpass
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import signal
import tempfile
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


class ReportCanvas(Canvas):
    def showPage(self):
        self.saveState()
        self.setFont("Vera", 8)
        self.setFillColor(colors.HexColor("#64748b"))
        self.drawRightString(A4[0] - 36, 22, f"AAF Gate | Page {self.getPageNumber()}")
        self.restoreState()
        super().showPage()


def location_text(finding):
    track = finding.get("track")
    return " | ".join(str(value) for value in (
        f'{track["name"]} (slot {track["slot_id"]})' if track else None,
        finding.get("record_in_timecode"), finding.get("clip_name"), finding.get("source_file"),
    ) if value is not None)


def position_text(position):
    if position is None:
        return "Not present"
    return " | ".join(filter(None, (
        location_text(position),
        f'Record in: {position["record_in_frames"]} frames',
        f'Source in: {position["source_in_frames"]} frames'
        if "source_in_frames" in position else None,
        f'Length: {position["length_frames"]} frames',
    )))


def render_pdf(destination, report):
    # These fonts ship with ReportLab, including in a packaged worker.
    font_root = files("reportlab").joinpath("fonts")
    for name, filename in (("Vera", "Vera.ttf"), ("VeraBold", "VeraBd.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(font_root.joinpath(filename))))
    body = ParagraphStyle("body", fontName="Vera", fontSize=8, leading=11, spaceAfter=5)
    title = ParagraphStyle("verdict", parent=body, fontName="VeraBold", fontSize=24, leading=30,
                           spaceAfter=14)
    heading = ParagraphStyle("rule", parent=body, fontName="VeraBold", fontSize=11, leading=15,
                             spaceBefore=12, spaceAfter=7, keepWithNext=True)
    rows = []

    def add(text, style=body):
        rows.append(Paragraph(escape(str(text)).replace("\n", "<br/>"), style))

    add(f'Verdict: {report["verdict"]}', title)

    def add_diff_items(items):
        for item in items:
            # The whole-reel summary is emitted first; keep its line at the top.
            if item["kind"] != "whole_reel_offset":
                add(item["kind"].replace("_", " ").capitalize(), heading)
            add(item.get("message", f'Clip {item["kind"]}.'))
            add(location_text(item))
            for role in ("flagged", "corrected"):
                positions = item.get(f"{role}_positions", [item.get(f"{role}_position")])
                for position in positions:
                    add(f"{role.capitalize()} position: {position_text(position)}")
            delta = item["delta_frames"]
            add(f"Delta: {delta} frames" if delta is not None else "Delta: Not applicable")
            for key in ("blocking", "harmful", "side", "paired_clips", "aaf_role", "aaf_path",
                        "flagged_handle_frames", "corrected_handle_frames",
                        "flagged_audio_sha256", "corrected_audio_sha256", "severity", "rule_name"):
                if key in item:
                    add(f'{key.replace("_", " ").capitalize()}: {item[key]}')
            for finding in item.get("findings", []):
                add(f'{finding["severity"]} - {finding["rule_name"]} | Rule: {finding["rule_id"]}')
                add(location_text(finding))
                add(finding["message"])
                if finding.get("fix_instruction"):
                    add(f'Fix: {finding["fix_instruction"]}')
    if "regressions" in report:
        # Only blocking items are regressions; INFO notes and harmless longer
        # handles are listed apart so a PASS never heads them as regressions.
        blocking = [item for item in report["regressions"] if item.get("blocking", True)]
        other = [item for item in report["regressions"] if not item.get("blocking", True)]
        sections = [(f"Regressions ({len(blocking)})", blocking)]
        if other:
            sections.append((f"Changes that do not block ({len(other)})", other))
        for section_title, items in sections:
            add(section_title, heading)
            if not items:
                add("None.")
            add_diff_items(items)

    if report.get("reason"):
        add(report["reason"])
    headers = [(None, report)]
    if "regressions" in report:
        headers = [("Flagged AAF", report["flagged"]), ("Corrected AAF", report["corrected"])]
    for header_title, header in headers:
        if header_title:
            add(header_title, heading)
            add(f'Verdict: {header["verdict"]}')
        for label, key in (("App version", "app_version"), ("Rule version", "rule_version"),
                           ("AAF", "aaf_path"), ("AAF SHA-256", "aaf_sha256"),
                           ("Run date and time", "run_datetime"), ("User", "user")):
            add(f'{label}: {header[key] if header[key] is not None else "Unavailable"}')
        add(" | ".join(f"{severity}: {count}" for severity, count in
                       header["counts"]["by_severity"].items()))
        if "regressions" in report:
            for rule_id, count in header["counts"]["by_rule"].items():
                add(f"{rule_id}: {count}")
    if "diff_summary" in report:
        summary = report["diff_summary"]
        add("Diff summary", heading)
        add(" | ".join(f"{kind.capitalize()}: {summary[kind]}"
                       for kind in ("resolved", "persisting", "new")))
        add(f'Paired clips: {summary["paired_clips"]} | '
            f'Blocking regressions: {summary["blocking_regressions"]}')
        for kind, count in summary["by_regression_kind"].items():
            add(f"{kind}: {count}")
        add("Corrected AAF findings", heading)
    for severity in ("FAIL", "WARN", "INFO"):
        groups = {}
        for finding in report["findings"]:
            if finding["severity"] == severity:
                groups.setdefault(finding["rule_id"], []).append(finding)
        for rule_id, findings in sorted(groups.items()):
            add(f'{severity} - {findings[0]["rule_name"]} ({len(findings)})', heading)
            add(f"Rule: {rule_id}")
            for finding in findings:
                location = location_text(finding)
                if location:
                    add(location)
                add(finding["message"])
                if finding.get("fix_instruction"):
                    add(f'Fix: {finding["fix_instruction"]}')
                affected = finding.get("likely_empty_clips", [])
                if affected:
                    add(f"Likely to import empty ({len(affected)})")
                    for clip in affected:
                        add(location_text(clip))
                rows.append(Spacer(1, 4))
    SimpleDocTemplate(
        str(destination), pagesize=A4, leftMargin=36, rightMargin=36,
        topMargin=36, bottomMargin=36, title=f'AAF Gate - {report["verdict"]}',
    ).build(rows, canvasmaker=ReportCanvas)


def publish_reports(sources, folder, name):
    """Hard links publish complete files atomically and refuse existing names."""
    number = 1
    while True:
        suffix = "" if number == 1 else f"_{number}"
        destinations = [folder / f"{name}{suffix}{source.suffix}" for source in sources]
        created = []
        try:
            for source, destination in zip(sources, destinations):
                # Defer termination until the new name is recorded for rollback.
                previous_mask = signal.pthread_sigmask(
                    signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT},
                )
                try:
                    os.link(source, destination)
                    created.append(destination)
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        except BaseException as error:
            for destination in created:
                destination.unlink()
            if not isinstance(error, FileExistsError):
                raise
            number += 1
        else:
            return [str(destination) for destination in destinations]


def staging_parent(folder, excluded_folders=()):
    """Keep staging outside the delivery, on its volume for atomic publication."""
    temporary = Path(tempfile.gettempdir()).resolve()
    excluded = (folder, *(path.resolve() for path in excluded_folders))
    device = folder.stat().st_dev
    if (not any(temporary.is_relative_to(path) for path in excluded)
            and temporary.stat().st_dev == device):
        return temporary
    for parent in folder.parents:
        if parent.stat().st_dev != device:
            break
        if not any(parent.is_relative_to(path) for path in excluded):
            return parent
    raise OSError("Cannot stage reports outside the AAF folder on the same volume.")


def aaf_report(
    path, verdict, findings, *, app_version, rule_version, run_datetime, reason=None,
):
    path = path.resolve()
    digest = None
    try:
        with path.open("rb") as stream:
            hasher = hashlib.sha256()
            while block := stream.read(1024 * 1024):
                hasher.update(block)
            digest = hasher.hexdigest()
    except OSError:
        if verdict != "ERROR":
            raise
    counts = Counter(finding["severity"] for finding in findings)
    report = {
        "schema_version": 1, "verdict": verdict,
        "app_version": app_version, "rule_version": rule_version,
        "aaf_path": str(path), "aaf_sha256": digest,
        "run_datetime": run_datetime.isoformat(), "user": getpass.getuser(),
        "counts": {
            "by_severity": {severity: counts[severity] for severity in ("FAIL", "WARN", "INFO")},
            "by_rule": dict(sorted(Counter(finding["rule_id"] for finding in findings).items())),
        },
        "findings": findings,
    }
    if reason:
        report["reason"] = reason
    return report


def write_reports(path, report, *, mode=None, excluded_folders=()):
    path = path.resolve()
    stamp = datetime.fromisoformat(report["run_datetime"]).strftime("%Y%m%d-%H%M")
    prefix = "AAFGate" if mode is None else f"AAFGate_{mode}"
    name = f'{path.stem}_{prefix}_{report["verdict"]}_{stamp}'
    staging = staging_parent(path.parent, excluded_folders)
    with tempfile.TemporaryDirectory(prefix="aaf-gate-", dir=staging) as scratch:
        pdf = Path(scratch) / "report.pdf"
        data = Path(scratch) / "report.json"
        render_pdf(pdf, report)
        data.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return publish_reports((pdf, data), path.parent, name)


def write_check_reports(path, verdict, findings, *, run_datetime=None, **metadata):
    report = aaf_report(
        path, verdict, findings,
        run_datetime=run_datetime or datetime.now().astimezone(), **metadata,
    )
    return write_reports(path, report)


def write_compare_reports(flagged, corrected, result, findings, diff_items, **metadata):
    headers = {}
    for role, path in (("flagged", flagged), ("corrected", corrected)):
        headers[role] = aaf_report(
            path, result.get(f"{role}_verdict", "ERROR"), findings[role], **metadata,
        )
        del headers[role]["findings"]
    counts = Counter(item["kind"] for item in diff_items)
    classifications = ("resolved", "persisting", "new")
    summary = {kind: counts[kind] for kind in classifications}
    summary.update(
        paired_clips=result.get("paired_clips", 0),
        blocking_regressions=result.get("blocking_regressions", 0),
        by_regression_kind={kind: counts[kind] for kind in (
            "added", "dropped", "sync_shift", "source_slip", "length_change",
            "track_change", "handle_change", "whole_reel_offset",
            "start_timecode_change", "reel_length_change", "audio_content_changed",
        )},
    )
    report = {
        "schema_version": 1, "verdict": result["verdict"],
        "run_datetime": headers["corrected"]["run_datetime"],
        **headers, "diff_summary": summary,
        "regressions": [dict(item, delta_frames=item.get("delta_frames"))
                        for item in diff_items if item["kind"] not in classifications],
        "findings": findings["corrected"],
    }
    if result.get("reason"):
        report["reason"] = result["reason"]
    return write_reports(corrected, report, mode="Compare", excluded_folders=(flagged.parent,))
