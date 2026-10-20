import CryptoKit
import Foundation

// HASH is the first two bytes of HMAC-SHA256(key, KIND NUL value); without a key it is ????.
/// Replaces personal values with typed codes [KIND·LABEL·HASH] (reader spec 5.6).
public struct PrivateDataCoder: Sendable {
    private let key: SymmetricKey?

    /// `keyHex` is 64 hex digits; anything else gives keyless coding.
    public init(keyHex: String?) { key = keyHex.flatMap(Self.decodeKey) }

    /// True when the coder hashes with a key.
    public var hasKey: Bool { key != nil }

    // `field` is a form field label: it goes in front for labelled detection, then is removed.
    /// Text with personal values coded, and the number of codes of each kind.
    public func code(_ text: String, field: String? = nil) -> (text: String, counts: [String: Int]) {
        if let field, !field.isEmpty {
            let prefix = field + ": "
            let (coded, counts) = code(prefix + text)
            return (PyText.removePrefix(coded, prefix), counts)
        }
        if text.isEmpty { return (text, [:]) }
        let pass = CodingPass(key: key)
        let lines = pass.labelled(PyText.split(text, "\n"))
        let coded = lines.map(pass.free).joined(separator: "\n")
        return (coded, pass.counts)
    }

    /// Counts the codes present in `text`, whoever made them.
    public static func countCodes(in text: String) -> [String: Int] {
        var counts: [String: Int] = [:]
        for m in PrivateDataPatterns.shared["codeCounter"].all(text as NSString) {
            counts[m.group(1) ?? "", default: 0] += 1
        }
        return counts
    }

    /// The `codes` metadata string: KIND=n pairs sorted by kind, only kinds above 0.
    public static func countString(_ counts: [String: Int]) -> String? {
        let pairs = counts.filter { $0.value > 0 }.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }
        return pairs.isEmpty ? nil : pairs.joined(separator: ",")
    }

    /// 32 bytes from 64 ASCII hex digits, or nil.
    private static func decodeKey(_ hex: String) -> SymmetricKey? {
        let units = Array(hex.utf8)
        guard units.count == 64 else { return nil }
        var bytes: [UInt8] = []
        for i in stride(from: 0, to: 64, by: 2) {
            guard let pair = UInt8(String(decoding: units[i..<i + 2], as: UTF8.self), radix: 16),
                  units[i..<i + 2].allSatisfy({ ($0 >= 48 && $0 <= 57) || ($0 | 0x20 >= 97 && $0 | 0x20 <= 102) })
            else { return nil }
            bytes.append(pair)
        }
        return SymmetricKey(data: bytes)
    }
}
