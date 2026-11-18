import Foundation
import Testing
import AAFGateCore

@Test @MainActor func compareLogsEventsAndDiagnostics() async throws {
    let fixture = try RunFixture(script: #"""
    printf '%s\n' 'Compare diagnostic' >&2
    printf '%s\n' '{"schema_version":1,"type":"future_event","message":"Compare raw event"}'
    printf '%s\n' '{"schema_version":1,"type":"terminal","verdict":"ERROR","reason":"Unrelated AAFs","report_paths":[],"report_error":"Permission denied"}'
    exit 2
    """#)
    defer { fixture.remove() }
    let directory = fixture.folder.appendingPathComponent("logs")
    let model = CompareModel(
        runner: WorkerRunner(executableURL: fixture.worker),
        logs: RunLogStore(directoryURL: directory)
    )
    model.setAAF(fixture.folder.appendingPathComponent("flagged.aaf"), for: .flagged)
    model.setAAF(fixture.folder.appendingPathComponent("corrected.aaf"), for: .corrected)
    model.start()
    try await eventually { !model.isRunning }
    #expect(model.current?.status == .finished(.error))
    #expect(model.current?.detail == "Unrelated AAFs")
    #expect(model.current?.reportSaveMessage == "Report could not be saved. Permission denied")
    let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
    let log = try #require(files.first)
    let content = try String(contentsOf: log, encoding: .utf8)
    #expect(content.contains("Compare diagnostic\n"))
    #expect(content.contains("Compare raw event"))
    #expect(content.contains(#""verdict":"ERROR""#))
}

@Test(arguments: [false, true]) @MainActor
func compareProgressCancelAndRetry(ignoresTerm: Bool) async throws {
    let fixture = try RunFixture(script: """
    case "$5" in
      *slow.aaf)
        \(ignoresTerm ? "trap '' TERM" : "trap 'exit 0' TERM")
        printf '%s\\n' '{"schema_version":1,"type":"progress","aaf_role":"corrected","clips_done":2,"clips_total":3,"bytes_read":8192}'
        while :; do sleep 0.01; done ;;
    esac
    printf '%s\\n' '{"schema_version":1,"type":"terminal","verdict":"PASS","flagged_verdict":"PASS","corrected_verdict":"PASS","blocking_regressions":0,"report_paths":[]}'
    """)
    defer { fixture.remove() }
    let model = CompareModel(runner: WorkerRunner(executableURL: fixture.worker))
    let flagged = fixture.folder.appendingPathComponent("flagged.aaf")
    let slow = fixture.folder.appendingPathComponent("slow.aaf")
    model.setAAF(flagged, for: .flagged)
    model.setAAF(slow, for: .corrected)
    model.start()
    try await eventually { model.current?.progress != nil }
    #expect(model.isRunning)
    #expect(model.canCancel)
    #expect(!model.canCompare)
    #expect(model.current?.progressRole == "corrected")
    #expect(model.current?.progress?.clipsDone == 2)
    #expect(model.current?.progress?.clipsTotal == 3)
    #expect(model.current?.progress?.bytesRead == 8192)
    model.setAAF(nil, for: .corrected)
    #expect(model.correctedAAF == slow)
    let runID = model.current?.id
    model.start()
    #expect(model.current?.id == runID)
    model.cancel()
    #expect(model.isCancelling)
    #expect(!model.canCancel)
    try await eventually { !model.isRunning }
    #expect(model.current?.status == .cancelled)
    #expect(model.current?.terminal == nil)
    #expect(model.current?.reportURLs.isEmpty == true)
    model.setAAF(fixture.folder.appendingPathComponent("retry.aaf"), for: .corrected)
    model.start()
    try await eventually { !model.isRunning }
    #expect(model.current?.status == .finished(.pass))
    #expect(model.current?.findings.isEmpty == true)
    #expect(model.current?.diffItems.isEmpty == true)
}

@Test @MainActor func compareRunsExplicitRolesAndRetainsCorrectedFindingsAndReportURLs() async throws {
    let fixture = try RunFixture(script: #"""
    if [ "$#" -ne 5 ] || [ "$1" != compare ] || [ "$2" != --flagged ] || [ "$4" != --corrected ]; then
        printf '%s\n' '{"schema_version":1,"type":"terminal","verdict":"ERROR","reason":"Incorrect arguments","report_paths":[]}'
        exit 2
    fi
    printf '%s\n' "$3" "$5" > "$(dirname "$0")/roles"
    printf '%s\n' '{"schema_version":1,"type":"finding","aaf_role":"flagged","rule_id":"container_corrupt","severity":"FAIL","message":"Flagged only."}'
    printf '%s\n' '{"schema_version":1,"type":"finding","aaf_role":"corrected","rule_id":"short_handles","severity":"WARN","message":"Corrected finding."}'
    printf '%s\n' '{"schema_version":1,"type":"diff_item","kind":"persisting","blocking":false,"corrected_finding":{"rule_id":"short_handles","severity":"WARN","message":"Corrected finding."}}'
    printf '%s\n' '{"schema_version":1,"type":"terminal","verdict":"PASS","flagged_verdict":"FAIL","corrected_verdict":"PASS","blocking_regressions":0,"paired_clips":3,"report_paths":["/tmp/Corrected delivery/reel #1_Compare.pdf","/tmp/Corrected delivery/reel #1_Compare.json"]}'
    """#)
    defer { fixture.remove() }
    let model = CompareModel(runner: WorkerRunner(executableURL: fixture.worker))
    let flagged = fixture.folder.appendingPathComponent("Flagged reel.aaf")
    let corrected = fixture.folder.appendingPathComponent("Corrected reel.aaf")
    model.setAAF(flagged, for: .flagged)
    model.setAAF(corrected, for: .corrected)
    model.start()
    try await eventually { !model.isRunning }
    let run = try #require(model.current)
    #expect(run.status == .finished(.pass))
    #expect(run.terminal?.flaggedVerdict == .fail)
    #expect(run.terminal?.blockingRegressions == 0)
    #expect(run.regressions.isEmpty)
    #expect(run.changes.isEmpty)
    #expect(run.findings.map(\.ruleID) == ["short_handles"])
    #expect(run.classifiedFindings("persisting").count == 1)
    #expect(run.reportURLs.map(\.path) == ["/tmp/Corrected delivery/reel #1_Compare.pdf", "/tmp/Corrected delivery/reel #1_Compare.json"])
    #expect(run.reportURLs.allSatisfy { $0.isFileURL })
    #expect(try String(contentsOf: fixture.folder.appendingPathComponent("roles"), encoding: .utf8) == "\(flagged.path)\n\(corrected.path)\n")
}

@Test @MainActor func compareRequiresTwoDifferentAAFSelections() throws {
    let fixture = try RunFixture(script: "exit 2")
    defer { fixture.remove() }
    let model = CompareModel(runner: WorkerRunner(executableURL: fixture.worker))
    let flagged = fixture.folder.appendingPathComponent("flagged.aaf")
    let corrected = fixture.folder.appendingPathComponent("corrected.aaf")
    try Data().write(to: flagged)
    #expect(!model.canCompare)
    model.setAAF(flagged, for: .flagged)
    #expect(!model.canCompare)
    model.start()
    #expect(!model.isRunning)
    #expect(model.current == nil)
    model.setAAF(flagged, for: .corrected)
    #expect(!model.canCompare)
    model.setAAF(fixture.folder.appendingPathComponent("sub/../flagged.aaf"), for: .corrected)
    #expect(!model.canCompare)
    let alias = fixture.folder.appendingPathComponent("alias.aaf")
    try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: flagged)
    model.setAAF(alias, for: .corrected)
    #expect(!model.canCompare)
    model.setAAF(corrected, for: .corrected)
    #expect(model.canCompare)
    #expect(model.flaggedAAF == flagged)
    #expect(model.correctedAAF == corrected)
    model.setAAF(nil, for: .flagged)
    #expect(!model.canCompare)
    model.setAAF(URL(string: "https://example.com/flagged.aaf")!, for: .flagged)
    #expect(!model.canCompare)
    model.setAAF(fixture.folder.appendingPathComponent("audio.wav"), for: .flagged)
    #expect(!model.canCompare)
}
