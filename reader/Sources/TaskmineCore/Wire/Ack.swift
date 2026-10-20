import Foundation

/// The server's answer to a batch, taskmine.ack/1: every event accepted or rejected once.
public struct Ack: Codable, Sendable, Equatable {
    /// The schema name every ack carries.
    public static let schema = "taskmine.ack/1"

    public let batchID: String
    public let accepted: [AckAccepted]
    public let rejected: [AckRejected]

    /// An ack for `batchID`.
    public init(batchID: String, accepted: [AckAccepted], rejected: [AckRejected]) {
        self.batchID = batchID
        self.accepted = accepted
        self.rejected = rejected
    }

    enum CodingKeys: String, CodingKey { case schema, batchID = "batch_id", accepted, rejected }

    /// Decodes and checks the schema.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        try WireFormat.expect(c, .schema, Self.schema)
        batchID = try c.decode(String.self, forKey: .batchID)
        accepted = try c.decode([AckAccepted].self, forKey: .accepted)
        rejected = try c.decode([AckRejected].self, forKey: .rejected)
    }

    /// Encodes the wire JSON.
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.schema, forKey: .schema)
        try c.encode(batchID, forKey: .batchID)
        try c.encode(accepted, forKey: .accepted)
        try c.encode(rejected, forKey: .rejected)
    }
}
