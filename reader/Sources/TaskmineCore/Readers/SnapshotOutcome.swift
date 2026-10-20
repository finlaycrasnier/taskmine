import Foundation

/// What the last snapshot attempt came to.
public enum SnapshotOutcome: Sendable, Equatable {
    case captured
    /// Record a content-free withheld gap of this category (spec 4.3).
    case withheld(String)
    /// Nothing recorded; the reason feeds coverage when present.
    case skipped(CoverageReason?)
}
