import Foundation

public enum WorkerEvent: Equatable, Sendable {
    case runStarted(mode: String, workerVersion: String, ruleVersion: String)
    case progress(clipsDone: Int, clipsTotal: Int, bytesRead: Int64, aafRole: String? = nil)
    case finding(WorkerFinding)
    case diffItem(DiffItem)
    case terminal(TerminalEvent)
}

public enum Verdict: String, Decodable, Sendable {
    case pass = "PASS"
    case fail = "FAIL"
    case error = "ERROR"
}

public struct TerminalEvent: Decodable, Equatable, Sendable {
    public let verdict: Verdict
    public let reason: String?
    public let reportPaths: [String]
    public let reportError: String?
    public let flaggedVerdict: Verdict?
    public let correctedVerdict: Verdict?
    public let blockingRegressions: Int?
    public let pairedClips: Int?
}

public struct WorkerEventDecoder: Sendable {
    public init() {}

    public func decode(line: String) throws -> WorkerEvent? {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let data = Data(line.utf8)
        let header = try decoder.decode(Header.self, from: data)
        guard header.schemaVersion == 1 else {
            throw WorkerProtocolError.unsupportedSchema(header.schemaVersion)
        }
        switch header.type {
        case "run_started":
            let value = try decoder.decode(Started.self, from: data)
            return .runStarted(mode: value.mode, workerVersion: value.workerVersion, ruleVersion: value.ruleVersion)
        case "progress":
            let value = try decoder.decode(Progress.self, from: data)
            return .progress(clipsDone: value.clipsDone, clipsTotal: value.clipsTotal, bytesRead: value.bytesRead, aafRole: value.aafRole)
        case "finding":
            return .finding(try decoder.decode(WorkerFinding.self, from: data))
        case "diff_item":
            return .diffItem(try decoder.decode(DiffItem.self, from: data))
        case "terminal":
            return .terminal(try decoder.decode(TerminalEvent.self, from: data))
        default:
            return nil
        }
    }
}

public enum WorkerProtocolError: LocalizedError, Sendable {
    case unsupportedSchema(Int)

    public var errorDescription: String? {
        switch self {
        case .unsupportedSchema(let version):
            return "Unsupported worker schema_version \(version). This app supports version 1."
        }
    }
}

private struct Header: Decodable {
    let schemaVersion: Int
    let type: String
}

private struct Started: Decodable {
    let mode: String
    let workerVersion: String
    let ruleVersion: String
}

private struct Progress: Decodable {
    let clipsDone: Int
    let clipsTotal: Int
    let bytesRead: Int64
    let aafRole: String?
}
