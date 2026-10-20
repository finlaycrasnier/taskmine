import Foundation

/// A value bound to or read from a SQLite statement.
enum SQLValue: Equatable {
    case int(Int64)
    case text(String)
    case null

    /// The text, or nil for another type.
    var string: String? { if case .text(let s) = self { return s } else { return nil } }

    /// The integer, or nil for another type.
    var int: Int64? { if case .int(let i) = self { return i } else { return nil } }
}
