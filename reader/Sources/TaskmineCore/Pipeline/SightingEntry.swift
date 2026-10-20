import Foundation

/// The open event of one window: its revision, sightings and times.
public struct SightingEntry: Sendable, Equatable {
    public internal(set) var revision: Int
    public internal(set) var sightingCount: Int
    public internal(set) var firstSeen: Date
    public internal(set) var lastSeen: Date
    /// When the last revision was written.
    public internal(set) var lastWritten: Date
    var contentKey: String
    var identityKey: String
    var kind: String
    var text: String
    var lastUsed: Int
}
