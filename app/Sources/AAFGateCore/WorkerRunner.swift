import Foundation
import Darwin

/// One process per run. Only worker events determine the displayed result.
public struct WorkerRunner: Sendable {
    public let executableURL: URL

    public init(executableURL: URL) {
        self.executableURL = executableURL
    }

    public func check(
        _ aaf: URL,
        log: WorkerLog? = nil,
        onEvent: @escaping @Sendable (WorkerEvent) async -> Void
    ) async throws -> TerminalEvent {
        try await run(arguments: ["check", aaf.path], log: log, onEvent: onEvent)
    }

    public func compare(
        flagged: URL,
        corrected: URL,
        log: WorkerLog? = nil,
        onEvent: @escaping @Sendable (WorkerEvent) async -> Void
    ) async throws -> TerminalEvent {
        try await run(
            arguments: ["compare", "--flagged", flagged.path, "--corrected", corrected.path],
            log: log, onEvent: onEvent
        )
    }

    private func run(
        arguments: [String],
        log: WorkerLog?,
        onEvent: @escaping @Sendable (WorkerEvent) async -> Void
    ) async throws -> TerminalEvent {
        let control = WorkerProcessControl()
        return try await withTaskCancellationHandler {
            try await Task.detached {
                let process = Process()
                process.executableURL = executableURL
                process.arguments = arguments
                process.environment = ProcessInfo.processInfo.environment.filter {
                    !$0.key.hasPrefix("PYTHON") && $0.key != "VIRTUAL_ENV"
                }
                let stdout = Pipe()
                let stderr = Pipe()
                process.standardOutput = stdout
                process.standardError = stderr
                try control.start(process)
                defer { control.finish() }

                // Drain stderr concurrently so a verbose worker cannot fill its pipe.
                let diagnostics = Task.detached {
                    var tail = Data()
                    while let chunk = try? await readChunk(stderr.fileHandleForReading), !chunk.isEmpty {
                        log?.append(chunk)
                        tail.append(chunk)
                        if tail.count > 8192 { tail = Data(tail.suffix(8192)) }
                    }
                    return String(decoding: tail, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
                }
                let decoder = WorkerEventDecoder()
                var terminal: TerminalEvent?
                func consume(_ data: Data) async throws {
                    guard !data.isEmpty else { return }
                    guard let line = String(data: data, encoding: .utf8) else {
                        throw WorkerRunError.invalidOutput("Worker output is not valid UTF-8.")
                    }
                    if let event = try decoder.decode(line: line) {
                        guard terminal == nil else {
                            throw WorkerRunError.invalidOutput("Worker sent an event after its terminal result.")
                        }
                        if case .terminal(let value) = event { terminal = value }
                        await onEvent(event)
                    }
                }

                do {
                    var pending = Data()
                    while let chunk = try await readChunk(stdout.fileHandleForReading), !chunk.isEmpty {
                        log?.append(chunk)
                        pending.append(chunk)
                        while let newline = pending.firstIndex(of: 0x0A) {
                            let line = Data(pending[..<newline])
                            pending.removeSubrange(...newline)
                            try await consume(line)
                        }
                    }
                    try await consume(pending)
                    await waitForExit(process)
                    let stderrText = await diagnostics.value
                    try control.checkCancellation()
                    guard let terminal else {
                        throw WorkerRunError.invalidOutput(
                            "Worker exited without a terminal event (status \(process.terminationStatus))."
                            + (stderrText.isEmpty ? "" : "\n\(stderrText)")
                        )
                    }
                    let expectedStatus: Int32 = switch terminal.verdict {
                    case .pass: 0
                    case .fail: 1
                    case .error: 2
                    }
                    guard process.terminationReason == .exit, process.terminationStatus == expectedStatus else {
                        throw WorkerRunError.invalidOutput("Worker exited unexpectedly (status \(process.terminationStatus)).")
                    }
                    return terminal
                } catch {
                    control.stop()
                    // Closing stdout also unblocks a worker still trying to write.
                    try? stdout.fileHandleForReading.close()
                    await waitForExit(process)
                    _ = await diagnostics.value
                    try control.checkCancellation()
                    throw error
                }
            }.value
        } onCancel: {
            control.cancel()
        }
    }
}

public enum WorkerRunError: LocalizedError, Sendable {
    case invalidOutput(String)

    public var errorDescription: String? {
        switch self {
        case .invalidOutput(let message): message
        }
    }
}

// FileHandle.read(upToCount:) can wait to fill the buffer on a pipe. A single
// POSIX read returns available bytes so progress is delivered while it is live.
private func readChunk(_ handle: FileHandle) async throws -> Data? {
    // Pipe reads block. Keep them off Swift's cooperative executor so multiple
    // runs cannot starve the tasks that consume events or finish cancellation.
    try await withCheckedThrowingContinuation { continuation in
        DispatchQueue.global(qos: .utility).async {
            do { continuation.resume(returning: try readAvailableChunk(handle)) }
            catch { continuation.resume(throwing: error) }
        }
    }
}

private func waitForExit(_ process: Process) async {
    await withCheckedContinuation { continuation in
        DispatchQueue.global(qos: .utility).async {
            process.waitUntilExit()
            continuation.resume()
        }
    }
}

private func readAvailableChunk(_ handle: FileHandle) throws -> Data? {
    var buffer = [UInt8](repeating: 0, count: 8192)
    while true {
        let count = Darwin.read(handle.fileDescriptor, &buffer, buffer.count)
        if count == 0 { return nil }
        if count > 0 { return Data(buffer.prefix(count)) }
        if errno != EINTR { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
    }
}

/// Serializes start/cancel/exit so cancellation before launch cannot be lost,
/// and a delayed SIGKILL can never target a later process with a reused PID.
private final class WorkerProcessControl: @unchecked Sendable {
    private let lock = NSLock()
    private var process: Process?
    private var cancelled = false

    func start(_ process: Process) throws {
        try lock.withLock {
            if cancelled { throw CancellationError() }
            try process.run()
            self.process = process
        }
    }

    func checkCancellation() throws {
        try lock.withLock {
            if cancelled { throw CancellationError() }
        }
    }

    func finish() { lock.withLock { process = nil } }

    func cancel() {
        lock.withLock { cancelled = true }
        stop()
    }

    func stop() {
        let needsKill = lock.withLock {
            guard let process, process.isRunning else { return false }
            process.terminate()
            return true
        }
        guard needsKill else { return }
        Task.detached {
            try? await Task.sleep(for: .milliseconds(500))
            self.lock.withLock {
                if let process = self.process, process.isRunning {
                    Darwin.kill(process.processIdentifier, SIGKILL)
                }
            }
        }
    }
}
