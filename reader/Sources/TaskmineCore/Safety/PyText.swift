import Foundation

/// Python `str` behaviour the rules depend on: code-point equality, `isspace`, `strip`, `split`.
enum PyText {
    /// The code points Python's `str.isspace` accepts.
    static func isSpace(_ value: UInt32) -> Bool {
        switch value {
        case 0x09...0x0D, 0x1C...0x20, 0x85, 0xA0, 0x1680, 0x2000...0x200A,
             0x2028, 0x2029, 0x202F, 0x205F, 0x3000:
            return true
        default:
            return false
        }
    }

    /// `isspace` for one UTF-16 unit; surrogates are never space.
    static func isSpace(unit: unichar) -> Bool { isSpace(UInt32(unit)) }

    /// Code-point equality, not Swift's canonical equivalence.
    static func same(_ a: String, _ b: String) -> Bool { a.utf8.elementsEqual(b.utf8) }

    /// Python `startswith` on code points.
    static func startsWith(_ s: String, _ prefix: String) -> Bool { s.utf8.starts(with: prefix.utf8) }

    /// Python `endswith` on code points.
    static func endsWith(_ s: String, _ suffix: String) -> Bool {
        s.utf8.count >= suffix.utf8.count && s.utf8.suffix(suffix.utf8.count).elementsEqual(suffix.utf8)
    }

    /// Python `removeprefix`.
    static func removePrefix(_ s: String, _ prefix: String) -> String {
        guard startsWith(s, prefix) else { return s }
        return String(decoding: s.utf8.dropFirst(prefix.utf8.count), as: UTF8.self)
    }

    /// Python `in` for a substring, compared on code points.
    static func contains(_ s: String, _ part: String) -> Bool {
        (s as NSString).range(of: part, options: .literal).location != NSNotFound
    }

    /// Python `replace(old, new)` on code points.
    static func replace(_ s: String, _ old: String, _ new: String) -> String {
        s.replacingOccurrences(of: old, with: new, options: .literal)
    }

    /// Python `len`: the number of Unicode scalars.
    static func count(_ s: String) -> Int { s.unicodeScalars.count }

    /// Python `s[:n]` on scalars.
    static func prefix(_ s: String, _ n: Int) -> String {
        var view = String.UnicodeScalarView()
        view.append(contentsOf: s.unicodeScalars.prefix(n))
        return String(view)
    }

    /// Python `split(sep)`, which never merges empty pieces.
    static func split(_ s: String, _ separator: String) -> [String] {
        (s as NSString).components(separatedBy: separator)
    }

    /// Python `strip(chars)`; nil strips whitespace.
    static func strip(_ s: String, _ chars: String? = nil) -> String { rstrip(lstrip(s, chars), chars) }

    /// Python `lstrip(chars)`; nil strips whitespace.
    static func lstrip(_ s: String, _ chars: String? = nil) -> String {
        let drop = stripTest(chars)
        var view = String.UnicodeScalarView()
        view.append(contentsOf: s.unicodeScalars.drop(while: drop))
        return String(view)
    }

    /// Python `rstrip(chars)`; nil strips whitespace.
    static func rstrip(_ s: String, _ chars: String? = nil) -> String {
        let drop = stripTest(chars)
        var scalars = Array(s.unicodeScalars)
        while let last = scalars.last, drop(last) { scalars.removeLast() }
        var view = String.UnicodeScalarView()
        view.append(contentsOf: scalars)
        return String(view)
    }

    /// The test a strip uses for one scalar.
    private static func stripTest(_ chars: String?) -> (Unicode.Scalar) -> Bool {
        guard let chars else { return { isSpace($0.value) } }
        let set = Set(chars.unicodeScalars)
        return { set.contains($0) }
    }

    /// Python `partition(sep)`: before, found, after.
    static func partition(_ s: String, _ separator: String) -> (String, Bool, String) {
        let ns = s as NSString
        let range = ns.range(of: separator, options: .literal)
        guard range.location != NSNotFound else { return (s, false, "") }
        return (ns.substring(to: range.location), true, ns.substring(from: range.location + range.length))
    }

    /// Python `rpartition(sep)`: before, found, after.
    static func rpartition(_ s: String, _ separator: String) -> (String, Bool, String) {
        let ns = s as NSString
        let range = ns.range(of: separator, options: [.literal, .backwards])
        guard range.location != NSNotFound else { return ("", false, s) }
        return (ns.substring(to: range.location), true, ns.substring(from: range.location + range.length))
    }

    /// The ASCII letters and digits of `s`.
    static func alnum(_ s: String) -> String { String(s.unicodeScalars.filter(isASCIIAlnum).map(Character.init)) }

    /// The ASCII digits of `s`.
    static func digits(_ s: String) -> String { String(s.unicodeScalars.filter(isASCIIDigit).map(Character.init)) }

    /// True for 0-9.
    static func isASCIIDigit(_ c: Unicode.Scalar) -> Bool { c.value >= 48 && c.value <= 57 }

    /// True for A-Z, a-z and 0-9.
    static func isASCIIAlnum(_ c: Unicode.Scalar) -> Bool {
        isASCIIDigit(c) || (c.value >= 65 && c.value <= 90) || (c.value >= 97 && c.value <= 122)
    }

    /// One UTF-16 unit of `s` as a string, or "" past the end (Python `s[i:i+1]`).
    static func unit(_ s: NSString, _ index: Int) -> String {
        index >= 0 && index < s.length ? s.substring(with: NSRange(location: index, length: 1)) : ""
    }

    /// Python `s[a:b]` on UTF-16 offsets, clamped like Python slices.
    static func slice(_ s: NSString, _ from: Int, _ to: Int? = nil) -> String {
        let a = max(0, min(from, s.length))
        let b = max(a, min(to ?? s.length, s.length))
        return s.substring(with: NSRange(location: a, length: b - a))
    }
}
