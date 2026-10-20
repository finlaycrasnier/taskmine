import Foundation

/// A failed address check: withheld with a category, or skipped with a coverage reason.
enum GateStop: Error {
    case withheld(String)
    case skip(CoverageReason?)

    /// The same outcome as a gate result.
    var result: GateResult {
        switch self {
        case .withheld(let category): return .withheld(category)
        case .skip(let reason): return .skip(reason)
        }
    }
}
