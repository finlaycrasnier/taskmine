import Foundation

/// Turns prepared readings and sighting outcomes into event revisions (reader spec 2.7, 2.8, 8.3).
@MainActor final class EventLedger {
    /// One open event: its id, last revision and the fields a revision keeps.
    private struct Open {
        let eventID: String
        var revision: Int
        let observedAt: Date
        let provenance: String
        let kind: String
        var text: String
        var metadata: [String: String]
        var trackerRevision: Int
        var sentCount: Int
    }

    private let sourceID: String
    private var windows: [String: Open] = [:]
    private var bursts: [String: Open] = [:]

    /// A ledger for events from `sourceID`.
    init(sourceID: String) { self.sourceID = sourceID }

    /// The revision to queue after `SightingTracker.observe`, or nil when nothing is due.
    func observed(windowKey: String, reading: Reading, outcome: SightingTracker.Outcome, entry: SightingEntry,
                  prefix: String) -> Event? {
        switch outcome {
        case .newEvent:
            let open = Open(eventID: prefix + UUID().uuidString.lowercased(), revision: 1, observedAt: entry.firstSeen,
                            provenance: reading.provenance, kind: reading.kind, text: reading.text,
                            metadata: reading.metadata, trackerRevision: entry.revision, sentCount: 0)
            windows[windowKey] = open
            return emit(&windows[windowKey]!, first: entry.firstSeen, count: entry.sightingCount, last: entry.lastSeen)
        case .extended:
            guard windows[windowKey] != nil, entry.revision > windows[windowKey]!.trackerRevision else { return nil }
            windows[windowKey]!.trackerRevision = entry.revision
            return emit(&windows[windowKey]!, first: entry.firstSeen, count: entry.sightingCount, last: entry.lastSeen)
        case .revisedAppend, .revisedSimilar:
            guard windows[windowKey] != nil else { return nil }
            windows[windowKey]!.trackerRevision = entry.revision
            windows[windowKey]!.text = reading.text
            windows[windowKey]!.metadata["codes"] = reading.metadata["codes"]
            windows[windowKey]!.metadata["text_revision"] = outcome == .revisedAppend ? "append" : "similar"
            return emit(&windows[windowKey]!, first: entry.firstSeen, count: entry.sightingCount, last: entry.lastSeen)
        }
    }

    /// The final sighting count of a window's open event, when it grew since the last revision.
    func flush(windowKey: String, entry: SightingEntry) -> Event? {
        guard let open = windows[windowKey], entry.sightingCount != open.sentCount else { return nil }
        return emit(&windows[windowKey]!, first: entry.firstSeen, count: entry.sightingCount, last: entry.lastSeen)
    }

    /// The window keys with an open event.
    var openWindows: [String] { Array(windows.keys) }

    /// One interaction event, revision 1.
    func interaction(_ reading: Reading, at time: Date) -> Event {
        var open = Open(eventID: "desktop-interaction-" + UUID().uuidString.lowercased(), revision: 1, observedAt: time,
                        provenance: reading.provenance, kind: reading.kind, text: reading.text,
                        metadata: reading.metadata, trackerRevision: 0, sentCount: 0)
        return emit(&open, first: time, count: 1, last: time)
    }

    /// An edit burst report: the edit action, a count revision, or the closing revision with the final value.
    func burst(_ reading: Reading, _ report: BurstEvent) -> Event? {
        let key = report.window
        switch report.kind {
        case .started:
            bursts[key] = Open(eventID: "desktop-interaction-" + UUID().uuidString.lowercased(), revision: 1,
                               observedAt: report.startedAt, provenance: reading.provenance, kind: reading.kind,
                               text: "", metadata: reading.metadata, trackerRevision: 0, sentCount: 0)
        case .revised:
            guard bursts[key] != nil else { return nil }
            bursts[key]!.revision += 1
        case .ended:
            guard bursts[key] != nil else { return nil }
            bursts[key]!.revision += 1
            if !reading.text.isEmpty {
                bursts[key]!.text = reading.text
                bursts[key]!.metadata["text_revision"] = "final"
                bursts[key]!.metadata["codes"] = reading.metadata["codes"]
            }
            bursts[key]!.metadata["value_omitted"] = reading.metadata["value_omitted"]
        }
        let event = make(bursts[key]!, first: report.startedAt, count: report.editCount, last: report.lastEditAt)
        if case .ended = report.kind { bursts[key] = nil }
        return event
    }

    /// Forgets every open event, for example after the local data is deleted.
    func reset() {
        windows = [:]
        bursts = [:]
    }

    /// Writes the next revision of a window event (revision 1 is the first).
    private func emit(_ open: inout Open, first: Date, count: Int, last: Date) -> Event {
        if open.sentCount > 0 { open.revision += 1 }
        open.sentCount = count
        return make(open, first: first, count: count, last: last)
    }

    /// The event for an open record with counts and times.
    private func make(_ open: Open, first: Date, count: Int, last: Date) -> Event {
        var metadata = open.metadata
        metadata["sighting_count"] = String(min(max(count, 1), 9_999_999))
        metadata["duration_s"] = String(min(max(Int(last.timeIntervalSince(first).rounded(.down)), 0), 9_999_999))
        let content = EventContent(kind: open.kind, text: open.text, metadata: metadata)
        return Event(eventID: open.eventID, revision: open.revision, sourceID: sourceID, observedAt: open.observedAt,
                     firstSeenAt: first, lastSeenAt: max(last, open.observedAt), provenance: open.provenance,
                     content: content)
    }
}
