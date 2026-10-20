import Foundation

/// Built-in exclusion rules: app blocklist, URL blocklist, private windows (reader spec 4).
public enum Surfaces {
    static let rules = Rules.blocklist
    /// App reasons in the order of blocklist.json.
    static let appOrder = ["password_manager", "authentication_dialog", "terminal"]

    /// The blocklist reason for an app bundle ID (prefix match, lower case), or nil.
    public static func appReason(_ bundleID: String) -> String? {
        let value = bundleID.lowercased()
        if value.isEmpty { return nil }
        let reasons = appOrder + rules.apps.keys.sorted().filter { !appOrder.contains($0) }
        return reasons.first { reason in
            (rules.apps[reason] ?? []).contains { PyText.startsWith(value, $0) }
        }
    }

    /// The blocklist reason for an http(s) address: host rules, then sign-in path segments.
    public static func urlReason(_ url: String) -> String? {
        guard let parts = try? URLSplit(PyText.strip(url)) else { return nil }
        guard ["http", "https"].contains(parts.scheme.lowercased()), let host = parts.hostname else { return nil }
        if let reason = hostReason(host) { return reason }
        let segments = PyText.split(PercentDecoding.unquote(parts.path).lowercased(), "/")
        return segments.contains(where: rules.signInSegments.contains) ? "sign_in_page" : nil
    }

    /// The blocklist reason for a host, in the order of blocklist host_order.
    static func hostReason(_ raw: String) -> String? {
        let host = PyText.strip(raw.lowercased(), ".")
        if host.isEmpty { return nil }
        func listed(_ items: [String]) -> Bool {
            items.contains { PyText.same(host, $0) || PyText.endsWith(host, "." + $0) }
        }
        let labels = PyText.split(host, ".")
        if listed(rules.hosts["password_manager"] ?? []) { return "password_manager" }
        if rules.sensitiveTLDs.contains(labels[labels.count - 1]) || listed(rules.hosts["sensitive_domain"] ?? []) {
            return "sensitive_domain"
        }
        guard labels.count > 2 else { return nil }
        if rules.hostLabels.sensitiveDomain.contains(labels[0]) { return "sensitive_domain" }
        if rules.hostLabels.signInPage.contains(labels[0]) { return "sign_in_page" }
        return nil
    }

    /// True when the title or a toolbar label shows a private browsing window.
    public static func isPrivateWindow(title: String, labels: [String] = []) -> Bool {
        let rules = rules.privateWindow
        let lowered = title.lowercased()
        if rules.titleContains.contains(where: { PyText.contains(lowered, $0) }) { return true }
        if rules.titleSuffixes.contains(where: { PyText.endsWith(lowered, $0) }) { return true }
        for label in labels {
            let value = PyText.strip(label.lowercased())
            if rules.labelsExact.contains(value) { return true }
            if rules.labelContains.contains(where: { PyText.contains(value, $0) }) { return true }
        }
        return false
    }
}
