import CryptoKit
import Foundation

/// Snapshot dedup against the previous snapshot of the same window (reader spec 2.8).
public final class SightingTracker {
    /// What a new snapshot did to the window's open event.
    public enum Outcome: Sendable, Equatable { case newEvent, extended, revisedAppend, revisedSimilar }

    /// Metadata keys that may change within one event.
    static let volatileKeys: Set<String> = [
        "trigger", "gap_reason", "sighting_count", "duration_s", "text_revision", "codes", "furniture",
    ]

    private let similarity: Double
    private let maxAge: TimeInterval
    private let persistInterval: TimeInterval
    private let capacity: Int
    private var entries: [String: SightingEntry] = [:]
    private var clock = 0

    /// A tracker: Jaccard threshold, longest event, interval between written revisions, windows kept.
    public init(similarity: Double = 0.92, maxAge: TimeInterval = 3_600, persistInterval: TimeInterval = 30,
                capacity: Int = 64) {
        self.similarity = similarity
        self.maxAge = maxAge
        self.persistInterval = persistInterval
        self.capacity = capacity
    }

    /// The open event of a window, or nil.
    public func entry(for windowKey: String) -> SightingEntry? { entries[windowKey] }

    /// Forgets a window, for example when focus leaves it and its final count is written.
    public func remove(_ windowKey: String) { entries[windowKey] = nil }

    /// Compares a snapshot with the window's open event and updates it.
    public func observe(windowKey: String, reading: Reading, at time: Date) -> Outcome {
        clock += 1
        let content = Self.contentKey(reading), identity = Self.identityKey(reading)
        guard var e = entries[windowKey], time >= e.lastSeen, time.timeIntervalSince(e.firstSeen) < maxAge else {
            return start(windowKey, reading, content, identity, time)
        }
        e.lastUsed = clock
        if e.contentKey == content {
            e.sightingCount += 1
            e.lastSeen = time
            if time.timeIntervalSince(e.lastWritten) >= persistInterval { write(&e, time) }
            entries[windowKey] = e
            return .extended
        }
        guard e.identityKey == identity, e.kind == "screen_text", reading.kind == "screen_text",
              let outcome = revision(old: e.text, new: reading.text) else {
            return start(windowKey, reading, content, identity, time)
        }
        e.sightingCount += 1
        e.lastSeen = time
        e.contentKey = content
        e.text = reading.text
        write(&e, time)
        entries[windowKey] = e
        return outcome
    }

    /// An append or similar revision of the old text, or nil.
    private func revision(old: String, new: String) -> Outcome? {
        if new.unicodeScalars.count > old.unicodeScalars.count, PyText.startsWith(new, old) { return .revisedAppend }
        return Self.jaccard(old, new) >= similarity ? .revisedSimilar : nil
    }

    /// Writes one revision.
    private func write(_ e: inout SightingEntry, _ time: Date) {
        e.revision += 1
        e.lastWritten = time
    }

    /// Opens a new event for the window, evicting the least recently used window when full.
    private func start(_ key: String, _ r: Reading, _ content: String, _ identity: String, _ time: Date) -> Outcome {
        if entries[key] == nil, entries.count >= capacity,
           let oldest = entries.min(by: { $0.value.lastUsed < $1.value.lastUsed })?.key {
            entries[oldest] = nil
        }
        entries[key] = SightingEntry(revision: 1, sightingCount: 1, firstSeen: time, lastSeen: time, lastWritten: time,
                                     contentKey: content, identityKey: identity, kind: r.kind, text: r.text,
                                     lastUsed: clock)
        return .newEvent
    }

    /// SHA-256 of kind, folded title and folded text, joined by U+001F.
    static func contentKey(_ r: Reading) -> String {
        sha256([r.kind, fold(r.title), fold(r.text)].joined(separator: "\u{1f}"))
    }

    /// SHA-256 of the title and every stable metadata pair.
    static func identityKey(_ r: Reading) -> String {
        let pairs = r.metadata.filter { !volatileKeys.contains($0.key) }.sorted { $0.key < $1.key }
            .map { "\($0.key)=\($0.value)" }
        return sha256((["title=" + r.title] + pairs).joined(separator: "\u{1f}"))
    }

    /// Lower case with whitespace runs collapsed to one space and trimmed.
    static func fold(_ text: String) -> String {
        text.lowercased().split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }

    /// Word 3-shingle Jaccard similarity over the first 20,000 scalars of lower-cased text.
    static func jaccard(_ a: String, _ b: String) -> Double {
        let sa = shingles(a), sb = shingles(b)
        let union = sa.union(sb).count
        return union == 0 ? 1 : Double(sa.intersection(sb).count) / Double(union)
    }

    /// FNV-1a 64 hashes of every three-word run; fewer than three words hash whole.
    static func shingles(_ text: String) -> Set<UInt64> {
        let words = PyText.prefix(text, 20_000).lowercased()
            .split(whereSeparator: { $0 == " " || $0 == "\n" || $0 == "\t" }).map(String.init)
        if words.count < 3 { return [fnv(words.joined(separator: " "))] }
        return Set((0...(words.count - 3)).map { fnv(words[$0..<$0 + 3].joined(separator: " ")) })
    }

    /// FNV-1a 64 of the UTF-8 bytes.
    static func fnv(_ text: String) -> UInt64 {
        text.utf8.reduce(0xcbf2_9ce4_8422_2325) { ($0 ^ UInt64($1)) &* 0x100_0000_01b3 }
    }

    /// Lower-case hex SHA-256 of the UTF-8 text.
    static func sha256(_ text: String) -> String {
        SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}
