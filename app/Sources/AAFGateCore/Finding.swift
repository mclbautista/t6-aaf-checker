import Foundation

public enum Severity: String, Decodable, CaseIterable, Sendable {
    case fail = "FAIL"
    case warn = "WARN"
    case info = "INFO"
}

public struct FindingTrack: Decodable, Equatable, Sendable {
    public let slotID: Int
    public let name: String

    private enum CodingKeys: String, CodingKey {
        case slotID = "slotId"
        case name
    }
}

public struct FindingLocation: Decodable, Equatable, Sendable {
    public let track: FindingTrack?
    public let recordInTimecode: String?
    public let clipName: String?
    public let sourceFile: String?
}

public struct WorkerFinding: Decodable, Equatable, Sendable {
    public let aafRole: String?
    public let ruleID: String
    public let ruleName: String
    public let severity: Severity
    public let location: FindingLocation
    public let message: String
    public let fixInstruction: String?
    public let likelyEmptyClips: [FindingLocation]

    public var track: FindingTrack? { location.track }
    public var recordInTimecode: String? { location.recordInTimecode }
    public var clipName: String? { location.clipName }
    public var sourceFile: String? { location.sourceFile }

    public var displayMessage: String {
        guard ruleID == "container_corrupt" else { return message }
        let count = likelyEmptyClips.count
        return "\(message) \(count) \(count == 1 ? "clip" : "clips") likely to import empty."
    }

    private enum CodingKeys: String, CodingKey {
        case ruleID = "ruleId"
        case ruleName, severity, message, fixInstruction, likelyEmptyClips, aafRole
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        aafRole = try values.decodeIfPresent(String.self, forKey: .aafRole)
        ruleID = try values.decode(String.self, forKey: .ruleID)
        ruleName = try values.decodeIfPresent(String.self, forKey: .ruleName) ?? ruleID
        severity = try values.decode(Severity.self, forKey: .severity)
        location = try FindingLocation(from: decoder)
        message = try values.decode(String.self, forKey: .message)
        fixInstruction = try values.decodeIfPresent(String.self, forKey: .fixInstruction)
        likelyEmptyClips = try values.decodeIfPresent([FindingLocation].self, forKey: .likelyEmptyClips) ?? []
    }
}
