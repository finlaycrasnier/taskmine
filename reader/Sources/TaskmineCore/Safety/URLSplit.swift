import Foundation

/// Python `urllib.parse.urlsplit` and `urlunsplit` (3.13), so URL rules agree with the core.
struct URLSplit {
    struct Invalid: Error {}

    let scheme: String
    let netloc: String
    let path: String
    let query: String
    let fragment: String

    private static let schemeChars = Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-.".unicodeScalars)
    private static let usesNetloc: Set<String> = [
        "", "ftp", "http", "gopher", "nntp", "telnet", "imap", "wais", "file", "mms", "https", "shttp",
        "snews", "prospero", "rtsp", "rtsps", "rtspu", "rsync", "svn", "svn+ssh", "sftp", "nfs", "git",
        "git+ssh", "ws", "wss", "itms-services",
    ]

    /// Splits `url`; throws where Python raises ValueError.
    init(_ input: String) throws {
        var url = String(String.UnicodeScalarView(input.unicodeScalars.drop { $0.value <= 0x20 }))
        for c in ["\t", "\r", "\n"] { url = PyText.replace(url, c, "") }
        var scheme = ""
        let (head, found, rest) = PyText.partition(url, ":")
        if found, let first = head.unicodeScalars.first, first.isASCII, first.properties.isAlphabetic,
           head.unicodeScalars.allSatisfy({ Self.schemeChars.contains($0) }) {
            scheme = head.lowercased()
            url = rest
        }
        var netloc = ""
        if PyText.startsWith(url, "//") { (netloc, url) = Self.splitNetloc(url) }
        try Self.checkBrackets(netloc)
        var fragment = ""
        var query = ""
        if PyText.contains(url, "#") { (url, _, fragment) = PyText.partition(url, "#") }
        if PyText.contains(url, "?") { (url, _, query) = PyText.partition(url, "?") }
        try Self.checkNetloc(netloc)
        self.scheme = scheme
        self.netloc = netloc
        self.path = url
        self.query = query
        self.fragment = fragment
    }

    /// The netloc after "//" up to the first "/", "?" or "#", and the rest.
    private static func splitNetloc(_ url: String) -> (String, String) {
        let ns = url as NSString
        var delim = ns.length
        for c in ["/", "?", "#"] {
            let r = ns.range(of: c, options: .literal, range: NSRange(location: 2, length: ns.length - 2))
            if r.location != NSNotFound { delim = min(delim, r.location) }
        }
        return (PyText.slice(ns, 2, delim), PyText.slice(ns, delim))
    }

    /// Python's bracket checks on an IPv6 netloc.
    private static func checkBrackets(_ netloc: String) throws {
        let open = PyText.contains(netloc, "["), close = PyText.contains(netloc, "]")
        if open != close { throw Invalid() }
        guard open else { return }
        let hostAndPort = PyText.rpartition(netloc, "@").2
        let (before, hasOpen, bracketed) = PyText.partition(hostAndPort, "[")
        var hostname: String
        if hasOpen {
            if !before.isEmpty { throw Invalid() }
            let (host, _, port) = PyText.partition(bracketed, "]")
            if !port.isEmpty && !PyText.startsWith(port, ":") { throw Invalid() }
            hostname = host
        } else {
            hostname = PyText.partition(hostAndPort, ":").0
        }
        try checkBracketedHost(hostname)
    }

    /// An IPvFuture literal or an IPv6 address; an IPv4 address in brackets is invalid.
    private static func checkBracketedHost(_ hostname: String) throws {
        if PyText.startsWith(hostname, "v") || PyText.startsWith(hostname, "V") {
            guard Rx("\\A[vV][a-fA-F0-9]+\\..+\\z").match(hostname as NSString) != nil else { throw Invalid() }
            return
        }
        let address = PyText.partition(hostname, "%").0
        var v6 = in6_addr()
        guard inet_pton(AF_INET6, address, &v6) == 1 else { throw Invalid() }
    }

    /// Rejects a non-ASCII netloc that NFKC turns into URL delimiters.
    private static func checkNetloc(_ netloc: String) throws {
        if netloc.isEmpty || netloc.unicodeScalars.allSatisfy(\.isASCII) { return }
        var n = netloc
        for c in ["@", ":", "#", "?"] { n = PyText.replace(n, c, "") }
        let normal = n.precomposedStringWithCompatibilityMapping
        if PyText.same(n, normal) { return }
        for c in ["/", "?", "#", "@", ":"] where PyText.contains(normal, c) { throw Invalid() }
    }

    /// The host and port part after any userinfo.
    private var hostinfo: (String, String?) {
        let info = PyText.rpartition(netloc, "@").2
        let (_, hasOpen, bracketed) = PyText.partition(info, "[")
        var host: String, port: String
        if hasOpen {
            let parts = PyText.partition(bracketed, "]")
            host = parts.0
            port = PyText.partition(parts.2, ":").2
        } else {
            let parts = PyText.partition(info, ":")
            host = parts.0
            port = parts.2
        }
        return (host, port.isEmpty ? nil : port)
    }

    /// Python `hostname`: lower case, zone kept as written; nil when empty.
    var hostname: String? {
        let host = hostinfo.0
        if host.isEmpty { return nil }
        let (name, percent, zone) = PyText.partition(host, "%")
        return name.lowercased() + (percent ? "%" : "") + zone
    }

    /// Python `port`: -1 when absent; throws when not an ASCII number in 0...65535.
    func port() throws -> Int {
        guard let text = hostinfo.1 else { return -1 }
        guard text.unicodeScalars.allSatisfy({ $0.isASCII && $0.properties.numericType == .decimal }),
              let value = Int(text), value <= 65535 else { throw Invalid() }
        return value
    }

    /// Python `urlunsplit((scheme, netloc, path, query, fragment))`.
    static func join(scheme: String, netloc: String, path: String, query: String = "", fragment: String = "") -> String {
        var url = path
        if !netloc.isEmpty {
            if !url.isEmpty && !PyText.startsWith(url, "/") { url = "/" + url }
            url = "//" + netloc + url
        } else if PyText.startsWith(url, "//") {
            url = "//" + url
        } else if !scheme.isEmpty, usesNetloc.contains(scheme), url.isEmpty || PyText.startsWith(url, "/") {
            url = "//" + url
        }
        if !scheme.isEmpty { url = scheme + ":" + url }
        if !query.isEmpty { url += "?" + query }
        if !fragment.isEmpty { url += "#" + fragment }
        return url
    }
}
