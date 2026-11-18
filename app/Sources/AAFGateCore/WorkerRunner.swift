import Foundation

/// One process per check. Only worker events determine the displayed result.
public struct WorkerRunner: Sendable {
    public let executableURL: URL

    public init(executableURL: URL) {
        self.executableURL = executableURL
    }

    public func check(
        _ aaf: URL,
        onEvent: @escaping @Sendable (WorkerEvent) async -> Void
    ) async throws -> TerminalEvent {
        try await Task.detached {
            let process = Process()
            process.executableURL = executableURL
            process.arguments = ["check", aaf.path]
            process.environment = ProcessInfo.processInfo.environment.filter {
                !$0.key.hasPrefix("PYTHON") && $0.key != "VIRTUAL_ENV"
            }
            let stdout = Pipe()
            let stderr = Pipe()
            process.standardOutput = stdout
            process.standardError = stderr
            try process.run()

            // Drain stderr concurrently so a verbose worker cannot fill its pipe.
            let diagnostics = Task.detached {
                var tail = Data()
                while let chunk = try? stderr.fileHandleForReading.read(upToCount: 8192), !chunk.isEmpty {
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
                while let chunk = try stdout.fileHandleForReading.read(upToCount: 64 * 1024), !chunk.isEmpty {
                    pending.append(chunk)
                    while let newline = pending.firstIndex(of: 0x0A) {
                        let line = Data(pending[..<newline])
                        pending.removeSubrange(...newline)
                        try await consume(line)
                    }
                }
                try await consume(pending)
                process.waitUntilExit()
                let stderrText = await diagnostics.value
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
                if process.isRunning { process.terminate() }
                // Closing stdout also unblocks a worker still trying to write.
                try? stdout.fileHandleForReading.close()
                process.waitUntilExit()
                _ = await diagnostics.value
                throw error
            }
        }.value
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
