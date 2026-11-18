import Foundation
import Testing
import AAFGateCore

private let progressLine = #"{"schema_version":1,"type":"progress","clips_done":2,"clips_total":3,"bytes_read":4758714}"#

struct RunFixture {
    let folder: URL
    let worker: URL

    init(script: String) throws {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        worker = folder.appendingPathComponent("worker")
        try ("#!/bin/sh\n" + script + "\n").write(to: worker, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: worker.path)
    }

    func remove() { try? FileManager.default.removeItem(at: folder) }
}

@MainActor
func eventually(_ condition: () -> Bool) async throws {
    let deadline = ContinuousClock.now + .seconds(5)
    while !condition(), ContinuousClock.now < deadline {
        try await Task.sleep(for: .milliseconds(10))
    }
    #expect(condition(), "Expected run state within five seconds")
}

@Test @MainActor func missingTerminalStreamFinishesAsError() async throws {
    let fixture = try RunFixture(script: "printf '%s\\n' '\(progressLine)'")
    defer { fixture.remove() }
    let queue = CheckQueue(runner: WorkerRunner(executableURL: fixture.worker))
    queue.enqueue([fixture.folder.appendingPathComponent("synthetic.aaf")])
    try await eventually { !queue.isRunning }
    let result = try #require(queue.completed.first)
    #expect(result.status == .finished(.error))
    #expect(result.detail.contains("without a terminal event"))
    #expect(result.reportPaths.isEmpty)
}

@Test @MainActor func multipleDropsRunSerialChecksWithSeparateVerdicts() async throws {
    let fixture = try RunFixture(script: """
    if [ "$1" != check ] || ! mkdir "$(dirname "$0")/busy"; then
        printf '%s\\n' '{"schema_version":1,"type":"terminal","verdict":"ERROR","report_paths":[],"reason":"Overlapping or non-check run"}'
        exit 2
    fi
    sleep 0.05
    rmdir "$(dirname "$0")/busy"
    case "$2" in
      *first.aaf) verdict=FAIL; status=1 ;;
      *second.aaf) verdict=PASS; status=0 ;;
      *) verdict=ERROR; status=2 ;;
    esac
    printf '{"schema_version":1,"type":"terminal","verdict":"%s","report_paths":[]}\\n' "$verdict"
    exit "$status"
    """)
    defer { fixture.remove() }
    let queue = CheckQueue(runner: WorkerRunner(executableURL: fixture.worker))
    let urls = ["first.aaf", "second.aaf", "third.aaf"].map { fixture.folder.appendingPathComponent($0) }
    queue.enqueue(Array(urls.prefix(2))) // A two-file Dock drop is always two checks.
    queue.enqueue([urls[2]]) // A drop arriving during a run joins the queue.
    try await eventually { !queue.isRunning }
    #expect(queue.completed.map(\.aaf) == urls)
    #expect(queue.completed.map(\.status) == [.finished(.fail), .finished(.pass), .finished(.error)])
}

@Test @MainActor func progressUpdatesBeforeWorkerExits() async throws {
    let fixture = try RunFixture(script: """
    printf '%s\\n' '\(progressLine)'
    i=0
    while [ ! -e "$(dirname "$0")/finish" ] && [ "$i" -lt 200 ]; do
        sleep 0.01
        i=$((i + 1))
    done
    printf '%s\\n' '{"schema_version":1,"type":"terminal","verdict":"PASS","report_paths":[]}'
    """)
    defer { fixture.remove() }
    let queue = CheckQueue(runner: WorkerRunner(executableURL: fixture.worker))
    queue.enqueue([fixture.folder.appendingPathComponent("synthetic.aaf")])
    try await eventually { queue.current?.progress?.clipsDone == 2 }
    #expect(queue.isRunning)
    #expect(queue.current?.progress?.clipsTotal == 3)
    #expect(queue.current?.progress?.bytesRead == 4_758_714)
    try Data().write(to: fixture.folder.appendingPathComponent("finish"))
    try await eventually { !queue.isRunning }
    #expect(queue.completed.first?.status == .finished(.pass))
}

@Test(arguments: [false, true]) @MainActor
func cancelStopsWorkerAndContinuesQueue(ignoresTerm: Bool) async throws {
    let fixture = try RunFixture(script: """
    case "$2" in
      *cancel.aaf)
        \(ignoresTerm ? "trap '' TERM" : "trap 'exit 0' TERM")
        printf '%s\\n' '\(progressLine)'
        i=0
        while [ "$i" -lt 200 ]; do sleep 0.01; i=$((i + 1)); done
        touch "$(dirname "$2")/unexpected-report.pdf"
        ;;
    esac
    printf '%s\\n' '{"schema_version":1,"type":"terminal","verdict":"PASS","report_paths":[]}'
    """)
    defer { fixture.remove() }
    let delivery = fixture.folder.appendingPathComponent("delivery")
    try FileManager.default.createDirectory(at: delivery, withIntermediateDirectories: true)
    let aaf = delivery.appendingPathComponent("cancel.aaf")
    try Data("synthetic placeholder".utf8).write(to: aaf)
    let before = try FileManager.default.contentsOfDirectory(atPath: delivery.path)
    let queue = CheckQueue(runner: WorkerRunner(executableURL: fixture.worker))
    queue.enqueue([aaf, delivery.appendingPathComponent("next.aaf")])
    try await eventually { queue.current?.progress != nil }
    let start = ContinuousClock.now
    queue.cancel()
    try await eventually { !queue.isRunning }
    #expect(start.duration(to: .now) < .seconds(2))
    #expect(queue.completed.map(\.status) == [.cancelled, .finished(.pass)])
    #expect(queue.completed.first?.reportPaths.isEmpty == true)
    #expect(try FileManager.default.contentsOfDirectory(atPath: delivery.path) == before)
    #expect(try Data(contentsOf: aaf) == Data("synthetic placeholder".utf8))
}

@Test @MainActor func cannotCancelAfterTerminalReportsArePublished() async throws {
    let fixture = try RunFixture(script: """
    printf '%s\\n' '\(progressLine)'
    while [ ! -e "$(dirname "$0")/terminal" ]; do sleep 0.01; done
    printf '%s\\n' '{"schema_version":1,"type":"terminal","verdict":"PASS","report_paths":[]}'
    while [ ! -e "$(dirname "$0")/finish" ]; do sleep 0.01; done
    """)
    defer { fixture.remove() }
    let queue = CheckQueue(runner: WorkerRunner(executableURL: fixture.worker))
    queue.enqueue([fixture.folder.appendingPathComponent("synthetic.aaf")])
    try await eventually { queue.current?.progress != nil }
    #expect(queue.canCancel)
    try Data().write(to: fixture.folder.appendingPathComponent("terminal"))
    try await eventually { !queue.canCancel }
    #expect(queue.isRunning)
    queue.cancel()
    try Data().write(to: fixture.folder.appendingPathComponent("finish"))
    try await eventually { !queue.isRunning }
    #expect(queue.completed.first?.status == .finished(.pass))
}

@Test @MainActor func cancelImmediatelyAfterEnqueueDoesNotLaunchWorker() async throws {
    let fixture = try RunFixture(script: "touch \"$(dirname \"$0\")/unexpected-launch\"")
    defer { fixture.remove() }
    let queue = CheckQueue(runner: WorkerRunner(executableURL: fixture.worker))
    queue.enqueue([fixture.folder.appendingPathComponent("synthetic.aaf")])
    queue.cancel()
    try await eventually { !queue.isRunning }
    #expect(queue.completed.first?.status == .cancelled)
    #expect(!FileManager.default.fileExists(atPath: fixture.folder.appendingPathComponent("unexpected-launch").path))
}

@Test @MainActor func completedRunsRetainFindingsAndFilteringDoesNotRerunWorker() async throws {
    let line = #"{"schema_version":1,"type":"finding","rule_id":"digital_silence","rule_name":"Digital silence","severity":"FAIL","track":{"slot_id":3,"name":"Audio 1"},"record_in_timecode":"01:00:00:00","clip_name":"Clip 1","source_file":"clip_1.wav","message":"Every sample is zero.","fix_instruction":"Re-export."}"#
    let fixture = try RunFixture(script: """
    printf 'launch\\n' >> "$(dirname "$0")/launches"
    case "$2" in
      *first.aaf)
        printf '%s\\n' '\(line)'
        printf '%s\\n' '{"schema_version":1,"type":"terminal","verdict":"FAIL","report_paths":[]}'
        exit 1 ;;
    esac
    printf '%s\\n' '{"schema_version":1,"type":"terminal","verdict":"PASS","report_paths":[]}'
    """)
    defer { fixture.remove() }
    let queue = CheckQueue(runner: WorkerRunner(executableURL: fixture.worker))
    queue.enqueue([fixture.folder.appendingPathComponent("first.aaf"), fixture.folder.appendingPathComponent("second.aaf")])
    try await eventually { !queue.isRunning }
    #expect(queue.completed.map(\.findings.count) == [1, 0])
    #expect(queue.current?.findings.isEmpty == true)
    let first = try #require(queue.completed.first)
    let model = FindingsModel(findings: first.findings)
    model.severities = [.warn]
    #expect(model.groups.isEmpty)
    model.severities = [.fail]
    #expect(model.groups.first?.rows.first?.finding.clipName == "Clip 1")
    #expect(first.status == .finished(.fail))
    #expect(try String(contentsOf: fixture.folder.appendingPathComponent("launches"), encoding: .utf8) == "launch\nlaunch\n")
}

@Test(arguments: ["PASS", "FAIL", "ERROR"]) @MainActor
func unsavedReportsPreserveVerdictAndWorkerReason(verdict: String) async throws {
    let status = ["PASS": 0, "FAIL": 1, "ERROR": 2][verdict]!
    let fixture = try RunFixture(script: """
    printf '%s\\n' '{"schema_version":1,"type":"terminal","verdict":"\(verdict)","report_paths":[],"reason":"Worker result reason","report_error":"Report not saved: Permission denied"}'
    exit \(status)
    """)
    defer { fixture.remove() }
    let queue = CheckQueue(runner: WorkerRunner(executableURL: fixture.worker))
    queue.enqueue([fixture.folder.appendingPathComponent("readonly.aaf")])
    try await eventually { !queue.isRunning }
    let result = try #require(queue.completed.first)
    #expect(result.status == .finished(Verdict(rawValue: verdict)!))
    #expect(result.reportURLs.isEmpty)
    #expect(result.reportSaveMessage == "Report could not be saved. Report not saved: Permission denied")
    #expect(result.detail == "Worker result reason")
}

@Test @MainActor func reportRevealKeepsBothExactFileURLs() async throws {
    let fixture = try RunFixture(script: #"printf '%s\n' '{"schema_version":1,"type":"terminal","verdict":"PASS","report_paths":["/tmp/Synthetic reports/clip #1.pdf","/tmp/Synthetic reports/clip #1.json"]}'"#)
    defer { fixture.remove() }
    let queue = CheckQueue(runner: WorkerRunner(executableURL: fixture.worker))
    queue.enqueue([fixture.folder.appendingPathComponent("synthetic.aaf")])
    try await eventually { !queue.isRunning }
    let result = try #require(queue.completed.first)
    #expect(result.reportURLs.map(\.path) == ["/tmp/Synthetic reports/clip #1.pdf", "/tmp/Synthetic reports/clip #1.json"])
    #expect(result.reportURLs.allSatisfy { $0.isFileURL })
    #expect(result.reportSaveMessage == nil)
}
