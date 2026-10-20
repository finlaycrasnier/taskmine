import Foundation

/// One redaction pass over normalised text, counting what it redacts (reader spec 5.2 step 4).
final class CredentialScan {
    private let blocks: Bool
    private let decode: Bool
    private(set) var hits = 0
    private var seen = 0
    private let rx = CredentialPatterns.shared
    private let limits = Rules.credential.limits
    private let redacted = Rules.credential.redacted

    /// `blocks` also replaces private-key blocks; `decode` looks inside percent-encoded tokens.
    init(blocks: Bool, decode: Bool = true) {
        self.blocks = blocks
        self.decode = decode
    }

    /// Counts one redaction and returns its replacement.
    private func hit(_ replacement: String? = nil) -> String {
        hits += 1
        return replacement ?? redacted
    }

    /// The redacted text, or nil when an unterminated quoted value excludes it.
    func run(_ input: String) -> String? {
        var value = input
        if decode && TextSafety.encodedPrivateKey(value) { return hit() }
        if blocks { value = rx.privateKeyBlock.sub(value) { _ in self.hit() } }
        if decode { value = rx.percentToken.sub(value, encoded) }
        value = rx.userinfo.sub(value, userinfo)
        value = rx.querySecret.sub(value, query)
        value = rx.bearer.sub(value, bearer)
        value = rx.providerTokens.sub(value) { _ in self.hit() }
        guard let labelled = labelled(value) else { return nil }
        return rx.genericToken.sub(splitLabels(labelled), generic)
    }

    /// A percent-encoded token: redacted when its decoded form still holds a secret.
    private func encoded(_ m: RxMatch) -> String {
        let token = m.value
        seen += 1
        if seen > limits.maxDecodedTokens || PyText.count(token) > limits.maxURL { return hit() }
        let decoded = TextSafety.decodeToken(token)
        if rx.percentRun.finds(decoded) { return hit() }
        if !PyText.same(decoded, token) {
            let inner = CredentialScan(blocks: false, decode: false)
            _ = inner.run(decoded)
            if inner.hits > 0 { return hit() }
        }
        return token
    }

    /// user:password@ in a URL.
    private func userinfo(_ m: RxMatch) -> String {
        let info = m.group(2) ?? ""
        let plain = !PyText.contains(info, ":") && !PyText.contains(info.lowercased(), "%3a")
        if PyText.same(info, redacted) || (plain && PyText.count(info) < 20) { return m.value }
        return hit((m.group(1) ?? "") + redacted + "@")
    }

    /// A secret-named query parameter.
    private func query(_ m: RxMatch) -> String {
        if PyText.endsWith(m.value, "=" + redacted) { return m.value }
        return hit((m.group(1) ?? "") + redacted)
    }

    /// A bearer token that is not a plain word.
    private func bearer(_ m: RxMatch) -> String {
        let token = m.group(2) ?? ""
        let letters = token.unicodeScalars.allSatisfy { $0.properties.isAlphabetic }
        if PyText.same(token, redacted) || (!token.isEmpty && letters) { return m.value }
        return hit((m.group(1) ?? "") + redacted)
    }

    /// A long token with enough digits, upper- and lower-case letters.
    private func generic(_ m: RxMatch) -> String {
        let token = m.value.unicodeScalars
        let digits = token.filter { $0.value >= 48 && $0.value <= 57 }.count
        let upper = token.filter { $0.value >= 65 && $0.value <= 90 }.count
        let lower = token.filter { $0.value >= 97 && $0.value <= 122 }.count
        let strong = digits >= limits.genericMinDigits && upper >= limits.genericMinUpper
            && lower >= limits.genericMinLower
        return strong ? hit() : m.value
    }

    /// Redacts the value after each label assignment; nil on an unterminated quote.
    private func labelled(_ value: String) -> String? {
        let ns = value as NSString
        let matches = rx.labelAssignment.all(ns)
        var out = ""
        var cursor = 0
        for index in matches.indices {
            let start = matches[index].end
            if start < cursor || start == ns.length { continue }
            guard let (end, quote) = labelValue(ns, matches, index) else {
                hits += 1
                return nil
            }
            let raw = PyText.slice(ns, start, end)
            if keepLabelValue(raw, quote, matches[index]) { continue }
            out += PyText.slice(ns, cursor, start) + hit(quote + redacted + quote)
            cursor = end
        }
        return out + PyText.slice(ns, cursor)
    }

    /// A label alone on a line redacts the next non-empty line within two lines.
    private func splitLabels(_ value: String) -> String {
        var lines = PyText.split(value, "\n")
        for index in lines.indices where rx.labelOnlyLine.match(lines[index] as NSString) != nil {
            for following in (index + 1)..<min(index + 3, lines.count) {
                let candidate = PyText.strip(lines[following])
                if candidate.isEmpty { continue }
                if !PyText.same(candidate, redacted), PyText.count(candidate) <= limits.maxSplitValue,
                   rx.labelOnlyLine.match(candidate as NSString) == nil {
                    lines[following] = hit()
                }
                break
            }
        }
        return lines.joined(separator: "\n")
    }
}
