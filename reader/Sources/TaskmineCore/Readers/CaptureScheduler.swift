import Foundation

/// Debounce and skipping for snapshots (reader spec 2.2); the clock is passed in.
struct CaptureScheduler {
    /// What kind of notification arrived.
    enum Trigger { case structural, typing }

    /// When the pending snapshot falls due, or nil.
    private(set) var dueAt: Date?
    private var typingSince: Date?
    private var structuralPending = false
    private var lastAttempt: Date?
    /// The last time a window was walked.
    private(set) var lastWalk: Date?
    /// A notification marked the window changed since the last walk.
    private(set) var dirty = true

    /// Records a notification and moves the due time.
    mutating func note(_ trigger: Trigger, at now: Date) {
        dirty = true
        switch trigger {
        case .structural:
            let next = now.addingTimeInterval(0.5)
            dueAt = min(dueAt ?? next, next)
            structuralPending = true
        case .typing:
            // A pending structural snapshot is sooner and reads the typed text too.
            if structuralPending, let dueAt, dueAt > now { return }
            let start = typingSince ?? now
            typingSince = start
            dueAt = min(now.addingTimeInterval(2), start.addingTimeInterval(8))
        }
    }

    /// Schedules one snapshot after `delay`, for example 1 s after a web-tree wake.
    mutating func schedule(after delay: TimeInterval, at now: Date) {
        let next = now.addingTimeInterval(delay)
        dueAt = min(dueAt ?? next, next)
        dirty = true
    }

    /// True when a snapshot may run now; a call inside the 0.5 s floor moves the due time instead.
    mutating func admit(at now: Date) -> Bool {
        if let lastAttempt, now.timeIntervalSince(lastAttempt) < 0.5 {
            dueAt = lastAttempt.addingTimeInterval(0.5)
            return false
        }
        lastAttempt = now
        dueAt = nil
        typingSince = nil
        structuralPending = false
        return true
    }

    /// True when the pending snapshot is due.
    func isDue(at now: Date) -> Bool { dueAt.map { $0 <= now } ?? false }

    /// True when a snapshot is pending.
    var isPending: Bool { dueAt != nil }

    /// Records a finished walk.
    mutating func walked(at now: Date) {
        lastWalk = now
        dirty = false
    }

    /// A new app activation: forget intervals and mark dirty.
    mutating func reset() {
        self = CaptureScheduler()
    }

    /// The backstop poll rule: walk, or count a sighting when the probe is unchanged.
    func pollShouldWalk(probeChanged: Bool, idle: Bool, at now: Date) -> Bool {
        if isPending { return false }
        guard let lastWalk else { return true }
        let age = now.timeIntervalSince(lastWalk)
        if !dirty && age < 6 { return false }
        if probeChanged { return true }
        return age >= 30 && !idle
    }
}
