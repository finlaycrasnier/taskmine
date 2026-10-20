import Foundation

/// The status file `status.json` (reader spec 8.6): states, counts and fixed codes, never text.
public struct StatusReport: Sendable, Encodable {
    public var updatedAt = Date()
    public var captureState = "paused"
    public var accessibilityGranted = false
    public var allowedApps: [String] = []
    public var pendingCount = 0
    public var lastCaptureAt: Date?
    public var lastUploadAt: Date?
    public var lastError: String?
    public var sessionAvailable = true
    public var screenCaptureGranted = false
    public var ocrEnabled = false
    public var ocrStatus = "off"
    public var coverage = Coverage()
    public var connectionState = "offline"
    public var connectionMessage = ""

    /// An empty report.
    public init() {}

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", updatedAt = "updated_at", captureState = "capture_state"
        case accessibilityGranted = "accessibility_granted", allowedApps = "allowed_app_bundle_ids"
        case pendingCount = "pending_count", lastCaptureAt = "last_capture_at", lastUploadAt = "last_upload_at"
        case lastError = "last_error", sessionAvailable = "session_available"
        case screenCaptureGranted = "screen_capture_granted", ocrEnabled = "ocr_enabled", ocrStatus = "ocr_status"
        case coverage, connectionState = "connection_state", connectionMessage = "connection_message"
    }

    /// Encodes snake_case keys and wire times.
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode("1", forKey: .schemaVersion)
        try c.encode(WireFormat.time(updatedAt), forKey: .updatedAt)
        try c.encode(captureState, forKey: .captureState)
        try c.encode(accessibilityGranted, forKey: .accessibilityGranted)
        try c.encode(allowedApps, forKey: .allowedApps)
        try c.encode(pendingCount, forKey: .pendingCount)
        try c.encode(lastCaptureAt.map(WireFormat.time), forKey: .lastCaptureAt)
        try c.encode(lastUploadAt.map(WireFormat.time), forKey: .lastUploadAt)
        try c.encode(lastError, forKey: .lastError)
        try c.encode(sessionAvailable, forKey: .sessionAvailable)
        try c.encode(screenCaptureGranted, forKey: .screenCaptureGranted)
        try c.encode(ocrEnabled, forKey: .ocrEnabled)
        try c.encode(ocrStatus, forKey: .ocrStatus)
        try c.encode(coverage, forKey: .coverage)
        try c.encode(connectionState, forKey: .connectionState)
        try c.encode(connectionMessage, forKey: .connectionMessage)
    }

    /// Writes the report atomically with mode 0600.
    public func write(to url: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .prettyPrinted, .withoutEscapingSlashes]
        try Paths.writePrivateAtomically(url, try encoder.encode(self))
    }
}
