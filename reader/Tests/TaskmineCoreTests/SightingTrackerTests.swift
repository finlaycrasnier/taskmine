// Snapshot dedup against the previous snapshot of the same window (reader spec 2.8).
//
// Choices the implementation must meet:
// - `entry(for windowKey:) -> SightingEntry?` exposes the open event of a window:
//   `revision`, `sightingCount`, `firstSeen`, `lastSeen` (all public).
// - newEvent: revision 1, sightingCount 1, firstSeen = lastSeen = the time.
// - extended: sightingCount + 1 and lastSeen moves. A revision is written (revision + 1)
//   only when at least persistInterval has passed since the last written revision.
// - revisedAppend and revisedSimilar write a revision at once (revision + 1).
// - The content key is SHA-256 of kind, lower-cased whitespace-collapsed title, and the
//   same of the text. Similarity: word 3-shingle Jaccard over the first 20,000 scalars.
//   An event covers at most 3,600 s: `at - firstSeen < maxAge` or a new event starts.
import Foundation
import Testing
import TaskmineCore

private let t0 = Date(timeIntervalSince1970: 1_790_000_000)

private func at(_ seconds: TimeInterval) -> Date { t0.addingTimeInterval(seconds) }

private func reading(
    _ text: String,
    title: String = "Onboarding - Northwind",
    kind: String = "screen_text",
    metadata: [String: String] = [
        "app_bundle_id": "com.apple.TextEdit", "app_name": "TextEdit",
        "window_ref": "window:6f1c2a3b-0000-4000-8000-000000000001",
    ]
) -> Reading {
    Reading(title: title, text: text, metadata: metadata, provenance: "native_accessibility", kind: kind)
}

/// `count` distinct words, "w0 w1 w2 ...".
private func words(_ count: Int, prefix: String = "w") -> [String] {
    (0..<count).map { "\(prefix)\($0)" }
}

@Suite struct SightingTrackerTests {
    let key = "window:6f1c2a3b-0000-4000-8000-000000000001"

    @Test func firstSnapshotIsNewEvent() throws {
        let tracker = SightingTracker()
        #expect(tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: t0) == .newEvent)
        let entry = try #require(tracker.entry(for: key))
        #expect(entry.revision == 1)
        #expect(entry.sightingCount == 1)
        #expect(entry.firstSeen == t0)
        #expect(entry.lastSeen == t0)
    }

    @Test func equalContentExtends() throws {
        let tracker = SightingTracker()
        _ = tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: t0)
        #expect(tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: at(5)) == .extended)
        let entry = try #require(tracker.entry(for: key))
        #expect(entry.sightingCount == 2)
        #expect(entry.lastSeen == at(5))
        #expect(entry.firstSeen == t0)
    }

    @Test func contentKeyIgnoresCaseAndWhitespace() {
        let tracker = SightingTracker()
        _ = tracker.observe(windowKey: key, reading: reading("Invoice  1042   Approved"), at: t0)
        let outcome = tracker.observe(windowKey: key, reading: reading("invoice 1042 approved"), at: at(5))
        #expect(outcome == .extended)
    }

    @Test func appendRevises() throws {
        let tracker = SightingTracker()
        _ = tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: t0)
        let outcome = tracker.observe(windowKey: key, reading: reading("Invoice 1042\nApproved by Maya"), at: at(5))
        #expect(outcome == .revisedAppend)
        #expect(try #require(tracker.entry(for: key)).revision == 2)
    }

    @Test func similarAtThresholdRevises() throws {
        // 100 words, the last one changed: 98 shingles each, 97 shared, Jaccard 97/99 = 0.98.
        let old = words(100)
        var new = old
        new[99] = "changed"
        let tracker = SightingTracker()
        _ = tracker.observe(windowKey: key, reading: reading(old.joined(separator: " ")), at: t0)
        let outcome = tracker.observe(windowKey: key, reading: reading(new.joined(separator: " ")), at: at(5))
        #expect(outcome == .revisedSimilar)
        #expect(try #require(tracker.entry(for: key)).revision == 2)
    }

    @Test func belowThresholdIsNewEvent() {
        // 100 words, every other word from 40 to 58 changed: 21 of 98 shingles change,
        // Jaccard 77/119 = 0.65.
        let old = words(100)
        var new = old
        for i in stride(from: 40, to: 60, by: 2) { new[i] = "changed\(i)" }
        let tracker = SightingTracker()
        _ = tracker.observe(windowKey: key, reading: reading(old.joined(separator: " ")), at: t0)
        let outcome = tracker.observe(windowKey: key, reading: reading(new.joined(separator: " ")), at: at(5))
        #expect(outcome == .newEvent)
    }

    @Test func similarityThresholdIsAParameter() {
        // Same pair as similarAtThresholdRevises (0.98) against a 0.99 threshold.
        let old = words(100)
        var new = old
        new[99] = "changed"
        let tracker = SightingTracker(similarity: 0.99)
        _ = tracker.observe(windowKey: key, reading: reading(old.joined(separator: " ")), at: t0)
        let outcome = tracker.observe(windowKey: key, reading: reading(new.joined(separator: " ")), at: at(5))
        #expect(outcome == .newEvent)
    }

    @Test func similarAppliesOnlyToScreenText() {
        let old = words(100)
        var new = old
        new[99] = "changed"
        let tracker = SightingTracker()
        _ = tracker.observe(windowKey: key, reading: reading(old.joined(separator: " "), kind: "interaction"), at: t0)
        let outcome = tracker.observe(
            windowKey: key, reading: reading(new.joined(separator: " "), kind: "interaction"), at: at(5))
        #expect(outcome == .newEvent)
    }

    @Test func titleChangeIsNewEvent() {
        let tracker = SightingTracker()
        _ = tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: t0)
        let outcome = tracker.observe(
            windowKey: key, reading: reading("Invoice 1042\nApproved", title: "Invoice 1043 - Northwind"), at: at(5))
        #expect(outcome == .newEvent)
    }

    @Test func metadataChangeIsNewEvent() {
        let tracker = SightingTracker()
        _ = tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: t0)
        var metadata = ["app_bundle_id": "com.apple.TextEdit", "app_name": "TextEdit"]
        metadata["window_ref"] = "window:6f1c2a3b-0000-4000-8000-000000000001"
        metadata["focus_name"] = "Amount"
        let outcome = tracker.observe(
            windowKey: key, reading: reading("Invoice 1042\nApproved", metadata: metadata), at: at(5))
        #expect(outcome == .newEvent)
    }

    @Test func newEventResetsEntry() throws {
        let tracker = SightingTracker()
        _ = tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: t0)
        _ = tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: at(5))
        _ = tracker.observe(windowKey: key, reading: reading("Something else entirely"), at: at(10))
        let entry = try #require(tracker.entry(for: key))
        #expect(entry.revision == 1)
        #expect(entry.sightingCount == 1)
        #expect(entry.firstSeen == at(10))
    }

    @Test func windowsAreIndependent() {
        let tracker = SightingTracker()
        let other = "window:6f1c2a3b-0000-4000-8000-000000000002"
        _ = tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: t0)
        #expect(tracker.observe(windowKey: other, reading: reading("Invoice 1042"), at: at(1)) == .newEvent)
        #expect(tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: at(2)) == .extended)
    }

    @Test func oneHourCap() {
        let tracker = SightingTracker()
        _ = tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: t0)
        #expect(tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: at(3599)) == .extended)
        #expect(tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: at(3600)) == .newEvent)
    }

    @Test func oneHourCapAlsoStopsRevisions() {
        let tracker = SightingTracker()
        _ = tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: t0)
        let outcome = tracker.observe(windowKey: key, reading: reading("Invoice 1042\nApproved"), at: at(3600))
        #expect(outcome == .newEvent)
    }

    @Test func maxAgeIsAParameter() {
        let tracker = SightingTracker(maxAge: 60)
        _ = tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: t0)
        #expect(tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: at(59)) == .extended)
        #expect(tracker.observe(windowKey: key, reading: reading("Invoice 1042"), at: at(60)) == .newEvent)
    }

    @Test func persistIntervalThirtySeconds() throws {
        let tracker = SightingTracker()
        let r = reading("Invoice 1042")
        _ = tracker.observe(windowKey: key, reading: r, at: t0)
        var revisions: [Int] = []
        var counts: [Int] = []
        for s in [10.0, 20, 35, 50, 70] {
            #expect(tracker.observe(windowKey: key, reading: r, at: at(s)) == .extended)
            let entry = try #require(tracker.entry(for: key))
            revisions.append(entry.revision)
            counts.append(entry.sightingCount)
        }
        // Writes at 35 (35 s after the first write) and at 70 (35 s after that).
        #expect(revisions == [1, 1, 2, 2, 3])
        #expect(counts == [2, 3, 4, 5, 6])
    }

    @Test func persistIntervalIsAParameter() throws {
        let tracker = SightingTracker(persistInterval: 5)
        let r = reading("Invoice 1042")
        _ = tracker.observe(windowKey: key, reading: r, at: t0)
        _ = tracker.observe(windowKey: key, reading: r, at: at(3))
        #expect(try #require(tracker.entry(for: key)).revision == 1)
        _ = tracker.observe(windowKey: key, reading: r, at: at(6))
        #expect(try #require(tracker.entry(for: key)).revision == 2)
    }
}
