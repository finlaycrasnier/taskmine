import Foundation

/// Coalesces value changes in a window into one edit action with revisions (reader spec 2.7).
public final class EditBurst {
    /// Seconds after the start at which mid-burst revisions fall: waits 5, 10, 20, 40, 60, 60.
    static let schedule: [TimeInterval] = [5, 15, 35, 75, 135, 195]

    private struct Open {
        let start: Date
        var lastEdit: Date
        var count: Int
        var revisions: Int
    }

    private let quietGap: TimeInterval
    private let maxDuration: TimeInterval
    private let clock: () -> Date
    private var open: [String: Open] = [:]

    /// A coalescer: quiet gap, longest burst and the clock `tick` reads.
    public init(quietGap: TimeInterval = 8, maxDuration: TimeInterval = 120, clock: @escaping () -> Date = Date.init) {
        self.quietGap = quietGap
        self.maxDuration = maxDuration
        self.clock = clock
    }

    /// Counts one edit; the first edit in a window opens a burst and returns `.started`.
    public func edit(window: String, at time: Date) -> BurstEvent? {
        if var burst = open[window] {
            burst.count += 1
            burst.lastEdit = time
            open[window] = burst
            return nil
        }
        let burst = Open(start: time, lastEdit: time, count: 1, revisions: 0)
        open[window] = burst
        return event(window, burst, .started)
    }

    /// What fell due at the clock's time: ends, then revisions, per window in start order.
    public func tick() -> [BurstEvent] {
        let now = clock()
        var out: [BurstEvent] = []
        for (window, burst) in open.sorted(by: { ($0.value.start, $0.key) < ($1.value.start, $1.key) }) {
            if now.timeIntervalSince(burst.lastEdit) >= quietGap {
                out.append(close(window, .quietGap))
            } else if now.timeIntervalSince(burst.start) >= maxDuration {
                out.append(close(window, .maxDuration))
            } else if let revised = revise(window, burst, now) {
                out.append(revised)
            }
        }
        return out
    }

    /// Ends every open burst because another interaction was recorded.
    public func interrupt() -> [BurstEvent] {
        open.sorted(by: { ($0.value.start, $0.key) < ($1.value.start, $1.key) }).map { close($0.key, .interaction) }
    }

    /// A revision when the next point of the schedule has passed, at most six.
    private func revise(_ window: String, _ burst: Open, _ now: Date) -> BurstEvent? {
        let elapsed = now.timeIntervalSince(burst.start)
        guard burst.revisions < Self.schedule.count, elapsed >= Self.schedule[burst.revisions] else { return nil }
        var next = burst
        while next.revisions < Self.schedule.count, elapsed >= Self.schedule[next.revisions] { next.revisions += 1 }
        open[window] = next
        return event(window, next, .revised)
    }

    /// Closes a burst and reports it.
    private func close(_ window: String, _ reason: BurstEvent.EndReason) -> BurstEvent {
        let burst = open.removeValue(forKey: window)!
        return event(window, burst, .ended(reason))
    }

    /// The report for a burst.
    private func event(_ window: String, _ b: Open, _ kind: BurstEvent.Kind) -> BurstEvent {
        BurstEvent(window: window, kind: kind, editCount: b.count,
                   durationSeconds: Int(b.lastEdit.timeIntervalSince(b.start).rounded(.down)),
                   startedAt: b.start, lastEditAt: b.lastEdit)
    }
}
