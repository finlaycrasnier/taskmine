import Foundation

// Nothing here returns a value it removed; only counts and fixed reason codes leave it.
/// Hides secrets in text, titles and URLs (reader spec 5.2; sanitizer version 2).
public enum TextSafety {
    /// What the input is: screen or field text, a window or focus title, or an address.
    public enum Mode: Sendable { case text, title, url }

    /// The outcome of `evaluate`: `text` is nil when the whole record is excluded.
    public struct Result: Sendable, Equatable {
        public let text: String?
        public let redactions: Int
        public let excluded: Bool
        public let reason: String?
    }

    /// The sanitizer version every saved record carries.
    public static let sanitizerVersion = 2

    static let rules = Rules.credential
    static let rx = CredentialPatterns.shared

    /// Removes secrets from `value`; `limit` clips the kept text to that many scalars.
    public static func evaluate(_ value: String, mode: Mode = .text, limit: Int? = nil) -> Result {
        if mode == .url {
            let safe = sanitizeURL(value)
            return Result(text: safe, redactions: 0, excluded: safe == nil, reason: safe == nil ? "credential_url" : nil)
        }
        let maxInput = rules.limits.maxInput
        guard PyText.count(value) <= maxInput else { return oversize }
        let normal = normalize(value)
        guard PyText.count(normal) <= maxInput else { return oversize }
        if let reason = exclusion(normal, title: mode == .title) {
            return Result(text: nil, redactions: 0, excluded: true, reason: reason)
        }
        let scan = CredentialScan(blocks: false)
        guard let result = scan.run(normal) else {
            return Result(text: nil, redactions: scan.hits, excluded: true, reason: "password_label")
        }
        return Result(text: clip(result, limit), redactions: scan.hits, excluded: false, reason: nil)
    }

    /// The result for input over the size limit.
    private static var oversize: Result {
        Result(text: nil, redactions: 1, excluded: true, reason: rules.reasons["oversize_or_unparseable"])
    }

    /// Detection only: the same rules as `evaluate` plus private-key blocks.
    public static func containsCredential(_ value: String) -> Bool {
        if PyText.count(value) > rules.limits.maxInput { return true }
        let normal = normalize(value)
        if PyText.count(normal) > rules.limits.maxInput || rx.privateKey.finds(normal) { return true }
        let scan = CredentialScan(blocks: true)
        _ = scan.run(normal)
        return scan.hits > 0
    }

    /// Scheme, host, port and path of a URL; nil for a credential-bearing or unusable URL.
    public static func sanitizeURL(_ url: String) -> String? {
        guard !url.isEmpty, PyText.count(url) <= rules.limits.maxURL,
              let parts = try? URLSplit(PyText.strip(url)), (try? parts.port()) != nil else {
            return nil
        }
        let host = PyText.rstrip((parts.hostname ?? "").lowercased(), ".")
        if parts.scheme.isEmpty || (parts.netloc.isEmpty && parts.path.isEmpty) { return nil }
        if rules.passwordHosts.contains(where: { PyText.same(host, $0) || PyText.endsWith(host, "." + $0) }) {
            return nil
        }
        let path = decodeToken(parts.path)
        if exclusion(path) != nil || rx.providerTokens.finds(path) || rx.labelAssignment.finds(path)
            || rx.percentRun.finds(path) || rx.providerTokens.finds(host) {
            return nil
        }
        let netloc = PyText.rpartition(parts.netloc, "@").2
        return URLSplit.join(scheme: parts.scheme, netloc: netloc, path: parts.path)
    }

    /// The checking form: NFKC, no invisible characters, plain spaces and line breaks.
    static func normalize(_ text: String) -> String {
        var value = PyText.prefix(text, rules.limits.maxInput).precomposedStringWithCompatibilityMapping
        value = rx.invisible.sub(value, with: "")
        value = rx.space.sub(value, with: " ")
        return rx.lineBreak.sub(value, with: "\n")
    }

    /// Percent-decodes up to max_decode_rounds times, then normalises.
    static func decodeToken(_ token: String) -> String {
        var value = token
        for _ in 0..<rules.limits.maxDecodeRounds {
            let decoded = PercentDecoding.unquote(value)
            if PyText.same(decoded, value) { break }
            value = decoded
        }
        return normalize(value)
    }

    /// True when a percent-encoded token decodes to a private key header.
    static func encodedPrivateKey(_ value: String) -> Bool {
        guard value.unicodeScalars.contains("%") else { return false }
        for (index, m) in rx.percentToken.all(value as NSString).enumerated() {
            if index >= rules.limits.maxDecodedTokens { return false }
            let token = m.value
            if PyText.count(token) <= rules.limits.maxURL, rx.privateKey.finds(decodeToken(token)) { return true }
        }
        return false
    }

    /// The reason the whole record is dropped, or nil.
    static func exclusion(_ value: String, title: Bool = false) -> String? {
        if rx.privateKey.finds(value) || encodedPrivateKey(value) { return "private_key" }
        if rx.recorderFiles.finds(value) || rx.secretFiles.finds(value) || rx.pathCredentials.finds(value)
            || (title && rx.titleCredentials.finds(value)) {
            return "credential_document"
        }
        return nil
    }

    /// Clips to `limit` scalars without leaving half a token at the end.
    static func clip(_ text: String, _ limit: Int?) -> String {
        guard let limit else { return text }
        let scalars = Array(text.unicodeScalars)
        if scalars.count <= limit { return text }
        if limit <= 0 { return "" }
        var cut = limit
        if !PyText.isSpace(scalars[limit].value) && !PyText.isSpace(scalars[limit - 1].value) {
            while cut > 0, !PyText.isSpace(scalars[cut - 1].value) { cut -= 1 }
        }
        var view = String.UnicodeScalarView()
        view.append(contentsOf: scalars[0..<cut])
        return String(view)
    }
}
