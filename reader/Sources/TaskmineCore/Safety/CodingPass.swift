import CryptoKit
import Foundation

/// Codes one text under one key and counts the codes it makes (reader spec 5.6).
final class CodingPass {
    private let key: SymmetricKey?
    private let rx = PrivateDataPatterns.shared
    private let rules = Rules.privateData
    private(set) var counts: [String: Int] = [:]

    /// A pass with a 32-byte key, or keyless when nil.
    init(key: SymmetricKey?) { self.key = key }

    /// The code string for one value; counts it.
    func codeFor(_ kind: String, _ label: String, _ normal: String) -> String {
        counts[kind, default: 0] += 1
        var digest = rules.keylessSuffix
        if let key {
            let mac = HMAC<SHA256>.authenticationCode(for: Data((kind + "\u{0}" + normal).utf8), using: key)
            digest = String(mac.map { String(format: "%02x", $0) }.joined().prefix(4))
        }
        return "[" + ([kind] + (label.isEmpty ? [] : [label]) + [digest]).joined(separator: rules.separator) + "]"
    }

    /// Codes labelled values; a label alone on a line codes the next non-empty line.
    func labelled(_ input: [String]) -> [String] {
        var lines = input
        for index in lines.indices {
            if let coded = labelledLine(lines[index]) {
                lines[index] = coded
                continue
            }
            if let only = rx.labelOnly.first(where: { $0.rx.match(lines[index] as NSString) != nil }) {
                codeNextLine(&lines, index, only.kind, only.checked)
            }
        }
        return lines
    }

    /// The line with every labelled value coded, or nil when nothing changed.
    func labelledLine(_ line: String) -> String? {
        let ns = line as NSString
        let found = labelMatches(ns)
        var out = ""
        var changed = false
        var cursor = 0
        for (position, label) in found.enumerated() {
            let valueStart = label.end
            if valueStart < cursor || valueStart >= ns.length { continue }
            var stop = ns.length
            if let boundary = rx["nextField"].search(ns, from: valueStart) { stop = boundary.start }
            if let later = found[(position + 1)...].first(where: { $0.start > valueStart }) { stop = min(stop, later.start) }
            let value = PyText.rstrip(PyText.slice(ns, valueStart, stop))
            if value.isEmpty || rx["startsWithCode"].match(value as NSString) != nil { continue }
            guard let replacement = fieldValue(label.kind, value, label.checked) else { continue }
            out += PyText.slice(ns, cursor, valueStart) + replacement
            changed = true
            cursor = valueStart + (value as NSString).length
        }
        return changed ? out + PyText.slice(ns, cursor) : nil
    }

    /// Every label match in a line, sorted like Python's (start, end, kind, checked) tuples.
    private func labelMatches(_ ns: NSString) -> [(start: Int, end: Int, kind: String, checked: Bool)] {
        var found: [(start: Int, end: Int, kind: String, checked: Bool)] = []
        for label in rx.fields {
            for m in label.rx.all(ns) { found.append((m.start, m.end, label.kind, label.checked)) }
        }
        return found.sorted { a, b in
            if a.start != b.start { return a.start < b.start }
            if a.end != b.end { return a.end < b.end }
            if a.kind != b.kind { return a.kind < b.kind }
            return !a.checked && b.checked
        }
    }

    /// Codes the first non-empty line within two lines when it looks like a `kind` value.
    private func codeNextLine(_ lines: inout [String], _ index: Int, _ kind: String, _ checked: Bool) {
        for following in (index + 1)..<min(index + 3, lines.count) {
            let candidate = PyText.strip(lines[following])
            if candidate.isEmpty { continue }
            if looksLike(kind, candidate), let replacement = fieldValue(kind, candidate, checked) {
                let raw = lines[following]
                let indent = raw.unicodeScalars.count - PyText.lstrip(raw).unicodeScalars.count
                lines[following] = PyText.prefix(raw, indent) + replacement
            }
            return
        }
    }

    /// Whether a line after a lone label reads as a value of that kind.
    private func looksLike(_ kind: String, _ text: String) -> Bool {
        if rx["startsWithCode"].match(text as NSString) != nil { return false }
        let digits = PyText.digits(text).count, alnum = PyText.alnum(text).count
        switch kind {
        case "IBAN": return alnum >= 10 && digits >= 4
        case "SALARY": return digits >= 1 && alnum <= 24
        case "CARD": return digits >= 4
        case "PHONE": return digits >= 6
        case "EMAIL": return PyText.contains(text, "@")
        default: return alnum >= 9 && digits >= 1
        }
    }

    /// The value after a label with its leading part coded; nil leaves it as it is.
    func fieldValue(_ kind: String, _ value: String, _ checked: Bool) -> String? {
        let ns = value as NSString
        guard let (start, end) = checked ? checkedExtent(ns) : fieldExtent(kind, ns) else { return nil }
        let (label, normal) = labelAndNormal(kind, PyText.slice(ns, start, end))
        return PyText.slice(ns, 0, start) + codeFor(kind, label, normal) + PyText.slice(ns, end)
    }

    /// The leading partita IVA or codice fiscale of a value after a checked label, or nil.
    private func checkedExtent(_ value: NSString) -> (Int, Int)? {
        guard let m = rx["leadingTaxID"].match(value) else { return nil }
        let normal = PyText.alnum(m.value).uppercased()
        let digits = PyText.removePrefix(normal, "IT")
        let vat = rx["elevenDigits"].match(digits as NSString) != nil && ValueChecks.luhn(digits)
        return vat || ValueChecks.codiceFiscaleValid(normal) ? (0, m.end) : nil
    }

    /// The leading value of the right shape after a label, else the whole value.
    private func fieldExtent(_ kind: String, _ value: NSString) -> (Int, Int) {
        if kind == "IBAN" || kind == "CARD" {
            let spans = kind == "IBAN" ? ibans(value) : cards(value)
            if let first = spans.first, first.start == 0 { return (0, first.end) }
        }
        let leading = ["CARD": "leadingCard", "EMAIL": "email", "PHONE": "leadingPhone", "TAXID": "leadingTaxID"][kind]
        if let leading, let m = rx[leading].match(value) { return (0, m.end) }
        return (0, value.length)
    }

    /// The code label and normal form of a labelled value of any shape.
    private func labelAndNormal(_ kind: String, _ value: String) -> (String, String) {
        switch kind {
        case "IBAN":
            let normal = PyText.alnum(value).uppercased()
            return (rx["ibanPrefix"].match(normal as NSString) != nil ? String(normal.prefix(2)) : "??", normal)
        case "CARD":
            let digits = PyText.digits(value)
            return (ValueChecks.cardBrand(digits) ?? "OTHER", digits.isEmpty ? PyText.alnum(value).uppercased() : digits)
        case "EMAIL":
            let address = PyText.strip(value).lowercased()
            return (PyText.contains(address, "@") ? ValueChecks.emailType(address) : "other", address)
        case "PHONE":
            let (normal, country) = ValueChecks.phoneNormal(value)
            return (country, normal)
        case "TAXID":
            return taxLabel(value)
        default:
            return ("", PyText.alnum(value).lowercased())
        }
    }

    /// TAXID: IT, or the country of a foreign tax ID whose prefix is an IBAN country.
    private func taxLabel(_ value: String) -> (String, String) {
        var normal = PyText.alnum(value).uppercased()
        if rx["italianVAT"].match(normal as NSString) != nil { normal = String(normal.dropFirst(2)) }
        let country = String(normal.prefix(2))
        let foreign = rx["foreignTaxID"].match(normal as NSString) != nil
            && rules.ibanLengths[country] != nil && country != "IT"
        return (foreign ? country : "IT", normal)
    }

    /// Codes values found without a label, skipping spans that touch an existing code.
    func free(_ line: String) -> String {
        let ns = line as NSString
        let taken = rx["codePattern"].all(ns).map { ($0.start, $0.end) }
        let spans = freeSpans(ns).enumerated()
            .filter { _, s in !taken.contains { s.start < $0.1 && $0.0 < s.end } }
            .sorted { a, b in
                let ka = (a.element.start, rules.priority[a.element.kind]!, a.element.start - a.element.end)
                let kb = (b.element.start, rules.priority[b.element.kind]!, b.element.start - b.element.end)
                return ka != kb ? ka < kb : a.offset < b.offset
            }.map(\.element)
        var out = ""
        var cursor = 0
        for span in spans where span.start >= cursor {
            out += PyText.slice(ns, cursor, span.start) + codeFor(span.kind, span.label, span.normal)
            cursor = span.end
        }
        return cursor == 0 && out.isEmpty ? line : out + PyText.slice(ns, cursor)
    }
}
