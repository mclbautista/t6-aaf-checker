import Foundation
import Testing
import AAFGateCore

@Test func decodesRunStarted() throws {
    let event = try WorkerEventDecoder().decode(line: #"{"schema_version":1,"type":"run_started","mode":"check","aaf_path":"/tmp/synthetic.aaf","worker_version":"0.1.0","rule_version":"1.0.0"}"#)
    #expect(event == .runStarted(mode: "check", workerVersion: "0.1.0", ruleVersion: "1.0.0"))
}

@Test func decodesProgress() throws {
    let event = try WorkerEventDecoder().decode(line: #"{"schema_version":1,"type":"progress","aaf_path":"/tmp/synthetic.aaf","clips_done":2,"clips_total":3,"bytes_read":4758714}"#)
    #expect(event == .progress(clipsDone: 2, clipsTotal: 3, bytesRead: 4_758_714))
}

@Test func decodesFinding() throws {
    let event = try WorkerEventDecoder().decode(line: #"{"schema_version":1,"type":"finding","aaf_path":"/tmp/synthetic.aaf","rule_id":"digital_silence","severity":"FAIL","track":null,"message":"Every sample is zero."}"#)
    #expect(event == .finding(ruleID: "digital_silence", severity: "FAIL", message: "Every sample is zero."))
}

@Test func decodesDiffItem() throws {
    let event = try WorkerEventDecoder().decode(line: #"{"schema_version":1,"type":"diff_item","kind":"sync_shift","blocking":true,"delta_frames":1}"#)
    #expect(event == .diffItem(kind: "sync_shift", blocking: true))
}

@Test(arguments: ["PASS", "FAIL", "ERROR"])
func decodesTerminal(verdict: String) throws {
    let line = """
    {"schema_version":1,"type":"terminal","verdict":"\(verdict)","reason":"Worker reason","report_paths":["/tmp/report.pdf","/tmp/report.json"],"report_error":"Report not saved"}
    """
    let event = try WorkerEventDecoder().decode(line: line)
    guard case .terminal(let terminal) = event else {
        Issue.record("Expected a terminal event")
        return
    }
    #expect(terminal.verdict.rawValue == verdict)
    #expect(terminal.reason == "Worker reason")
    #expect(terminal.reportPaths == ["/tmp/report.pdf", "/tmp/report.json"])
    #expect(terminal.reportError == "Report not saved")
}

@Test func ignoresUnknownEventKind() throws {
    let event = try WorkerEventDecoder().decode(line: #"{"schema_version":1,"type":"future_event","future_payload":{"value":42}}"#)
    #expect(event == nil)
}

@Test(arguments: ["progress", "future_event"])
func refusesUnknownSchemaVersion(kind: String) throws {
    do {
        _ = try WorkerEventDecoder().decode(line: """
        {"schema_version":99,"type":"\(kind)","clips_done":0,"clips_total":1,"bytes_read":0}
        """)
        Issue.record("Unsupported schema_version must be refused, including unknown event kinds")
    } catch {
        #expect(error.localizedDescription == "Unsupported worker schema_version 99. This app supports version 1.")
    }
}
