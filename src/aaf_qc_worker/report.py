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
    if report.get("reason"):
        add(report["reason"])
    for label, key in (("App version", "app_version"), ("Rule version", "rule_version"),
                       ("AAF", "aaf_path"), ("AAF SHA-256", "aaf_sha256"),
                       ("Run date and time", "run_datetime"), ("User", "user")):
        add(f'{label}: {report[key] if report[key] is not None else "Unavailable"}')
    add(" | ".join(f"{severity}: {count}" for severity, count in
                   report["counts"]["by_severity"].items()))
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


def staging_parent(folder):
    """Keep staging outside the delivery, on its volume for atomic publication."""
    temporary = Path(tempfile.gettempdir()).resolve()
    device = folder.stat().st_dev
    if not temporary.is_relative_to(folder) and temporary.stat().st_dev == device:
        return temporary
    parent = folder.parent
    if parent != folder and parent.stat().st_dev == device:
        return parent
    raise OSError("Cannot stage reports outside the AAF folder on the same volume.")


def write_check_reports(
    path, verdict, findings, *, app_version, rule_version, reason=None, run_datetime=None,
):
    path = path.resolve()
    run_datetime = run_datetime or datetime.now().astimezone()
    stamp = run_datetime.strftime("%Y%m%d-%H%M")
    name = f"{path.stem}_AAFGate_{verdict}_{stamp}"
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
    with tempfile.TemporaryDirectory(prefix="aaf-gate-", dir=staging_parent(path.parent)) as scratch:
        pdf = Path(scratch) / "report.pdf"
        data = Path(scratch) / "report.json"
        render_pdf(pdf, report)
        data.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return publish_reports((pdf, data), path.parent, name)
