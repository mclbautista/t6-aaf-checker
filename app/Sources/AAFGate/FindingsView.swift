import SwiftUI
import AAFGateCore

struct FindingsView: View {
    @StateObject private var model: FindingsModel
    @State private var selection: FindingRow.ID?
    private let total: Int

    init(findings: [WorkerFinding]) {
        _model = StateObject(wrappedValue: FindingsModel(findings: findings))
        total = findings.count
    }

    private var selectedFinding: WorkerFinding? {
        model.groups.lazy.flatMap(\.rows).first { $0.id == selection }?.finding
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Findings (\(total))").font(.headline)
                Spacer()
                ForEach(Severity.allCases, id: \.self) { severity in
                    Toggle(severity.rawValue, isOn: Binding(
                        get: { model.severities.contains(severity) },
                        set: { enabled in
                            if enabled { model.severities.insert(severity) }
                            else { model.severities.remove(severity) }
                        }
                    ))
                    .toggleStyle(.checkbox)
                    .accessibilityIdentifier("filter-\(severity.rawValue)")
                }
            }
            if total == 0 {
                Text("No findings.").foregroundStyle(.secondary)
            } else {
                Table(of: FindingRow.self, selection: $selection) {
                    TableColumn("Severity") { row in
                        Text(row.finding.severity.rawValue)
                            .foregroundStyle(severityColor(row.finding.severity))
                    }.width(65)
                    TableColumn("Rule") { row in Text(row.finding.ruleName) }
                        .width(min: 100, ideal: 170)
                    TableColumn("Track") { row in Text(trackLabel(row.finding.track)) }
                        .width(min: 85, ideal: 110)
                    TableColumn("Record TC") { row in
                        Text(row.finding.recordInTimecode ?? "—").monospacedDigit()
                    }.width(105)
                    TableColumn("Clip") { row in Text(row.finding.clipName ?? "—") }
                        .width(min: 85, ideal: 120)
                    TableColumn("Source file") { row in Text(row.finding.sourceFile ?? "—") }
                        .width(min: 100, ideal: 140)
                    TableColumn("Message") { row in Text(row.finding.displayMessage) }
                        .width(min: 180, ideal: 300)
                    TableColumn("Fix instruction") { row in Text(row.finding.fixInstruction ?? "—") }
                        .width(min: 150, ideal: 250)
                } rows: {
                    ForEach(model.groups) { group in
                        Section("\(group.severity.rawValue) · \(group.ruleName) (\(group.count))") {
                            ForEach(group.rows) { row in TableRow(row) }
                        }
                    }
                }
                .accessibilityIdentifier("findings-table")
                .overlay {
                    if model.groups.isEmpty {
                        Text("No findings match the selected severities.")
                            .padding().background(.regularMaterial)
                    }
                }
                .frame(minHeight: 180)
                if let finding = selectedFinding {
                    FindingDetail(finding: finding).id(selection)
                } else {
                    Text("Select a finding to read its full message and fix instruction.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

private struct FindingDetail: View {
    let finding: WorkerFinding

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                Text("\(finding.severity.rawValue) · \(finding.ruleName)").font(.headline)
                Text("\(trackLabel(finding.track)) · \(finding.recordInTimecode ?? "—") · \(finding.clipName ?? "—") · \(finding.sourceFile ?? "—")")
                    .foregroundStyle(.secondary)
                Text(finding.displayMessage)
                if let fix = finding.fixInstruction { Text("Fix: \(fix)") }
                if !finding.likelyEmptyClips.isEmpty {
                    DisclosureGroup("Likely to import empty (\(finding.likelyEmptyClips.count))") {
                        LazyVStack(alignment: .leading, spacing: 6) {
                            ForEach(Array(finding.likelyEmptyClips.enumerated()), id: \.offset) { _, clip in
                                Text("\(trackLabel(clip.track)) · \(clip.recordInTimecode ?? "—") · \(clip.clipName ?? "—") · \(clip.sourceFile ?? "—")")
                            }
                        }.padding(.leading, 12)
                    }
                }
            }
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
        }
        .frame(maxHeight: 190)
        .background(.quaternary.opacity(0.3))
        .accessibilityIdentifier("finding-detail")
    }
}

private func trackLabel(_ track: FindingTrack?) -> String {
    guard let track else { return "File level" }
    return "\(track.slotID) · \(track.name)"
}

private func severityColor(_ severity: Severity) -> Color {
    switch severity {
    case .fail: .red
    case .warn: .orange
    case .info: .secondary
    }
}
