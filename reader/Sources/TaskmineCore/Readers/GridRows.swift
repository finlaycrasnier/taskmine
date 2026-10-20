import Foundation

/// Joins spreadsheet cells of one row as `A2: value | B2: value` (reader spec 2.5).
struct GridRows {
    private static let address = Rx("^([A-Z]{1,3})([1-9][0-9]{0,6})\\z")
    private var row: String?
    private var parts: [String] = []

    /// The row number of a cell address such as `B12`, or nil when the label is not an address.
    static func rowNumber(_ label: String) -> String? {
        address.match(label as NSString)?.group(2)
    }

    /// Adds one non-empty cell; returns the previous row's line when the row changes.
    mutating func add(address: String, row number: String, value: String) -> String? {
        let flushed = number == row ? nil : flush()
        row = number
        parts.append("\(address): \(value)")
        return flushed
    }

    /// The pending row line, if any, and an empty buffer.
    mutating func flush() -> String? {
        defer { parts = []; row = nil }
        return parts.isEmpty ? nil : parts.joined(separator: " | ")
    }
}
