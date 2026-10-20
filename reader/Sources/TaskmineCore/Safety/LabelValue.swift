import Foundation

/// Where the value after a label assignment ends (reader spec 5.2, label assignments).
extension CredentialScan {
    /// The end of the value after label match `index` and its quote; nil if unterminated.
    func labelValue(_ value: NSString, _ matches: [RxMatch], _ index: Int) -> (Int, String)? {
        let start = matches[index].end
        let first = PyText.unit(value, start)
        let quote = ["\"", "'", "`"].contains(first) ? first : ""
        var end = start
        var limit: Int
        if !quote.isEmpty {
            end = Self.closingQuote(value, start, quote)
            if end < 0 { return nil }
            limit = value.length
        } else {
            let newline = value.range(of: "\n", options: .literal,
                                      range: NSRange(location: start, length: value.length - start))
            limit = newline.location == NSNotFound ? value.length : newline.location
        }
        limit = min(limit, Self.nextLabelBoundary(value, matches, index, end) ?? limit)
        if let field = CredentialPatterns.shared.fieldBoundary.search(value, from: end, to: limit) {
            limit = field.start
        }
        if !quote.isEmpty, !PyText.strip(PyText.slice(value, end, limit), " \t\n})];,").isEmpty { return nil }
        if quote.isEmpty { end = limit }
        while end > start, PyText.isSpace(unit: value.character(at: end - 1)) { end -= 1 }
        return (end, quote)
    }

    /// The start of the next label at or after `end`, less the separators before it.
    static func nextLabelBoundary(_ value: NSString, _ matches: [RxMatch], _ index: Int, _ end: Int) -> Int? {
        guard let following = matches[(index + 1)...].first(where: { $0.start >= end }) else { return nil }
        var boundary = following.start
        while boundary > end, "\\\"'".contains(PyText.unit(value, boundary - 1)) { boundary -= 1 }
        while boundary > end, ",; \t&".contains(PyText.unit(value, boundary - 1)) { boundary -= 1 }
        return boundary
    }

    /// The index after the closing quote, or -1 when the quote never closes.
    static func closingQuote(_ value: NSString, _ start: Int, _ quote: String) -> Int {
        var end = start + 1
        while end < value.length {
            let unit = PyText.unit(value, end)
            if unit == "\\" && quote != "`" {
                end += 2
                continue
            }
            if unit == quote { return end + 1 }
            end += 1
        }
        return -1
    }

    /// True for an empty, already redacted or code-like value after a label.
    func keepLabelValue(_ raw: String, _ quote: String, _ match: RxMatch) -> Bool {
        let redacted = Rules.credential.redacted
        if raw.isEmpty || PyText.same(raw, redacted) { return true }
        if !quote.isEmpty {
            let ns = raw as NSString
            let inner = PyText.slice(ns, 1, ns.length - 1)
            return inner.isEmpty || PyText.same(inner, redacted)
        }
        guard let operatorGroup = match.group(1), !operatorGroup.isEmpty else { return false }
        return CredentialPatterns.shared.codeValue.match(raw as NSString) != nil
    }
}
