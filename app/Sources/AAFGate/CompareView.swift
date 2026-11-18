import AppKit
import SwiftUI
import UniformTypeIdentifiers
import AAFGateCore

struct CompareView: View {
    @ObservedObject var model: CompareModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Compare a corrected export").font(.title2.bold())
            HStack(alignment: .top, spacing: 16) {
                AAFWell(model: model, role: .flagged)
                AAFWell(model: model, role: .corrected)
            }
            HStack {
                Button("Compare") { model.start() }
                    .buttonStyle(.borderedProminent)
                    .disabled(!model.canCompare)
                    .accessibilityIdentifier("start-compare")
                if model.isRunning {
                    ProgressView().controlSize(.small)
                    Text(progressLabel)
                    Spacer()
                    Button(model.isCancelling ? "Cancelling…" : "Cancel") { model.cancel() }
                        .disabled(!model.canCancel)
                        .accessibilityIdentifier("cancel-compare")
                } else {
                    Text("Select two different AAFs of the same reel.").foregroundStyle(.secondary)
                }
            }
            if model.isRunning, let progress = model.current?.progress {
                ProgressView(value: Double(progress.clipsDone), total: Double(max(1, progress.clipsTotal)))
                Text("\(progress.clipsDone) of \(progress.clipsTotal) clips · \(ByteCountFormatter.string(fromByteCount: progress.bytesRead, countStyle: .file)) read")
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("compare-progress")
            }
            if let run = model.current, run.status != .running {
                CompareResult(run: run).id(run.id)
            } else {
                Spacer()
            }
            if let error = model.logRetentionError { Text(error).foregroundStyle(.secondary) }
        }
        .padding(24)
        .frame(minWidth: 1100, minHeight: 620, alignment: .topLeading)
    }

    private var progressLabel: String {
        guard let role = model.current?.progressRole.flatMap(AAFRole.init(rawValue:)) else {
            return "Comparing…"
        }
        return "Reading \(role.label)…"
    }
}

private struct AAFWell: View {
    @ObservedObject var model: CompareModel
    let role: AAFRole
    @State private var isTargeted = false

    private var url: URL? { role == .flagged ? model.flaggedAAF : model.correctedAAF }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(role.label).font(.headline)
            Text(url?.lastPathComponent ?? "Drop an AAF here")
                .font(.title3).lineLimit(1).truncationMode(.middle)
            Text(url?.path ?? "Choose one file for this role.")
                .font(.caption).foregroundStyle(.secondary)
                .lineLimit(2).truncationMode(.middle).textSelection(.enabled)
                .help(url?.path ?? role.label)
            HStack {
                Button("Choose…") { choose() }
                    .accessibilityLabel("Choose \(role.label)")
                    .accessibilityIdentifier("choose-\(role.rawValue)")
                if url != nil {
                    Button("Clear") { model.setAAF(nil, for: role) }
                        .accessibilityLabel("Clear \(role.label)")
                }
            }.disabled(model.isRunning)
        }
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 135, alignment: .topLeading)
        .background(isTargeted ? Color.accentColor.opacity(0.15) : Color.secondary.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .accessibilityIdentifier("\(role.rawValue)-well")
        .onDrop(of: [UTType.fileURL], isTargeted: $isTargeted) { providers in
            guard !model.isRunning, providers.count == 1 else { return false }
            Task {
                let urls = await droppedFileURLs(providers)
                if let url = urls.first { model.setAAF(url, for: role) }
            }
            return true
        }
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.title = "Choose \(role.label)"
        panel.allowedContentTypes = [UTType(filenameExtension: "aaf") ?? .data]
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.begin { response in
            if response == .OK, let url = panel.url { model.setAAF(url, for: role) }
        }
    }
}

private struct CompareResult: View {
    let run: CompareRun

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(verdictLabel).font(.system(size: 42, weight: .bold, design: .rounded))
                    .foregroundStyle(verdictColor)
                    .accessibilityIdentifier("compare-verdict")
                if let verdict = run.terminal?.flaggedVerdict {
                    Text("Flagged AAF: \(verdict.rawValue)").foregroundStyle(.secondary)
                }
                if let verdict = run.terminal?.correctedVerdict {
                    Text("Corrected AAF: \(verdict.rawValue)").foregroundStyle(.secondary)
                }
                Spacer()
                if !run.reportURLs.isEmpty {
                    Button("Reveal reports") { NSWorkspace.shared.activateFileViewerSelecting(run.reportURLs) }
                        .accessibilityIdentifier("reveal-compare-reports")
                }
            }
            // Wells may be edited for the next run; these names belong to this result.
            Text("\(run.flaggedAAF.lastPathComponent) → \(run.correctedAAF.lastPathComponent)")
                .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            if !run.detail.isEmpty { Text(run.detail).textSelection(.enabled) }
            if let message = run.reportSaveMessage {
                Text(message).foregroundStyle(.orange).textSelection(.enabled)
            }
            if case .finished = run.status {
                VSplitView {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 14) {
                            Text("Regressions (\(run.terminal?.blockingRegressions.map(String.init) ?? "—"))")
                                .font(.headline)
                            if run.regressions.isEmpty {
                                Text(run.terminal?.verdict == .error ? "Comparison could not complete." : "No regressions.")
                                    .foregroundStyle(.secondary)
                            }
                            DiffRows(items: run.regressions)
                            Text("Changes that do not block (\(run.changes.count))").font(.headline)
                            if run.changes.isEmpty { Text("None.").foregroundStyle(.secondary) }
                            DiffRows(items: run.changes)
                            Divider()
                            Text("Resolved: \(run.classifiedFindings("resolved").count) · Persisting: \(run.classifiedFindings("persisting").count) · New: \(run.classifiedFindings("new").count)")
                                .font(.headline)
                            ForEach(["resolved", "persisting", "new"], id: \.self) { kind in
                                DisclosureGroup("\(kind.capitalized) findings (\(run.classifiedFindings(kind).count))") {
                                    ForEach(Array(run.classifiedFindings(kind).enumerated()), id: \.offset) { _, item in
                                        ClassifiedFinding(item: item)
                                    }
                                }
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(.trailing, 8)
                    }.frame(minHeight: 140, idealHeight: 260)
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Corrected AAF findings").font(.headline)
                        FindingsView(findings: run.findings)
                    }.padding(.top, 12).frame(minHeight: 90)
                }
            }
        }
    }

    private var verdictLabel: String {
        switch run.status {
        case .running: "Comparing"
        case .cancelled: "Cancelled"
        case .finished(let verdict): verdict.rawValue
        }
    }

    private var verdictColor: Color {
        switch run.status {
        case .finished(.pass): .green
        case .finished(.fail): .red
        case .finished(.error): .orange
        case .running, .cancelled: .secondary
        }
    }
}

private struct DiffRows: View {
    let items: [DiffItem]

    var body: some View {
        ForEach(Array(items.enumerated()), id: \.offset) { _, item in
            VStack(alignment: .leading, spacing: 6) {
                Text(item.message ?? item.kind.capitalized).font(.headline)
                if let clip = item.clipName { Text("\(clip) · \(item.sourceFile ?? "—")") }
                Text("Delta (frames): \(item.deltaFrames?.description ?? "—")")
                    .monospacedDigit()
                if item.flaggedPositions != nil || item.correctedPositions != nil {
                    DisclosureGroup("Paired clip positions (\(item.flaggedPositions?.count ?? 0))") {
                        ForEach(Array((item.flaggedPositions ?? []).enumerated()), id: \.offset) { index, position in
                            PositionPair(flagged: position, corrected: item.correctedPositions?.indices.contains(index) == true ? item.correctedPositions?[index] : nil)
                        }
                    }
                } else {
                    PositionPair(flagged: item.flaggedPosition, corrected: item.correctedPosition)
                }
                ForEach(Array(item.findings.enumerated()), id: \.offset) { _, finding in
                    FindingNote(finding: finding).padding(.leading, 16)
                }
            }
            .textSelection(.enabled)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary.opacity(0.3))
        }
    }
}

private struct PositionPair: View {
    let flagged: DiffPosition?
    let corrected: DiffPosition?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Flagged position: \(label(flagged))")
            Text("Corrected position: \(label(corrected))")
        }.font(.caption).foregroundStyle(.secondary).monospacedDigit()
    }

    private func label(_ position: DiffPosition?) -> String {
        guard let position else { return "—" }
        let track = position.track.map { "\($0.name) (slot \($0.slotID))" } ?? "Sequence"
        return "\(track) · \(position.recordInTimecode ?? "—") · record \(position.recordInFrames?.description ?? "—") frames · source in \(position.sourceInFrames?.description ?? "—") · length \(position.lengthFrames?.description ?? "—")"
    }
}

private struct ClassifiedFinding: View {
    let item: DiffItem

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let finding = item.flaggedFinding {
                Text("Flagged AAF").font(.caption.bold())
                FindingNote(finding: finding)
            }
            if let finding = item.correctedFinding {
                Text("Corrected AAF").font(.caption.bold())
                FindingNote(finding: finding)
            }
        }.padding(10).textSelection(.enabled)
    }
}

private struct FindingNote: View {
    let finding: WorkerFinding

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("\(finding.severity.rawValue) · \(finding.ruleName)").bold()
            Text("\(finding.track.map { "\($0.name) (slot \($0.slotID))" } ?? "File level") · \(finding.recordInTimecode ?? "—") · \(finding.clipName ?? "—") · \(finding.sourceFile ?? "—")")
                .foregroundStyle(.secondary)
            Text(finding.displayMessage)
            if let fix = finding.fixInstruction { Text("Fix: \(fix)") }
        }.font(.caption)
    }
}
