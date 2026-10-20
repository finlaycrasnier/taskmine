import Foundation

/// One accepted event in an ack: stored, duplicate or stale.
public struct AckAccepted: Codable, Sendable, Equatable {
    public let eventID: String
    public let revision: Int
    public let status: String

    /// An accepted event.
    public init(eventID: String, revision: Int, status: String) {
        self.eventID = eventID
        self.revision = revision
        self.status = status
    }

    enum CodingKeys: String, CodingKey { case eventID = "event_id", revision, status }
}
