import Foundation

/// Why a reader could not start.
public enum ReaderError: Error, Equatable {
    /// "Accessibility permission is required for live capture."
    case accessibilityUnavailable
    /// Screen Recording is not granted; the picture reader stays off.
    case screenRecordingUnavailable
}
