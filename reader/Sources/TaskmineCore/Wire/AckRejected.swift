import Foundation

/// One rejected event in an ack, with its code and an optional reason.
public struct AckRejected: Codable, Sendable, Equatable {
    public let eventID: String
    public let revision: Int
    public let code: String
    public let reason: String?

    /// A rejected event.
    public init(eventID: String, revision: Int, code: String, reason: String? = nil) {
        self.eventID = eventID
        self.revision = revision
        self.code = code
        self.reason = reason
    }

    enum CodingKeys: String, CodingKey { case eventID = "event_id", revision, code, reason }

    /// Encodes the wire JSON; an absent reason writes no key.
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(eventID, forKey: .eventID)
        try c.encode(revision, forKey: .revision)
        try c.encode(code, forKey: .code)
        try c.encodeIfPresent(reason, forKey: .reason)
    }
}
