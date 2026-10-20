import Foundation

/// A batch of stored event payloads, sent as taskmine.batch/1.
public struct Batch: Sendable, Equatable {
    /// The schema name every batch carries.
    public static let schema = "taskmine.batch/1"

    public let batchID: String
    public let events: [Data]

    /// A batch of event payloads, each one the JSON of a taskmine.event/1.
    public init(batchID: String, events: [Data]) {
        self.batchID = batchID
        self.events = events
    }

    /// The wire JSON {schema, batch_id, source_id, events}; the event bytes go in unchanged.
    public func body(sourceID: String) throws -> Data {
        guard WireFormat.isID(batchID), WireFormat.isID(sourceID), (1...100).contains(events.count) else {
            throw QueueError.invalidPayload
        }
        var out = Data("{\"schema\":".utf8) + (try Self.json(Self.schema))
        out += Data(",\"batch_id\":".utf8) + (try Self.json(batchID))
        out += Data(",\"source_id\":".utf8) + (try Self.json(sourceID))
        out += Data(",\"events\":[".utf8)
        for (index, event) in events.enumerated() {
            if index > 0 { out += Data(",".utf8) }
            out += event
        }
        return out + Data("]}".utf8)
    }

    /// A JSON string literal.
    private static func json(_ text: String) throws -> Data {
        try JSONSerialization.data(withJSONObject: text, options: [.fragmentsAllowed, .withoutEscapingSlashes])
    }
}
