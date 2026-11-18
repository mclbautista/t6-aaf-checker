import Combine

public struct FindingRow: Identifiable, Sendable {
    /// The event's offset is stable even when findings have identical contents.
    public let id: Int
    public let finding: WorkerFinding
}

public struct FindingGroup: Identifiable, Sendable {
    public var id: String { ruleID }
    public let ruleID: String
    public let ruleName: String
    public let severity: Severity
    public let rows: [FindingRow]
    public var count: Int { rows.count }
}

/// Presentation only: preserves worker order within each rule.
@MainActor
public final class FindingsModel: ObservableObject {
    @Published public private(set) var groups: [FindingGroup]
    @Published public var severities = Set(Severity.allCases) {
        didSet { groups = allGroups.filter { severities.contains($0.severity) } }
    }
    private let allGroups: [FindingGroup]

    public init(findings: [WorkerFinding]) {
        let rows = findings.enumerated().map { FindingRow(id: $0.offset, finding: $0.element) }
        allGroups = Dictionary(grouping: rows, by: { $0.finding.ruleID }).map { ruleID, rows in
            FindingGroup(
                ruleID: ruleID, ruleName: rows[0].finding.ruleName,
                severity: rows[0].finding.severity, rows: rows
            )
        }.sorted {
            let left = Severity.allCases.firstIndex(of: $0.severity)!
            let right = Severity.allCases.firstIndex(of: $1.severity)!
            return left == right ? $0.ruleID < $1.ruleID : left < right
        }
        groups = allGroups
    }
}
