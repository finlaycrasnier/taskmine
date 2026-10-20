import Foundation

/// An ICU pattern with Python `re` match semantics over UTF-16 offsets.
struct Rx: @unchecked Sendable {
    let regex: NSRegularExpression

    /// Compiles a rule pattern with Python semantics; a bad pattern in a bundled rule file is a build error.
    init(_ pattern: String, _ options: NSRegularExpression.Options = []) {
        let python = PatternTranslation.python(pattern, caseInsensitive: options.contains(.caseInsensitive))
        do { regex = try NSRegularExpression(pattern: python, options: options) } catch {
            fatalError("bad rule pattern: \(pattern)")
        }
    }

    /// Python `search(s, pos, endpos)`: lookbehind sees before `from`, the text ends at `to`.
    func search(_ s: NSString, from: Int = 0, to: Int? = nil) -> RxMatch? {
        let end = min(to ?? s.length, s.length)
        guard from <= end else { return nil }
        let text = end < s.length ? s.substring(to: end) as NSString : s
        let found = regex.firstMatch(
            in: text as String, options: [.withTransparentBounds, .withoutAnchoringBounds],
            range: NSRange(location: from, length: end - from))
        return found.map { RxMatch(result: $0, text: text) }
    }

    /// Python `match(s, pos)`: a match that starts exactly at `at`.
    func match(_ s: NSString, at: Int = 0) -> RxMatch? {
        guard at <= s.length else { return nil }
        let found = regex.firstMatch(
            in: s as String, options: [.anchored, .withTransparentBounds, .withoutAnchoringBounds],
            range: NSRange(location: at, length: s.length - at))
        return found.map { RxMatch(result: $0, text: s) }
    }

    /// True when the pattern matches anywhere in `s`.
    func finds(_ s: String) -> Bool { search(s as NSString) != nil }

    /// Python `finditer`: every non-overlapping match, left to right.
    func all(_ s: NSString) -> [RxMatch] {
        regex.matches(in: s as String, range: NSRange(location: 0, length: s.length))
            .map { RxMatch(result: $0, text: s) }
    }

    /// Python `sub` with a function: each match replaced by what `replace` returns.
    func sub(_ s: String, _ replace: (RxMatch) -> String) -> String {
        let ns = s as NSString
        var out = ""
        var cursor = 0
        for m in all(ns) {
            out += ns.substring(with: NSRange(location: cursor, length: m.start - cursor))
            out += replace(m)
            cursor = m.end
        }
        guard cursor > 0 || !out.isEmpty else { return s }
        return out + ns.substring(from: cursor)
    }

    /// Python `sub` with a fixed replacement string (no template escapes).
    func sub(_ s: String, with replacement: String) -> String { sub(s) { _ in replacement } }
}
