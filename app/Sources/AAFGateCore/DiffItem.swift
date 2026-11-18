import Foundation

/// Worker frame values are integers or exact rational strings, never rounded.
public enum FrameValue: Decodable, Equatable, Sendable, CustomStringConvertible {
    case integer(Int64)
    case fraction(String)

    public init(from decoder: any Decoder) throws {
        let value = try decoder.singleValueContainer()
        if let integer = try? value.decode(Int64.self) { self = .integer(integer) }
        else { self = .fraction(try value.decode(String.self)) }
    }

    public var description: String {
        switch self {
        case .integer(let value): String(value)
        case .fraction(let value): value
        }
    }
}

public struct DiffPosition: Decodable, Equatable, Sendable {
    public let track: FindingTrack?
    public let recordInTimecode: String?
    public let recordInFrames: FrameValue?
    public let sourceInFrames: FrameValue?
    public let lengthFrames: FrameValue?
}

public struct DiffItem: Decodable, Equatable, Sendable {
    public let kind: String
    public let blocking: Bool
    public let clipName: String?
    public let sourceFile: String?
    public let message: String?
    public let severity: Severity?
    public let ruleName: String?
    public let deltaFrames: FrameValue?
    public let flaggedPosition: DiffPosition?
    public let correctedPosition: DiffPosition?
    public let flaggedPositions: [DiffPosition]?
    public let correctedPositions: [DiffPosition]?
    public let flaggedFinding: WorkerFinding?
    public let correctedFinding: WorkerFinding?
    private let attachedFindings: [WorkerFinding]?
    public var findings: [WorkerFinding] { attachedFindings ?? [] }

    private enum CodingKeys: String, CodingKey {
        case kind, blocking, clipName, sourceFile, message, deltaFrames
        case severity, ruleName
        case flaggedPosition, correctedPosition
        case flaggedPositions, correctedPositions
        case flaggedFinding, correctedFinding
        case attachedFindings = "findings"
    }
}
