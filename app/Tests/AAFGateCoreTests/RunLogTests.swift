import Foundation
import Testing
import AAFGateCore

@Test @MainActor func eachRunLogsRawEventsAndStderrWithoutOverwriting() async throws {
    let raw = #"{"schema_version":1,"type":"future_event","payload":"preserve me"}"#
    let fixture = try RunFixture(script: """
    printf '%s\\n' '\(raw)'
    printf '%s\\n' 'synthetic worker diagnostic' >&2
    printf '%s\\n' '{"schema_version":1,"type":"terminal","verdict":"PASS","report_paths":[]}'
    """)
    defer { fixture.remove() }
    let folder = fixture.folder.appendingPathComponent("Library/Logs/AAF Gate")
    let logs = RunLogStore(directoryURL: folder, now: { Date(timeIntervalSince1970: 1_700_000_000) })
    let queue = CheckQueue(runner: WorkerRunner(executableURL: fixture.worker), logs: logs)
    let aaf = fixture.folder.appendingPathComponent("synthetic.aaf")
    for _ in 0..<2 {
        queue.enqueue([aaf])
        try await eventually { !queue.isRunning }
    }
    let files = try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
    #expect(files.count == 2)
    #expect(files.allSatisfy { $0.lastPathComponent.hasPrefix("20231114-221320-000_synthetic") && $0.pathExtension == "log" })
    for file in files {
        let content = try String(contentsOf: file, encoding: .utf8)
        #expect(content.contains(raw + "\n"))
        #expect(content.contains("synthetic worker diagnostic\n"))
        #expect(content.contains(#""verdict":"PASS""#))
    }
    #expect(queue.completed.map(\.status) == [.finished(.pass), .finished(.pass)])
}

@Test @MainActor func launchDeletesOnlyLogsOlderThanThirtyDays() throws {
    let fixture = try RunFixture(script: "exit 0")
    defer { fixture.remove() }
    let folder = fixture.folder.appendingPathComponent("logs")
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let ages: [String: TimeInterval] = [
        "expired.log": 30 * 86400 + 1,
        "boundary.log": 30 * 86400,
        "recent.log": 29 * 86400,
        "future.log": -86400,
        "keep.txt": 60 * 86400,
    ]
    for (name, age) in ages {
        let file = folder.appendingPathComponent(name)
        try Data("existing log".utf8).write(to: file)
        try FileManager.default.setAttributes([.modificationDate: now.addingTimeInterval(-age)], ofItemAtPath: file.path)
    }
    let outside = fixture.folder.appendingPathComponent("outside.log")
    try Data("untouched".utf8).write(to: outside)
    try FileManager.default.createSymbolicLink(at: folder.appendingPathComponent("link.log"), withDestinationURL: outside)
    let queue = CheckQueue(
        runner: WorkerRunner(executableURL: fixture.worker),
        logs: RunLogStore(directoryURL: folder, now: { now })
    )
    #expect(!queue.isRunning)
    #expect(try FileManager.default.contentsOfDirectory(atPath: folder.path).sorted() == [
        "boundary.log", "future.log", "keep.txt", "link.log", "recent.log",
    ])
    #expect(try String(contentsOf: outside, encoding: .utf8) == "untouched")
}
