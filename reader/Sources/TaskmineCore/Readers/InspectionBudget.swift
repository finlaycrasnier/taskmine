import Foundation

/// Bounds of a privacy inspection (reader spec 2.4).
struct InspectionBudget: Sendable {
    var maxNodes = 500
    var maxDepth = 16
    var seconds: TimeInterval = 0.75
    var maxCells = 3_000

    /// The standard window budget.
    static let standard = InspectionBudget()
    /// The re-check before a final field value is kept (spec 2.7).
    static let finalValue = InspectionBudget(seconds: 0.5)
}
