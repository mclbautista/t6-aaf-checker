import Foundation
import Testing
import AAFGateCore

@Test func decodesAllFindingColumnsAndLikelyEmptyClips() throws {
    let line = #"{"schema_version":1,"type":"finding","rule_id":"container_corrupt","rule_name":"Container corrupt","severity":"FAIL","track":null,"record_in_timecode":null,"clip_name":null,"source_file":null,"message":"Invalid container.","fix_instruction":"Export with linked media.","likely_empty_clips":[{"track":{"slot_id":4,"name":"Audio 2"},"record_in_timecode":"01:00:02:00","clip_name":"Clip 2","source_file":"clip_2.wav"}]}"#
    guard case .finding(let finding) = try WorkerEventDecoder().decode(line: line) else {
        Issue.record("Expected finding")
        return
    }
    #expect(finding.ruleID == "container_corrupt")
    #expect(finding.ruleName == "Container corrupt")
    #expect(finding.severity == .fail)
    #expect(finding.track == nil)
    #expect(finding.recordInTimecode == nil)
    #expect(finding.clipName == nil)
    #expect(finding.sourceFile == nil)
    #expect(finding.message == "Invalid container.")
    #expect(finding.fixInstruction == "Export with linked media.")
    let clip = try #require(finding.likelyEmptyClips.first)
    #expect(clip.track?.slotID == 4)
    #expect(clip.track?.name == "Audio 2")
    #expect(clip.recordInTimecode == "01:00:02:00")
    #expect(clip.clipName == "Clip 2")
    #expect(clip.sourceFile == "clip_2.wav")
}

func finding(_ rule: String, _ severity: String, clip: String = "Clip", timecode: String = "01:00:00:00") throws -> WorkerFinding {
    let event = try WorkerEventDecoder().decode(line: """
    {"schema_version":1,"type":"finding","rule_id":"\(rule)","rule_name":"\(rule)","severity":"\(severity)","track":{"slot_id":3,"name":"Audio 1"},"record_in_timecode":"\(timecode)","clip_name":"\(clip)","source_file":"source.wav","message":"Check this clip.","fix_instruction":"Re-export."}
    """)
    guard case .finding(let value) = event else { throw FindingTestError.missingFinding }
    return value
}

private enum FindingTestError: Error { case missingFinding }

@Test @MainActor func groupsByRuleWithCountsSeverityPriorityAndWorkerOrder() throws {
    let findings = try [
        finding("metadata", "WARN"),
        finding("low_level", "INFO"),
        finding("silence", "FAIL", clip: "Later", timecode: "01:00:20:00"),
        finding("container", "FAIL"),
        finding("silence", "FAIL", clip: "Earlier", timecode: "01:00:01:00"),
        finding("metadata", "WARN"),
    ]
    let model = FindingsModel(findings: findings)
    #expect(model.groups.map(\.ruleID) == ["container", "silence", "metadata", "low_level"])
    #expect(model.groups.map(\.severity) == [.fail, .fail, .warn, .info])
    #expect(model.groups.map(\.count) == [1, 2, 2, 1])
    #expect(model.groups[1].rows.map(\.finding.clipName) == ["Later", "Earlier"])
    #expect(model.groups[1].rows.map(\.id) == [2, 4])
    let row = model.groups[1].rows[0].finding
    #expect(row.track?.slotID == 3)
    #expect(row.track?.name == "Audio 1")
    #expect(row.recordInTimecode == "01:00:20:00")
    #expect(row.sourceFile == "source.wav")
    #expect(row.fixInstruction == "Re-export.")
    #expect(FindingsModel(findings: []).groups.isEmpty)
}

@Test @MainActor func severitySetFiltersAndRestoresRowsWithStableIDs() throws {
    let model = FindingsModel(findings: try [
        finding("metadata", "WARN"), finding("silence", "FAIL"),
        finding("low_level", "INFO"), finding("metadata", "WARN"),
    ])
    let originalIDs = model.groups.flatMap(\.rows).map(\.id)
    #expect(model.severities == Set(Severity.allCases))
    model.severities = [.warn, .info]
    #expect(model.groups.map(\.ruleID) == ["metadata", "low_level"])
    #expect(model.groups.map(\.count) == [2, 1])
    model.severities = [.fail]
    #expect(model.groups.map(\.ruleID) == ["silence"])
    model.severities = []
    #expect(model.groups.isEmpty)
    model.severities = Set(Severity.allCases)
    #expect(model.groups.flatMap(\.rows).map(\.id) == originalIDs)
}

@Test(arguments: [0, 1, 271]) func containerMessageIncludesLikelyEmptyCount(count: Int) throws {
    let clip = #"{"track":{"slot_id":3,"name":"Audio 1"},"record_in_timecode":"01:00:00:00","clip_name":"Clip","source_file":"clip.wav"}"#
    let clips = Array(repeating: clip, count: count).joined(separator: ",")
    let line = """
    {"schema_version":1,"type":"finding","rule_id":"container_corrupt","severity":"FAIL","message":"Invalid container.","likely_empty_clips":[\(clips)]}
    """
    guard case .finding(let value) = try WorkerEventDecoder().decode(line: line) else {
        Issue.record("Expected finding")
        return
    }
    let expected = count == 1 ? "Invalid container. 1 clip likely to import empty." : "Invalid container. \(count) clips likely to import empty."
    #expect(value.displayMessage == expected)
    #expect(value.likelyEmptyClips.count == count)
    #expect(try finding("metadata", "WARN").displayMessage == "Check this clip.")
}

@Test @MainActor func largeResultsKeepAllRowsAndFilterInteractively() throws {
    let fail = try finding("silence", "FAIL")
    let warn = try finding("metadata", "WARN")
    let info = try finding("low_level", "INFO")
    let model = FindingsModel(findings:
        Array(repeating: info, count: 600) + Array(repeating: warn, count: 1000) + [fail]
    )
    #expect(model.groups.map(\.count) == [1, 1000, 600])
    #expect(Set(model.groups.flatMap(\.rows).map(\.id)).count == 1601)
    let start = ContinuousClock.now
    for _ in 0..<100 {
        model.severities = [.warn]
        #expect(model.groups.first?.count == 1000)
        model.severities = Set(Severity.allCases)
    }
    #expect(start.duration(to: .now) < .seconds(1))
    #expect(model.groups.map(\.count) == [1, 1000, 600])
}
