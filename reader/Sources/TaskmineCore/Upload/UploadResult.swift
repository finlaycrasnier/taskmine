import Foundation

/// What one batch upload came to (docs/CONTRACTS.md section 2, reader spec 8.6).
public enum UploadResult: Sendable, Equatable {
    /// 2xx with an ack for the batch.
    case acked(Ack)
    /// Keep the batch and send it again next cycle; the code is fixed, never text.
    case retry(String)
    /// Stop recording and show the message for the stop.
    case stop(RecorderState.Stop)
}
