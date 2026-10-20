import Foundation

/// What the menu shows: whether capture runs, its title, detail and primary action (reader spec 1.5, 9.3).
public struct RecorderState: Sendable, Equatable {
    /// The menu's primary action.
    public enum Action: Sendable, Equatable { case record, pause, resume }

    /// Why uploads stopped recording (docs/CONTRACTS.md section 2).
    public enum Stop: String, Sendable, Equatable { case rejected, tooLarge = "too_large", upgradeNeeded = "upgrade_needed" }

    /// The user chose Record my activity and has not paused since.
    public var userWantsRecording = false
    /// Recording was on before the last quit and the user has not chosen since.
    public var offSinceRestart = false
    /// The core's policy state, or nil when the core cannot be reached.
    public var policyState: Policy.State?
    public var accessibilityGranted = false
    /// The Mac is awake, unlocked and on the console.
    public var sessionAvailable = true
    /// Set when the core refused uploads; cleared when the user records again.
    public var stopped: Stop?
    /// The last surface that was not captured, shown while recording.
    public var lastReason: CoverageReason?
    public var canvasNotice = false
    public var quarantined = 0

    /// A paused state.
    public init() {}

    /// True when every condition for capture holds.
    public var isCapturing: Bool {
        userWantsRecording && stopped == nil && policyState == .active && sessionAvailable && accessibilityGranted
    }

    /// The summary title.
    public var title: String {
        if let stopped { return stopped == .upgradeNeeded ? "Update to keep recording" : "Mac disconnected" }
        if !userWantsRecording { return offSinceRestart ? "Recording is off" : "Recording paused" }
        if isCapturing { return "Recording activity" }
        if policyState == nil { return "Cannot reach the server" }
        if policyState == .paused { return "Paused, still connected" }
        return "Waiting to record"
    }

    /// The summary detail.
    public var detail: String {
        if let stopped { return Self.stopMessage(stopped) }
        if !userWantsRecording { return pausedDetail }
        if !sessionAvailable { return "Activity recording is paused while this Mac is locked or a call is active." }
        if policyState == nil {
            return "Recording paused because its settings, permissions or local service could not be verified."
        }
        if policyState == .paused { return CoverageReason.sourceDisabled.explanation }
        if !accessibilityGranted { return CoverageReason.permissionUnavailable.explanation }
        return recordingDetail
    }

    /// The primary action, or nil when none applies.
    public var primaryAction: Action? {
        if stopped == .upgradeNeeded { return nil }
        if stopped != nil { return .record }
        if userWantsRecording { return .pause }
        return offSinceRestart ? .resume : .record
    }

    /// The status item tooltip.
    public var tooltip: String { isCapturing ? "Recording permitted activity" : "Activity recording paused" }

    /// The detail while paused.
    private var pausedDetail: String {
        var parts = [offSinceRestart ? "Recording is off since the app restarted. Resume?"
            : "Recording is paused. Choose Record my activity to save permitted actions and visible context."]
        if !accessibilityGranted { parts.append("Allow Accessibility permission in this menu first.") }
        return parts.joined(separator: " ")
    }

    /// The detail while recording.
    private var recordingDetail: String {
        var parts = ["Saving permitted actions and visible context."]
        if let lastReason, lastReason != .canvasNeedsScreenRecording { parts.append(lastReason.explanation) }
        if canvasNotice { parts.append(CoverageReason.canvasNeedsScreenRecording.explanation) }
        if quarantined > 0 {
            let items = quarantined == 1 ? "1 item was" : "\(quarantined) items were"
            parts.append("\(items) set aside on this Mac because the server could not accept them. Recording continues.")
        }
        parts.append("Chrome, Edge, Brave, Arc and Firefox pages are read directly. "
            + "Safari pages are not read yet. Use Chrome, Edge, Brave, Arc or Firefox for now.")
        return parts.joined(separator: " ")
    }

    /// The message for an upload stop (reader spec 8.6).
    public static func stopMessage(_ stop: Stop) -> String {
        switch stop {
        case .upgradeNeeded: return "Update Taskmine to keep recording."
        case .rejected, .tooLarge:
            return "The service rejected pending activity. Recording is stopped. Review the pending activity and "
                + "its capture permissions before choosing Record my activity. Nothing was marked as uploaded."
        }
    }
}
