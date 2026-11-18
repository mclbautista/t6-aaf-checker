import Combine
import Foundation

public enum CheckStatus: Equatable, Sendable {
    case cancelled
    case running
    case finished(Verdict)
}

public struct CheckProgress: Equatable, Sendable {
    public let clipsDone: Int
    public let clipsTotal: Int
    public let bytesRead: Int64
}

public struct CheckRun: Identifiable, Sendable {
    public let id = UUID()
    public let aaf: URL
    public var status: CheckStatus = .running
    public var progress: CheckProgress?
    public var detail = ""
    public var reportPaths: [String] = []
    public var findings: [WorkerFinding] = []
    public var reportError: String?
    public var reportURLs: [URL] { reportPaths.map { URL(fileURLWithPath: $0) } }
    public var reportSaveMessage: String? {
        guard reportPaths.isEmpty, let reportError else { return nil }
        return "Report could not be saved. \(reportError)"
    }
}

/// App-facing run state. Verdicts come only from completed worker runs.
@MainActor
public final class CheckQueue: ObservableObject {
    @Published public private(set) var current: CheckRun?
    @Published public private(set) var completed: [CheckRun] = []
    @Published public private(set) var pending: [URL] = []
    @Published public private(set) var isRunning = false
    @Published public private(set) var isCancelling = false
    @Published public private(set) var logRetentionError: String?
    @Published private var receivedTerminal = false
    public var canCancel: Bool { isRunning && !isCancelling && !receivedTerminal }
    private var activeTask: Task<TerminalEvent, Error>?
    private var receivedFindings: [WorkerFinding] = []
    private let runner: WorkerRunner
    private let logs: RunLogStore?

    public init(runner: WorkerRunner, logs: RunLogStore? = nil) {
        self.runner = runner
        self.logs = logs
        do { try logs?.removeExpiredLogs() }
        catch { logRetentionError = "Old logs could not be removed: \(error.localizedDescription)" }
    }

    public func enqueue(_ urls: [URL]) {
        for url in urls.map(\.standardizedFileURL) {
            guard url.isFileURL, url.pathExtension.lowercased() == "aaf" else { continue }
            guard !pending.contains(url), !(isRunning && current?.aaf == url) else { continue }
            pending.append(url)
        }
        guard !isRunning, !pending.isEmpty else { return }
        isRunning = true
        receivedTerminal = false
        Task { await drainQueue() }
    }

    public func cancel() {
        guard canCancel else { return }
        isCancelling = true
        activeTask?.cancel()
    }

    private func receive(_ event: WorkerEvent) {
        if case .finding(let finding) = event { receivedFindings.append(finding) }
        if case .terminal = event { receivedTerminal = true }
        if case .progress(let done, let total, let bytes) = event {
            current?.progress = CheckProgress(clipsDone: done, clipsTotal: total, bytesRead: bytes)
        }
    }

    private func drainQueue() async {
        while !pending.isEmpty {
            let aaf = pending.removeFirst()
            current = CheckRun(aaf: aaf)
            receivedFindings = []
            receivedTerminal = false
            var log: WorkerLog?
            var logError: String?
            do { log = try logs?.makeLog(for: aaf) }
            catch { logError = error.localizedDescription }
            let runLog = log
            let task = Task {
                try await runner.check(aaf, log: runLog) { event in
                    await self.receive(event)
                }
            }
            activeTask = task
            if isCancelling { task.cancel() }
            do {
                let terminal = try await task.value
                if isCancelling { throw CancellationError() }
                current?.status = .finished(terminal.verdict)
                current?.detail = terminal.reason ?? ""
                current?.reportError = terminal.reportError
                current?.reportPaths = terminal.reportPaths
                current?.findings = receivedFindings
            } catch is CancellationError {
                current?.status = .cancelled
                current?.reportPaths = []
            } catch {
                current?.status = .finished(.error)
                current?.detail = error.localizedDescription
            }
            if let error = logError ?? log?.failure {
                current?.detail += "\nLog could not be saved: \(error)"
            }
            if let current { completed.append(current) }
            activeTask = nil
            isCancelling = false
        }
        isRunning = false
    }
}
