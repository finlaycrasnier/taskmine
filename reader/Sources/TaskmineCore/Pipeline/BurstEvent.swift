import Foundation

/// What an edit burst reports: its start, a mid-burst revision, or its end.
public struct BurstEvent: Sendable, Equatable {
    /// The kind of report.
    public enum Kind: Sendable, Equatable { case started, revised, ended(EndReason) }

    /// Why a burst ended.
    public enum EndReason: Sendable, Equatable {
        /// No edit for `quietGap` seconds.
        case quietGap
        /// `maxDuration` seconds since the first edit.
        case maxDuration
        /// Another interaction (click, focus, shortcut, scroll) was recorded.
        case interaction
    }

    public let window: String
    public let kind: Kind
    /// Edits seen so far (sighting_count).
    public let editCount: Int
    /// Whole seconds from the first edit to the last edit.
    public let durationSeconds: Int
    /// When the first edit happened.
    public let startedAt: Date
    /// When the last edit happened.
    public let lastEditAt: Date
}
