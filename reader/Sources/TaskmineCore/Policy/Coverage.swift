import Foundation

/// Coverage counters for the status file: counts and fixed reasons only (reader spec 4.4).
public struct Coverage: Sendable, Equatable, Encodable {
    public private(set) var capturedCount = 0
    public private(set) var excludedCount = 0
    public private(set) var unavailableCount = 0
    public private(set) var lastReason: CoverageReason?
    public private(set) var lastReasonAt: Date?
    public var quarantinedCount = 0
    public private(set) var canvasScreenRecordingMissingAt: Date?

    /// Empty counters; they reset on every launch.
    public init() {}

    /// Counts one stored reading and clears the last reason.
    public mutating func captured() {
        capturedCount += 1
        lastReason = nil
    }

    /// Counts one surface that was not captured.
    public mutating func record(_ reason: CoverageReason, at time: Date) {
        if reason.isExclusion { excludedCount += 1 } else { unavailableCount += 1 }
        lastReason = reason
        lastReasonAt = time
        if reason == .canvasNeedsScreenRecording { canvasScreenRecordingMissingAt = time }
    }

    /// True for 120 s after the last canvas document missed for want of Screen Recording.
    public func showsCanvasNotice(at time: Date) -> Bool {
        canvasScreenRecordingMissingAt.map { time.timeIntervalSince($0) <= 120 } ?? false
    }

    enum CodingKeys: String, CodingKey {
        case capturedCount = "captured_count", excludedCount = "excluded_count"
        case unavailableCount = "unavailable_count", lastReason = "last_reason", lastReasonAt = "last_reason_at"
        case quarantinedCount = "quarantined_count", canvasAt = "canvas_screen_recording_missing_at"
    }

    /// Encodes snake_case keys and wire times.
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(capturedCount, forKey: .capturedCount)
        try c.encode(excludedCount, forKey: .excludedCount)
        try c.encode(unavailableCount, forKey: .unavailableCount)
        try c.encode(lastReason?.rawValue, forKey: .lastReason)
        try c.encode(lastReasonAt.map(WireFormat.time), forKey: .lastReasonAt)
        try c.encode(quarantinedCount, forKey: .quarantinedCount)
        try c.encode(canvasScreenRecordingMissingAt.map(WireFormat.time), forKey: .canvasAt)
    }
}
