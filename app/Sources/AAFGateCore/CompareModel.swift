import Combine
import Foundation

public enum AAFRole: String, Sendable {
    case flagged
    case corrected

    public var label: String {
        switch self {
        case .flagged: "Flagged AAF"
        case .corrected: "Corrected AAF"
        }
    }
}

public struct CompareRun: Identifiable, Sendable {
    public let id = UUID()
    public let flaggedAAF: URL
    public let correctedAAF: URL
    public var status: CheckStatus = .running
    public var progress: CheckProgress?
    public var progressRole: String?
    public var terminal: TerminalEvent?
    public var detail = ""
    public var findings: [WorkerFinding] = []
    public var diffItems: [DiffItem] = []

    public var regressions: [DiffItem] { diffItems.filter(\.blocking) }
    public var changes: [DiffItem] {
        diffItems.filter { !$0.blocking && !["resolved", "persisting", "new"].contains($0.kind) }
    }
    public func classifiedFindings(_ kind: String) -> [DiffItem] {
        diffItems.filter { $0.kind == kind }
    }
    public var reportURLs: [URL] { (terminal?.reportPaths ?? []).map { URL(fileURLWithPath: $0) } }
    public var reportSaveMessage: String? {
        guard reportURLs.isEmpty, let error = terminal?.reportError else { return nil }
        return "Report could not be saved. \(error)"
    }
}

@MainActor
public final class CompareModel: ObservableObject {
    @Published public private(set) var flaggedAAF: URL?
    @Published public private(set) var correctedAAF: URL?
    @Published public private(set) var current: CompareRun?
    @Published public private(set) var isRunning = false
    @Published public private(set) var isCancelling = false
    @Published public private(set) var logRetentionError: String?
    @Published private var receivedTerminal = false
    public var canCancel: Bool { isRunning && !isCancelling && !receivedTerminal }
    private var activeTask: Task<Void, Never>?
    private let runner: WorkerRunner
    private let logs: RunLogStore?

    public init(runner: WorkerRunner, logs: RunLogStore? = nil) {
        self.runner = runner
        self.logs = logs
        do { try logs?.removeExpiredLogs() }
        catch { logRetentionError = "Old logs could not be removed: \(error.localizedDescription)" }
    }

    public var canCompare: Bool {
        guard !isRunning, let flaggedAAF, let correctedAAF else { return false }
        return flaggedAAF.resolvingSymlinksInPath() != correctedAAF.resolvingSymlinksInPath()
    }

    public func setAAF(_ url: URL?, for role: AAFRole) {
        guard !isRunning else { return }
        if let url, !url.isFileURL || url.pathExtension.lowercased() != "aaf" { return }
        switch role {
        case .flagged: flaggedAAF = url?.standardizedFileURL
        case .corrected: correctedAAF = url?.standardizedFileURL
        }
    }

    public func start() {
        guard canCompare, let flaggedAAF, let correctedAAF else { return }
        current = CompareRun(flaggedAAF: flaggedAAF, correctedAAF: correctedAAF)
        isRunning = true
        receivedTerminal = false
        activeTask = Task {
            var log: WorkerLog?
            var logError: String?
            do { log = try logs?.makeLog(for: correctedAAF) }
            catch { logError = error.localizedDescription }
            do {
                let terminal = try await runner.compare(flagged: flaggedAAF, corrected: correctedAAF, log: log) { event in
                    await self.receive(event)
                }
                try Task.checkCancellation()
                current?.terminal = terminal
                current?.status = .finished(terminal.verdict)
                current?.detail = terminal.reason ?? ""
            } catch is CancellationError {
                current?.status = .cancelled
            } catch {
                current?.status = .finished(.error)
                current?.detail = error.localizedDescription
            }
            if let error = logError ?? log?.failure {
                current?.detail += "\nLog could not be saved: \(error)"
            }
            isRunning = false
            isCancelling = false
            activeTask = nil
        }
    }

    public func cancel() {
        guard canCancel else { return }
        isCancelling = true
        activeTask?.cancel()
    }

    private func receive(_ event: WorkerEvent) {
        switch event {
        case .terminal:
            receivedTerminal = true
        case .finding(let finding) where finding.aafRole == "corrected":
            current?.findings.append(finding)
        case .diffItem(let item):
            current?.diffItems.append(item)
        case .progress(let done, let total, let bytes, let role):
            current?.progress = CheckProgress(clipsDone: done, clipsTotal: total, bytesRead: bytes)
            current?.progressRole = role
        default: break
        }
    }
}
