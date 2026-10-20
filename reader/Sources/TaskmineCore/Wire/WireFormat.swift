import Foundation

/// Shared wire rules: UTC times as YYYY-MM-DDTHH:MM:SSZ, identifier shape, decode failures.
enum WireFormat {
    static let idPattern = Rx("^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\\z")
    static let provenances: Set<String> = [
        "native_accessibility", "native_interaction", "screen_ocr", "app_reader", "synthetic",
    ]

    /// A date in the wire format, whole seconds, UTC.
    static func time(_ date: Date) -> String {
        Date.ISO8601FormatStyle(timeZone: TimeZone(identifier: "UTC")!).format(date)
    }

    /// Parses a wire date; fractional seconds are accepted.
    static func parse(_ text: String) -> Date? {
        guard text.hasSuffix("Z") else { return nil }
        if let date = try? Date.ISO8601FormatStyle().parse(text) { return date }
        return try? Date.ISO8601FormatStyle(includingFractionalSeconds: true).parse(text)
    }

    /// True for a valid event, batch or source identifier.
    static func isID(_ text: String) -> Bool { idPattern.match(text as NSString) != nil }

    /// Decodes a wire date from a keyed container.
    static func decodeTime<K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ key: K) throws -> Date {
        let text = try c.decode(String.self, forKey: key)
        guard let date = parse(text) else { throw invalid(c, key, "not a UTC date-time") }
        return date
    }

    /// Decodes an identifier and checks its shape.
    static func decodeID<K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ key: K) throws -> String {
        let text = try c.decode(String.self, forKey: key)
        guard isID(text) else { throw invalid(c, key, "not a valid identifier") }
        return text
    }

    /// Decodes a string that must equal `expected`.
    static func expect<K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ key: K, _ expected: String) throws {
        guard try c.decode(String.self, forKey: key) == expected else { throw invalid(c, key, "must be \(expected)") }
    }

    /// A data-corrupted error for one key.
    static func invalid<K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ key: K, _ why: String) -> DecodingError {
        .dataCorruptedError(forKey: key, in: c, debugDescription: why)
    }
}
