import Foundation

/// Lossless text tidy, normalisation version 2 (reader spec 6.1): markers, never deletions of content.
public enum Tidy {
    static let rules = Rules.normalization
    static let lineBreak = Rx(rules.lineBreak)
    static let invisibleRun = Rx(rules.invisibleRun)
    static let spaceRun = Rx(rules.spaceRun)
    static let webAddress = Rx(rules.webAddress, rules.webAddressFlags.contains("i") ? [.caseInsensitive] : [])
    static let clock = Rx(rules.clockLine, [.caseInsensitive])
    static let tracking = Set(rules.tracking)

    /// Marker text by name, from normalization_rules.json.
    static func marker(_ name: String) -> String { rules.markers[name]! }

    /// Tidied text: invisible runs, tracking, fragments, repeats and clock lines become markers.
    public static func text(_ input: String) -> String {
        var value = lineBreak.sub(input, with: "\n")
        value = invisibleRun.sub(value, with: marker("invisible"))
        var runs: [(line: String, times: Int)] = []
        for raw in PyText.split(value, "\n") {
            let line = PyText.strip(spaceRun.sub(addresses(raw), with: " "), " ")
            if line.isEmpty { continue }
            if let last = runs.last, PyText.same(last.line, line) {
                runs[runs.count - 1].times += 1
            } else {
                runs.append((line, 1))
            }
        }
        return collapse(runs).joined(separator: "\n")
    }

    /// Clock lines become one marker per run; repeated lines get a repeat marker after them.
    private static func collapse(_ runs: [(line: String, times: Int)]) -> [String] {
        var kept: [String] = []
        for (line, times) in runs {
            if isClockLine(line) {
                if kept.last != marker("clock") { kept.append(marker("clock")) }
                continue
            }
            kept.append(line)
            if times > 1 { kept.append(PyText.replace(marker("repeat"), "N", String(times))) }
        }
        return kept
    }

    /// The URL without tracking parameters (one marker instead) and with a marked fragment.
    public static func url(_ input: String) -> String {
        let (address, _, fragment) = PyText.partition(input, "#")
        let (base, hasQuery, query) = PyText.partition(address, "?")
        var result = address
        if hasQuery {
            var kept: [String] = []
            var tracked = false
            for parameter in PyText.split(query, "&") where !parameter.isEmpty {
                let key = PyText.partition(parameter, "=").0.lowercased()
                if PyText.startsWith(key, rules.trackingPrefix) || tracking.contains(key) {
                    tracked = true
                } else {
                    kept.append(parameter)
                }
            }
            if tracked { kept.append(marker("tracking")) }
            result = base + (kept.isEmpty ? "" : "?" + kept.joined(separator: "&"))
        }
        if !fragment.isEmpty { result += "#" + marker("fragment") }
        return result
    }

    /// True for a line that shows only a time, a relative time or a day name.
    public static func isClockLine(_ line: String) -> Bool { clock.finds(line) }

    /// Rewrites every web address in a line; trailing punctuation stays outside it.
    private static func addresses(_ line: String) -> String {
        guard PyText.contains(line.lowercased(), "http") else { return line }
        return webAddress.sub(line) { m in
            let found = m.value
            let address = PyText.rstrip(found, rules.trailing)
            return url(address) + PyText.slice(found as NSString, (address as NSString).length)
        }
    }
}
