import Foundation
import Testing
import AAFGateCore

private let shortHandle = #"{"rule_id":"short_handles","rule_name":"Short handles","severity":"WARN","track":{"slot_id":5,"name":"Audio 3"},"record_in_timecode":"00:00:00:00","clip_name":"Clip 3","source_file":"clip_3.wav","message":"Fewer than 120 frames of handles."}"#

@Test func decodesAudioContentChangedAsInformation() throws {
    let event = try WorkerEventDecoder().decode(line: #"{"schema_version":1,"type":"diff_item","kind":"audio_content_changed","blocking":false,"severity":"INFO","rule_name":"Audio content changed","clip_name":"Clip 1","source_file":"clip_1.wav","message":"Audio content changed in the used range.","flagged_position":{"track":null,"record_in_timecode":"00:00:00:00"},"corrected_position":{"track":null,"record_in_timecode":"00:00:00:00"}}"#)
    guard case .diffItem(let item) = event else {
        Issue.record("Expected audio note")
        return
    }
    #expect(item.kind == "audio_content_changed")
    #expect(!item.blocking)
    #expect(item.severity == .info)
    #expect(item.ruleName == "Audio content changed")
    #expect(item.message == "Audio content changed in the used range.")
    #expect(item.correctedPosition?.recordInTimecode == "00:00:00:00")
}

@Test(arguments: ["flagged", "corrected"])
func compareEventsRetainTheirAAFRole(role: String) throws {
    let decoder = WorkerEventDecoder()
    let findingLine = """
    {"schema_version":1,"type":"finding","aaf_role":"\(role)","rule_id":"short_handles","severity":"WARN","message":"Short handles."}
    """
    guard case .finding(let finding) = try decoder.decode(line: findingLine) else {
        Issue.record("Expected role-tagged finding")
        return
    }
    #expect(finding.aafRole == role)
    let progressLine = """
    {"schema_version":1,"type":"progress","aaf_role":"\(role)","clips_done":1,"clips_total":3,"bytes_read":8192}
    """
    #expect(try decoder.decode(line: progressLine) == .progress(clipsDone: 1, clipsTotal: 3, bytesRead: 8192, aafRole: role))
}

@Test func decodesIdenticalCompareTerminalAndBothReportPaths() throws {
    let event = try WorkerEventDecoder().decode(line: #"{"schema_version":1,"type":"terminal","verdict":"PASS","flagged_verdict":"FAIL","corrected_verdict":"PASS","blocking_regressions":0,"paired_clips":3,"report_paths":["/tmp/corrected/Synthetic_AAFGate_Compare_PASS.pdf","/tmp/corrected/Synthetic_AAFGate_Compare_PASS.json"]}"#)
    guard case .terminal(let terminal) = event else {
        Issue.record("Expected compare terminal")
        return
    }
    #expect(terminal.verdict == .pass)
    #expect(terminal.flaggedVerdict == .fail)
    #expect(terminal.correctedVerdict == .pass)
    #expect(terminal.blockingRegressions == 0)
    #expect(terminal.pairedClips == 3)
    #expect(terminal.reportPaths == ["/tmp/corrected/Synthetic_AAFGate_Compare_PASS.pdf", "/tmp/corrected/Synthetic_AAFGate_Compare_PASS.json"])
}

@Test func decodesUnrelatedCompareErrorWithoutRegressionCount() throws {
    let reason = "Fewer than half the clips pair; the AAFs do not look like the same reel."
    let line = """
    {"schema_version":1,"type":"terminal","verdict":"ERROR","flagged_verdict":"PASS","corrected_verdict":"PASS","paired_clips":0,"reason":"\(reason)","report_paths":[]}
    """
    guard case .terminal(let terminal) = try WorkerEventDecoder().decode(line: line) else {
        Issue.record("Expected compare error")
        return
    }
    #expect(terminal.verdict == .error)
    #expect(terminal.reason == reason)
    #expect(terminal.flaggedVerdict == .pass)
    #expect(terminal.correctedVerdict == .pass)
    #expect(terminal.blockingRegressions == nil)
}

@Test func decodesWholeReelOffsetWithAllPositions() throws {
    let event = try WorkerEventDecoder().decode(line: #"{"schema_version":1,"type":"diff_item","kind":"whole_reel_offset","blocking":true,"delta_frames":-2,"paired_clips":1,"message":"Whole reel moved by -2 frames.","flagged_positions":[{"track":{"slot_id":3,"name":"Audio 1"},"record_in_timecode":"00:00:00:02","record_in_frames":2,"source_in_frames":120,"length_frames":24}],"corrected_positions":[{"track":{"slot_id":3,"name":"Audio 1"},"record_in_timecode":"00:00:00:00","record_in_frames":0,"source_in_frames":120,"length_frames":24}]}"#)
    guard case .diffItem(let item) = event else {
        Issue.record("Expected whole-reel offset")
        return
    }
    #expect(item.message == "Whole reel moved by -2 frames.")
    #expect(item.deltaFrames == .integer(-2))
    #expect(item.flaggedPositions?.first?.recordInFrames == .integer(2))
    #expect(item.correctedPositions?.first?.recordInFrames == .integer(0))
}

@Test(arguments: ["resolved", "persisting", "new"])
func decodesFindingClassificationWithBothFindings(kind: String) throws {
    let line = """
    {"schema_version":1,"type":"diff_item","kind":"\(kind)","blocking":false,"rule_id":"short_handles","flagged_finding":\(kind == "new" ? "null" : shortHandle),"corrected_finding":\(kind == "resolved" ? "null" : shortHandle)}
    """
    guard case .diffItem(let item) = try WorkerEventDecoder().decode(line: line) else {
        Issue.record("Expected classified finding")
        return
    }
    #expect(item.kind == kind)
    #expect(!item.blocking)
    #expect((item.flaggedFinding != nil) == (kind != "new"))
    #expect((item.correctedFinding != nil) == (kind != "resolved"))
    #expect((item.correctedFinding ?? item.flaggedFinding)?.ruleID == "short_handles")
}

@Test func droppedClipRetainsItsFindingsBelowItsFlaggedPosition() throws {
    let line = """
    {"schema_version":1,"type":"diff_item","kind":"dropped","blocking":true,"clip_name":"Clip 3","source_file":"clip_3.wav","flagged_position":{"track":{"slot_id":5,"name":"Audio 3"},"record_in_timecode":"00:00:00:00","record_in_frames":0,"source_in_frames":2,"length_frames":2},"corrected_position":null,"delta_frames":null,"findings":[\(shortHandle)]}
    """
    guard case .diffItem(let item) = try WorkerEventDecoder().decode(line: line) else {
        Issue.record("Expected dropped clip")
        return
    }
    #expect(item.kind == "dropped")
    #expect(item.blocking)
    #expect(item.flaggedPosition?.track?.slotID == 5)
    #expect(item.correctedPosition == nil)
    #expect(item.deltaFrames == nil)
    #expect(item.findings.count == 1)
    #expect(item.findings.first?.ruleID == "short_handles")
    #expect(item.findings.first?.clipName == "Clip 3")
}
