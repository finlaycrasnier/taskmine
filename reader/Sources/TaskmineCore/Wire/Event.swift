import Foundation

/// One record on the wire, taskmine.event/1 (docs/CONTRACTS.md section 2).
public struct Event: Codable, Sendable, Equatable {
    /// The schema name every event carries.
    public static let schema = "taskmine.event/1"

    public let eventID: String
    public let revision: Int
    public let sourceID: String
    public let observedAt: Date
    public let firstSeenAt: Date
    public let lastSeenAt: Date
    public let provenance: String
    public let content: EventContent

    /// An event; times are kept to whole seconds on the wire.
    public init(eventID: String, revision: Int, sourceID: String, observedAt: Date, firstSeenAt: Date,
                lastSeenAt: Date, provenance: String, content: EventContent) {
        self.eventID = eventID
        self.revision = revision
        self.sourceID = sourceID
        self.observedAt = observedAt
        self.firstSeenAt = firstSeenAt
        self.lastSeenAt = lastSeenAt
        self.provenance = provenance
        self.content = content
    }

    enum CodingKeys: String, CodingKey {
        case schema, eventID = "event_id", revision, sourceID = "source_id", observedAt = "observed_at"
        case firstSeenAt = "first_seen_at", lastSeenAt = "last_seen_at", provenance, content
    }

    /// Decodes and checks the schema, identifiers, revision and provenance.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        try WireFormat.expect(c, .schema, Self.schema)
        eventID = try WireFormat.decodeID(c, .eventID)
        revision = try c.decode(Int.self, forKey: .revision)
        guard revision >= 1 else { throw WireFormat.invalid(c, .revision, "below 1") }
        sourceID = try WireFormat.decodeID(c, .sourceID)
        observedAt = try WireFormat.decodeTime(c, .observedAt)
        firstSeenAt = try WireFormat.decodeTime(c, .firstSeenAt)
        lastSeenAt = try WireFormat.decodeTime(c, .lastSeenAt)
        provenance = try c.decode(String.self, forKey: .provenance)
        guard WireFormat.provenances.contains(provenance) else { throw WireFormat.invalid(c, .provenance, "unknown") }
        content = try c.decode(EventContent.self, forKey: .content)
    }

    /// Encodes the wire JSON with snake_case keys and Z times.
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.schema, forKey: .schema)
        try c.encode(eventID, forKey: .eventID)
        try c.encode(revision, forKey: .revision)
        try c.encode(sourceID, forKey: .sourceID)
        try c.encode(WireFormat.time(observedAt), forKey: .observedAt)
        try c.encode(WireFormat.time(firstSeenAt), forKey: .firstSeenAt)
        try c.encode(WireFormat.time(lastSeenAt), forKey: .lastSeenAt)
        try c.encode(provenance, forKey: .provenance)
        try c.encode(content, forKey: .content)
    }
}
